import subprocess
import os
import tempfile
import sys
import argparse
from typing import List, Tuple, Optional, Dict, Any
import json
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
import io
import re
from urllib.parse import urlparse, parse_qs
import random
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import video_tarja
load_dotenv()

# ══════════════════════════════════════════════════════════════
#  UTILITÁRIOS
# ══════════════════════════════════════════════════════════════

def formatar_tempo(tempo: str) -> str:
    partes = tempo.strip().split(":")
    if len(partes) == 1:
        segundos = int(partes[0])
        return f"{segundos//3600:02d}:{(segundos%3600)//60:02d}:{segundos%60:02d}"
    elif len(partes) == 2:
        return f"00:{int(partes[0]):02d}:{int(partes[1]):02d}"
    elif len(partes) == 3:
        return f"{int(partes[0]):02d}:{int(partes[1]):02d}:{int(partes[2]):02d}"
    else:
        raise ValueError(f"Formato de tempo inválido: '{tempo}'")


def tempo_para_segundos(tempo: str) -> int:
    h, m, s = map(int, formatar_tempo(tempo).split(":"))
    return h * 3600 + m * 60 + s


def verificar_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        print("[ERRO] FFmpeg não encontrado. Instale em: https://ffmpeg.org/download.html")
        return False


# ══════════════════════════════════════════════════════════════
#  CORTE DE VÍDEO
# ══════════════════════════════════════════════════════════════

def cortar_video(
    arquivo_entrada: str,
    arquivo_saida: str,
    inicio: str,
    fim: str,
    reencoder: bool = False,
    pos_tarja: bool = False,
) -> bool:
    """
    Corta um vídeo entre os tempos de início e fim.
    pos_tarja=True força reencoding para preservar frames das tarjas aplicadas.
    """
    if not verificar_ffmpeg():
        return False
    if not os.path.isfile(arquivo_entrada):
        print(f"[ERRO] Arquivo não encontrado: '{arquivo_entrada}'")
        return False

    if pos_tarja:
        reencoder = True

    inicio_fmt = formatar_tempo(inicio)
    fim_fmt    = formatar_tempo(fim)
    codec_flags = ["-c", "copy"] if not reencoder else ["-c:v", "libx264", "-c:a", "aac"]

    comando = [
        "ffmpeg", "-y",
        "-i", arquivo_entrada,
        "-ss", inicio_fmt,
        "-to", fim_fmt,
        *codec_flags,
        arquivo_saida
    ]

    modo = "Reencoding (preciso)" if reencoder else "Stream copy (rápido)"
    if pos_tarja:
        modo += " [forçado por pos_tarja]"

    print(f"\n✂  Cortando: {inicio_fmt} → {fim_fmt}")
    print(f"   Entrada : {arquivo_entrada}")
    print(f"   Saída   : {arquivo_saida}")
    print(f"   Modo    : {modo}\n")

    resultado = subprocess.run(comando, stderr=subprocess.PIPE, text=True)
    if resultado.returncode == 0:
        print(f"[OK] Vídeo salvo em: '{arquivo_saida}'")
        return True
    else:
        print(f"[ERRO] FFmpeg falhou:\n{resultado.stderr}")
        return False


def cortar_multiplos_trechos(
    arquivo_entrada: str,
    pasta_saida: str,
    trechos: List[Tuple[str, str]],
    prefixo: str = "trecho",
    extensao: Optional[str] = None,
    reencoder: bool = False,
    pos_tarja: bool = False,
) -> List[str]:
    """Corta múltiplos trechos de um mesmo vídeo."""
    os.makedirs(pasta_saida, exist_ok=True)
    ext = extensao or os.path.splitext(arquivo_entrada)[1]

    print(f"\n{'='*55}")
    print(f"  Vídeo    : {arquivo_entrada}")
    print(f"  Trechos  : {len(trechos)}")
    print(f"  Destino  : {pasta_saida}")
    if pos_tarja:
        print(f"  Modo     : Reencoding forçado (pos_tarja=True)")
    print(f"{'='*55}")

    arquivos_gerados = []
    for i, (inicio, fim) in enumerate(trechos, start=1):
        nome_saida = os.path.join(pasta_saida, f"{prefixo}_{i:02d}{ext}")
        ok = cortar_video(arquivo_entrada, nome_saida, inicio, fim,
                          reencoder=reencoder, pos_tarja=pos_tarja)
        if ok:
            arquivos_gerados.append(nome_saida)

    print(f"\n✔ Concluído: {len(arquivos_gerados)}/{len(trechos)} trechos cortados.")
    return arquivos_gerados


# ══════════════════════════════════════════════════════════════
#  JUNÇÃO DE VÍDEOS
# ══════════════════════════════════════════════════════════════

def juntar_videos(
    lista_arquivos: List[str],
    arquivo_saida: str,
    reencoder: bool = False,
    width: int = 1920,
    height: int = 1080,
    fps: str = "30000/1001",  # 29.97; se quiser 30 cravado, troque para "30"
    sr: int = 48000,
) -> bool:
    """Junta uma lista de vídeos em um único arquivo."""
    if not verificar_ffmpeg():
        return False

    for f in lista_arquivos:
        if not os.path.isfile(f):
            print(f"[ERRO] Arquivo não encontrado: '{f}'")
            return False

    if len(lista_arquivos) < 2:
        print("[ERRO] Informe pelo menos 2 vídeos para juntar.")
        return False

    print(f"\n{'='*55}")
    print(f"  🔗 Juntando {len(lista_arquivos)} vídeo(s)...")
    for i, f in enumerate(lista_arquivos, 1):
        print(f"    {i}. {f}")
    print(f"  Saída : {arquivo_saida}")
    print(f"  Modo  : {'Reencoding' if reencoder else 'Stream copy (rápido)'}")
    print(f"{'='*55}\n")

    # lista para concat demuxer (modo copy)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f_tmp:
        for arquivo in lista_arquivos:
            caminho_abs = os.path.abspath(arquivo).replace("'", r"\'")
            f_tmp.write(f"file '{caminho_abs}'\n")
        caminho_lista = f_tmp.name

    try:
        if not reencoder:
            comando = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", caminho_lista,
                "-c", "copy",
                arquivo_saida
            ]
        else:
            # inputs
            inputs = []
            for f in lista_arquivos:
                inputs += ["-i", f]
            n = len(lista_arquivos)

            # ✅ Normaliza CADA input antes do concat
            # - scale para 1920x1080
            # - setsar=1 para evitar SAR diferente (ex: 0:1)
            # - fps fixo para todo mundo
            # - áudio: 48kHz stereo
            filters = []
            concat_inputs = []

            for i in range(n):
                filters.append(
                    f"[{i}:v]scale={width}:{height},setsar=1,fps={fps},format=yuv420p[v{i}]"
                )
                filters.append(
                    f"[{i}:a]aformat=sample_rates={sr}:channel_layouts=stereo[a{i}]"
                )
                concat_inputs.append(f"[v{i}][a{i}]")

            filter_str = ";".join(filters) + ";" + "".join(concat_inputs) + f"concat=n={n}:v=1:a=1[vout][aout]"

            comando = [
                "ffmpeg", "-y", *inputs,
                "-filter_complex", filter_str,
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                arquivo_saida
            ]

        resultado = subprocess.run(comando, stderr=subprocess.PIPE, text=True)
        if resultado.returncode == 0:
            print(f"[OK] Vídeo final salvo em: '{arquivo_saida}'")
            return True
        else:
            print(f"[ERRO] FFmpeg falhou:\n{resultado.stderr}")
            return False
    finally:
        try:
            os.unlink(caminho_lista)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════
