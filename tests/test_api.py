# -*- coding: utf-8 -*-
"""Testes de la API (plano §9): autenticación cerrada, lectura desde SQLite,
jobs persistidos y arranque sin API_KEY."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient

import db
from conftest import crear_scrape_con_observaciones, observacion

URL = "https://souenergy.com.br/kit-mono-7440.html"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", "clave-test")
    monkeypatch.setenv("SOUENERGY_DB", str(tmp_path / "catalogo.sqlite3"))
    import api
    importlib.reload(api)

    conn = db.conectar(tmp_path / "catalogo.sqlite3", modo="rw")
    db.migrar(conn)
    crear_scrape_con_observaciones(conn, [
        observacion(URL, preco=1234567, preco_pix="R$ 12.345,67",
                    marca="SOLPLANET", fase="mono", modulo_potencia_w=620,
                    potencia_kwp=7.44, quantidade=12)])
    conn.close()
    return TestClient(api.app)


def test_sin_chave_403(client):
    r = client.get("/precos", params={"potencia": 7})
    assert r.status_code == 403


def test_chave_invalida_403(client):
    r = client.get("/precos", params={"potencia": 7},
                   headers={"X-API-Key": "incorrecta"})
    assert r.status_code == 403


def test_precos_desde_sqlite(client):
    r = client.get("/precos", params={"potencia": 7},
                   headers={"X-API-Key": "clave-test"})
    assert r.status_code == 200
    datos = r.json()
    assert datos["source"] == "database"
    assert datos["desactualizado"] is False
    assert datos["coleta_aprovada_em"]
    assert len(datos["solplanet"]) == 1
    assert datos["solplanet"][0]["preco_normalizado"] == 1234567


def test_estado(client):
    r = client.get("/", headers={"X-API-Key": "clave-test"})
    assert r.status_code == 200
    assert r.json()["source"] == "database"
    assert r.json()["desactualizado"] is False


def test_job_persistido_sobrevive_reinicio(client, tmp_path, monkeypatch):
    r = client.post("/jobs", params={"potencia": 7},
                    headers={"X-API-Key": "clave-test"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    r2 = client.get(f"/jobs/{job_id}", headers={"X-API-Key": "clave-test"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "done"
    assert r2.json()["result"]["source"] == "database"

    # "Reinício": nueva conexión de lectura ve el job persistido
    conn = db.conectar(tmp_path / "catalogo.sqlite3", modo="ro")
    try:
        assert db.obtener_job_consulta(conn, job_id) is not None
    finally:
        conn.close()


def test_sin_coleta_aprobada_503(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", "clave-test")
    monkeypatch.setenv("SOUENERGY_DB", str(tmp_path / "vacia.sqlite3"))
    import api
    importlib.reload(api)
    conn = db.conectar(tmp_path / "vacia.sqlite3", modo="rw")
    db.migrar(conn)
    conn.close()
    c = TestClient(api.app)
    r = c.get("/precos", params={"potencia": 7},
              headers={"X-API-Key": "clave-test"})
    assert r.status_code == 503


def test_arranque_sin_api_key_falla(monkeypatch):
    """Autenticación cerrada por defecto: sin API_KEY la API no arranca."""
    monkeypatch.setenv("API_KEY", "x")
    import api
    importlib.reload(api)
    monkeypatch.delenv("API_KEY")
    with pytest.raises(RuntimeError):
        importlib.reload(api)