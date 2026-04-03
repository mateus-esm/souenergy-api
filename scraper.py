import time
import re
import os
import logging
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("scraper")

USUARIO = os.getenv("USUARIO")
SENHA   = os.getenv("SENHA")

URL_HOME      = "https://souenergy.com.br/"
URL_SOLPLANET = "https://souenergy.com.br/inversores-e-microinversores/solplanet.html"
URL_HOYMILES  = "https://souenergy.com.br/inversores-e-microinversores/hoymiles.html"

TOLERANCIA_KWP = 0.15


# ─── UTILITÁRIOS ─────────────────────────────────────────────────────────────

def limpar_obstaculos(page):
    for sel in ['.btn-popup-welcome', 'button.btn-store-view[data-store-view="1"]',
                '.btn-popup-clear-quote', '.action-close']:
        try: page.evaluate(f"document.querySelector('{sel}')?.click()")
        except: pass
    time.sleep(0.3)

def esperar_e_limpar(page, segundos=2):
    fim = time.time() + segundos
    while time.time() < fim:
        limpar_obstaculos(page)
        time.sleep(0.5)

def scroll_pagina(page):
    for _ in range(4):
        page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        time.sleep(0.4)
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.3)

def extrair_kwp(texto):
    padroes = [
        r'(\d+[\.,]\d+)\s*kWp',
        r'(\d+[\.,]\d+)\s*k[wW]',
        r'(\d+)\s*kWp',
        r'(\d+)\s*k[wW]',
    ]
    for p in padroes:
        m = re.search(p, texto, re.IGNORECASE)
        if m:
            val = m.group(1).replace('.', '').replace(',', '.')
            return float(val)
    return 0.0

def extrair_potencia_card(card):
    try:
        titulo = card.query_selector('.product-item-link').inner_text()
        pot = extrair_kwp(titulo)
        if pot > 0:
            return pot
        desc_el = card.query_selector('.short-description')
        if desc_el:
            return extrair_kwp(desc_el.inner_text())
    except: pass
    return 0.0


# ─── LOGIN ────────────────────────────────────────────────────────────────────

def tentar_logar(page):
    log.info("Tentando login...")
    page.evaluate("document.querySelector('.loginIcon')?.click()")
    esperar_e_limpar(page, 3)
    try:
        page.fill('#email', USUARIO, force=True)
        page.fill('#pass', SENHA, force=True)
    except:
        limpar_obstaculos(page)
        page.fill('#email', USUARIO, force=True)
        page.fill('#pass', SENHA, force=True)
    page.evaluate("document.querySelector('#send2')?.click()")
    try: page.wait_for_load_state('networkidle', timeout=30000)
    except: pass


# ─── COLETA DE CARDS ──────────────────────────────────────────────────────────

def carregar_url_e_coletar_cards(page, url, marca):
    for tentativa in range(3):
        try:
            page.goto(url, timeout=90000, wait_until='domcontentloaded')
            esperar_e_limpar(page, 3)
            scroll_pagina(page)
            page.wait_for_selector('.product-item', timeout=12000)
            break
        except:
            if tentativa == 2:
                log.warning(f"[{marca}] Sem .product-item em: {url}")
                return [], []
            time.sleep(1)

    cards = page.query_selector_all('.product-item')
    log.info(f"[{marca}] {len(cards)} cards encontrados em {url}")

    produtos = []
    subcategorias = []

    for card in cards:
        try:
            link_el = card.query_selector('.product-item-link')
            if not link_el:
                continue
            nome = link_el.inner_text().strip()
            href = link_el.get_attribute('href') or ""
            pot  = extrair_potencia_card(card)

            if pot > 0:
                produtos.append({"nome": nome, "url": href, "potencia": pot})
            else:
                if href:
                    subcategorias.append(href)
        except: continue

    return produtos, subcategorias


def coletar_todos_produtos(page, url_raiz, marca):
    produtos, subcategorias = carregar_url_e_coletar_cards(page, url_raiz, marca)

    if not produtos and subcategorias:
        log.info(f"[{marca}] Descendo para subcategorias ({len(subcategorias)} encontradas)")
        for sub_url in subcategorias:
            sub_prods, _ = carregar_url_e_coletar_cards(page, sub_url, marca)
            produtos.extend(sub_prods)
            if produtos:
                break

    if produtos:
        pagina = 2
        while pagina <= 5:
            url_pag = f"{url_raiz}?p={pagina}"
            prods_pag, _ = carregar_url_e_coletar_cards(page, url_pag, marca)
            if not prods_pag:
                break
            produtos.extend(prods_pag)
            pagina += 1

    vistos = set()
    unicos = []
    for p in produtos:
        if p['url'] not in vistos:
            vistos.add(p['url'])
            unicos.append(p)

    log.info(f"[{marca}] {len(unicos)} kits com potência identificada")
    return unicos