#  FLUXO COMPLETO: CORTAR E JUNTAR
# ══════════════════════════════════════════════════════════════

def cortar_e_juntar(
    arquivo_entrada: str,
    trechos: List[Tuple[str, str]],
    arquivo_saida_final: str,
    pasta_temporaria: str = "temp_trechos",
    reencoder: bool = False,
    pos_tarja: bool = False,
) -> bool:
    print("\n🎬 INICIANDO: CORTAR E JUNTAR\n")
    arquivos_cortados = cortar_multiplos_trechos(
        arquivo_entrada=arquivo_entrada,
        pasta_saida=pasta_temporaria,
        trechos=trechos,
        reencoder=reencoder,
        pos_tarja=pos_tarja,
    )
    if not arquivos_cortados:
        print("[ERRO] Nenhum trecho foi cortado. Abortando junção.")
        return False

    print("\n🔗 Juntando os trechos...\n")
    ok = juntar_videos(arquivos_cortados, arquivo_saida_final, reencoder=reencoder)
    if ok:
        print(f"\n🎉 Tudo pronto! Vídeo final: '{arquivo_saida_final}'")
    return ok


# ══════════════════════════════════════════════════════════════
#  APLICAR TARJAS (OVERLAY PNG)
# ══════════════════════════════════════════════════════════════

def aplicar_tarjas(
    arquivo_entrada: str,
    arquivo_saida: str,
    lista_tarjas: List[Tuple[str, str, str]],
    reencoder: bool = True,
    margem_inferior_px: int = 0,
    centralizar: bool = False,
) -> bool:
    """
    Aplica múltiplas tarjas PNG em tempos específicos.

    lista_tarjas:
        [
            ("tarja_rodrigo.png", "00:01:03", "00:01:08"),
            ("tarja_fred.png",    "00:02:43", "00:02:48"),
        ]

    margem_inferior_px : distância da borda inferior (0 = colado na borda)
    centralizar        : True = centraliza horizontalmente / False = alinha à esquerda
    """
    if not verificar_ffmpeg():
        return False
    if not os.path.isfile(arquivo_entrada):
        print(f"[ERRO] Arquivo não encontrado: {arquivo_entrada}")
        return False
    if not lista_tarjas:
        print("[ERRO] Nenhuma tarja informada.")
        return False

    inputs = ["-i", arquivo_entrada]
    filter_parts = []

    for idx, (imagem, inicio, fim) in enumerate(lista_tarjas):
        if not os.path.isfile(imagem):
            print(f"[ERRO] Tarja não encontrada: {imagem}")
            return False

        inputs += ["-i", imagem]
        inicio_seg = tempo_para_segundos(inicio)
        fim_seg    = tempo_para_segundos(fim)

        base          = "[0:v]" if idx == 0 else f"[v{idx}]"
        overlay_label = f"[v{idx+1}]"

        x_expr = "(main_w-overlay_w)/2" if centralizar else "0"
        y_expr = f"(main_h-overlay_h)-{margem_inferior_px}"

        filtro = (
            f"{base}[{idx+1}:v]"
            f"overlay={x_expr}:{y_expr}:"
            f"enable='between(t,{inicio_seg},{fim_seg})'"
            f"{overlay_label}"
        )
        filter_parts.append(filtro)

    comando = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[v{len(lista_tarjas)}]",
        "-map", "0:a?",
        "-c:v", "libx264" if reencoder else "copy",
        "-c:a", "aac"    if reencoder else "copy",
        arquivo_saida
    ]

    print("\n🎨 Aplicando tarjas...")
    resultado = subprocess.run(comando, stderr=subprocess.PIPE, text=True)
    if resultado.returncode == 0:
        print(f"[OK] Vídeo com tarjas salvo em: {arquivo_saida}")
        return True
    else:
        print(f"[ERRO] FFmpeg falhou:\n{resultado.stderr}")
        return False


# ══════════════════════════════════════════════════════════════
#  ZOOM LENTO (KEN BURNS) EM INTERVALOS
# ══════════════════════════════════════════════════════════════

def obter_fps_racional(video_path: str) -> str:
    """
    Retorna FPS como racional (ex.: '30000/1001'), ideal pra evitar drift/aceleração.
    """
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "default=nokey=1:noprint_wrappers=1",
            video_path
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    fps = (r.stdout or "").strip()
    return fps if fps else "30000/1001"

def converter_formato_videos(arquivos, pasta_saida="videos_normalizados"):
    os.makedirs(pasta_saida, exist_ok=True)

    for origem in arquivos:
        base = os.path.splitext(os.path.basename(origem))[0]
        destino = os.path.join(pasta_saida, f"{base}.mp4")

        subprocess.run([
            "ffmpeg", "-y",
            "-i", origem,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000",
            "-r", "30000/1001",   # melhor que "29.97"
            destino
        ], check=True)

        print(f"[OK] Normalizado: {destino}")

def obter_resolucao(video_path: str) -> Tuple[int, int]:
    """
    Retorna (width, height) do vídeo de entrada.
    """
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x",
            video_path
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    s = (r.stdout or "").strip()
    if "x" in s:
        w, h = s.split("x", 1)
        return int(w), int(h)
    # fallback
    return 1920, 1080


