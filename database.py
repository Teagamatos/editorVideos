import os
from supabase import create_client, Client
from dotenv import load_dotenv
from logger import get_logger

load_dotenv()
log = get_logger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL:
    raise ValueError("Variável de ambiente SUPABASE_URL não definida.")
if not SUPABASE_KEY:
    raise ValueError("Variável de ambiente SUPABASE_KEY não definida.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
log.info("Conexão com Supabase inicializada.")

TABLE = "automacao_podcast"


def salvar_solicitacao(titulo: str, link: str, roteiro: str, enviado: bool, created_at: str) -> dict:
    payload = {
        "titulo": titulo,
        "link": link,
        "roteiro": roteiro,
        "enviado": enviado,
        "status": "pendente",
        "created_at": created_at,
    }
    log.info(f"Salvando solicitação no Supabase | titulo='{titulo}'")
    response = supabase.table(TABLE).insert(payload).execute()

    if not response.data:
        raise RuntimeError("Supabase retornou resposta vazia ao inserir solicitação.")

    record = response.data[0]
    log.info(f"Solicitação salva | id={record.get('id')}")
    return record


def atualizar_status(
    job_id: int | str,
    status: str,
    erro: str | None = None,
    link_video_final: str | None = None,
) -> None:
    """
    Atualiza status do job no Supabase.

    status: 'pendente' | 'processando' | 'concluido' | 'erro'
    erro: traceback em caso de falha (salvo truncado a 2000 chars)
    link_video_final: webViewLink do vídeo no Drive após upload
    """
    payload: dict = {"status": status}

    if erro is not None:
        payload["erro"] = erro[:2000]

    if link_video_final is not None:
        payload["link_video_final"] = link_video_final

    log.info(f"Atualizando status | job_id={job_id} | status='{status}'" +
             (f" | link_video_final={link_video_final}" if link_video_final else ""))
    try:
        supabase.table(TABLE).update(payload).eq("id", job_id).execute()
    except Exception as exc:
        # Não propaga — falha de status não deve derrubar o pipeline
        log.error(f"Falha ao atualizar status | job_id={job_id}: {exc}", exc_info=True)
