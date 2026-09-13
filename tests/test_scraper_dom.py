"""Regressões do DOM Magento observado no VPS em SE-PRICES-003."""
import pytest
from playwright.sync_api import Error, sync_playwright

import scraper


@pytest.fixture
def pagina_dom():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Instale o Chromium com python3 -m playwright install chromium")
            raise
        page = browser.new_page()
        yield page
        browser.close()


def test_grid_apos_minicarrinho_oculto(pagina_dom, monkeypatch):
    """O minicarrinho não pode bloquear a espera nem entrar no inventário."""
    pagina_dom.set_content("""
        <aside hidden><li class="product-item">
          <a class="product-item-link" href="/carrinho.html">Carrinho</a>
        </li></aside>
        <main><ol class="products">
          <li class="product-item" style="display:none">
            <a class="product-item-link" href="/kit-a.html">Kit A</a>
          </li>
          <li class="product-item">
            <a class="product-item-link" href="/kit-b.html">Kit B</a>
          </li>
        </ol></main>
    """)
    monkeypatch.setattr(scraper, "navegar_autenticado", lambda *a, **k: None)
    res = scraper.cargar_url_y_colectar_cards(
        pagina_dom, "https://souenergy.com.br/solplanet.html")
    assert res["estado"] == "ok"
    assert [p["url"] for p in res["productos"]] == ["/kit-a.html", "/kit-b.html"]


@pytest.mark.parametrize("conteudo, vazio", [
    ('<div class="toolbar"><p class="toolbar-amount">Itens 1-12 de <span>20</span></p></div>', False),
    ('<div class="toolbar"><p class="toolbar-amount"><span>10</span> resultados</p></div>', False),
    ('<div class="toolbar"><p class="toolbar-amount">0 resultados</p></div>', True),
    ('<div class="message info empty" hidden>Não há produtos</div>', False),
    ('<div class="message info empty">Não há produtos</div>', True),
    ('<p>Promoção: 20 resultados</p>', False),
    ('<ol class="products"><li class="product-item">Produto</li></ol>'
     '<div class="message info empty">Não há produtos</div>', False),
])
def test_vazio_exige_evidencia_no_catalogo(pagina_dom, conteudo, vazio):
    pagina_dom.set_content(
        '<aside class="no-results">No products</aside><main>'
        + conteudo + '</main>')
    assert scraper.es_listado_vacio(pagina_dom) is vazio


def test_sessao_aguarda_marcador_dinamico(pagina_dom, monkeypatch):
    pagina_dom.set_content('<main>Catálogo</main>')
    monkeypatch.setattr(pagina_dom, "goto", lambda *a, **k: None)
    pagina_dom.evaluate("""() => setTimeout(() => {
        const el = document.createElement('div');
        el.className = 'login-container logged';
        el.textContent = 'Sessão autenticada';
        document.body.appendChild(el);
    }, 300)""")
    scraper.navegar_autenticado(
        pagina_dom, "https://souenergy.com.br/solplanet.html", 1000)
    assert scraper.verificar_autenticado(pagina_dom)