def aplicar_zoom_lento(
    arquivo_entrada: str,
    arquivo_saida: str,
    lista_zooms: List[Tuple[str, str, float, float]],
) -> bool:
    """
    Aplica zoom lento (Ken Burns) em intervalos específicos do vídeo.
    O restante do vídeo permanece sem zoom.

    lista_zooms:
        [
            ("00:00:58", "00:01:10", 1.0,  1.12),  # zoom-in
            ("00:05:00", "00:05:08", 1.12, 1.0),   # zoom-out
        ]
    """
    if not verificar_ffmpeg():
        return False
    if not os.path.isfile(arquivo_entrada):
        print(f"[ERRO] Arquivo não encontrado: {arquivo_entrada}")
        return False
    if not lista_zooms:
        print("[ERRO] Nenhum zoom informado.")
        return False

    # Detecta propriedades reais do vídeo (evita aceleração e mismatch)
    target_w, target_h = obter_resolucao(arquivo_entrada)
    fps_r = obter_fps_racional(arquivo_entrada)  # ex.: "30000/1001"

    # Converte e ordena por tempo de início
    intervals = []
    for (ini, fim, z0, z1) in lista_zooms:
        s0 = tempo_para_segundos(ini)
        s1 = tempo_para_segundos(fim)
        if s1 <= s0:
            print(f"[ERRO] Intervalo inválido: {ini} → {fim}")
            return False
        intervals.append((s0, s1, float(z0), float(z1)))
    intervals.sort(key=lambda x: x[0])

    fc: List[str] = []
    v_segs: List[str] = []
    a_segs: List[str] = []
    cur = 0.0
    idx = 0

    for (s0, s1, z0, z1) in intervals:
        # Segmento normal antes do zoom
        if s0 > cur:
            fc.append(
                f"[0:v]trim=start={cur}:end={s0},setpts=PTS-STARTPTS,"
                f"scale={target_w}:{target_h}:flags=lanczos,setsar=1[v{idx}]"
            )
            fc.append(f"[0:a]atrim=start={cur}:end={s0},asetpts=PTS-STARTPTS[a{idx}]")
            v_segs.append(f"[v{idx}]")
            a_segs.append(f"[a{idx}]")
            idx += 1

        # Segmento com zoom
        dur = s1 - s0

        # Calcula frames com base no fps racional (aproxima sem drift)
        # Pegamos o fps numérico via ffprobe pode ser mais chato; então usamos um fallback:
        # 29.97 -> ~30, 25 -> 25, etc. Para zoom suave, isso é suficiente.
        # Se quiser hiper preciso, dá pra parsear fps_r em fração.
        try:
            num, den = fps_r.split("/")
            fps_num = float(num) / float(den)
        except Exception:
            fps_num = 30.0

        total_frames = max(2, int(round(dur * fps_num)))
        denom = max(1, total_frames - 1)

        zoom_expr = f"{z0}+({z1}-{z0})*(on/{denom})"

        # x/y estáveis (inteiros e pares) -> reduz tremor no yuv420p
        x_expr = "2*round((iw - iw/zoom)/4)"
        y_expr = "2*round((ih - ih/zoom)/4)"

        fc.append(
            f"[0:v]trim=start={s0}:end={s1},setpts=PTS-STARTPTS,"
            f"scale={target_w}:{target_h}:flags=lanczos,setsar=1,"
            f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
            f"d=1:s={target_w}x{target_h}:fps={fps_r},gblur=sigma=0.3,format=yuv420p"
            f"[v{idx}]"
        )

        fc.append(f"[0:a]atrim=start={s0}:end={s1},asetpts=PTS-STARTPTS[a{idx}]")
        v_segs.append(f"[v{idx}]")
        a_segs.append(f"[a{idx}]")
        idx += 1
        cur = s1

    # Segmento normal após o último zoom
    fc.append(
        f"[0:v]trim=start={cur},setpts=PTS-STARTPTS,"
        f"scale={target_w}:{target_h}:flags=lanczos,setsar=1[v{idx}]"
    )
    fc.append(f"[0:a]atrim=start={cur},asetpts=PTS-STARTPTS[a{idx}]")
    v_segs.append(f"[v{idx}]")
    a_segs.append(f"[a{idx}]")

    n = len(v_segs)
    fc.append(f"{''.join(v_segs)}concat=n={n}:v=1:a=0[vout]")
    fc.append(f"{''.join(a_segs)}concat=n={n}:v=0:a=1[aout]")

    comando = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-sws_flags", "lanczos+accurate_rnd+full_chroma_int",
        "-i", arquivo_entrada,
        "-filter_complex", ";".join(fc),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k",
        arquivo_saida
    ]

    print("\n🔎 Aplicando zoom lento...")
    resultado = subprocess.run(comando, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if resultado.returncode == 0:
        print(f"[OK] Vídeo com zoom salvo em: {arquivo_saida}")
        return True
    else:
        print(f"[ERRO] FFmpeg falhou:\n{resultado.stderr}")
        return False
# ══════════════════════════════════════════════════════════════
#  ADICIONAR MÚSICA/ÁUDIO EM TEMPOS ESPECÍFICOS
# ══════════════════════════════════════════════════════════════

def adicionar_musica(
    arquivo_entrada: str,
    arquivo_saida: str,
    lista_audios: List[Tuple[str, str, Optional[str], float]],
    volume_original: float = 1.0,
    reencoder_video: bool = False,
) -> bool:
    """
    Mixa um ou mais áudios por cima do áudio original do vídeo.

    ⚠️ Os timestamps são relativos ao vídeo de entrada (que começa em 00:00:00).
       Se vier após cortar+juntar, use os tempos do clipe final.

    lista_audios:
        [
            ("vinheta.wav",  "00:00:00", "00:00:08", 1.0),   # toca só nesse intervalo
            ("bgm.mp3",      "00:00:08", None,        0.20),  # toca do 00:08 até o fim
        ]

    Parâmetros:
        volume_original  : volume da fala (1.0 = 100%)
        reencoder_video  : True = reencoda o vídeo também (mais lento)
    """
    if not verificar_ffmpeg():
        return False
    if not os.path.isfile(arquivo_entrada):
        print(f"[ERRO] Arquivo não encontrado: {arquivo_entrada}")
        return False
    if not lista_audios:
        print("[ERRO] Nenhum áudio informado.")
        return False

    inputs = ["-i", arquivo_entrada]
    for (audio_path, _, _, _) in lista_audios:
        if not os.path.isfile(audio_path):
            print(f"[ERRO] Áudio não encontrado: {audio_path}")
            return False
        inputs += ["-i", audio_path]

    filter_parts: List[str] = []
    mix_inputs:   List[str] = []

    # Áudio original
    filter_parts.append(f"[0:a]volume={volume_original}[a0]")
    mix_inputs.append("[a0]")

    # Cada áudio extra
    for idx, (audio_path, inicio, fim, vol) in enumerate(lista_audios, start=1):
        inicio_ms = tempo_para_segundos(inicio) * 1000
        chain = [f"[{idx}:a]", f"volume={vol}", f"adelay={inicio_ms}|{inicio_ms}"]

        if fim is not None:
            dur = tempo_para_segundos(fim) - tempo_para_segundos(inicio)
            if dur <= 0:
                print(f"[ERRO] Intervalo inválido: {inicio} → {fim}")
                return False
            chain += [f"atrim=0:{dur}", "asetpts=N/SR/TB"]

        filter_parts.append(",".join(chain) + f"[ax{idx}]")
        mix_inputs.append(f"[ax{idx}]")

    filter_parts.append(
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:"
        f"duration=longest:dropout_transition=0[aout]"
    )

    comando = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "0:v",
        "-map", "[aout]",
        "-c:a", "aac", "-b:a", "192k",
        "-c:v", "libx264" if reencoder_video else "copy",
        arquivo_saida
    ]

    print("\n🔊 Mixando música...")
    resultado = subprocess.run(comando, stderr=subprocess.PIPE, text=True)
    if resultado.returncode == 0:
        print(f"[OK] Vídeo com música salvo em: {arquivo_saida}")
        return True
    else:
        print(f"[ERRO] FFmpeg falhou:\n{resultado.stderr}")
        return False


