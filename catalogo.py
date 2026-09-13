# -*- coding: utf-8 -*-
"""Configuração do catálogo SouEnergy: entradas, classificação, URLs canónicas
e relatório de cobertura.

Plano SE-PRICES-002 §3/§4:
- Não limitar a duas marcas: as entradas são configurables (env `CATALOGO_ENTRADAS`
  ou `config/entradas_catalogo.json`).
- Não usar potência como classificador: inspeccionar tipo de página/DOM.
- Canonicalizar URLs relativas, fragmentos e parámetros de rastreamento;
  preservar parámetros que identifican variantes.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"

# Parámetros de rastreamento que não identifican variantes comerciales
_PARAMS_RASTREO = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'gclid', 'fbclid', 'gbraid', 'wbraid', 'yclid', 'igshid', 'ref',
}

# Segmentos de ruta típicos de categoría en Magento
_SEGMENTOS_CATEGORIA = (
    'inversores-e-microinversores', 'kits-solares', 'paineles-solares',
    'estructuras', 'accesorios', 'baterias', 'categorias', 'solplanet',
    'hoymiles', 'microinversores', 'inversores',
)


def cargar_entradas(env_override: str | None = None) -> list[dict]:
    """Carga las entradas del catálogo.

    Precedência: variable de ambiente `CATALOGO_ENTRADAS` (formato
    "nome|url|marca;nome|url|marca") > config/entradas_catalogo.json.
    """
    valor = env_override if env_override is not None else os.getenv("CATALOGO_ENTRADAS")
    if valor and valor.strip():
        entradas = []
        for item in valor.split(';'):
            item = item.strip()
            if not item:
                continue
            partes = [p.strip() for p in item.split('|')]
            if len(partes) != 3 or not partes[1]:
                raise ValueError(f"Entrada de catálogo inválida: {item!r}")
            entradas.append({"nome": partes[0], "url": partes[1], "marca": partes[2]})
        if entradas:
            return entradas
    archivo = CONFIG_DIR / "entradas_catalogo.json"
    if archivo.exists():
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        return datos.get("entradas", [])
    return []


def canonicalizar_url(url: str, base: str | None = None) -> str:
    """URL canónica: absoluta, sem fragmento, sem parámetros de rastreo.

    Preserva parámetros que identifican variantes (p, id, sku, ...).
    """
    if not url:
        return ""
    if base:
        url = urljoin(base, url)
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https"):
        return url
    query = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True)
             if k.lower() not in _PARAMS_RASTREO]
    return urlunsplit((partes.scheme, partes.netloc, partes.path,
                       urlencode(query), ""))


def es_enlace_categoria(href: str) -> bool:
    """Heurística: o link parece de categoría (no de produto).

    Categorías Magento suelen tener rutas con segmentos de categoría conocidos
    o terminar en '/' (directorio). Produtos terminan en '.html' com ruta
    de produto. Esta heurística é só uma pista; a verificação final acontece
    na página de detalle (ver scraper.recorrer_catalogo).
    """
    if not href:
        return False
    ruta = urlsplit(href).path.lower().rstrip('/')
    if not ruta:
        return False
    # O menu também contém páginas institucionais, inclusive rotas Magento
    # /catalog/category/view/s/quem-somos/id/...; não são catálogo de produtos.
    institucionais = {
        "quem-somos", "calculadora", "formas-de-pagamento",
        "politica-de-privacidade", "politica-de-vendas", "sobre",
    }
    if any(segmento.removesuffix(".html") in institucionais
           for segmento in ruta.split("/")):
        return False
    if ruta.endswith('.html'):
        # Un .html bajo un segmento de categoría conocido puede ser subcategoría
        for seg in _SEGMENTOS_CATEGORIA:
            if f'/{seg}/' in ruta or ruta.endswith(f'/{seg}'):
                return True
        return False
    return True


def classificar_candidato(nome: str, href: str, tiene_precio: bool) -> str:
    """Clasifica un candidato como 'producto' | 'subcategoria' | 'desconocido'.

    No usa potência. Un card con precio visible é produto; un enlace de
    categoría (heurística) é subcategoría; si no, 'desconocido' (se decide
    al abrir el detalle).
    """
    if tiene_precio:
        return 'producto'
    if es_enlace_categoria(href):
        return 'subcategoria'
    return 'desconocido'


def firma_cards(cards: list[dict]) -> str:
    """Assinatura de un conjunto de cards (detección de repetición de página)."""
    items = sorted((c.get("url", ""), c.get("nome", "")) for c in cards)
    return repr(items)


def relatorio_cobertura(*, entradas, categorias_planeadas, categorias_visitadas,
                        paginas_visitadas, productos_descubiertos,
                        detalles_validos, indisponibles, fallos, duplicados,
                        fuera_mapeo, fila_esgotada, autenticado) -> dict:
    """Relatório de cobertura de una ejecución (plano §4.3)."""
    return {
        "entradas": entradas,
        "categorias_planeadas": categorias_planeadas,
        "categorias_visitadas": categorias_visitadas,
        "paginas_visitadas": paginas_visitadas,
        "productos_descubiertos": productos_descubiertos,
        "detalles_validos": detalles_validos,
        "indisponibles": indisponibles,
        "fallos": fallos,
        "duplicados": duplicados,
        "fuera_mapeo": fuera_mapeo,
        "fila_esgotada": fila_esgotada,
        "autenticado": autenticado,
        "completa": bool(fila_esgotada and autenticado and fallos == 0
                         and detalles_validos > 0),
    }


def validar_entradas(entradas: list[dict]) -> None:
    """Valida que las entradas tengan nome/url/marca no vacíos."""
    if not entradas:
        raise ValueError("Nenhuma entrada de catálogo configurada")
    for e in entradas:
        if not url_autorizada(e.get("url", "")):
            raise ValueError("Entrada fora do domínio ou rota autorizados")
        if not e.get("nome") or not e.get("url") or not e.get("marca"):
            raise ValueError(f"Entrada de catálogo incompleta: {e!r}")

def url_autorizada(url: str) -> bool:
    """Somente catálogo da origem; exclui conta, carrinho e ações de sessão."""
    partes = urlsplit(url)
    return (partes.scheme == "https"
            and partes.hostname in ("souenergy.com.br", "www.souenergy.com.br")
            and not partes.username and not partes.password
            and not re.search(r"/(customer|checkout|cart|wishlist|logout|account)(/|$)", partes.path, re.I))
