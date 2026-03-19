import json
import os
import shutil
import subprocess
import tempfile
import traceback

import video_tarja
from video_editor import (
    interpretar_roteiro_com_openai_texto,
    gerar_variaveis_pipeline,
    baixar_arquivo_drive_por_link,
    aplicar_tarjas,
    cortar_video,
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
from notifier import notificar_erro_clickup

log = get_logger(__name__)

CREDS_PATH = None
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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
        notificar_erro_clickup(
            job_id=job_id,
            titulo=titulo,
            etapa="pipeline",
            erro=f"{type(exc).__name__}: {exc}",
            link_video=link,
        )

    finally:
        os.chdir(original_dir)
        _limpar_job_dir(job_dir, job_id)


# ══════════════════════════════════════════════════════════════════
#  Pipeline interno
# ══════════════════════════════════════════════════════════════════

def disparar_automacao_cortes(
    titulo: str,
    link: str,
    roteiro: str,
    registro: dict,
    pasta_destino_id: str,
) -> None:
    job_id = registro.get("id", "?")
    log.info(f"[job={job_id}] === AUTOMACAO CORTES DISPARADA === titulo='{titulo}'")
    log.info(f"[job={job_id}] Pasta de destino Drive (cortes): {pasta_destino_id}")

    job_dir = tempfile.mkdtemp(prefix=f"job_cortes_{job_id}_")
    original_dir = os.getcwd()
    log.info(f"[job={job_id}] Diretorio de trabalho (cortes): {job_dir}")

    atualizar_status(job_id, "processando")

    try:
        os.chdir(job_dir)
        _executar_pipeline_cortes(
            titulo=titulo,
            link=link,
            roteiro=roteiro,
            job_id=job_id,
            pasta_destino_id=pasta_destino_id,
            centralizar_falante=False,
        )
        atualizar_status(job_id, "concluido")
        log.info(f"[job={job_id}] Pipeline de cortes concluido com sucesso.")

    except Exception as exc:
        tb = traceback.format_exc()
        log.error(f"[job={job_id}] Falha no pipeline de cortes: {exc}\n{tb}")
        atualizar_status(job_id, "erro", erro=f"{type(exc).__name__}: {exc}\n\n{tb}")
        notificar_erro_clickup(
            job_id=job_id,
            titulo=titulo,
            etapa="pipeline_cortes",
            erro=f"{type(exc).__name__}: {exc}",
            link_video=link,
        )

    finally:
        os.chdir(original_dir)
        _limpar_job_dir(job_dir, job_id)


def disparar_automacao_cortes_teste_centralizado(
    titulo: str,
    link: str,
    roteiro: str,
    registro: dict,
    pasta_destino_id: str,
) -> None:
    job_id = registro.get("id", "?")
    log.info(f"[job={job_id}] === AUTOMACAO CORTES TESTE CENTRALIZADO === titulo='{titulo}'")
    log.info(f"[job={job_id}] Pasta de destino Drive (cortes): {pasta_destino_id}")

    job_dir = tempfile.mkdtemp(prefix=f"job_cortes_track_{job_id}_")
    original_dir = os.getcwd()
    log.info(f"[job={job_id}] Diretorio de trabalho (cortes tracking): {job_dir}")

    atualizar_status(job_id, "processando")

    try:
        os.chdir(job_dir)
        _executar_pipeline_cortes(
            titulo=titulo,
            link=link,
            roteiro=roteiro,
            job_id=job_id,
            pasta_destino_id=pasta_destino_id,
            centralizar_falante=True,
        )
        atualizar_status(job_id, "concluido")
        log.info(f"[job={job_id}] Pipeline de cortes tracking concluido com sucesso.")

    except Exception as exc:
        tb = traceback.format_exc()
        log.error(f"[job={job_id}] Falha no pipeline de cortes tracking: {exc}\n{tb}")
        atualizar_status(job_id, "erro", erro=f"{type(exc).__name__}: {exc}\n\n{tb}")
        notificar_erro_clickup(
            job_id=job_id,
            titulo=titulo,
            etapa="pipeline_cortes_tracking",
            erro=f"{type(exc).__name__}: {exc}",
            link_video=link,
        )

    finally:
        os.chdir(original_dir)
        _limpar_job_dir(job_dir, job_id)


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
    lista_arquivos_juncao = [_resolver_arquivo_juncao(c) for c in config["lista_arquivos"]]
    log.info(f"[job={job_id}] [7/11] Juntando {len(lista_arquivos_juncao)} arquivos...")
    file_junto = "video_junto.mp4"
    ok = juntar_videos(
        lista_arquivos=lista_arquivos_juncao,
        arquivo_saida=file_junto,
        reencoder=True,
    )
    if not ok:
        raise RuntimeError("FFmpeg falhou ao juntar vídeos.")

    # ── PASSO 8: Aplicar QR Code ──────────────────────────────────
    log.info(f"[job={job_id}] [8/11] Aplicando QR Code...")
    file_qr = "video_com_qr.mp4"
    qr_path = _asset_path(pasta, "QR CODE.png")
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
        {"path": _asset_path(pasta, "inserção.mov"),    "start": tempo_para_segundos("00:10:00"), "end": tempo_para_segundos("00:10:12")},
        {"path": _asset_path(pasta, "inscreva-se.mov"), "start": tempo_para_segundos("00:15:00"), "end": tempo_para_segundos("00:15:07")},
        {"path": _asset_path(pasta, "instagram.mov"),   "start": tempo_para_segundos("00:20:00"), "end": tempo_para_segundos("00:20:10")},
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
            credenciais_json_path=None,
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

def _executar_pipeline_cortes(
    titulo: str,
    link: str,
    roteiro: str,
    job_id,
    pasta_destino_id: str,
    centralizar_falante: bool = False,
) -> None:
    log.info(f"[job={job_id}] [1/5] Interpretando roteiro de cortes com OpenAI...")
    texto_para_ia = f"TITULO DO EPISODIO:\n{titulo}\n\nROTEIRO:\n{roteiro}".strip()

    try:
        roteiro_json = interpretar_roteiro_com_openai_texto(texto_para_ia)
    except Exception as exc:
        raise RuntimeError(f"Falha ao interpretar roteiro com OpenAI: {exc}") from exc

    trechos = _extrair_trechos(roteiro_json)
    if not trechos:
        raise RuntimeError("Nenhum trecho valido retornado pela IA para corte.")
    log.info(f"[job={job_id}] Trechos identificados para corte: {len(trechos)}")

    log.info(f"[job={job_id}] [2/5] Baixando video do Google Drive...")
    try:
        arquivo_video = baixar_arquivo_drive_por_link(
            link_drive=link,
            pasta_saida="entrada",
            extensoes=[".mp4", ".mov"],
        )
    except Exception as exc:
        raise RuntimeError(f"Falha ao baixar video do Drive (link='{link}'): {exc}") from exc

    log.info(f"[job={job_id}] [3/5] Cortando {len(trechos)} trechos...")
    cortados = _cortar_trechos_com_log(
        arquivo_entrada=arquivo_video,
        trechos=trechos,
        pasta_saida="trechos",
        reencoder=True,
        pos_tarja=False,
        job_id=job_id,
    )
    if not cortados:
        raise RuntimeError("Nenhum trecho cortado. Verifique os timestamps do roteiro.")

    log.info(f"[job={job_id}] [4/5] Juntando trechos cortados...")
    arquivo_final = "video_cortado_final.mp4"
    if len(cortados) == 1:
        shutil.copy2(cortados[0], arquivo_final)
    else:
        ok = juntar_videos(
            lista_arquivos=cortados,
            arquivo_saida=arquivo_final,
            reencoder=True,
        )
        if not ok:
            raise RuntimeError("FFmpeg falhou ao juntar os trechos cortados.")

    log.info(f"[job={job_id}] Convertendo cortes para formato vertical 9:16...")
    arquivo_vertical = "video_cortado_final_9x16.mp4"
    if centralizar_falante:
        _converter_para_9x16_com_tracking(
            arquivo_entrada=arquivo_final,
            arquivo_saida=arquivo_vertical,
        )
    else:
        _converter_para_9x16(
            arquivo_entrada=arquivo_final,
            arquivo_saida=arquivo_vertical,
        )
    arquivo_final = arquivo_vertical

    nome_drive = _sanitizar_nome(f"{titulo} - cortes") + ".mp4"
    target_seconds = int(os.getenv("CORTES_TARGET_SECONDS", "180"))
    if target_seconds > 0:
        duracao_final = _obter_duracao_segundos(arquivo_final)
        if duracao_final and duracao_final > target_seconds:
            log.info(
                f"[job={job_id}] Ajustando duração dos cortes de {duracao_final:.2f}s para {target_seconds}s sem perder conteúdo..."
            )
            arquivo_ajustado = "video_cortado_final_3min.mp4"
            _acelerar_para_duracao(
                arquivo_entrada=arquivo_final,
                arquivo_saida=arquivo_ajustado,
                duracao_atual=duracao_final,
                duracao_alvo=target_seconds,
            )
            arquivo_final = arquivo_ajustado

    log.info(f"[job={job_id}] [5/5] Enviando video de cortes para o Drive...")
    try:
        resultado_upload = fazer_upload_drive(
            arquivo_local=arquivo_final,
            folder_id=pasta_destino_id,
            credenciais_json_path=None,
            nome_no_drive=nome_drive,
            mime_type="video/mp4",
        )
    except Exception as exc:
        raise RuntimeError(f"Falha ao fazer upload dos cortes para o Drive: {exc}") from exc

    atualizar_status(job_id, "concluido", link_video_final=resultado_upload["link"])
    log.info(
        f"[job={job_id}] Upload de cortes concluido | "
        f"file_id={resultado_upload['id']} | "
        f"link={resultado_upload['link']}"
    )


def _cortar_trechos_com_log(
    *,
    arquivo_entrada: str,
    trechos: list[tuple[str, str]],
    pasta_saida: str,
    reencoder: bool,
    pos_tarja: bool,
    job_id,
) -> list[str]:
    os.makedirs(pasta_saida, exist_ok=True)
    ext = os.path.splitext(arquivo_entrada)[1] or ".mp4"
    gerados: list[str] = []

    for i, (inicio, fim) in enumerate(trechos, start=1):
        saida = os.path.join(pasta_saida, f"trecho_{i:02d}{ext}")
        log.info(f"[job={job_id}] Cortando trecho {i}/{len(trechos)}: {inicio} -> {fim}")
        try:
            ok = cortar_video(
                arquivo_entrada=arquivo_entrada,
                arquivo_saida=saida,
                inicio=inicio,
                fim=fim,
                reencoder=reencoder,
                pos_tarja=pos_tarja,
            )
        except Exception as exc:
            log.warning(f"[job={job_id}] Falha no trecho {i}: {exc}")
            ok = False

        if ok:
            gerados.append(saida)
            log.info(f"[job={job_id}] Trecho {i}/{len(trechos)} concluido.")
        else:
            log.warning(f"[job={job_id}] Trecho {i}/{len(trechos)} ignorado por falha.")

    log.info(f"[job={job_id}] Cortes finalizados: {len(gerados)}/{len(trechos)}")
    return gerados


def _sanitizar_nome(texto: str) -> str:
    """Remove caracteres inválidos para nome de arquivo no Drive."""
    import re
    texto = texto.strip()
    texto = re.sub(r'[\\/*?:"<>|]', "", texto)   # chars proibidos no Drive/Windows
    texto = re.sub(r"\s+", " ", texto)             # espaços duplos
    return texto[:200]                             # Drive aceita até 255 chars


def _extrair_trechos(roteiro_json: dict) -> list[tuple[str, str]]:
    bruto = roteiro_json.get("trechos", [])
    trechos: list[tuple[str, str]] = []
    ignorados = 0
    for item in bruto:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        inicio = str(item[0]).strip()
        fim = str(item[1]).strip()
        if not inicio or not fim:
            continue
        try:
            if tempo_para_segundos(fim) <= tempo_para_segundos(inicio):
                ignorados += 1
                continue
        except Exception:
            ignorados += 1
            continue
        trechos.append((inicio, fim))
    if ignorados:
        log.warning("Trechos ignorados por duração inválida (fim <= início): %s", ignorados)
    return trechos


def _obter_duracao_segundos(arquivo_video: str) -> float:
    comando = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        arquivo_video,
    ]
    resultado = subprocess.run(comando, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if resultado.returncode != 0:
        raise RuntimeError(f"Falha ao obter duração do vídeo: {resultado.stderr.strip()}")
    try:
        return float((resultado.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError("Não foi possível interpretar a duração do vídeo.") from exc


def _atempo_chain(fator: float) -> str:
    partes: list[str] = []
    restante = fator
    while restante > 2.0:
        partes.append("atempo=2.0")
        restante /= 2.0
    while restante < 0.5:
        partes.append("atempo=0.5")
        restante /= 0.5
    partes.append(f"atempo={restante:.6f}")
    return ",".join(partes)


def _acelerar_para_duracao(
    arquivo_entrada: str,
    arquivo_saida: str,
    duracao_atual: float,
    duracao_alvo: int,
) -> None:
    if duracao_atual <= 0 or duracao_alvo <= 0:
        raise ValueError("Duração inválida para ajuste.")

    fator = duracao_atual / duracao_alvo
    if fator <= 1.0:
        shutil.copy2(arquivo_entrada, arquivo_saida)
        return

    filtro = f"[0:v]setpts=PTS/{fator:.6f}[v];[0:a]{_atempo_chain(fator)}[a]"
    comando = [
        "ffmpeg", "-y",
        "-i", arquivo_entrada,
        "-filter_complex", filtro,
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-c:a", "aac",
        arquivo_saida,
    ]
    resultado = subprocess.run(comando, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if resultado.returncode != 0:
        raise RuntimeError(f"Falha ao ajustar duração para {duracao_alvo}s: {resultado.stderr}")


def _converter_para_9x16(
    arquivo_entrada: str,
    arquivo_saida: str,
    largura: int = 1080,
    altura: int = 1920,
) -> None:
    filtro = (
        f"scale={largura}:{altura}:force_original_aspect_ratio=increase,"
        f"crop={largura}:{altura}"
    )
    comando = [
        "ffmpeg", "-y",
        "-i", arquivo_entrada,
        "-vf", filtro,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        arquivo_saida,
    ]
    resultado = subprocess.run(comando, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if resultado.returncode != 0:
        raise RuntimeError(f"Falha ao converter vídeo para 9:16: {resultado.stderr}")


def _converter_para_9x16_com_tracking(
    arquivo_entrada: str,
    arquivo_saida: str,
    largura: int = 1080,
    altura: int = 1920,
) -> None:
    try:
        import cv2
    except Exception as exc:
        raise RuntimeError(
            "OpenCV não disponível para tracking. Instale opencv-python-headless e use endpoint de teste novamente."
        ) from exc

    cap = cv2.VideoCapture(arquivo_entrada)
    if not cap.isOpened():
        raise RuntimeError("Falha ao abrir vídeo para tracking 9:16.")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if src_w <= 0 or src_h <= 0:
        cap.release()
        raise RuntimeError("Dimensões inválidas do vídeo para tracking 9:16.")

    crop_w = max(1, int(src_h * 9 / 16))
    if crop_w > src_w:
        crop_w = src_w
    crop_h = src_h

    tmp_sem_audio = "video_cortes_tracking_sem_audio.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(tmp_sem_audio, fourcc, fps, (largura, altura))
    if not out.isOpened():
        cap.release()
        raise RuntimeError("Falha ao iniciar escrita do vídeo tracking.")

    face_detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if face_detector.empty():
        out.release()
        cap.release()
        raise RuntimeError("Falha ao carregar classificador de rosto para tracking.")

    center_x = src_w / 2.0
    target_center_x = center_x
    alpha = float(os.getenv("TRACKING_ALPHA", "0.05"))
    detect_every = int(os.getenv("TRACKING_DETECT_EVERY", "6"))
    deadzone_px = float(os.getenv("TRACKING_DEADZONE_PX", "80"))
    max_move_px = float(os.getenv("TRACKING_MAX_MOVE_PX", "12"))
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        if frame_idx % max(1, detect_every) == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
            if len(faces) > 0:
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                face_center = x + (w / 2.0)
                target_center_x = face_center

        delta = target_center_x - center_x
        if abs(delta) <= deadzone_px:
            delta = 0.0
        if delta > max_move_px:
            delta = max_move_px
        elif delta < -max_move_px:
            delta = -max_move_px
        center_x = center_x + (alpha * delta if delta != 0.0 else 0.0)

        x1 = int(round(center_x - crop_w / 2))
        x1 = max(0, min(x1, src_w - crop_w))
        cropped = frame[0:crop_h, x1:x1 + crop_w]
        resized = cv2.resize(cropped, (largura, altura), interpolation=cv2.INTER_LINEAR)
        out.write(resized)
        frame_idx += 1

    out.release()
    cap.release()

    comando_audio = [
        "ffmpeg", "-y",
        "-i", tmp_sem_audio,
        "-i", arquivo_entrada,
        "-map", "0:v:0",
        "-map", "1:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        arquivo_saida,
    ]
    resultado = subprocess.run(comando_audio, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if resultado.returncode != 0:
        raise RuntimeError(f"Falha ao mesclar áudio no tracking 9:16: {resultado.stderr}")


def _asset_path(*parts: str) -> str:
    return os.path.join(BASE_DIR, *parts)


def _resolver_arquivo_juncao(caminho: str) -> str:
    """
    Resolve caminhos da junção no ambiente do Railway:
    - trechos/* ficam relativos ao job_dir (cwd atual)
    - assets estÃ¡ticos (GP/*, RTB/*) viram caminho absoluto em BASE_DIR
    """
    if os.path.isabs(caminho):
        return caminho

    normalizado = caminho.replace("\\", "/").lstrip("./")
    if normalizado.startswith("trechos/"):
        return caminho

    return _asset_path(*normalizado.split("/"))


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