# ══════════════════════════════════════════════════════════════
#  BGM COM DUCKING AUTOMÁTICO (ideal para podcast)
# ══════════════════════════════════════════════════════════════
def pegar_musica_aleatoria(pasta: str) -> str:
    if not os.path.isdir(pasta):
        raise FileNotFoundError(f"Pasta não encontrada: {pasta}")

    arquivos = [
        f for f in os.listdir(pasta)
        if f.lower().endswith((".mp3", ".wav", ".m4a"))
    ]

    if not arquivos:
        raise FileNotFoundError(f"Nenhuma música encontrada na pasta: {pasta}")

    musica_escolhida = random.choice(arquivos)

    return os.path.join(pasta, musica_escolhida)


def adicionar_bgm_com_ducking(
    arquivo_entrada: str,
    arquivo_saida: str,
    # bgm_path: str,
    inicio: str = "00:00:00",
    fim: Optional[str] = None,
    volume_bgm: float = 0.20,
    duck_db: float = 10.0,
    attack: float = 0.20,
    release: float = 0.60,
) -> bool:
    bgm_path = pegar_musica_aleatoria(os.environ.get("BGM_FOLDER", "musicas cortes"))
    if not verificar_ffmpeg():
        return False
    if not os.path.isfile(arquivo_entrada):
        print(f"[ERRO] Arquivo não encontrado: {arquivo_entrada}")
        return False
    if not os.path.isfile(bgm_path):
        print(f"[ERRO] BGM não encontrado: {bgm_path}")
        return False

    inicio_ms = tempo_para_segundos(inicio) * 1000

    dur_expr = ""
    if fim is not None:
        dur = tempo_para_segundos(fim) - tempo_para_segundos(inicio)
        if dur <= 0:
            print(f"[ERRO] Intervalo inválido: {inicio} → {fim}")
            return False
        dur_expr = f",atrim=0:{dur},asetpts=N/SR/TB"

    # Mapeia "duck_db" para um ratio (simples e eficaz)
    # 6 dB  -> ~4
    # 10 dB -> ~8
    # 15 dB -> ~12
    ratio = max(2.0, min(20.0, duck_db * 0.8))  # clamp 2..20

    # Compressão aplicada NO BGM, usando a voz como sidechain
    # Ordem do sidechaincompress: [main][sidechain]
    # main = bgm, sidechain = voz
    filter_complex = (
        f"[1:a]volume={volume_bgm},adelay={inicio_ms}|{inicio_ms}{dur_expr}[bgm];"
        f"[bgm][0:a]sidechaincompress=threshold=0.02:ratio={ratio}:"
        f"attack={attack}:release={release}[bgmduck];"
        f"[0:a][bgmduck]amix=inputs=2:duration=longest:dropout_transition=0[aout]"
    )

    comando = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", arquivo_entrada,
        "-i", bgm_path,
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        arquivo_saida
    ]

    print("\n🎚️  Adicionando BGM com ducking...")
    resultado = subprocess.run(comando, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if resultado.returncode == 0:
        print(f"[OK] Vídeo com BGM salvo em: {arquivo_saida}")
        return True
    else:
        print(f"[ERRO] FFmpeg falhou:\n{resultado.stderr}")
        return False


# ══════════════════════════════════════════════════════════════
#  Inserir inserção de vídeo
# ══════════════════════════════════════════════════════════════

def inserir_video_em_baixo(
    video_principal: str,
    saida: str,
    insercoes: List[Dict],
    largura_relativa: float = 0.60,
    margem_inferior: int = 40,
    crf: int = 18,
    preset: str = "medium",
) -> bool:
    """
    Sobrepõe 1+ vídeos menores na parte inferior do vídeo principal em intervalos.

    insercoes = [
      {"path": "...mp4", "start": 60, "end": 75},
      ...
    ]
    Mantém áudio do principal.
    """
    if not os.path.isfile(video_principal):
        print(f"[ERRO] Vídeo principal não encontrado: {video_principal}")
        return False
    if not insercoes:
        print("[ERRO] Nenhuma inserção informada.")
        return False

    for i, ins in enumerate(insercoes):
        if not os.path.isfile(ins["path"]):
            print(f"[ERRO] Inserção {i} não encontrada: {ins['path']}")
            return False
        if float(ins["end"]) <= float(ins["start"]):
            print(f"[ERRO] Inserção {i} com intervalo inválido (end <= start).")
            return False

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video_principal]
    for ins in insercoes:
        cmd += ["-i", ins["path"]]

    fc = []
    fc.append("[0:v]setpts=PTS-STARTPTS[base]")

    current = "base"

    for idx, ins in enumerate(insercoes, start=1):
        start = float(ins["start"])
        end = float(ins["end"])

        # prepara inserção e desloca no tempo
        fc.append(f"[{idx}:v]setpts=PTS-STARTPTS+{start}/TB[ins{idx}]")

        # scale2ref: escala a inserção baseado no tamanho do vídeo base do momento
        # iw/ih = dimensões do overlay (inserção)
        # main_w/main_h = dimensões do vídeo principal (ref)
        fc.append(
            f"[ins{idx}][{current}]scale2ref="
            f"w=main_w*{largura_relativa}:h=-1[insS{idx}][ref{idx}]"
        )

        x_expr = "(main_w-overlay_w)/2"
        y_expr = f"main_h-overlay_h-{margem_inferior}"

        fc.append(
            f"[ref{idx}][insS{idx}]overlay="
            f"x={x_expr}:y={y_expr}:"
            f"enable='between(t,{start},{end})':"
            f"eof_action=pass"
            f"[v{idx}]"
        )

        current = f"v{idx}"

    cmd += [
        "-filter_complex", ";".join(fc),
        "-map", f"[{current}]",
        "-map", "0:a?",
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
        "-c:a", "aac", "-b:a", "192k",
        saida
    ]

    print("🎬 Inserindo vídeo na parte inferior...")
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if r.returncode == 0:
        print(f"[OK] Saída gerada: {saida}")
        return True
    else:
        print(f"[ERRO] FFmpeg falhou:\n{r.stderr}")
        return False


