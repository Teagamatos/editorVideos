import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from logger import get_logger
from database import salvar_solicitacao, atualizar_status
from automacao import disparar_automacao
from fastapi.middleware.cors import CORSMiddleware
log = get_logger(__name__)

app = FastAPI(title="Podcast Video Automation")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ou coloque só o seu front-end
    allow_credentials=True,
    allow_methods=["*"],  # permite GET, POST, OPTIONS, etc.
    allow_headers=["*"],
)
# ══════════════════════════════════════════════════════════════════
#  Middleware: loga toda request com tempo de resposta
# ══════════════════════════════════════════════════════════════════

@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    log.info(f"[{request_id}] ▶ {request.method} {request.url.path}")
    t0 = datetime.utcnow()
    try:
        response = await call_next(request)
        ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
        log.info(f"[{request_id}] ◀ {response.status_code} ({ms}ms)")
        return response
    except Exception as exc:
        ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
        log.error(f"[{request_id}] ✗ Erro não tratado ({ms}ms): {exc}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": "Erro interno inesperado."})


# ══════════════════════════════════════════════════════════════════
#  Handler global de exceções não capturadas
# ══════════════════════════════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error(f"Exceção global capturada: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "Erro interno do servidor."},
    )


# ══════════════════════════════════════════════════════════════════
#  Schema
# ══════════════════════════════════════════════════════════════════

class VideoRequest(BaseModel):
    tituloVideo: str = Field(..., min_length=1, max_length=500)
    linkVideo: str = Field(..., min_length=1, max_length=2000)
    roteiroTexto: str = Field(..., min_length=10)
    timestamp: Optional[datetime] = None



# ══════════════════════════════════════════════════════════════════
#  Endpoints
# ══════════════════════════════════════════════════════════════════
@app.get("/")
async def root():
    return {"service": "editorVideos", "status": "running"}

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/video-request")
async def receber_formulario(payload: VideoRequest, background_tasks: BackgroundTasks):
    titulo = payload.tituloVideo.strip()
    link = payload.linkVideo.strip()
    roteiro = payload.roteiroTexto.strip()

    # Mock da pasta destino
    pasta_destino_id = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs"

    log.info(
        f"Nova solicitação | titulo='{titulo}' | "
        f"pasta_destino='{pasta_destino_id}' | link='{link[:60]}...'"
    )

    created_at = payload.timestamp.isoformat() if payload.timestamp else datetime.utcnow().isoformat()

    try:
        registro = salvar_solicitacao(
            titulo=titulo,
            link=link,
            roteiro=roteiro,
            enviado=False,
            created_at=created_at,
        )
    except Exception as exc:
        log.error(f"Falha ao salvar no Supabase: {exc}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Não foi possível registrar a solicitação. Tente novamente.",
        )

    job_id = registro.get("id", "?")
    log.info(f"Solicitação salva | job_id={job_id}")

    background_tasks.add_task(
        disparar_automacao,
        titulo=titulo,
        link=link,
        roteiro=roteiro,
        registro=registro,
        pasta_destino_id=pasta_destino_id,
    )

    return {
        "success": True,
        "message": "Solicitação recebida. Processamento iniciado em background.",
        "job_id": job_id,
        "data": registro,
    }