# ─── EXTRAÇÃO DA PÁGINA DO PRODUTO ───────────────────────────────────────────

def analisar_produto(page, nome, potencia):
    try: page.wait_for_selector('.product-info-main', timeout=10000)
    except: return None

    preco = None
    for sel in ['.pix-price-container .price', '.price-final_price .price', '.price']:
        try:
            val = page.locator(sel).first.inner_text().strip()
            if val:
                preco = val
                break
        except: pass

    try: texto = page.locator('.product-info-main').inner_text()
    except: texto = ""

    def extrair_campo(texto, *chaves, stop=None):
        stop = stop or ['PAINEL', 'CONECTOR', 'CABO', 'ESTRUTURA', 'KIT', 'GARANTIA']
        fim = '|'.join(stop)
        for chave in chaves:
            m = re.search(rf'{chave}.*?:\s*([\s\S]*?)(?:{fim}|$)', texto, re.IGNORECASE)
            if m:
                val = m.group(1).strip().split('\n')[0].strip()
                if val:
                    return val
        return None

    return {
        "nome":      nome,
        "potencia":  potencia,
        "preco_pix": preco,
        "inversor":  extrair_campo(texto, 'MICROINVERSOR', 'INVERSOR',
                                   stop=['PAINEL', 'CONECTOR', 'CABO', 'ESTRUTURA', 'KIT']),
        "modulo":    extrair_campo(texto, 'PAINEL FOTOVOLTAICO', 'PAINEL',
                                   stop=['CONECTOR', 'CABO', 'ESTRUTURA', 'KIT', 'GARANTIA']),
        "estrutura": extrair_campo(texto, 'ESTRUTURA',
                                   stop=['CONECTOR', 'CABO', 'KIT', 'GARANTIA']),
    }


# ─── PROCESSAMENTO POR CATEGORIA ─────────────────────────────────────────────

def processar_categoria(page, url, marca, potencia_alvo):
    produtos = coletar_todos_produtos(page, url, marca)

    if not produtos:
        log.warning(f"[{marca}] Nenhum kit encontrado")
        return []

    viaveis = [p for p in produtos if p['potencia'] >= potencia_alvo]
    pot_grupo = min(p['potencia'] for p in viaveis) if viaveis else max(produtos, key=lambda x: x['potencia'])['potencia']

    grupo = [p for p in produtos if abs(p['potencia'] - pot_grupo) <= TOLERANCIA_KWP]
    log.info(f"[{marca}] Potência alvo: {pot_grupo} kWp | {len(grupo)} opção(ões)")

    resultados = []
    for i, kit in enumerate(grupo, 1):
        log.info(f"[{marca}] [{i}/{len(grupo)}] {kit['nome'][:60]}")
        try:
            page.goto(kit['url'], timeout=60000, wait_until='domcontentloaded')
            esperar_e_limpar(page, 2)
            dados = analisar_produto(page, kit['nome'], kit['potencia'])
            if dados:
                resultados.append(dados)
        except Exception as e:
            log.warning(f"[{marca}] Erro ao ler kit: {e}")

    return resultados


# ─── FUNÇÃO PRINCIPAL ─────────────────────────────────────────────────────────

def consultar_precos(potencia_alvo: float) -> dict:
    log.info(f"Iniciando consulta para {potencia_alvo} kWp")

    with sync_playwright() as p:
        # sem slow_mo em headless — poupa ~30s por consulta
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()

        page.goto(URL_HOME, timeout=90000)
        esperar_e_limpar(page, 5)

        for _ in range(2):
            if (page.is_visible(".login-container.logged") or
                    page.is_visible(".loginIcon span:has-text('Olá')")):
                log.info("Login confirmado")
                break
            tentar_logar(page)
            esperar_e_limpar(page, 5)

        res_sol = processar_categoria(page, URL_SOLPLANET, "SOLPLANET", potencia_alvo)
        res_hoy = processar_categoria(page, URL_HOYMILES,  "HOYMILES",  potencia_alvo)

        browser.close()

    log.info("Consulta finalizada")
    return {
        "potencia_alvo_kwp": potencia_alvo,
        "solplanet": res_sol,
        "hoymiles":  res_hoy,
    }