# ----------------------------
# 1) Leitura do documento
# ----------------------------
def extrair_texto_documento(caminho: str) -> str:
    path = Path(caminho)
    ext = path.suffix.lower()

    if ext == ".pdf":
        # pip install pypdf
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        partes: List[str] = []
        for page in reader.pages:
            partes.append(page.extract_text() or "")
        texto = "\n".join(partes).strip()
        if not texto:
            raise ValueError("Não consegui extrair texto do PDF (talvez seja um PDF escaneado/imagem).")
        return texto

    if ext == ".docx":
        # pip install python-docx
        from docx import Document

        doc = Document(str(path))
        texto = "\n".join(p.text for p in doc.paragraphs).strip()
        if not texto:
            raise ValueError("DOCX sem texto detectável.")
        return texto

    raise ValueError(f"Formato não suportado: {ext}. Use PDF ou DOCX.")


# ----------------------------
# 2) Prompt completo (o mesmo que você aprovou)
# ----------------------------
PROMPT_SISTEMA = """Você é um extrator de instruções de edição de vídeo para um pipeline automatizado em Python.

Retorne SOMENTE JSON válido, seguindo o schema fornecido pelo response_format (JSON Schema).
Não inclua explicações, comentários ou texto fora do JSON.
"""

PROMPT_USUARIO = """Leia o roteiro de edição e preencha os campos do schema.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGRAS GERAIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1) Identifique automaticamente o programa:
   - Se for "Raise The Bar" → programa = "RTB"
   - Se for "Gestão de Pessoas" → programa = "GP"

2) Extraia o nome do episódio (nome do convidado + empresa se houver).

3) Considere apenas a parte principal do roteiro e ignore tudo abaixo (inclusive) de:
   - "Corte de Divulgação"
   - "Segue orientações para o recorte dos shorts"
   - "Corte inicial (Youtube)"
   - "Capítulos"
   - "CORTES DE IMPACTO"
   - Qualquer seção de shorts ou cortes para redes sociais

4) Normalize TODOS os tempos para "HH:MM:SS"
   - Sempre com 2 dígitos
   - Se não houver horas, usar "00"
   - Exemplo: 9:06 → 00:09:06

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ORDEM (REGRA CRÍTICA)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- A lista "trechos" DEVE respeitar ESTRITAMENTE a ordem textual do roteiro.
- NUNCA ordenar por tempo cronológico.
- Em linhas com "+", extrair da ESQUERDA para a DIREITA.
- Mesmo que o tempo volte (ex: 24:12 antes de 09:06), manter a ordem do texto.
- Se ordenar por horário, a resposta está errada.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A) SOURCE VIDEO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Se houver "Link do episódio: X", preencher:
  "source_label" = X
- Se X parecer URL → preencher source_url
- Caso contrário → source_url = null

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
B) TRECHOS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1) TRECHOS SOLTOS:
   - Linha com intervalos separados por "+" vira múltiplos trechos.
   - Preservar a ordem textual (esquerda → direita).

2) TRECHOS CONTÍNUOS:
   - Se houver "A introdução começa em XX:XX", iniciar trecho contínuo nesse tempo.
   - Se houver "Inserir vinheta de abertura em T":
        → o trecho contínuo anterior termina em T
   - Se houver "CORTAR A - B":
        → o trecho termina em A
        → o próximo começa em B
   - Se houver outro "CORTAR C - D":
        → aplicar mesma lógica
   - Se houver "finalizar em F":
        → último trecho termina em F

3) REGRAS IMPORTANTES:
   - NÃO criar automaticamente trecho "00:00:00 - <início da introdução>"
     a menos que o roteiro peça explicitamente.
   - A vinheta NÃO consome tempo do bruto.
     Mesmo que a vinheta seja ["01:14","01:15"],
     o trecho seguinte deve começar em "01:14" (não 01:15).
   - NÃO gerar trechos com duração zero.

Formato obrigatório:
"trechos": [
  ["HH:MM:SS","HH:MM:SS"]
]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
C) TARJAS (OBRIGATÓRIO CAPTURAR TODAS)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- TODA instrução contendo "Inserir tarja" deve gerar item em "tarjas".
- Isso inclui hosts e convidados.
- Não ignorar nenhuma tarja.

Regras:
- start = tempo indicado
- end = start + 5 segundos (se não houver duração explícita)
- texto = nome completo e cargo exatamente como aparece no roteiro
- arquivo = nome plausível no formato:
    tarja_nome_sobrenome.png
    (sem acentos, minúsculo, com underscore)

Manter a ordem textual das tarjas.

Formato:
"tarjas": [
  {
    "arquivo": "string",
    "start": "HH:MM:SS",
    "end": "HH:MM:SS",
    "texto": "string"
  }
]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
D) VINHETAS (CAMINHO DEPENDE DO PROGRAMA)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

O campo "arquivo" deve incluir o caminho correto conforme o programa:

Se programa = "GP":
  abertura → "GP/Vinheta_Abertura_h264.mp4"
  encerramento → "GP/vinheta_encerramento.mp4"

Se programa = "RTB":
  abertura → "RTB/RaisethebarVinheta_abertura.mp4"
  encerramento → "RTB/RaisetheBarVinheta_Encerramento.mp4"

Regras:
- Abertura:
    → usar tempo indicado
    → se não houver duração explícita, usar intervalo de 1 segundo
- Encerramento:
    → usar tempo final indicado
- Capturar asset_url se existir, senão null

Formato:
"vinhetas": {
  "abertura": {
    "arquivo": "string",
    "intervalo": ["HH:MM:SS","HH:MM:SS"],
    "asset_url": "string|null"
  },
  "encerramento": {
    "arquivo": "string",
    "momento": "HH:MM:SS",
    "asset_url": "string|null"
  }
}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
E) JUNÇÃO FINAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- A lista "juncao" deve seguir EXATAMENTE a ordem de "trechos".
- Usar index 1-based.
- Inserir "vinheta_abertura" após o trecho que termina no tempo da vinheta.
- Finalizar sempre com "vinheta_encerramento".

Formato:
"juncao": [
  { "tipo": "trecho", "index": 1 },
  { "tipo": "vinheta_abertura" },
  { "tipo": "vinheta_encerramento" }
]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK FINAL OBRIGATÓRIO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ANTES DE RESPONDER:

- Confirmar que a ordem dos "trechos" segue a ordem textual.
- Confirmar que NÃO existe trecho começando em 00:00:00,
  salvo se o roteiro pedir explicitamente.
- Confirmar que vinheta não alterou início real dos trechos.
- Confirmar que TODAS as tarjas foram capturadas.
- Confirmar que não existem trechos de duração zero.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FORMATO FINAL OBRIGATÓRIO (JSON)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{
  "programa": "RTB ou GP",
  "episodio": "string",
  "source_video": {
    "source_label": "string",
    "source_url": "string|null"
  },
  "trechos": [
    ["HH:MM:SS","HH:MM:SS"]
  ],
  "tarjas": [
    {
      "arquivo": "string",
      "start": "HH:MM:SS",
      "end": "HH:MM:SS",
      "texto": "string"
    }
  ],
  "vinhetas": {
    "abertura": {
      "arquivo": "string",
      "intervalo": ["HH:MM:SS","HH:MM:SS"],
      "asset_url": "string|null"
    },
    "encerramento": {
      "arquivo": "string",
      "momento": "HH:MM:SS",
      "asset_url": "string|null"
    }
  },
  "juncao": [
    { "tipo": "trecho", "index": 1 }
  ]
}

Retorne SOMENTE JSON válido.

ROTEIRO:
---
{ROTEIRO}
---"""


