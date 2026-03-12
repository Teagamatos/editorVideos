import json
import os
import shutil
import tempfile
import traceback

import video_tarja
from video_editor import (
    interpretar_roteiro_com_openai_texto,
    gerar_variaveis_pipeline,
    baixar_arquivo_drive_por_link,
    aplicar_tarjas,
    cortar_multiplos_trechos,
    juntar_videos,
    adicionar_bgm_com_ducking,
    inserir_video_em_baixo,
    fazer_upload_drive,
    tempo_para_segundos,
    separar_tarja,
)
from database import atualizar_status
from logger import get_logger

log = get_logger(__name__)

CREDS_PATH = "tfclab-secret.json"

# ══════════════════════════════════════════════════════════════════
#  Ponto de entrada público
# ══════════════════════════════════════════════════════════════════

def disparar_automacao(
    titulo: str,
    link: str,
    roteiro: str,
    registro: dict,
    pasta_destino_id: str,
) -> None:
    job_id = registro.get("id", "?")
    log.info(f"[job={job_id}] === AUTOMAÇÃO DISPARADA === titulo='{titulo}'")
    log.info(f"[job={job_id}] Pasta de destino Drive: {pasta_destino_id}")

    # Diretório isolado por job — evita colisão em requests simultâneas
    job_dir = tempfile.mkdtemp(prefix=f"job_{job_id}_")
    original_dir = os.getcwd()
    log.info(f"[job={job_id}] Diretório de trabalho: {job_dir}")

    atualizar_status(job_id, "processando")

    try:
        os.chdir(job_dir)
        _executar_pipeline(titulo, link, roteiro, registro, job_id, pasta_destino_id)
        atualizar_status(job_id, "concluido")
        log.info(f"[job={job_id}] ✅ Pipeline concluído com sucesso.")

    except Exception as exc:
        tb = traceback.format_exc()
        log.error(f"[job={job_id}] ✗ Falha no pipeline: {exc}\n{tb}")
        atualizar_status(job_id, "erro", erro=f"{type(exc).__name__}: {exc}\n\n{tb}")

    finally:
        os.chdir(original_dir)
        _limpar_job_dir(job_dir, job_id)


# ══════════════════════════════════════════════════════════════════
#  Pipeline interno
# ══════════════════════════════════════════════════════════════════

