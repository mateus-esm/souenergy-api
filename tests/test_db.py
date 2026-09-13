# -*- coding: utf-8 -*-
"""Testes de persistencia (plano §9): primer ciclo, preço igual, cambio de un
centavo, nulo comprovado, restauración, doble promoção, interrupción,
ausencias completas, reinício y lectura durante promoção."""
import uuid

import db
from conftest import crear_scrape_con_observaciones, observacion

URL = "https://souenergy.com.br/kit-teste.html"


def _n_historico(conn, url=URL):
    return len(db.obtener_historico(conn, url))


def _promover(conn, observaciones):
    scrape_id = uuid.uuid4().hex
    db.crear_scrape(conn, scrape_id=scrape_id, versao_coletor="test",
                    contexto_preco="cliente", autenticado=True)
    for obs in observaciones:
        db.guardar_observacion(conn, scrape_id=scrape_id, url=obs["url"],
                               status=obs["status"], dados=obs.get("datos"))
    db.marcar_scrape(conn, scrape_id=scrape_id, status="executando",
                     descobertos=len(observaciones),
                     processados=len(observaciones), falhas=0)
    db.promover_scrape(conn, scrape_id=scrape_id)
    return scrape_id


def test_primer_ciclo(conn):
    _promover(conn, [observacion(URL, preco=1234567, preco_pix="R$ 12.345,67")])
    catalogo = db.obtener_catalogo_aprobado(conn)
    assert len(catalogo) == 1
    assert catalogo[0]["preco_normalizado"] == 1234567
    assert _n_historico(conn) == 1
    assert db.obtener_historico(conn, URL)[0]["motivo"] == "inicial"


def test_precio_igual_no_duplica_historico(conn):
    _promover(conn, [observacion(URL, preco=1234567)])
    _promover(conn, [observacion(URL, preco=1234567)])
    assert _n_historico(conn) == 1


def test_cambio_un_centavo(conn):
    _promover(conn, [observacion(URL, preco=1234567)])
    _promover(conn, [observacion(URL, preco=1234568)])
    assert _n_historico(conn) == 2
    assert db.obtener_historico(conn, URL)[-1]["motivo"] == "alterado"


def test_nulo_comprovado(conn):
    _promover(conn, [observacion(URL, preco=1234567)])
    _promover(conn, [observacion(URL, preco=None, preco_pix=None,
                                 disponibilidade="indisponible")])
    assert _n_historico(conn) == 2
    assert db.obtener_historico(conn, URL)[-1]["motivo"] == "indisponivel"
    assert db.obtener_catalogo_aprobado(conn)[0]["disponibilidade"] == "indisponible"


def test_restauracion_disponibilidad(conn):
    _promover(conn, [observacion(URL, preco=1234567)])
    _promover(conn, [observacion(URL, preco=None, disponibilidade="indisponible")])
    _promover(conn, [observacion(URL, preco=1240000, disponibilidade="disponible")])
    assert _n_historico(conn) == 3
    assert db.obtener_historico(conn, URL)[-1]["motivo"] == "restabelecido"


def test_mismo_scrape_promovido_dos_veces(conn):
    scrape_id = _promover(conn, [observacion(URL, preco=1234567)])
    # Reejecutar promoção con el mismo scrape_id no duplica histórico
    db.promover_scrape(conn, scrape_id=scrape_id)
    assert _n_historico(conn) == 1


def test_interrupcion_antes_de_promocion(conn):
    scrape_id = uuid.uuid4().hex
    db.crear_scrape(conn, scrape_id=scrape_id, versao_coletor="test",
                    contexto_preco="cliente", autenticado=True)
    db.guardar_observacion(conn, scrape_id=scrape_id, url=URL, status="ok",
                           dados=observacion(URL, preco=1234567)["datos"])
    # Proceso interrumpido: se marca interrompido, observaciones se conservan
    db.marcar_interrompido(conn, scrape_id=scrape_id)
    fila = conn.execute("SELECT * FROM scrapes WHERE id = ?",
                        (scrape_id,)).fetchone()
    assert fila["status"] == "interrompido"
    assert conn.execute("SELECT COUNT(*) AS n FROM observacoes WHERE scrape_id = ?",
                        (scrape_id,)).fetchone()["n"] == 1
    # Sin promoção: el catálogo sigue vacío
    assert db.obtener_catalogo_aprobado(conn) == []


