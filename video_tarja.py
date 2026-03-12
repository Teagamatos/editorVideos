import unicodedata
import os
import sys
import re
import argparse
from pathlib import Path
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("❌  pip install pillow")

load_dotenv()

CAMPO_AZUL   = {"row_top": 823, "row_bottom": 918, "col_left": 20, "col_right": 710, "padding_x": 18}
CAMPO_BRANCO = {"row_top": 928, "row_bottom": 996, "col_left": 10, "col_right": 570, "padding_x": 12}

_FONTES_NORMAL = [
    r"C:\Users\thiag\AppData\Local\Microsoft\Windows\Fonts\Montserrat-Bold.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_FONTES_BOLD = [
    r"C:\Users\thiag\AppData\Local\Microsoft\Windows\Fonts\Montserrat-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

SYSTEM_PROMPT = (
    "Você formata cargos para tarjas de vídeo de podcast.\n"
    "Formato obrigatório:\n"
    "- Máximo 2 linhas\n"
    "- Até 50 caracteres por linha\n"
    "- Linha 1: Cargo principal\n"
    "- Linha 2: 'do' ou 'da' + Empresa\n"
    "- Preserve o mais importante do cargo\n"
    "- Sem ponto final\n"
    "- Responda APENAS com o texto final formatado, "
    "respeitando as quebras de linha, sem aspas nem explicações"
)

PROVEDORES = {
    "gemini": {"env": "GEMINI_API_KEY", "label": "Gemini 2.0 Flash"},
    "openai": {"env": "OPENAI_API_KEY", "label": "GPT-4o"},
    "claude": {"env": "ANTHROPIC_API_KEY", "label": "Claude"},
}
PRIORIDADE = ["gemini", "openai", "claude"]

def obter_template(programa):

    programa = programa.upper()

    pasta = BASE_DIR / programa

    template = pasta / "tarja_template.png"

    if not template.exists():
        raise FileNotFoundError(
            f"Template não encontrado: {template}"
        )

    return template

def detectar_provedor(forcado=None):
    ordem = [forcado] if forcado else PRIORIDADE
    for nome in ordem:
        chave = os.environ.get(PROVEDORES[nome]["env"])
        if chave:
            return nome, chave

    print("\n❌  Nenhuma chave de API encontrada. Defina uma das variáveis:\n")
    for n, info in PROVEDORES.items():
        print(f"    {info['env']:<25} → {info['label']}")
    sys.exit(1)


def _via_gemini(api_key: str, nome: str, cargo_completo: str) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        sys.exit("❌  pip install google-genai")

    client = genai.Client(api_key=api_key)
    resposta = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
        contents=f"Nome: {nome}\nCargo completo: {cargo_completo}",
    )
    return resposta.text.strip().strip('"').strip("'")


def _via_openai(api_key: str, nome: str, cargo_completo: str) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("❌  pip install openai")

    client = OpenAI(api_key=api_key)
    resposta = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Nome: {nome}\nCargo completo: {cargo_completo}"},
        ],
        max_tokens=60,
        temperature=0,
    )
    return resposta.choices[0].message.content.strip().strip('"').strip("'")


def _via_claude(api_key: str, nome: str, cargo_completo: str) -> str:
    try:
        import anthropic
    except ImportError:
        sys.exit("❌  pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key)
    resposta = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=60,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Nome: {nome}\nCargo completo: {cargo_completo}"}],
    )
    return resposta.content[0].text.strip().strip('"').strip("'")


_CHAMADAS = {"gemini": _via_gemini, "openai": _via_openai, "claude": _via_claude}


def formatar_cargo(nome: str, cargo_completo: str, provider=None) -> str:
    provedor, api_key = detectar_provedor(provider)
    label = PROVEDORES[provedor]["label"]
    print(f"🤖  {label} formatando cargo...")

    cargo = _CHAMADAS[provedor](api_key, nome, cargo_completo)
    return cargo[:37] + "..." if len(cargo) > 60 else cargo


def _fonte(candidatos):
    for p in candidatos:
        if Path(p).exists():
            return p
    sys.exit("❌  Nenhuma fonte TrueType encontrada.")