def _executar_pipeline(
    titulo: str,
    link: str,
    roteiro: str,
    registro: dict,
    job_id,
    pasta_destino_id: str,
) -> None:

    # ── PASSO 1: Interpretar roteiro com IA ───────────────────────
    log.info(f"[job={job_id}] [1/11] Interpretando roteiro com OpenAI...")
    texto_para_ia = f"TÍTULO DO EPISÓDIO:\n{titulo}\n\nROTEIRO:\n{roteiro}".strip()

    try:
        roteiro_json = interpretar_roteiro_com_openai_texto(texto_para_ia)
    except Exception as exc:
        raise RuntimeError(f"Falha ao interpretar roteiro com OpenAI: {exc}") from exc

    log.info(f"[job={job_id}] JSON da IA:\n{json.dumps(roteiro_json, ensure_ascii=False, indent=2)}")

    # ── PASSO 2: Gerar config do pipeline ─────────────────────────
    log.info(f"[job={job_id}] [2/11] Gerando variáveis do pipeline...")
    try:
        config = gerar_variaveis_pipeline(roteiro_json)
    except Exception as exc:
        raise RuntimeError(f"Falha ao gerar variáveis do pipeline: {exc}") from exc

    programa = config["programa"].lower()
    if programa == "rtb":
        pasta = "RTB"
    elif programa == "gp":
        pasta = "GP"
    else:
        raise ValueError(f"Programa desconhecido: '{config['programa']}'. Esperado: RTB ou GP.")

    log.info(f"[job={job_id}] Programa: {programa.upper()} | Trechos: {len(config['trechos'])} | Tarjas: {len(config['lista_nome_tarja'])}")

    # ── PASSO 3: Criar tarjas ──────────────────────────────────────
    log.info(f"[job={job_id}] [3/11] Gerando tarjas...")

    for i, item in enumerate(config["lista_nome_tarja"], 1):
        try:
            dados = separar_tarja(item)
            nome = dados["nome"]
            cargo = dados["cargo"]
            arquivo = dados["arquivo"]
            log.info(f"[job={job_id}]   Tarja {i}: {nome} | {cargo}")
            video_tarja.main(pasta, nome, cargo, arquivo, "openai")
        except Exception as exc:
            raise RuntimeError(f"Falha ao criar tarja {i} ({item}): {exc}") from exc

    # ── PASSO 4: Baixar vídeo do Drive ────────────────────────────
    log.info(f"[job={job_id}] [4/11] Baixando vídeo do Google Drive...")
    try:
        arquivo_video = baixar_arquivo_drive_por_link(
            link_drive=link,
            pasta_saida=os.path.join(pasta, "Video"),
            extensoes=[".mp4", ".mov"],
        )
    except Exception as exc:
        raise RuntimeError(f"Falha ao baixar vídeo do Drive (link='{link}'): {exc}") from exc

    log.info(f"[job={job_id}] Vídeo baixado: {arquivo_video}")

    # ── PASSO 5: Aplicar tarjas ────────────────────────────────────
    log.info(f"[job={job_id}] [5/11] Aplicando tarjas...")
    file_com_tarja = "video_com_tarja.mp4"
    ok = aplicar_tarjas(
        arquivo_entrada=arquivo_video,
        arquivo_saida=file_com_tarja,
        lista_tarjas=config["lista_tarjas"],
    )
    if not ok:
        raise RuntimeError("FFmpeg falhou ao aplicar tarjas.")

    # ── PASSO 6: Cortar trechos ───────────────────────────────────
    log.info(f"[job={job_id}] [6/11] Cortando {len(config['trechos'])} trechos...")
    cortados = cortar_multiplos_trechos(
        arquivo_entrada=file_com_tarja,
        pasta_saida="trechos",
        trechos=config["trechos"],
        reencoder=True,
        pos_tarja=True,
    )
    if not cortados:
        raise RuntimeError("Nenhum trecho cortado. Verifique os timestamps do roteiro.")
    log.info(f"[job={job_id}] {len(cortados)}/{len(config['trechos'])} trechos cortados.")

    # ── PASSO 7: Juntar trechos + vinhetas ───────────────────────
    log.info(f"[job={job_id}] [7/11] Juntando {len(config['lista_arquivos'])} arquivos...")
    file_junto = "video_junto.mp4"
    ok = juntar_videos(
        lista_arquivos=config["lista_arquivos"],
        arquivo_saida=file_junto,
        reencoder=True,
    )
    if not ok:
        raise RuntimeError("FFmpeg falhou ao juntar vídeos.")

    # ── PASSO 8: Aplicar QR Code ──────────────────────────────────
    log.info(f"[job={job_id}] [8/11] Aplicando QR Code...")
    file_qr = "video_com_qr.mp4"
    qr_path = os.path.join(pasta, "QR CODE.png")
    if not os.path.isfile(qr_path):
        raise FileNotFoundError(f"QR Code não encontrado: {qr_path}")

    ok = aplicar_tarjas(
        arquivo_entrada=file_junto,
        arquivo_saida=file_qr,
        lista_tarjas=[(qr_path, "00:03:00", "00:43:00")],
    )
    if not ok:
        raise RuntimeError("FFmpeg falhou ao aplicar QR Code.")

    # ── PASSO 9: BGM com ducking ──────────────────────────────────
    log.info(f"[job={job_id}] [9/11] Adicionando música de fundo...")
    file_final = "video_com_musica.mp4"
    ok = adicionar_bgm_com_ducking(
        arquivo_entrada=file_qr,
        arquivo_saida=file_final,
        inicio="00:00:00",
        fim="00:00:20",
        volume_bgm=0.18,
        duck_db=12,
    )
    if not ok:
        raise RuntimeError("FFmpeg falhou ao adicionar BGM.")

    # ── PASSO 10: Inserções finais ────────────────────────────────
    log.info(f"[job={job_id}] [10/11] Aplicando inserções finais...")
    insercoes_candidatas = [
        {"path": os.path.join(pasta, "inserção.mov"),    "start": tempo_para_segundos("00:10:00"), "end": tempo_para_segundos("00:10:12")},
        {"path": os.path.join(pasta, "inscreva-se.mov"), "start": tempo_para_segundos("00:15:00"), "end": tempo_para_segundos("00:15:07")},
        {"path": os.path.join(pasta, "instagram.mov"),   "start": tempo_para_segundos("00:20:00"), "end": tempo_para_segundos("00:20:10")},
    ]

    for ins in insercoes_candidatas:
        if not os.path.isfile(ins["path"]):
            log.warning(f"[job={job_id}] Inserção não encontrada, será ignorada: {ins['path']}")

    insercoes_validas = [ins for ins in insercoes_candidatas if os.path.isfile(ins["path"])]
    arquivo_final = "video_finalizado.mp4"

    if insercoes_validas:
        ok = inserir_video_em_baixo(
            video_principal=file_final,
            saida=arquivo_final,
            insercoes=insercoes_validas,
        )
        if not ok:
            raise RuntimeError("FFmpeg falhou ao aplicar inserções finais.")
    else:
        log.warning(f"[job={job_id}] Nenhuma inserção válida encontrada. Usando vídeo sem inserções.")
        os.rename(file_final, arquivo_final)

    # ── PASSO 11: Upload para o Google Drive ──────────────────────
    log.info(f"[job={job_id}] [11/11] Enviando vídeo finalizado para o Drive...")

    # Nome do arquivo no Drive: título do episódio sanitizado
    nome_drive = _sanitizar_nome(titulo) + ".mp4"
    log.info(f"[job={job_id}] Nome no Drive: '{nome_drive}' | Pasta ID: '{pasta_destino_id}'")

    try:
        resultado_upload = fazer_upload_drive(
            arquivo_local=arquivo_final,
            folder_id=pasta_destino_id,
            credenciais_json_path=CREDS_PATH,
            nome_no_drive=nome_drive,
            mime_type="video/mp4",
        )
    except Exception as exc:
        raise RuntimeError(f"Falha ao fazer upload para o Drive: {exc}") from exc

    log.info(
        f"[job={job_id}] ✅ Upload concluído | "
        f"file_id={resultado_upload['id']} | "
        f"link={resultado_upload['link']}"
    )

    # Salva o link do vídeo final no Supabase
    atualizar_status(job_id, "concluido", link_video_final=resultado_upload["link"])


# ══════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════

def _sanitizar_nome(texto: str) -> str:
    """Remove caracteres inválidos para nome de arquivo no Drive."""
    import re
    texto = texto.strip()
    texto = re.sub(r'[\\/*?:"<>|]', "", texto)   # chars proibidos no Drive/Windows
    texto = re.sub(r"\s+", " ", texto)             # espaços duplos
    return texto[:200]                             # Drive aceita até 255 chars


def _limpar_job_dir(job_dir: str, job_id) -> None:
    keep = os.getenv("KEEP_JOB_DIRS", "false").lower() == "true"
    if keep:
        log.info(f"[job={job_id}] KEEP_JOB_DIRS=true — diretório mantido: {job_dir}")
        return
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
        log.info(f"[job={job_id}] Diretório temporário removido: {job_dir}")
    except Exception as exc:
        log.warning(f"[job={job_id}] Falha ao remover diretório temporário: {exc}")