def test_dos_ausencias_completas_inactiva(conn):
    _promover(conn, [observacion(URL, preco=1234567)])
    # Coleta 2: no observa el producto (ausencia 1)
    _promover(conn, [observacion("https://souenergy.com.br/otro.html",
                                 preco=100000)])
    assert db.obtener_catalogo_aprobado(conn)[0]["ativo"] == 1
    # Coleta 3: ausencia 2 -> inactivo
    _promover(conn, [observacion("https://souenergy.com.br/otro.html",
                                 preco=100000)])
    fila = conn.execute("SELECT * FROM productos WHERE url = ?",
                        (URL,)).fetchone()
    assert fila["ativo"] == 0
    assert fila["ausencias_completas"] == 2


def test_falha_parcial_no_incrementa_ausencias(conn):
    _promover(conn, [observacion(URL, preco=1234567)])
    # Coleta parcial (rejeitada): no se promueve, no toca ausencias
    scrape_id = uuid.uuid4().hex
    db.crear_scrape(conn, scrape_id=scrape_id, versao_coletor="test",
                    contexto_preco="cliente", autenticado=True)
    db.guardar_observacion(conn, scrape_id=scrape_id, url=URL, status="erro",
                           erro_codigo="timeout")
    db.marcar_scrape(conn, scrape_id=scrape_id, status="rejeitado",
                     erro_codigo="cobertura_incompleta")
    fila = conn.execute("SELECT * FROM productos WHERE url = ?",
                        (URL,)).fetchone()
    assert fila["ativo"] == 1
    assert fila["ausencias_completas"] == 0


def test_reinicio_preserva_catalogo_y_jobs(conn, tmp_path):
    scrape_id = crear_scrape_con_observaciones(
        conn, [observacion(URL, preco=1234567)])
    db.crear_job_consulta(conn, job_id="job-1", potencia=7.0,
                          resultado={"ok": True}, scrape_id=scrape_id)
    # "Reinício": nueva conexión sobre el mismo archivo
    c2 = db.conectar(tmp_path / "catalogo.sqlite3", modo="ro")
    try:
        assert len(db.obtener_catalogo_aprobado(c2)) == 1
        assert db.obtener_job_consulta(c2, "job-1") is not None
    finally:
        c2.close()


def test_lectura_durante_escritura(conn, conn_ro):
    """La API (conexión ro) lee sin bloquear mientras el escritor tiene una
    transacción abierta (WAL)."""
    crear_scrape_con_observaciones(conn, [observacion(URL, preco=1234567)])
    conn.execute("BEGIN")
    conn.execute("INSERT INTO scrapes (id, iniciado_em, status,"
                 " versao_coletor, contexto_preco) VALUES"
                 " ('pendiente', '2026-09-13T00:00:00Z', 'executando', 'x', 'c')")
    # Lectura concurrente no bloquea y ve el estado commiteado
    assert len(db.obtener_catalogo_aprobado(conn_ro)) == 1
    conn.rollback()


def test_integridad(conn):
    crear_scrape_con_observaciones(conn, [observacion(URL, preco=1234567)])
    assert db.integrity_check(conn) == ["ok"]


def test_backup_y_restauracion(conn, tmp_path):
    crear_scrape_con_observaciones(conn, [observacion(URL, preco=1234567)])
    destino = tmp_path / "backups" / "catalogo.sqlite3"
    db.backup(conn, destino)
    assert destino.exists()
    c2 = db.conectar(destino, modo="ro")
    try:
        assert len(db.obtener_catalogo_aprobado(c2)) == 1
        assert db.integrity_check(c2) == ["ok"]
    finally:
        c2.close()