def _ajustar_fonte(draw, texto, caminho, max_w, max_h, tamanho_inicial):
    tamanho = tamanho_inicial
    while tamanho >= 8:
        fonte = ImageFont.truetype(caminho, tamanho)
        bbox = draw.textbbox((0, 0), texto, font=fonte)
        if (bbox[2] - bbox[0]) <= max_w and (bbox[3] - bbox[1]) <= max_h:
            return fonte, bbox
        tamanho -= 1

    fonte = ImageFont.truetype(caminho, 8)
    return fonte, draw.textbbox((0, 0), texto, font=fonte)


def gerar_tarja(nome: str, cargo_completo: str, saida: str, template: str, provider=None):
    cargo = formatar_cargo(nome, cargo_completo, provider)
    print(f"    Entrada : {cargo_completo!r}")
    print(f"    Saída   : {cargo!r}  ({len(cargo)} chars)")

    if not Path(template).exists():
        sys.exit(
            f"❌  Template não encontrado: {template}\n"
            f"    Salve o arquivo 'tarja_template.png' na mesma pasta do script."
        )

    img = Image.open(template).convert("RGBA")
    draw = ImageDraw.Draw(img)

    az = CAMPO_AZUL
    az_w = az["col_right"] - az["col_left"] - az["padding_x"] * 2
    az_h = az["row_bottom"] - az["row_top"]
    fonte_nome, bbox = _ajustar_fonte(
        draw, nome, _fonte(_FONTES_BOLD),
        max_w=az_w, max_h=int(az_h * 0.75),
        tamanho_inicial=int(az_h * 0.60),
    )
    y = az["row_top"] + (az_h - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((az["col_left"] + az["padding_x"], y), nome,
              font=fonte_nome, fill=(255, 255, 255, 255))

    br = CAMPO_BRANCO
    br_w = br["col_right"] - br["col_left"] - br["padding_x"] * 2
    br_h = br["row_bottom"] - br["row_top"]
    fonte_cargo, bbox2 = _ajustar_fonte(
        draw, cargo, _fonte(_FONTES_NORMAL),
        max_w=br_w, max_h=int(br_h * 0.80),
        tamanho_inicial=int(br_h * 0.58),
    )
    y2 = br["row_top"] + (br_h - (bbox2[3] - bbox2[1])) // 2 - bbox2[1]
    draw.text((br["col_left"] + br["padding_x"], y2), cargo,
              font=fonte_cargo, fill=(30, 30, 30, 255))

    Path(saida).parent.mkdir(parents=True, exist_ok=True)
    img.save(saida)
    print(f"✅  Tarja salva: {saida}")


def gerar_nome_tarja(nome: str) -> str:
    nome = unicodedata.normalize("NFKD", nome)
    nome = nome.encode("ascii", "ignore").decode("ascii")
    nome = nome.lower()
    nome = re.sub(r"[^a-z0-9_\s]", "", nome)
    nome = re.sub(r"\s+", "_", nome.strip())
    return f"tarja_{nome}.png"


def main(programa, nome, cargo, saida, provider):
    template = obter_template(programa)
    gerar_tarja(
        nome,
        cargo,
        saida,
        template,
        provider
    )


def main_cli():
    parser = argparse.ArgumentParser(
        description="Gera tarja GP usando IA (Gemini, GPT-4o ou Claude)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--nome", required=True, help='Nome do convidado. Ex: "Fred Lopes"')
    parser.add_argument("--cargo", required=True, help="Cargo completo sem limite de caracteres")
    parser.add_argument("--saida", default=None, help="Caminho do PNG de saída")
    parser.add_argument("--template", default="tarja_template.png", help="Template PNG")
    parser.add_argument("--provider", choices=["openai", "gemini", "claude"], default=None)

    args = parser.parse_args()

    saida = args.saida if args.saida else gerar_nome_tarja(args.nome)
    gerar_tarja(args.nome, args.cargo, saida, args.template, args.provider)


if __name__ == "__main__":
    main_cli()