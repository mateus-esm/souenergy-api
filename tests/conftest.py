# -*- coding: utf-8 -*-
"""Fixtures de test: DB temporal, página falsa (Playwright duck-typing) y
helpers para construir observaciones/coletas."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

# Modo rápido del scraper para testes (sin esperas reales)
os.environ.setdefault("SOUENERGY_SCRAPER_RAPIDO", "1")

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import db  # noqa: E402


# ─── DB temporal ──────────────────────────────────────────────────────────────

@pytest.fixture()
def conn(tmp_path):
    """Conexión SQLite migrada en directorio temporal."""
    ruta = tmp_path / "catalogo.sqlite3"
    c = db.conectar(ruta, modo="rw")
    db.migrar(c)
    yield c
    c.close()


@pytest.fixture()
def conn_ro(conn, tmp_path):
    """Conexión de lectura sobre la misma DB (como la API)."""
    c = db.conectar(tmp_path / "catalogo.sqlite3", modo="ro")
    yield c
    c.close()


def observacion(url, *, status="ok", preco=None, preco_pix=None,
                nome="Kit teste", marca="SOLPLANET", tipo_inversor="string",
                fase="mono", modulo_potencia_w=620, quantidade=12,
                potencia_kwp=7.44, disponibilidade="disponible",
                tipo_produto="kit", sku=None, inversor="Solplanet X1",
                modulo="Painel 620W", estrutura="Estrutura A",
                motivo_nao_exportavel=None):
    """Construye una observación 'ok' con datos normalizados."""
    return {
        "url": url, "status": status,
        "datos": {
            "nome": nome, "url": url, "sku": sku,
            "tipo_produto": tipo_produto, "marca": marca,
            "marca_modulo": None, "potencia_kwp": potencia_kwp,
            "fase": fase, "tipo_inversor": tipo_inversor,
            "inversor": inversor, "modulo": modulo,
            "modulo_potencia_w": modulo_potencia_w,
            "quantidade_modulos": quantidade, "estrutura": estrutura,
            "preco_pix": preco_pix, "preco_normalizado": preco,
            "moeda": "BRL", "disponibilidade": disponibilidade,
            "motivo_nao_exportavel": motivo_nao_exportavel,
            "origem_campos": {"preco_pix": ".pix-price-container .price"},
        },
    }


def crear_scrape_con_observaciones(conn, observaciones, **kwargs):
    """Crea scrape + observaciones y lo promueve. Devuelve scrape_id."""
    scrape_id = uuid.uuid4().hex
    db.crear_scrape(conn, scrape_id=scrape_id, versao_coletor="test",
                    contexto_preco="cliente", autenticado=True)
    for obs in observaciones:
        db.guardar_observacion(conn, scrape_id=scrape_id, url=obs["url"],
                               status=obs["status"],
                               dados=obs.get("datos"),
                               erro_codigo=obs.get("erro_codigo"))
    db.marcar_scrape(conn, scrape_id=scrape_id, status="executando",
                     descobertos=len(observaciones),
                     processados=len(observaciones), falhas=0)
    db.promover_scrape(conn, scrape_id=scrape_id)
    return scrape_id


# ─── Página falsa (Playwright duck-typing) ────────────────────────────────────

class ElementoFalso:
    def __init__(self, texto="", atributos=None):
        self._texto = texto
        self._atributos = atributos or {}

    def inner_text(self):
        return self._texto

    def is_visible(self):
        return True

    def get_attribute(self, nombre):
        return self._atributos.get(nombre)


class _Card:
    def __init__(self, nome, href, precio):
        self._link = ElementoFalso(texto=nome, atributos={"href": href})
        self._precio = precio

    def query_selector(self, sel):
        if sel == '.product-item-link':
            return self._link
        if sel in ('.price', '.price-final_price', '.pix-price-container'):
            return ElementoFalso() if self._precio else None
        return None


class PaginaFalsa:
    """Sirve definiciones de página estructuradas (sin HTML real)."""

    def __init__(self, paginas: dict):
        self._paginas = paginas
        self.url_actual = None
        self.visitas = []

    def _def(self):
        return self._paginas.get(self.url_actual, {})

    def goto(self, url, timeout=None, wait_until=None):
        self.url_actual = url
        self.visitas.append(url)

    def wait_for_selector(self, sel, timeout=None, state=None):
        d = self._def()
        # Solo `timeout=True` simula un timeout real. Una página sin cards de
        # producto (índice de subcategorías) es un listado válido con 0 cards.
        if d.get("timeout"):
            raise TimeoutError("timeout simulado")

    def query_selector_all(self, sel):
        d = self._def()
        if sel == 'main .products .product-item':
            return [_Card(c["nome"], c["href"], c.get("precio", False))
                    for c in d.get("cards", [])]
        if sel == 'main .message.info.empty' and d.get("vacio"):
            return [ElementoFalso(texto=d.get("body", ""))]
        if sel in ('.nav a', '.navigation a', '.categories a', '.widget a',
                   '.breadcrumbs a', '.menu a'):
            return [ElementoFalso(atributos={"href": h})
                    for h in d.get("enlaces_categoria", [])]
        return []

    def query_selector(self, sel):
        d = self._def()
        if sel == '.product-info-main':
            if d.get("es_producto"):
                return ElementoFalso(texto=d.get("ficha", ""))
            return None
        if sel == 'main .products .product-item':
            return ElementoFalso() if d.get("cards") else None
        if sel in ('.pix-price-container .price', '.pix-price .price',
                   '[data-price-type="pix"] .price'):
            return ElementoFalso(texto=d["precio_pix"]) if d.get("precio_pix") else None
        if sel in ('.sku', '.product-sku', '[itemprop="sku"]'):
            return ElementoFalso(texto=d["sku"]) if d.get("sku") else None
        if sel in ('.stock', '.availability', '.product-stock'):
            return ElementoFalso(texto=d["disponibilidad"]) if d.get("disponibilidad") else None
        if sel in ('.pages-item-next a', 'a.next', 'a[rel="next"]', '.next i-page'):
            return ElementoFalso(atributos={"href": d["siguiente"]}) if d.get("siguiente") else None
        if sel in ('.pages', '.pager', '.toolbar .pages'):
            return ElementoFalso() if d.get("toolbar") else None
        return None

    def is_visible(self, sel):
        return True

    def evaluate(self, js):
        return None

    def fill(self, *a, **k):
        return None

    def wait_for_load_state(self, *a, **k):
        return None

    def inner_text(self, sel):
        d = self._def()
        if sel == 'body':
            return d.get("body", "")
        return ""

    def title(self):
        return self._def().get("titulo", "")


def pagina_producto(url, *, nome="Kit teste", ficha="", titulo="",
                    precio_pix="R$ 12.345,67", sku=None,
                    disponibilidad="disponible", siguiente=None):
    """Definición de página de detalle de producto."""
    return {
        "es_producto": True, "ficha": ficha, "titulo": titulo or nome,
        "precio_pix": precio_pix, "sku": sku,
        "disponibilidad": disponibilidad, "siguiente": siguiente,
    }


def pagina_listado(url, *, cards=None, enlaces_categoria=None, siguiente=None,
                   vacio=False, timeout=False, toolbar=False, body=""):
    return {
        "cards": cards or [], "enlaces_categoria": enlaces_categoria or [],
        "siguiente": siguiente, "vacio": vacio, "timeout": timeout,
        "toolbar": toolbar, "body": body,
    }


def card(nome, href, precio=False):
    return {"nome": nome, "href": href, "precio": precio}