# ----------------------------
# 3) JSON Schema (Structured Outputs)
# ----------------------------
JSON_SCHEMA: Dict[str, Any] = {
    "name": "roteiro_edicao",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["programa", "episodio", "source_video", "tarjas", "trechos", "vinhetas", "juncao"],
        "properties": {
            "programa": {"type": "string", "enum": ["RTB", "GP"]},
            "episodio": {"type": "string"},
            "source_video": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_label", "source_url"],
                "properties": {
                    "source_label": {"type": "string"},
                    "source_url": {"type": ["string", "null"]},
                },
            },
            "tarjas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["arquivo", "start", "end", "texto"],
                    "properties": {
                        "arquivo": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "texto": {"type": "string"},
                    },
                },
            },
            "trechos": {
                "type": "array",
                "items": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "string"},
                },
            },
            "vinhetas": {
                "type": "object",
                "additionalProperties": False,
                "required": ["abertura", "encerramento"],
                "properties": {
                    "abertura": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["arquivo", "intervalo", "asset_url"],
                        "properties": {
                            "arquivo": {"type": "string"},
                            "intervalo": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 2,
                                "items": {"type": "string"},
                            },
                            "asset_url": {"type": ["string", "null"]},
                        },
                    },
                    "encerramento": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["arquivo", "momento", "asset_url"],
                        "properties": {
                            "arquivo": {"type": "string"},
                            "momento": {"type": "string"},
                            "asset_url": {"type": ["string", "null"]},
                        },
                    },
                },
            },
            "juncao": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tipo"],
                    "properties": {
                        "tipo": {"type": "string", "enum": ["trecho", "vinheta_abertura", "vinheta_encerramento"]},
                        "index": {"type": "integer"},
                    },
                },
            },
        },
    },
}


# ----------------------------
# 4) Função principal: interpreta roteiro via OpenAI
# ----------------------------
def _strip_json_fences(s: str) -> str:
    """Remove ```json ... ``` caso o modelo devolva com fence (fallback)."""
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def interpretar_roteiro_com_openai_texto(
    texto_roteiro: str,
    model: str = "gpt-4.1",
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    if not texto_roteiro or not texto_roteiro.strip():
        raise ValueError("texto_roteiro está vazio")

    client = OpenAI()

    user_prompt = PROMPT_USUARIO.replace("{ROTEIRO}", texto_roteiro.strip())

    # 1) Responses API + Structured Outputs
    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": user_prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": JSON_SCHEMA["name"],
                    "schema": JSON_SCHEMA["schema"],
                    "strict": True,
                }
            },
        )

        out_text = getattr(resp, "output_text", None)
        if not out_text:
            # fallback defensivo para SDKs/respostas diferentes
            out_text = resp.output[0].content[0].text

        out_text = _strip_json_fences(out_text)
        return json.loads(out_text)

    except Exception:
        # 2) Fallback: Chat Completions + JSON mode
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )

        out_text = resp.choices[0].message.content or ""
        out_text = _strip_json_fences(out_text)

        try:
            return json.loads(out_text)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Modelo não retornou JSON válido. Erro: {e}\nSaída:\n{out_text}"
            )

def interpretar_roteiro_com_openai_pdf(
    caminho_documento: str,
    model: str = "gpt-4.1",
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    texto = extrair_texto_documento(caminho_documento)
    client = OpenAI()

    user_prompt = PROMPT_USUARIO.replace("{ROTEIRO}", texto)

    # 1) Tenta usar Responses API com json_schema (SDK mais novo)
    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_schema", "json_schema": JSON_SCHEMA},
        )

        out_text = getattr(resp, "output_text", None)
        if not out_text:
            out_text = resp.output[0].content[0].text

        out_text = _strip_json_fences(out_text)
        return json.loads(out_text)

    except TypeError as e:
        # Cai aqui quando "response_format" não existe no SDK atual
        if "unexpected keyword argument 'response_format'" not in str(e):
            raise

    # 2) Fallback: Chat Completions + JSON mode (funciona em SDKs mais antigos)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    out_text = resp.choices[0].message.content or ""
    out_text = _strip_json_fences(out_text)

    try:
        return json.loads(out_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Modelo não retornou JSON válido. Erro: {e}\nSaída:\n{out_text}")

def extrair_drive_id(url: str) -> Dict[str, str]:
    """Retorna {"tipo": "file"|"folder", "id": "<ID>"} a partir de um link do Drive."""
    if not url or not isinstance(url, str):
        raise ValueError("URL inválida.")

    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return {"tipo": "file", "id": m.group(1)}

    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    if m:
        return {"tipo": "folder", "id": m.group(1)}

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "id" in qs and qs["id"]:
        return {"tipo": "file", "id": qs["id"][0]}

    raise ValueError(f"Não consegui extrair ID do link: {url}")


def drive_service():
    load_dotenv()
    val = os.getenv("GOOGLE_CREDENTIALS_JSON")

    if not val:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON não definido")

    # resolve caminho relativo ao projeto
    base_dir = Path(__file__).resolve().parent
    cred_path = (base_dir / val).resolve()
    print("VAL:", val)
    print("RESOLVED:", cred_path)
    print("EXISTS:", cred_path.exists())
    if cred_path.exists():

        with open(cred_path, "r", encoding="utf-8") as f:
            info = json.load(f)

    else:

        # caso seja JSON direto
        info = json.loads(val)

    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/drive"]
    )

    return build("drive", "v3", credentials=creds)

def listar_arquivos_em_pasta(
    folder_id: str,
    nome_contem: Optional[str] = None,
    extensoes: Optional[List[str]] = None,
    max_results: int = 50
) -> List[Dict[str, str]]:
    """Lista arquivos dentro de uma pasta do Drive."""
    service = drive_service()

    q = f"'{folder_id}' in parents and trashed=false"
    if nome_contem:
        safe = nome_contem.replace("'", "\\'")
        q += f" and name contains '{safe}'"

    results = []
    page_token = None

    while True:
        resp = service.files().list(
            q=q,
            fields="nextPageToken, files(id,name,mimeType,modifiedTime)",
            pageToken=page_token,
            pageSize=max_results,
            orderBy="modifiedTime desc"
        ).execute()

        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if extensoes:
        exts = tuple(e.lower() for e in extensoes)
        results = [f for f in results if f["name"].lower().endswith(exts)]

    return results


