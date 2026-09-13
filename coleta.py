# -*- coding: utf-8 -*-
"""CLI do ciclo completo de coleta: scrape -> observações -> validação ->
promoção -> exportação candidata (-> publicação opcional).

Plano SE-PRICES-002 §4.3/§5/§7:
- `flock` adquirido atomicamente antes de abrir browser (execuções manuais e
  agendadas não se pisan). Nunca confiar em verificar existência de arquivo.
- Observações persistidas incrementalmente, sem transação aberta durante
  navegación. Promoção atómica só após validar execução inteira.
- Cobertura incompleta / login falho / queda > 20% / zero produtos -> status
  rejeitado, saída não zero, NINGUNA promoção.
- Códigos de saída: 0 ok · 1 erro · 2 login_falhou · 3 rejeitado ·
  4 interrompido (recuperación) · 5 lock.

Uso:
    python coleta.py [--db PATH] [--no-promote] [--limite-paginas N]
                     [--limite-tiempo-min N] [--entradas "a|url|marca;..."]
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

import db
from alertas import enviar_alerta, enviar_resumen_cambios
from catalogo import cargar_entradas, relatorio_cobertura, validar_entradas
from scraper import (BloqueadoError, Limites, LoginError, VERSION,
                     scrapear_tudo)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("coleta")

EXIT_OK = 0
EXIT_ERRO = 1
EXIT_LOGIN = 2
EXIT_REJEITADO = 3
EXIT_INTERROMPIDO = 4
EXIT_LOCK = 5

QUEDA_MAXIMA_PCT = 20.0


def _db_path(args) -> str:
    return args.db or os.getenv("SOUENERGY_DB", "/var/lib/souenergy/catalogo.sqlite3")


def _lockfile_path(args) -> str:
    return args.lockfile or os.getenv(
        "SOUENERGY_LOCK", "/var/lib/souenergy/coleta.lock")


def adquirir_lock(ruta: str):
    """flock exclusivo no bloqueante. Devuelve el fd o None si ya hay otro."""
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(ruta, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def recuperar_interrumpidos(conn) -> None:
    """Recuperación: coleta interrumpida mantiene observaciones de diagnóstico
    y se marca `interrompido`; la promoção requiere nueva coleta autenticada."""
    filas = conn.execute(
        "SELECT id FROM scrapes WHERE status = 'executando'").fetchall()
    for f in filas:
        db.marcar_interrompido(conn, scrape_id=f["id"])
        log.warning(f"Coleta interrumpida marcada: {f['id']}")


def validar_cobertura(conn, scrape_id: str, metricas: dict,
                      inventario: list[dict]) -> tuple[bool, str]:
    """Criterio de completud (plano §4.3): fila esgotada, autenticado,
    sin fallos sin resolución, >0 produtos y sin queda > 20% vs último ciclo
    aprobado. Devuelve (ok, motivo)."""
    if not metricas.get("autenticado", False):
        return False, "sessão não autenticada"
    if not metricas.get("fila_esgotada", False):
        return False, "fila não esgotada (cobertura incompleta)"
    if metricas.get("fallos", 0) > 0:
        return False, f"{metricas['fallos']} falhas sem resolução"
    if metricas.get("productos_descubiertos", 0) == 0:
        return False, "zero produtos descobertos"

    # Queda > 20% vs último ciclo aprovado (por total)
    previo = conn.execute(
        "SELECT * FROM scrapes WHERE status = 'ok' AND id != ?"
        " ORDER BY finalizado_em DESC LIMIT 1", (scrape_id,)).fetchone()
    if previo is not None:
        total_previo = previo["descobertos"] or 0
        total_actual = metricas.get("productos_descubiertos", 0)
        if total_previo > 0 and total_actual < total_previo * (1 - QUEDA_MAXIMA_PCT / 100):
            return False, (f"queda de {total_previo} -> {total_actual} "
                           f"produtos (> {QUEDA_MAXIMA_PCT:.0f}%)")
        # Queda por categoría anteriormente no vacía
        previo_por_cat = _categorias_de_cobertura(previo)
        actual_por_cat = _contar_por_categoria(inventario)
        for cat, n_previo in previo_por_cat.items():
            if n_previo == 0:
                continue
            n_actual = actual_por_cat.get(cat, 0)
            if n_actual < n_previo * (1 - QUEDA_MAXIMA_PCT / 100):
                return False, (f"queda na categoria {cat}: "
                               f"{n_previo} -> {n_actual}")
    return True, ""


def _categorias_de_cobertura(scrape) -> dict[str, int]:
    try:
        datos = json.loads(scrape["cobertura_json"] or "{}")
        return datos.get("por_categoria", {})
    except (json.JSONDecodeError, AttributeError):
        return {}


def _contar_por_categoria(inventario: list[dict]) -> dict[str, int]:
    contagem: dict[str, int] = {}
    for prod in inventario:
        for cat in prod.get("categorias", []):
            contagem[cat] = contagem.get(cat, 0) + 1
    return contagem


def _alertar_cambios(conn, scrape_id: str) -> None:
    """Resumen de cambios + alerta de variación anormal (> umbral, plano §7)."""
    cambios = db.obtener_cambios_scrape(conn, scrape_id)
    if not cambios:
        return
    variacion_maxima = float(os.getenv("VARIACION_MAXIMA_PCT", "30"))
    anormales = []
    for c in cambios:
        anterior, nuevo = c["preco_anterior"], c["preco"]
        if anterior and nuevo:
            pct = abs(nuevo - anterior) / anterior * 100
            if pct > variacion_maxima:
                anormales.append({
                    "nome": c["nome"], "url": c["url"],
                    "preco_anterior": anterior, "preco_nuevo": nuevo,
                    "motivo": c["motivo"], "pct": round(pct, 1)})
    if anormales:
        lineas = "\n".join(
            f"- {a['nome']}: {a['preco_anterior']} -> {a['preco_nuevo']} "
            f"({a['pct']}%)" for a in anormales[:20])
        enviar_alerta("Variación anormal de precios SouEnergy", lineas,
                      nivel="warning", evento="variacion_anormal")
    enviar_resumen_cambios(
        [{"nome": c["nome"], "url": c["url"], "preco_anterior": c["preco_anterior"],
          "preco_nuevo": c["preco"], "motivo": c["motivo"]} for c in cambios],
        {"alterados": len(cambios)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coleta completa SouEnergy")
    parser.add_argument("--db", help="Ruta del SQLite (default: SOUENERGY_DB)")
    parser.add_argument("--lockfile", help="Ruta del lock (default: SOUENERGY_LOCK)")
    parser.add_argument("--no-promote", action="store_true",
                        help="Solo scrape + observaciones, sin promoção")
    parser.add_argument("--limite-paginas", type=int, default=None)
    parser.add_argument("--limite-tiempo-min", type=int, default=None)
    parser.add_argument("--entradas", default=None,
                        help='Entradas "nome|url|marca;..." (override)')
    parser.add_argument("--contexto-preco", default="cliente")
    args = parser.parse_args(argv)

    fd = adquirir_lock(_lockfile_path(args))
    if fd is None:
        log.error("Outra coleta já está em execução (lock ocupado)")
        return EXIT_LOCK
    try:
        return _ejecutar(args)
    finally:
        os.close(fd)


def _ejecutar(args) -> int:
    conn = db.conectar(_db_path(args), modo="rw")
    try:
        db.migrar(conn)
        recuperar_interrumpidos(conn)

        entradas = cargar_entradas(args.entradas)
        validar_entradas(entradas)
        scrape_id = uuid.uuid4().hex
        db.crear_scrape(conn, scrape_id=scrape_id,
                        versao_coletor=VERSION,
                        contexto_preco=args.contexto_preco)

        limites = Limites(max_paginas=args.limite_paginas,
                          max_tiempo_min=args.limite_tiempo_min)
        log.info(f"Coleta {scrape_id} iniciada — {len(entradas)} entradas")
        try:
            resultado = scrapear_tudo(entradas=entradas, limites=limites,
                                      contexto_preco=args.contexto_preco)
        except LoginError as e:
            db.marcar_scrape(conn, scrape_id=scrape_id,
                             status="login_falhou", erro_codigo="login_falhou")
            log.error(f"Login falho: {e}")
            enviar_alerta("Login falho SouEnergy", str(e), nivel="critical",
                          evento="login_falhou")
            return EXIT_LOGIN
        except BloqueadoError as e:
            db.marcar_scrape(conn, scrape_id=scrape_id, status="erro",
                             erro_codigo="bloqueado")
            log.error(f"Bloqueo persistente: {e}")
            enviar_alerta("Bloqueo persistente (403/CAPTCHA)", str(e),
                          nivel="critical", evento="bloqueado")
            return EXIT_ERRO
        except Exception as e:
            db.marcar_scrape(conn, scrape_id=scrape_id, status="erro",
                             erro_codigo="excepcion")
            log.exception("Erro durante la coleta")
            return EXIT_ERRO

        # Persistir observaciones incrementalmente
        for obs in resultado["observaciones"]:
            db.guardar_observacion(
                conn, scrape_id=scrape_id, url=obs["url"],
                status=obs["status"],
                dados=obs.get("datos"),
                erro_codigo=obs.get("erro_codigo"),
                tentativas=obs.get("tentativas", 1))

        metricas = resultado["metricas"]
        metricas["autenticado"] = resultado["autenticado"]
        cobertura = relatorio_cobertura(
            entradas=[e["nome"] for e in entradas],
            categorias_planeadas=metricas["categorias_planeadas"],
            categorias_visitadas=metricas["categorias_visitadas"],
            paginas_visitadas=metricas["paginas_visitadas"],
            productos_descubiertos=metricas["productos_descubiertos"],
            detalles_validos=metricas["detalles_validos"],
            indisponibles=metricas["indisponibles"],
            fallos=metricas["fallos"],
            duplicados=metricas["duplicados"],
            fuera_mapeo=metricas["fuera_mapeo"],
            fila_esgotada=metricas["fila_esgotada"],
            autenticado=resultado["autenticado"])
        cobertura["por_categoria"] = _contar_por_categoria(
            resultado["inventario"])

        db.marcar_scrape(conn, scrape_id=scrape_id, status="executando",
                         descobertos=metricas["productos_descubiertos"],
                         processados=metricas["detalles_validos"],
                         falhas=metricas["fallos"],
                         cobertura_json=cobertura)

        ok, motivo = validar_cobertura(conn, scrape_id, metricas,
                                       resultado["inventario"])
        if not ok:
            db.marcar_scrape(conn, scrape_id=scrape_id, status="rejeitado",
                             erro_codigo="cobertura_incompleta")
            log.error(f"Coleta rejeitada: {motivo}")
            enviar_alerta("Coleta rejeitada SouEnergy", motivo,
                          nivel="warning", evento="cobertura_incompleta")
            return EXIT_REJEITADO

        alteracoes = 0
        if not args.no_promote:
            alteracoes = db.promover_scrape(conn, scrape_id=scrape_id)
            log.info(f"Promoção ok — {alteracoes} cambios de precio")
            _alertar_cambios(conn, scrape_id)

        # Exportación candidata + validación de contrato
        try:
            from exportar_precos import exportar_precios
            ruta, hash_json, reporte = exportar_precios(conn, directorio=None)
            log.info(f"precios.json candidato: {ruta} "
                     f"(hash {hash_json[:16]}…)")
            log.info(f"Mapeo: {reporte['elegidos']} elegidos, "
                     f"{reporte['no_mapeados']} no mapeados, "
                     f"{reporte['conflictos']} conflictos")
        except Exception as e:
            log.warning(f"Exportación candidata falló (no bloquea la coleta "
                        f"aprobada): {e}")

        log.info(f"Coleta {scrape_id} completada — "
                 f"{metricas['productos_descubiertos']} produtos, "
                 f"{metricas['detalles_validos']} detalles válidos, "
                 f"{metricas['fallos']} falhas")
        return EXIT_OK
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())