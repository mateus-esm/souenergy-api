# -*- coding: utf-8 -*-
"""API SouEnergy — consultas sobre SQLite (última coleta aprovada).

Plano SE-PRICES-002 §3:
- Sin estado en memoria: jobs y cache viven en SQLite (`jobs_consulta`).
- Autenticación cerrada por defecto: sin `API_KEY` configurada la API NO
  arranca; comparación en tiempo constante. Sin chave en el navegador.
- `GET /precos?potencia=` consulta el snapshot aprovado con `source=database`,
  fecha de coleta e indicación de desactualización; sin snapshot -> 503.
- `POST /jobs` + `GET /jobs/{id}` son capa de compatibilidad: crean un
  resultado de consulta ya concluido persistido en `jobs_consulta` (202 + URL
  de consulta). Ninguna fila volátil de scraping.
- La API abre el catálogo en modo lectura; las transacciones cortas de
  `jobs_consulta` van en conexión separada de las de promoción. El colector
  es el único escritor del catálogo.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader

import db

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("api")

VERSION = "3.0.0"

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError(
        "API_KEY ausente o vacía — la API no arranca (autenticación cerrada)")

DB_PATH = os.getenv("SOUENERGY_DB", "/var/lib/souenergy/catalogo.sqlite3")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(
    title="SouEnergy Price API",
    description=(
        "Consulta preços de kits solares (SOLPLANET y HOYMILES) na SouEnergy, "
        "a partir da última coleta aprovada em SQLite.\n\n"
        "Autenticación: header `X-API-Key`. Sin chave en el navegador.\n"
        "Endpoints:\n"
        "  GET /precos?potencia=X — snapshot aprovado (source=database)\n"
        "  POST /jobs?potencia=X  — compatibilidade: resultado ya concluido\n"
        "  GET /jobs/{job_id}     — resultado persistido\n"
        "  GET /precios           — catálogo aprobado completo\n"
        "  GET /                  — estado"
    ),
    version=VERSION,
)


def verificar_chave(key: str | None = Security(api_key_header)):
    """Comparación en tiempo constante; header ausente/vacío -> 403."""
    if not key or not hmac.compare_digest(key, API_KEY):
        raise HTTPException(status_code=403, detail="API key inválida")
    return key


def _conn_ro():
    return db.conectar(DB_PATH, modo="ro")


def _conn_rw():
    return db.conectar(DB_PATH, modo="rw")


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.get("/")
def estado():
    conn = _conn_ro()
    try:
        scrape = db.obtener_ultimo_scrape_aprobado(conn)
        return {
            "status": "ok",
            "version": VERSION,
            "source": "database",
            "coleta_aprovada_em": scrape["finalizado_em"] if scrape else None,
            "desactualizado": scrape is None,
        }
    finally:
        conn.close()


@app.get("/precos")
def precos(potencia: float, key: str = Security(verificar_chave)):
    """Compatibilidade legada: snapshot aprovado agrupado por marca."""
    conn = _conn_ro()
    try:
        scrape = db.obtener_ultimo_scrape_aprobado(conn)
        if scrape is None:
            raise HTTPException(
                status_code=503,
                detail="Sin coleta aprovada — datos no disponibles")
        return db.obtener_precios_por_potencia(conn, potencia)
    finally:
        conn.close()


@app.get("/precios")
def precios_completos(key: str = Security(verificar_chave)):
    """Catálogo aprobado completo (modo lectura)."""
    conn = _conn_ro()
    try:
        scrape = db.obtener_ultimo_scrape_aprobado(conn)
        if scrape is None:
            raise HTTPException(
                status_code=503,
                detail="Sin coleta aprovada — datos no disponibles")
        filas = db.obtener_catalogo_aprobado(conn, scrape["id"])
        return {
            "source": "database",
            "coleta_aprovada_em": scrape["finalizado_em"],
            "total": len(filas),
            "productos": [dict(f) for f in filas],
        }
    finally:
        conn.close()


@app.post("/jobs", status_code=202)
def crear_job(potencia: float, key: str = Security(verificar_chave)):
    """Compatibilidade: crea un resultado de consulta ya concluido en
    `jobs_consulta`. Ninguna fila volátil de scraping."""
    job_id = uuid.uuid4().hex
    conn_ro = _conn_ro()
    try:
        scrape = db.obtener_ultimo_scrape_aprobado(conn_ro)
        if scrape is None:
            raise HTTPException(
                status_code=503,
                detail="Sin coleta aprovada — datos no disponibles")
        resultado = db.obtener_precios_por_potencia(conn_ro, potencia)
    finally:
        conn_ro.close()
    conn_rw = _conn_rw()
    try:
        db.crear_job_consulta(conn_rw, job_id=job_id, potencia=potencia,
                              resultado=resultado, scrape_id=scrape["id"])
    finally:
        conn_rw.close()
    return {"job_id": job_id, "status": "done",
            "url": f"/jobs/{job_id}"}


@app.get("/jobs/{job_id}")
def obtener_job(job_id: str, key: str = Security(verificar_chave)):
    conn = _conn_ro()
    try:
        job = db.obtener_job_consulta(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job no encontrado")
        return {
            "job_id": job_id,
            "status": job["status"],
            "result": json.loads(job["resultado_json"] or "{}"),
        }
    finally:
        conn.close()