def baixar_drive_file_id(
    file_id: str,
    pasta_saida: str = ".",
    nome_saida: Optional[str] = None,
) -> str:
    """Baixa um arquivo do Drive por fileId (robusto e validado)."""
    service = drive_service()

    # pega name e size para validar integridade
    meta = service.files().get(fileId=file_id, fields="name,size").execute()
    nome_original = meta.get("name", file_id)
    size_drive = int(meta.get("size") or 0)

    os.makedirs(pasta_saida, exist_ok=True)

    nome_final = nome_saida if nome_saida else nome_original
    caminho_final = os.path.join(pasta_saida, nome_final)

    request = service.files().get_media(fileId=file_id)

    # ✅ garante flush/close
    with open(caminho_final, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"⬇️ Download {int(status.progress() * 100)}%")

    # ✅ valida tamanho
    size_disk = os.path.getsize(caminho_final)
    print(f"📦 Size: drive={size_drive} bytes | disk={size_disk} bytes")

    if size_drive and abs(size_disk - size_drive) > 1024:
        # remove arquivo quebrado pra não atrapalhar o pipeline
        try:
            os.remove(caminho_final)
        except OSError:
            pass
        raise RuntimeError("Download incompleto: tamanho do arquivo no disco não bate com o Drive.")

    print(f"✅ Baixado: {caminho_final}")
    return caminho_final

def baixar_arquivo_drive_por_link(
    link_drive: str,
    pasta_saida: str = ".",
    nome_saida: Optional[str] = None,
    nome_contem: Optional[str] = None,
    extensoes: Optional[List[str]] = None,
    pegar_mais_recente: bool = True,
) -> str:
    """
    - Se link de arquivo: baixa direto.
    - Se link de pasta: lista e baixa 1 arquivo da pasta (por filtro ou mais recente).
    """
    info = extrair_drive_id(link_drive)

    if info["tipo"] == "file":
        return baixar_drive_file_id(
            file_id=info["id"],
            pasta_saida=pasta_saida,
            nome_saida=nome_saida,
        )

    arquivos = listar_arquivos_em_pasta(
        folder_id=info["id"],
        nome_contem=nome_contem,
        extensoes=extensoes,
    )

    if not arquivos:
        raise FileNotFoundError("Nenhum arquivo encontrado na pasta com os filtros informados.")

    escolhido = arquivos[0] if pegar_mais_recente else arquivos[-1]

    return baixar_drive_file_id(
        file_id=escolhido["id"],
        pasta_saida=pasta_saida,
        nome_saida=nome_saida or escolhido["name"],
    )


def fazer_upload_drive(
    arquivo_local: str,
    folder_id: str,
    credenciais_json_path: str = "tfclab-secret.json",
    nome_no_drive: Optional[str] = None,
    mime_type: str = "video/mp4",
) -> Dict[str, str]:
    """
    Faz upload de um arquivo local para uma pasta do Google Drive.

    Retorna dict com {"id": "<fileId>", "name": "<nome>", "link": "<webViewLink>"}.

    Parâmetros:
        arquivo_local        : caminho do arquivo a enviar
        folder_id            : ID da pasta de destino no Drive
        credenciais_json_path: path do JSON de service account (fallback se env var ausente)
        nome_no_drive        : nome do arquivo no Drive (padrão = basename do arquivo local)
        mime_type            : MIME type do arquivo (padrão video/mp4)
    """
    from googleapiclient.http import MediaFileUpload

    if not os.path.isfile(arquivo_local):
        raise FileNotFoundError(f"Arquivo para upload não encontrado: {arquivo_local}")

    nome_final = nome_no_drive or os.path.basename(arquivo_local)
    tamanho_bytes = os.path.getsize(arquivo_local)

    print(f"\n📤 Iniciando upload para o Drive...")
    print(f"   Arquivo : {arquivo_local} ({tamanho_bytes / 1024 / 1024:.1f} MB)")
    print(f"   Destino : pasta/{folder_id}")
    print(f"   Nome    : {nome_final}")

    service = drive_service()

    file_metadata = {
        "name": nome_final,
        "parents": [folder_id],
    }

    media = MediaFileUpload(
        arquivo_local,
        mimetype=mime_type,
        resumable=True,          # upload retomável — essencial para vídeos grandes
        chunksize=10 * 1024 * 1024,  # chunks de 10 MB
    )

    request = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,webViewLink",
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"   ⬆️  Upload {pct}%")

    print(f"✅ Upload concluído: {response.get('webViewLink')}")
    return {
        "id": response.get("id"),
        "name": response.get("name"),
        "link": response.get("webViewLink"),
    }

def ajustar_duracao(input_video, output_video, fator):
    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_video,
        "-filter_complex",
        f"[0:v]setpts=PTS/{fator}[v];[0:a]atempo={fator}[a]",
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-c:a", "aac",
        output_video
    ]

    subprocess.run(cmd, check=True)

def reduzir_pausas(video_in: str, video_out: str, silent_speed: float = 4.0, video_speed: float = 1.0):
    # melhor chamar pelo módulo pra evitar problema de PATH
    cmd = [
        sys.executable, "-m", "auto_editor",
        video_in,
        "--silent-speed", str(silent_speed),
        "--video-speed", str(video_speed),
        "-o", video_out
    ]
    subprocess.run(cmd, check=True)

