# -*- coding: utf-8 -*-
"""Alertas operacionais com tentativas limitadas y deduplicación por
ejecución/evento. Sin canal configurado, solo log (nunca falla la coleta).

Plano SE-PRICES-002 §7:
- Alertas previstas: login falho, 403/CAPTCHA/429 persistente, zero produtos,
  cobertura incompleta, queda de contagem, mapeo inválido, variación anormal,
  disco cheio, backup falho, publicación/deploy falho, ausencia de coleta
  aprovada por 36 h.
- Resumen de cambios: produto, precio anterior/nuevo y totales, sin segredos.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request

log = logging.getLogger("alertas")

MAX_TENTATIVAS = 3
ESPERA_BASE = 2.0

# Deduplicación: {evento: fecha_iso} — una alerta por evento y día
_enviadas: dict[str, str] = {}


def _canal_configurado() -> bool:
    return bool(os.getenv("ALERTA_WEBHOOK_URL") or
                (os.getenv("ALERTA_TELEGRAM_TOKEN")
                 and os.getenv("ALERTA_TELEGRAM_CHAT_ID")))


def _enviar_http(url: str, payload: dict) -> None:
    datos = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=datos,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def enviar_alerta(titulo: str, mensaje: str, nivel: str = "info",
                  evento: str | None = None) -> None:
    """Envía una alerta con reintentos limitados y deduplicación por evento/día.

    Nunca incluye segredos: el mensaje debe estar ya sanitizado por el
    llamador. Sin canal configurado, solo log.
    """
    hoy = time.strftime("%Y-%m-%d")
    if evento:
        clave = f"{evento}:{hoy}"
        if _enviadas.get(clave) == hoy:
            return
        _enviadas[clave] = hoy

    if not _canal_configurado():
        log.info(f"[ALERTA {nivel}] {titulo}: {mensaje}")
        return

    webhook = os.getenv("ALERTA_WEBHOOK_URL")
    token = os.getenv("ALERTA_TELEGRAM_TOKEN")
    chat_id = os.getenv("ALERTA_TELEGRAM_CHAT_ID")
    ultimo_error = None
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            if webhook:
                _enviar_http(webhook, {"titulo": titulo, "mensaje": mensaje,
                                       "nivel": nivel, "evento": evento})
            else:
                texto = f"[{nivel}] {titulo}\n{mensaje}"
                _enviar_http(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    {"chat_id": chat_id, "text": texto})
            log.info(f"Alerta enviada ({evento or titulo})")
            return
        except Exception as e:  # noqa: BLE001 — reintento limitado
            ultimo_error = e
            time.sleep(ESPERA_BASE * tentativa)
    log.error(f"Alerta no entregada ({evento or titulo}): {ultimo_error}")


def enviar_resumen_cambios(cambios: list[dict], totais: dict) -> None:
    """Resumen de cambios de precio: produto, anterior/nuevo, totales."""
    if not cambios:
        return
    lineas = []
    for c in cambios[:20]:
        lineas.append(
            f"- {c.get('nome', c.get('url', '?'))}: "
            f"{c.get('preco_anterior')} -> {c.get('preco_nuevo')} "
            f"({c.get('motivo', '')})")
    if len(cambios) > 20:
        lineas.append(f"- … y {len(cambios) - 20} más")
    resumen = "\n".join(lineas)
    totales_txt = ", ".join(f"{k}={v}" for k, v in totais.items())
    enviar_alerta("Cambios de precios SouEnergy",
                  f"{resumen}\nTotales: {totales_txt}",
                  nivel="info", evento="cambios_precios")