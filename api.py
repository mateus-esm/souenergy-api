import uuid
import time
import logging
import threading
import os
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Security, BackgroundTasks
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from scraper import consultar_precos

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("api")

API_KEY        = os.getenv("API_KEY")
CACHE_TTL_SEC  = 3600  # 1 hora
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(
    title="SouEnergy Price API",
    description=(
        "Consulta preços de kits solares (Solplanet e Hoymiles) na SouEnergy.\n\n"
        "Como usar:\n"
        "1. `POST /jobs?potencia=7` — inicia a consulta e retorna um `job_id`\n"
        "2. `GET /jobs/{job_id}` — verifica o status e obtém o resultado quando pronto\n\n"
        "A consulta demora 3-5 minutos (scraping real de browser)."
    ),
    version="2.0.0",
)

# ─── ESTADO EM MEMÓRIA ────────────────────────────────────────────────────────
# { job_id: { status, result, error, created_at, potencia } }
jobs: dict = {}

# Cache: { potencia: { result, cached_at } }
cache: dict = {}

# Impede dois browsers rodando ao mesmo tempo
scraper_lock = threading.Lock()


# ─── AUTH ─────────────────────────────────────────────────────────────────────

def verificar_chave(key: str = Security(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="API key inválida")
    return key


# ─── CACHE ────────────────────────────────────────────────────────────────────

def cache_valido(potencia: float) -> dict | None:
    entry = cache.get(potencia)
    if entry and (time.time() - entry["cached_at"]) < CACHE_TTL_SEC:
        log.info(f"Cache hit para {potencia} kWp")
        return entry["result"]
    return None


# ─── WORKER DO JOB ────────────────────────────────────────────────────────────

def executar_job(job_id: str, potencia: float):
    jobs[job_id]["status"] = "running"
    log.info(f"[{job_id}] Iniciando scraping para {potencia} kWp")

    # Verifica cache antes de bloquear o browser
    cached = cache_valido(potencia)
    if cached:
        jobs[job_id]["status"]  = "done"
        jobs[job_id]["result"]  = cached
        jobs[job_id]["source"]  = "cache"
        log.info(f"[{job_id}] Resultado do cache")
        return

    with scraper_lock:
        try:
            result = consultar_precos(potencia)
            cache[potencia] = {"result": result, "cached_at": time.time()}
            jobs[job_id]["status"] = "done"
            jobs[job_id]["result"] = result
            jobs[job_id]["source"] = "live"
            log.info(f"[{job_id}] Scraping concluído")
        except Exception as e:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"]  = str(e)
            log.error(f"[{job_id}] Erro: {e}")


# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return {"status": "online", "version": "2.0.0", "docs": "/docs"}


@app.post(
    "/jobs",
    summary="Iniciar consulta de preços",
    description=(
        "Inicia uma consulta assíncrona. Retorna um `job_id` imediatamente.\n"
        "Use `GET /jobs/{job_id}` para verificar quando o resultado estiver pronto."
    ),
    responses={
        202: {"description": "Job criado — use GET /jobs/{job_id} para acompanhar"},
        403: {"description": "API key inválida"},
        422: {"description": "Parâmetro inválido"},
        429: {"description": "Já existe um scraping em andamento"},
    }
)
def criar_job(
    potencia: float,
    background_tasks: BackgroundTasks,
    key: str = Security(verificar_chave),
):
    if potencia <= 0 or potencia > 100:
        raise HTTPException(status_code=422, detail="Potência deve ser entre 0 e 100 kWp")

    # Bloqueia se já houver job rodando (sem cache disponível)
    if scraper_lock.locked() and not cache_valido(potencia):
        raise HTTPException(status_code=429, detail="Scraping em andamento. Tente novamente em alguns minutos.")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "job_id":     job_id,
        "status":     "queued",
        "potencia":   potencia,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result":     None,
        "error":      None,
        "source":     None,
    }

    background_tasks.add_task(executar_job, job_id, potencia)
    log.info(f"Job criado: {job_id} | potencia={potencia}")

    return JSONResponse(status_code=202, content={
        "job_id":   job_id,
        "status":   "queued",
        "poll_url": f"/jobs/{job_id}",
        "message":  "Consulta iniciada. Verifique o status em /jobs/{job_id} (demora 3-5 min).",
    })


@app.get(
    "/jobs/{job_id}",
    summary="Verificar status / obter resultado",
    responses={
        200: {"description": "Status do job (queued | running | done | error)"},
        403: {"description": "API key inválida"},
        404: {"description": "Job não encontrado"},
    }
)
def obter_job(job_id: str, key: str = Security(verificar_chave)):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return JSONResponse(content=job)


@app.get(
    "/precos",
    summary="Consulta síncrona (bloqueante)",
    description=(
        "Consulta síncrona — a resposta só chega após 3-5 minutos de scraping.\n"
        "**Recomendado apenas para testes.** Use `POST /jobs` para uso em produção."
    ),
)
def get_precos_sync(potencia: float, key: str = Security(verificar_chave)):
    if potencia <= 0 or potencia > 100:
        raise HTTPException(status_code=422, detail="Potência deve ser entre 0 e 100 kWp")

    cached = cache_valido(potencia)
    if cached:
        return JSONResponse(content={**cached, "source": "cache"})

    if scraper_lock.locked():
        raise HTTPException(status_code=429, detail="Scraping em andamento. Use POST /jobs.")

    try:
        with scraper_lock:
            result = consultar_precos(potencia)
            cache[potencia] = {"result": result, "cached_at": time.time()}
            return JSONResponse(content={**result, "source": "live"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
