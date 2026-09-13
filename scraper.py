# -*- coding: utf-8 -*-
"""Colector completo do catálogo SouEnergy (Playwright).

Plano SE-PRICES-002 §4:
- Descoberta recursiva (BFS) de categorías/páginas/produtos, paginación por
  categoría, sem teto de 5 páginas, sem potência como classificador.
- Autenticación estricta: falha -> LoginError, nunca precios de visitante.
- `scrapear_tudo()` mantiene una sessão e devolve inventario, observaciones y
  métricas. NUNCA persiste ni toca Git (responsabilidade de coleta.py/db.py).
- Un navegador, una página de detalle por vez, intervalo 2-5 s con variación.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from catalogo import (canonicalizar_url, cargar_entradas, classificar_candidato,
                      es_enlace_categoria, firma_cards, validar_entradas, url_autorizada)
from normalizacao import (calcular_potencia_total, extrair_fase,
                          extrair_marca_inversor, extrair_marca_modulo,
                          extrair_quantidade_modulos, extrair_tipo_inversor,
                          normalizar_potencia_kwp,
                          normalizar_potencia_modulo_w, normalizar_precio)

load_dotenv()

log = logging.getLogger("scraper")

VERSION = "3.0.0"

USUARIO = os.getenv("USUARIO")
SENHA = os.getenv("SENHA")
URL_HOME = "https://souenergy.com.br/"

# Selectores específicos de PIX dentro da área do produto. NUNCA fallback
# genérico: sem PIX comprovado, preco_pix = None + motivo.
SELECTORES_PIX = [
    ".pix-price-container .price",
    ".pix-price .price",
    '[data-price-type="pix"] .price',
]
SELECTORES_SKU = [".sku", ".product-sku", '[itemprop="sku"]']
SELECTORES_DISPONIBILIDAD = [".stock", ".availability", ".product-stock"]

TEXTO_INDISPONIBLE = re.compile(
    r'indisponível|indisponivel|fora de estoque|sem estoque|esgotado|sin stock|sem stock|no disponible|indisponible|agotado',
    re.IGNORECASE)
TEXTO_DISPONIBLE = re.compile(
    r'disponível|disponivel|em estoque|disponible|en stock|em stock|stock disponible', re.IGNORECASE)

MAX_INTENTOS_LOGIN = 2
MAX_INTENTOS_DETALLE = 3
MAX_INTENTOS_LISTADO = 3

# Modo rápido para testes (sin esperas reales de red/scroll)
RAPIDO = os.getenv("SOUENERGY_SCRAPER_RAPIDO", "0") == "1"


class LoginError(Exception):
    """Autenticación no confirmada — abortar, nunca scrapear como visitante."""


class BloqueadoError(Exception):
    """403 persistente / CAPTCHA / bloqueo — encerrar y alertar."""


class Limites:
    """Límites operacionais de uma coleta. Alcançarlos marca execução
    incompleta (bloquea promoção), não aprova parcialmente."""

    def __init__(self, max_paginas: int | None = None,
                 max_tiempo_min: int | None = None):
        self.max_paginas = max_paginas or int(os.getenv("LIMITE_PAGINAS", "200"))
        self.max_tiempo = (max_tiempo_min
                           or int(os.getenv("LIMITE_TIEMPO_MIN", "60"))) * 60
        self.inicio = time.time()
        self.paginas = 0

    def excedidos(self) -> bool:
        return (self.paginas >= self.max_paginas
                or (time.time() - self.inicio) > self.max_tiempo)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sanitizar_error(exc: Exception) -> str:
    """Error sanitizado: sin correos, tokens ni datos de cuenta."""
    texto = str(exc)
    texto = re.sub(r'[\w.+-]+@[\w.-]+', '[EMAIL]', texto)
    texto = re.sub(r'(token|senha|password|api[_-]?key)[=:]\s*\S+',
                   r'\1=[REDACTADO]', texto, flags=re.IGNORECASE)
    return texto[:500]


# ─── Utilidades de página ─────────────────────────────────────────────────────

def limpar_obstaculos(page) -> None:
    for sel in ['.btn-popup-welcome', 'button.btn-store-view[data-store-view="1"]',
                '.btn-popup-clear-quote', '.action-close']:
        try:
            page.evaluate(f"document.querySelector('{sel}')?.click()")
        except Exception:
            pass
    if not RAPIDO:
        time.sleep(0.3)


def esperar_e_limpar(page, segundos: float = 2) -> None:
    if RAPIDO:
        limpar_obstaculos(page)
        return
    fim = time.time() + segundos
    while time.time() < fim:
        limpar_obstaculos(page)
        time.sleep(0.5)


def estabilizar_scroll(page, max_iteraciones: int = 8) -> None:
    """Scroll verificando estabilización de la cantidad de cards (carga
    incremental), en lugar de movimientos fijos."""
    anterior = -1
    for _ in range(1 if RAPIDO else max_iteraciones):
        page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        if not RAPIDO:
            time.sleep(0.5)
        try:
            n = len(page.query_selector_all('.product-item'))
        except Exception:
            n = anterior
        if n == anterior:
            break
        anterior = n
    page.evaluate("window.scrollTo(0, 0)")
    if not RAPIDO:
        time.sleep(0.3)


# ─── Autenticación estricta ───────────────────────────────────────────────────

def verificar_autenticado(page) -> bool:
    try:
        return (page.is_visible(".login-container.logged")
                or page.is_visible(".loginIcon span:has-text('Olá')"))
    except Exception:
        return False


def tentar_logar(page) -> None:
    log.info("Tentando login...")
    page.evaluate("document.querySelector('.loginIcon')?.click()")
    esperar_e_limpar(page, 3)
    try:
        page.fill('#email', USUARIO, force=True)
        page.fill('#pass', SENHA, force=True)
    except Exception:
        limpar_obstaculos(page)
        page.fill('#email', USUARIO, force=True)
        page.fill('#pass', SENHA, force=True)
    page.evaluate("document.querySelector('#send2')?.click()")
    try:
        page.wait_for_load_state('networkidle', timeout=30000)
    except Exception:
        pass


def autenticar(page) -> None:
    """Login duro: valida credenciales y confirma marcador autenticado tras
    cada intento (inclusive el último). Máximo 2 intentos por ciclo."""
    if not USUARIO or not SENHA:
        raise LoginError("USUARIO/SENHA ausentes — configurar .env")
    page.goto(URL_HOME, timeout=90000, wait_until='domcontentloaded')
    esperar_e_limpar(page, 5)
    for intento in range(1, MAX_INTENTOS_LOGIN + 1):
        if verificar_autenticado(page):
            log.info("Login confirmado")
            return
        log.info(f"Intento de login {intento}/{MAX_INTENTOS_LOGIN}")
        tentar_logar(page)
        esperar_e_limpar(page, 5)
        if verificar_autenticado(page):
            log.info("Login confirmado")
            return
    raise LoginError(
        "No se pudo confirmar la autenticación tras "
        f"{MAX_INTENTOS_LOGIN} intentos — se aborta la coleta")


def verificar_sesion(page) -> None:
    """Conferir sessão nas transições relevantes; expiração exige
    reautenticación limitada. Si no se confirma, abortar (sin promoção)."""
    if verificar_autenticado(page):
        return
    log.warning("Sesión expirada — reautenticando (1 intento)")
    tentar_logar(page)
    esperar_e_limpar(page, 5)
    if not verificar_autenticado(page):
        raise LoginError("Sesión no confirmada tras expiración")


def navegar_autenticado(page, url: str, timeout: int) -> None:
    """Restringe navegação e rejeita resposta bloqueada ou sessão perdida."""
    from catalogo import url_autorizada
    if not url_autorizada(url):
        raise BloqueadoError("URL fora do catálogo autorizado")
    resposta = page.goto(url, timeout=timeout, wait_until="domcontentloaded")
    if resposta is not None and resposta.status >= 400:
        raise BloqueadoError("Resposta HTTP de erro; coleta interrompida")
    esperar_e_limpar(page, 1)
    if not verificar_autenticado(page):
        raise LoginError("Sessão perdida após navegação; nenhuma promoção permitida")
    if es_bloqueo(page):
        raise BloqueadoError("Bloqueio detectado após navegação")


# ─── Lectura de listados ──────────────────────────────────────────────────────

def es_listado_vacio(page) -> bool:
    """Estado explícito de listagem vazia (no confundir con timeout)."""
    for sel in ['.message.info.empty', '.category-empty', '.no-results',
                '.toolbar .toolbar-amount span:has-text("0")']:
        try:
            if page.query_selector(sel):
                return True
        except Exception:
            pass
    try:
        texto = page.inner_text('body') or ""
        if re.search(r'no hay productos|no products|não há produtos|0 resultados',
                     texto, re.IGNORECASE):
            return True
    except Exception:
        pass
    return False


def es_bloqueo(page) -> bool:
    try:
        status = page.status_code if hasattr(page, 'status_code') else 200
        if status in (403, 429):
            return True
        texto = (page.inner_text('body') or "").lower()
        return 'captcha' in texto or 'access denied' in texto
    except Exception:
        return False


def colectar_cards_de_pagina(page, url_actual: str) -> dict:
    """Lee cards del listado ya cargado. Devuelve productos/subcategorías.

    No usa potência como classificador: precio visible -> producto;
    enlace de categoría -> subcategoría; ambíguo -> producto candidato
    (se verifica en el detalle).
    """
    cards = page.query_selector_all('.product-item')
    productos, subcategorias = [], []
    for card in cards:
        try:
            link = card.query_selector('.product-item-link')
            if not link:
                continue
            nome = link.inner_text().strip()
            href = link.get_attribute('href') or ""
            if not href:
                continue
            tiene_precio = bool(card.query_selector(
                '.price, .price-final_price, .pix-price-container'))
            cls = classificar_candidato(nome, href, tiene_precio)
            if cls == 'subcategoria':
                subcategorias.append(href)
            else:
                productos.append({"nome": nome, "url": href})
        except Exception:
            continue
    return {"productos": productos, "subcategorias": subcategorias}


def colectar_enlaces_categoria(page, url_actual: str) -> list[str]:
    """Enlaces de categoría del menú/navegación de la página (hermanos y
    niveles superiores), dentro del dominio del catálogo."""
    enlaces = []
    selectores = ['.nav a', '.navigation a', '.categories a', '.widget a',
                  '.breadcrumbs a', '.menu a']
    for sel in selectores:
        try:
            for el in page.query_selector_all(sel):
                href = el.get_attribute('href') or ""
                if href and url_autorizada(canonicalizar_url(href, url_actual)) and es_enlace_categoria(href):
                    enlaces.append(href)
        except Exception:
            continue
    return enlaces


def cargar_url_y_colectar_cards(page, url: str) -> dict:
    """Carga un listado y devuelve cards + siguiente página + estado.

    Timeout sin selector = error, no "catálogo vacío". 403/429 persistente
    -> BloqueadoError. Estado 'vacio' solo con marcador explícito.
    """
    for tentativa in range(1, MAX_INTENTOS_LISTADO + 1):
        try:
            navegar_autenticado(page, url, timeout=90000)
            esperar_e_limpar(page, 3)
            estabilizar_scroll(page)
            page.wait_for_selector('.product-item', timeout=12000)
            break
        except (BloqueadoError, LoginError):
            raise
        except Exception as e:
            if es_bloqueo(page):
                raise BloqueadoError(f"Bloqueo (403/CAPTCHA) en {url}")
            enlaces = colectar_enlaces_categoria(page, url)
            if enlaces and page.query_selector(".categories a, .category-view .widget a"):
                return {"productos": [], "subcategorias": enlaces,
                        "siguiente": None, "estado": "ok"}
            if es_listado_vacio(page):
                log.info(f"Listado vacío explícito: {url}")
                return {"productos": [], "subcategorias": [],
                        "siguiente": None, "estado": "vacio"}
            if tentativa >= MAX_INTENTOS_LISTADO:
                log.warning(f"Sin .product-item tras {MAX_INTENTOS_LISTADO} "
                            f"intentos: {url} ({sanitizar_error(e)})")
                return {"productos": [], "subcategorias": [],
                        "siguiente": None, "estado": "error"}
            if not RAPIDO:
                time.sleep(1.5 * tentativa)

    datos = colectar_cards_de_pagina(page, url)
    datos["siguiente"] = obtener_siguiente_pagina(page, url)
    datos["estado"] = "ok"
    return datos


def url_sin_pagina(url: str) -> str:
    """URL base de una paginación (quita el parámetro `p`)."""
    partes = urlsplit(url)
    params = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True)
              if k != 'p']
    return urlunsplit((partes.scheme, partes.netloc, partes.path,
                       urlencode(params), ""))


def obtener_siguiente_pagina(page, url_actual: str) -> str | None:
    """Siguiente página real de la categoría.

    Solo pagina con evidencia real: link "next" en el DOM o toolbar de
    paginación presente. Nunca auto-incrementa `p` solo porque exista el
    parámetro en la URL (evita paginación infinita sin siguiente real).
    """
    for sel in ['.pages-item-next a', 'a.next', 'a[rel="next"]',
                '.next i-page']:
        try:
            el = page.query_selector(sel)
            if el and el.get_attribute('href'):
                return canonicalizar_url(el.get_attribute('href'), url_actual)
        except Exception:
            continue
    # Toolbar de paginación presente -> siguiente página: incrementa `p`
    # (o lo crea con ?p=2 si la URL no lo lleva), preservando el resto.
    try:
        if page.query_selector('.pages, .pager, .toolbar .pages'):
            partes = urlsplit(url_actual)
            params = dict(parse_qsl(partes.query, keep_blank_values=True))
            try:
                params['p'] = str(int(params.get('p', '1')) + 1)
            except ValueError:
                return None
            return urlunsplit((partes.scheme, partes.netloc, partes.path,
                               urlencode(params), ""))
    except Exception:
        pass
    return None


# ─── Extracción de la página de producto ──────────────────────────────────────

def _texto_selector(page, selector: str) -> str | None:
    try:
        el = page.query_selector(selector)
        if el:
            val = el.inner_text().strip()
            return val or None
    except Exception:
        pass
    return None


def extraer_disponibilidad(page) -> str:
    for sel in SELECTORES_DISPONIBILIDAD:
        val = _texto_selector(page, sel)
        if val:
            if TEXTO_INDISPONIBLE.search(val):
                return "indisponible"
            if TEXTO_DISPONIBLE.search(val):
                return "disponible"
    try:
        body = page.inner_text('body') or ""
        if TEXTO_INDISPONIBLE.search(body):
            return "indisponible"
    except Exception:
        pass
    return "desconocida"


def extraer_sku(page) -> str | None:
    for sel in SELECTORES_SKU:
        val = _texto_selector(page, sel)
        if val:
            return val
    try:
        meta = page.query_selector('meta[itemprop="sku"]')
        if meta:
            return meta.get_attribute('content')
    except Exception:
        pass
    return None


def extraer_precio_pix(page) -> tuple[str | None, str | None, str | None]:
    """PIX comprovado: texto raw, selector/origen, motivo si no hay.

    NUNCA rellenar PIX con fallback genérico (.price a secas).
    """
    for sel in SELECTORES_PIX:
        val = _texto_selector(page, sel)
        if val:
            return val, sel, None
    return None, None, "sem_pix"


def extraer_datos_producto(page, prod: dict) -> dict:
    """Extrae todos los campos del producto desde la página de detalle."""
    try:
        ficha = page.query_selector('.product-info-main')
        texto_ficha = ficha.inner_text() if ficha else ""
    except Exception:
        texto_ficha = ""
    try:
        titulo = page.title() or ""
    except Exception:
        titulo = ""

    preco_pix, origen_pix, motivo_pix = extraer_precio_pix(page)
    preco_normalizado = normalizar_precio(preco_pix) if preco_pix else None
    if preco_pix and preco_normalizado is None:
        motivo_pix = "precio_no_normalizable"

    # Fase: precedência ficha técnica > título; conflito -> None
    fase = extrair_fase(texto_ficha)
    if fase is None:
        fase = extrair_fase(titulo)

    # Inversor / módulo / estrutura (campos da ficha). El dos puntos es
    # opcional: "Inversor SOLPLANET" y "INVERSOR: SOLPLANET X1" ambos valen.
    def extrair_campo(*chaves, stop=None):
        stop = stop or ['PAINEL', 'CONECTOR', 'CABO', 'ESTRUTURA', 'KIT',
                        'GARANTIA']
        fim = '|'.join(stop)
        for chave in chaves:
            m = re.search(rf'{chave}\s*:?\s*([\s\S]*?)(?:{fim}|$)', texto_ficha,
                          re.IGNORECASE)
            if m:
                val = m.group(1).strip().split('\n')[0].strip()
                if val:
                    return val
        return None

    inversor = extrair_campo('MICROINVERSOR', 'INVERSOR',
                             stop=['PAINEL', 'CONECTOR', 'CABO', 'ESTRUTURA',
                                   'KIT'])
    modulo = extrair_campo('PAINEL FOTOVOLTAICO', 'PAINEL',
                           stop=['CONECTOR', 'CABO', 'ESTRUTURA', 'KIT',
                                 'GARANTIA'])
    estrutura = extrair_campo('ESTRUTURA',
                              stop=['CONECTOR', 'CABO', 'KIT', 'GARANTIA'])

    # Marca/tipo: del campo si se extrajo; si no, de toda la ficha (fallback)
    marca_inversor = extrair_marca_inversor(inversor) or \
        extrair_marca_inversor(texto_ficha)
    marca_modulo = extrair_marca_modulo(modulo) or \
        extrair_marca_modulo(texto_ficha)
    tipo_inversor = extrair_tipo_inversor(inversor) or \
        extrair_tipo_inversor(texto_ficha)
    potencia_modulo_w = normalizar_potencia_modulo_w(modulo or titulo)
    quantidade = extrair_quantidade_modulos(f"{titulo} {texto_ficha}")

    # Potência total: da ficha/título (origem) e calculada (qty × W / 1000)
    potencia_kwp = normalizar_potencia_kwp(f"{titulo} {texto_ficha}")
    potencia_calculada = calcular_potencia_total(quantidade, potencia_modulo_w)
    motivo_nao_exportavel = None
    if (potencia_kwp is not None and potencia_calculada is not None
            and abs(potencia_kwp - potencia_calculada) > 0.05):
        motivo_nao_exportavel = "divergencia_potencia"

    # tipo_produto: kit si tiene inversor+módulo+estructura
    if inversor and modulo and estrutura:
        tipo_produto = "kit"
    elif inversor or modulo:
        tipo_produto = "equipamento"
    else:
        tipo_produto = "desconhecido"

    disponibilidad = extraer_disponibilidad(page)
    sku = extraer_sku(page)

    origem = {
        "preco_pix": origen_pix,
        "potencia_kwp": "titulo_ficha",
        "fase": "ficha_tecnica" if extrair_fase(texto_ficha) else "titulo",
        "inversor": "ficha_tecnica",
        "modulo": "ficha_tecnica",
    }

    return {
        "nome": prod.get("nome", ""),
        "url": prod["url"],
        "sku": sku,
        "tipo_produto": tipo_produto,
        "marca": marca_inversor,
        "marca_modulo": marca_modulo,
        "potencia_kwp": potencia_kwp,
        "potencia_calculada": potencia_calculada,
        "fase": fase,
        "tipo_inversor": tipo_inversor,
        "inversor": inversor,
        "modulo": modulo,
        "modulo_potencia_w": potencia_modulo_w,
        "quantidade_modulos": quantidade,
        "estrutura": estrutura,
        "preco_pix": preco_pix,
        "preco_normalizado": preco_normalizado,
        "moeda": "BRL",
        "disponibilidade": disponibilidad,
        "motivo_nao_exportavel": motivo_nao_exportavel,
        "origem_campos": origem,
    }


def analizar_producto(page, prod: dict) -> dict:
    """Abre el detalle del producto y devuelve una observación.

    Si la URL resulta ser una categoría (no .product-info-main), devuelve
    status 'es_categoria' con los cards para re-enfileirar.
    """
    for intento in range(1, MAX_INTENTOS_DETALLE + 1):
        try:
            navegar_autenticado(page, prod["url"], timeout=60000)
            esperar_e_limpar(page, 2)
            if page.query_selector('.product-info-main') is None:
                if page.query_selector('.product-item'):
                    cards = colectar_cards_de_pagina(page, prod["url"])
                    return {"status": "es_categoria", "url": prod["url"],
                            **cards}
                return {"status": "erro", "url": prod["url"],
                        "erro_codigo": "sem_detalle",
                        "tentativas": intento}
            datos = extraer_datos_producto(page, prod)
            return {"status": "ok", "url": prod["url"], "datos": datos,
                    "tentativas": intento}
        except (BloqueadoError, LoginError):
            raise
        except Exception as e:
            if intento >= MAX_INTENTOS_DETALLE:
                return {"status": "erro", "url": prod["url"],
                        "erro_codigo": "timeout",
                        "erro": sanitizar_error(e), "tentativas": intento}
            if not RAPIDO:
                time.sleep(2.0 * intento)
    return {"status": "erro", "url": prod["url"], "erro_codigo": "timeout",
            "tentativas": MAX_INTENTOS_DETALLE}


# ─── Recorrido del catálogo (BFS) ─────────────────────────────────────────────

def recorrer_catalogo(page, entrada: dict, limites: Limites,
                      entradas_adicionais=None, on_observacao=None) -> dict:
    """BFS sobre categorías/páginas + visita de todos los detalles únicos."""
    cola_categorias = list(dict.fromkeys(canonicalizar_url(e["url"])
                           for e in [entrada, *(entradas_adicionais or [])]))
    cola_productos: list[str] = []
    # La entrada es una categoría: cuenta en planeadas/visitadas y evita
    # re-enfileirar la raíz si aparece como enlace de subcategoría.
    categorias_vistas = set(cola_categorias)
    paginas_vistas = set()
    productos_vistos: dict[str, dict] = {}
    observaciones = []
    firmas: dict[str, str] = {}
    metricas = {
        "categorias_planeadas": 0, "categorias_visitadas": 0,
        "paginas_visitadas": 0, "productos_descubiertos": 0,
        "detalles_validos": 0, "indisponibles": 0, "fallos": 0,
        "duplicados": 0, "fuera_mapeo": 0, "fila_esgotada": False,
    }

    while (cola_categorias or cola_productos) and not limites.excedidos():
        if cola_categorias:
            url = cola_categorias.pop(0)
            if url in paginas_vistas:
                continue
            paginas_vistas.add(url)
            limites.paginas += 1
            metricas["paginas_visitadas"] += 1
            verificar_sesion(page)
            res = cargar_url_y_colectar_cards(page, url)
            if res["estado"] == "error":
                metricas["fallos"] += 1
                continue
            if res["estado"] == "vacio":
                continue

            # Subcategorías (hermanos y niveles) — enfileirar siempre
            for sub in res["subcategorias"]:
                sub_c = canonicalizar_url(sub, url)
                if url_autorizada(sub_c) and sub_c not in categorias_vistas:
                    categorias_vistas.add(sub_c)
                    cola_categorias.append(sub_c)
            # Enlaces de categoría del menú/navegación
            for sub in colectar_enlaces_categoria(page, url):
                sub_c = canonicalizar_url(sub, url)
                if url_autorizada(sub_c) and sub_c not in categorias_vistas and sub_c != url:
                    categorias_vistas.add(sub_c)
                    cola_categorias.append(sub_c)

            # Productos (dedup global por URL canónica; conserva categorías)
            for prod in res["productos"]:
                p_c = canonicalizar_url(prod["url"], url)
                if not url_autorizada(p_c):
                    continue
                if p_c in productos_vistos:
                    metricas["duplicados"] += 1
                else:
                    productos_vistos[p_c] = {"nome": prod["nome"], "url": p_c,
                                             "categorias": set()}
                    cola_productos.append(p_c)
                productos_vistos[p_c]["categorias"].add(url)

            # Paginación con detección de repetición por firma de cards
            base = url_sin_pagina(url)
            firma = firma_cards(res["productos"])
            if firmas.get(base) == firma:
                pass  # página repetida — no paginar más esta categoría
            else:
                firmas[base] = firma
                siguiente = res.get("siguiente")
                if siguiente and url_autorizada(siguiente) and siguiente not in paginas_vistas:
                    cola_categorias.append(siguiente)
        else:
            # Procesar detalles de productos
            url_prod = cola_productos.pop(0)
            prod = productos_vistos[url_prod]
            obs = analizar_producto(page, prod)
            if obs["status"] == "es_categoria":
                # La URL era una categoría: re-enfileirar y seguir BFS.
                # Deja de contar como producto descubierto.
                productos_vistos.pop(url_prod, None)
                if url_prod not in categorias_vistas:
                    categorias_vistas.add(url_prod)
                    cola_categorias.append(url_prod)
                for sub in obs.get("subcategorias", []):
                    sub_c = canonicalizar_url(sub, url_prod)
                    if url_autorizada(sub_c) and sub_c not in categorias_vistas:
                        categorias_vistas.add(sub_c)
                        cola_categorias.append(sub_c)
                for p in obs.get("productos", []):
                    p_c = canonicalizar_url(p["url"], url_prod)
                    if p_c == url_prod:
                        continue  # auto-referencia: no re-enfileirar
                    if p_c not in productos_vistos:
                        productos_vistos[p_c] = {"nome": p["nome"],
                                                 "url": p_c,
                                                 "categorias": set()}
                        cola_productos.append(p_c)
                    productos_vistos[p_c]["categorias"].add(url_prod)
                continue
            observaciones.append(obs)
            if on_observacao is not None:
                on_observacao(obs)
            if obs["status"] == "ok":
                metricas["detalles_validos"] += 1
            elif obs["status"] == "indisponivel":
                metricas["indisponibles"] += 1
            else:
                metricas["fallos"] += 1

    metricas["categorias_planeadas"] = len(categorias_vistas)
    metricas["categorias_visitadas"] = len(
        categorias_vistas & paginas_vistas)
    metricas["productos_descubiertos"] = len(productos_vistos)
    metricas["fila_esgotada"] = (not cola_categorias and not cola_productos
                                 and not limites.excedidos())

    return {"observaciones": observaciones,
            "inventario": [{"nome": v["nome"], "url": k,
                            "categorias": sorted(v["categorias"])}
                           for k, v in productos_vistos.items()],
            "metricas": metricas}


# ─── Función principal ────────────────────────────────────────────────────────

def scrapear_tudo(entradas: list[dict] | None = None,
                  limites: Limites | None = None,
                  contexto_preco: str = "cliente", on_observacao=None) -> dict:
    """Colecta completa del catálogo. Devuelve inventario, observaciones y
    métricas. No persiste ni publica (responsabilidade de coleta.py)."""
    entradas = entradas if entradas is not None else cargar_entradas()
    validar_entradas(entradas)
    limites = limites or Limites()
    iniciado = _ahora()
    if not USUARIO or not SENHA:
        raise LoginError("USUARIO/SENHA ausentes; configure o ambiente antes da coleta")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()
        try:
            autenticar(page)
            res = recorrer_catalogo(page, entradas[0], limites,
                                    entradas_adicionais=entradas[1:],
                                    on_observacao=on_observacao)
            observaciones = res["observaciones"]
            inventario = res["inventario"]
            metricas = res["metricas"]
            return {
                "versao_coletor": VERSION,
                "iniciado_em": iniciado,
                "contexto_preco": contexto_preco,
                "autenticado": True,
                "observaciones": observaciones,
                "inventario": inventario,
                "metricas": metricas,
            }
        finally:
            browser.close()
