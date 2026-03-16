import json
import os
from datetime import datetime, timezone
from typing import Any, Optional
from urllib import error, request

from logger import get_logger

log = get_logger(__name__)


def notificar_erro_clickup(
    *,
    job_id: Any,
    titulo: str,
    etapa: str,
    erro: str,
    link_video: Optional[str] = None,
) -> bool:
    """
    Envia notificação de erro para um canal do ClickUp via API.
    Requer:
      - CLICKUP_API_TOKEN
      - CLICKUP_WORKSPACE_ID
      - CLICKUP_CHANNEL_ID
    """
    api_token = os.getenv("CLICKUP_API_TOKEN", "").strip()
    workspace_id = os.getenv("CLICKUP_WORKSPACE_ID", "").strip()
    channel_id = os.getenv("CLICKUP_CHANNEL_ID", "").strip()

    if not api_token or not workspace_id or not channel_id:
        log.info(
            "[job=%s] ClickUp API não configurada (CLICKUP_API_TOKEN/CLICKUP_WORKSPACE_ID/CLICKUP_CHANNEL_ID); notificação ignorada.",
            job_id,
        )
        return False

    timestamp = datetime.now(timezone.utc).isoformat()
    mensagem = (
        "ERRO NO PIPELINE DE VIDEO\n"
        f"job_id: {job_id}\n"
        f"titulo: {titulo}\n"
        f"etapa: {etapa}\n"
        f"erro: {erro}\n"
        f"timestamp_utc: {timestamp}"
    )
    if link_video:
        mensagem += f"\nlink_video_origem: {link_video}"

    url = f"https://api.clickup.com/api/v3/workspaces/{workspace_id}/chat/channels/{channel_id}/messages"
    timeout = int(os.getenv("CLICKUP_API_TIMEOUT", "10"))

    # API de Chat do ClickUp é experimental; tentamos formatos comuns de payload.
    payloads = [
        {"text": mensagem},
        {"message": mensagem},
        {"content": mensagem},
    ]

    try:
        for payload in payloads:
            data = json.dumps(payload).encode("utf-8")
            req = request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": api_token,
                },
                method="POST",
            )
            try:
                with request.urlopen(req, timeout=timeout) as resp:
                    status_code = getattr(resp, "status", None) or resp.getcode()
                    if 200 <= status_code < 300:
                        log.info(
                            "[job=%s] Notificação de erro enviada para canal ClickUp (status=%s).",
                            job_id,
                            status_code,
                        )
                        return True
            except error.HTTPError as exc:
                # Se o payload for inválido, tentamos a próxima variação.
                if 400 <= exc.code < 500:
                    continue
                raise
    except error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        log.warning(
            "[job=%s] Falha HTTP ao notificar canal ClickUp via API: status=%s body=%s",
            job_id,
            exc.code,
            body[:500],
        )
    except Exception as exc:
        log.warning("[job=%s] Falha ao notificar canal ClickUp via API: %s", job_id, exc)

    log.warning(
        "[job=%s] Não foi possível enviar mensagem ao canal ClickUp. "
        "Verifique token, workspace_id, channel_id e permissões.",
        job_id,
    )

    return False