def gerar_variaveis_pipeline(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converte o JSON estruturado em variáveis prontas
    para aplicar_tarjas, cortar_multiplos_trechos e juntar_videos.
    """

    # 🔹 1) Trechos → lista de tuplas
    trechos: List[Tuple[str, str]] = [
        (inicio, fim) for inicio, fim in data["trechos"]
    ]

    # 🔹 2) Tarjas → lista de tuplas (arquivo, start, end)
    lista_tarjas: List[Tuple[str, str, str]] = [
        (t["arquivo"], t["start"], t["end"])
        for t in data["tarjas"]
    ]

    # 🔹 3) Vinhetas
    vinheta_abertura = data["vinhetas"]["abertura"]["arquivo"]
    vinheta_encerramento = data["vinhetas"]["encerramento"]["arquivo"]

    # 🔹 4) Lista de arquivos para juntar_videos()
    lista_arquivos: List[str] = []

    lista_tarjas_texto: List[Tuple[str, str, str]] = [
        (t["arquivo"], t["texto"])
        for t in data["tarjas"]
    ]

    for item in data["juncao"]:
        if item["tipo"] == "trecho":
            idx = item["index"]
            lista_arquivos.append(os.path.join("trechos", f"trecho_{idx:02d}.mp4"))

        elif item["tipo"] == "vinheta_abertura":
            lista_arquivos.append(vinheta_abertura.replace("\\", "/"))

        elif item["tipo"] == "vinheta_encerramento":
            lista_arquivos.append(vinheta_encerramento.replace("\\", "/"))

    return {
        "programa": data["programa"],
        "episodio": data["episodio"],
        "source_label": data["source_video"]["source_label"],
        "trechos": trechos,
        "lista_tarjas": lista_tarjas,
        "lista_arquivos": lista_arquivos,
        "vinheta_abertura_url": data["vinhetas"]["abertura"]["asset_url"],
        "vinheta_encerramento_url": data["vinhetas"]["encerramento"]["asset_url"],
        "lista_nome_tarja": lista_tarjas_texto,
    }

def separar_tarja(item):
    arquivo = item[0]
    texto = item[1].strip()

    if " - " in texto:
        nome, cargo = texto.split(" - ", 1)
    elif "," in texto:
        nome, cargo = texto.split(",", 1)
    else:
        raise ValueError(f"Formato inesperado: {texto}")

    return {
        "nome": nome.strip(),
        "cargo": cargo.strip(),
        "arquivo": arquivo
    }

# ══════════════════════════════════════════════════════════════
#  EXECUÇÃO PRINCIPAL
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":

#     cortar_multiplos_trechos(
#          arquivo_entrada=r'RTB\\Video\\Cópia de EPS4 - 18 novembro 2025 .mp4',
#          pasta_saida="trechos",
#          trechos=[('00:24:12', '00:24:21'), ('00:00:27', '00:00:50'), ('00:03:41', '00:03:50'), ('00:05:04', '00:05:09'), ('00:06:33', '00:06:53'),
#                   ('00:07:20', '00:07:26'), ('00:07:43', '00:07:52'), ('00:08:12', '00:08:14'), ('00:08:20', '00:08:22'), ('00:08:32', '00:08:38'),
#                   ('00:08:44', '00:08:45'), ('00:08:50', '00:08:55'), ('00:09:01', '00:09:08'), ('00:09:17', '00:09:18'), ('00:09:45', '00:09:51'),
#                   ('00:10:10', '00:10:12'), ('00:10:17', '00:10:21'), ('00:31:35', '00:31:45'), ('00:32:17', '00:32:36'), ('00:32:47', '00:33:00'),
#                   ('00:33:22', '00:33:24'), ('00:33:40', '00:34:05'), ('00:34:08', '00:34:16'), ('00:34:20', '00:34:26'), ('00:42:28', '00:42:45')],
#          reencoder=True,
#          pos_tarja=True,
#      )
    file =  r"C:\Users\thiag\Downloads\EPS4.mp4"
    roteiro = r"C:\Users\thiag\Downloads\155. Roteiro de edição RTB -  Leandro Herculano Cargo _ Elementar Comunicação.docx.pdf"
    roteiro_json = interpretar_roteiro_com_openai_texto(roteiro)
    print(json.dumps(roteiro_json, ensure_ascii=False, indent=2))
    config =gerar_variaveis_pipeline(roteiro_json)
    print(config["trechos"])
    print(config["lista_tarjas"])
    print(config["lista_arquivos"])
    print(config["lista_nome_tarja"])
    programa = config["programa"].lower()
    if programa == "rtb":
        pasta = r'RTB\\'
    elif programa == "gp":
        pasta = r'GP\\'
    else:
        print('Erro, não foi dectado nenhum programa no Roteiro')
    for item in config["lista_nome_tarja"]:
        nome, cargo, arquivo = (
            separar_tarja(item)["nome"],
            separar_tarja(item)["cargo"],
            separar_tarja(item)["arquivo"]
        )
        criar_tarja = video_tarja.main(pasta, nome, cargo, arquivo, 'openai')

    arquivo = baixar_arquivo_drive_por_link(
        link_drive="https://drive.google.com/drive/u/0/folders/1GwZQG1h2yVcYw7Wctv6DQYNXxq04pOlw",
        pasta_saida=f"{pasta}Video",
        extensoes=[".mp4", ".mov"]  # recomendo filtrar
    )

    # ── PASSO 1: Aplicar tarjas no vídeo original ─────────────
    file_com_tarja = "video_com_tarja.mp4"
    aplicar_tarjas(
        arquivo_entrada=arquivo,
        arquivo_saida=file_com_tarja,
        lista_tarjas=config["lista_tarjas"],)

    # # # # ── PASSO 2: Cortar os trechos (pos_tarja=True preserva as tarjas) ──
    cortar_multiplos_trechos(
        arquivo_entrada=file_com_tarja,
        pasta_saida="trechos",
        trechos=config["trechos"],
        reencoder=True,
        pos_tarja=True,
    )
    #
    # # # ── PASSO 3: Juntar trechos + vinhetas ───────────────────
    file_junto = "video_junto.mp4"
    juntar_videos(
        lista_arquivos=config["lista_arquivos"],
        arquivo_saida=file_junto,
        reencoder=True,
    )
    file = 'video_com_qr.mp4'

    aplicar_tarjas(arquivo_entrada=file_junto, arquivo_saida=file, lista_tarjas=[(F"{pasta}QR CODE.png", "00:03:00", "00:43:00"),])

    # ── PASSO 4 (opcional): Zoom lento em momentos específicos ──
    # file_zoom = "video_com_zoom.mp4"
    # aplicar_zoom_lento(
    #     arquivo_entrada=file_junto,
    #     arquivo_saida=file_zoom,
    #     lista_zooms=[
    #         ("00:00:00", "00:00:31", 1.0,  1.43),  # zoom-in na entrada
    #         # ("00:05:00", "00:05:08", 1.12, 1.0),   # zoom-out
    #     ],
    # )

   #  # # ── PASSO 5: Adicionar música com ducking ─────────────────
    file_final = "video_com_musica.mp4"
    adicionar_bgm_com_ducking(
        arquivo_entrada=file,   # ou file_zoom se usar o zoom
        arquivo_saida=file_final,
        inicio="00:00:00",
        fim="00:00:20",
        volume_bgm=0.18,
        duck_db=12,
    )

    inserir_video_em_baixo(video_principal=file_final,
                           saida='video_finalizado.mp4',
                           insercoes=[{
        "path": rf"{pasta}inserção.mov",
        "start": tempo_para_segundos("00:10:00"),
        "end": tempo_para_segundos("00:10:12"),
    },
   {
       "path": rf"{pasta}inscreva-se.mov",
       "start": tempo_para_segundos("00:15:00"),
       "end": tempo_para_segundos("00:015:07"),
   },
    {
        "path": rf"{pasta}instagram.mov",
        "start": tempo_para_segundos("00:20:00"),
        "end": tempo_para_segundos("00:20:10"),
    }])


    print("\n✅ Pipeline completo finalizado!")
    print(f"   Arquivo final: video_finalizado.mp4")

