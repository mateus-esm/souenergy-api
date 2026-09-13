# -*- coding: utf-8 -*-
"""Testes de catálogo: entradas, URLs canónicas, classificação y cobertura."""
import catalogo


def test_canonicalizar_quita_rastreo_y_fragmento():
    url = ("https://souenergy.com.br/kit.html?utm_source=x&p=2#frag")
    assert catalogo.canonicalizar_url(url) == \
        "https://souenergy.com.br/kit.html?p=2"


def test_canonicalizar_relativa():
    base = "https://souenergy.com.br/categorias/"
    assert catalogo.canonicalizar_url("../kit.html", base) == \
        "https://souenergy.com.br/kit.html"


def test_canonicalizar_preserva_variantes():
    url = "https://souenergy.com.br/kit.html?sku=ABC&gclid=zzz"
    assert catalogo.canonicalizar_url(url) == \
        "https://souenergy.com.br/kit.html?sku=ABC"


def test_es_enlace_categoria():
    assert catalogo.es_enlace_categoria(
        "https://souenergy.com.br/inversores-e-microinversores/solplanet.html")
    assert not catalogo.es_enlace_categoria(
        "https://souenergy.com.br/kit-solar-7440w.html")


def test_classificar_no_usa_potencia():
    # Precio visible -> producto, aunque no tenga kWp en el nombre
    assert catalogo.classificar_candidato("Kit sin kWp",
                                          "https://x/kit.html", True) == "producto"
    # Enlace de categoría sin precio -> subcategoría
    assert catalogo.classificar_candidato(
        "Solplanet", "https://x/inversores-e-microinversores/solplanet.html",
        False) == "subcategoria"
    # Ambíguo -> desconocido (se verifica en el detalle)
    assert catalogo.classificar_candidato("Algo", "https://x/algo.html",
                                          False) == "desconocido"


def test_cargar_entradas_env_override():
    entradas = catalogo.cargar_entradas(
        "SOLPLANET|https://souenergy.com.br/a.html|SOLPLANET;"
        "HUAWEI|https://souenergy.com.br/b.html|HUAWEI")
    assert len(entradas) == 2
    assert entradas[0]["marca"] == "SOLPLANET"


def test_cargar_entradas_invalida():
    import pytest
    with pytest.raises(ValueError):
        catalogo.cargar_entradas("solo-nombre")


def test_firma_cards_detecta_repeticion():
    cards_a = [{"nome": "A", "url": "https://x/a"}, {"nome": "B", "url": "https://x/b"}]
    cards_b = [{"nome": "B", "url": "https://x/b"}, {"nome": "A", "url": "https://x/a"}]
    cards_c = [{"nome": "C", "url": "https://x/c"}]
    assert catalogo.firma_cards(cards_a) == catalogo.firma_cards(cards_b)
    assert catalogo.firma_cards(cards_a) != catalogo.firma_cards(cards_c)


def test_relatorio_cobertura_completa():
    r = catalogo.relatorio_cobertura(
        entradas=["SOLPLANET"], categorias_planeadas=3, categorias_visitadas=3,
        paginas_visitadas=5, productos_descubiertos=10, detalles_validos=10,
        indisponibles=0, fallos=0, duplicados=1, fuera_mapeo=0,
        fila_esgotada=True, autenticado=True)
    assert r["completa"] is True


def test_relatorio_cobertura_incompleta():
    r = catalogo.relatorio_cobertura(
        entradas=["SOLPLANET"], categorias_planeadas=3, categorias_visitadas=2,
        paginas_visitadas=3, productos_descubiertos=10, detalles_validos=9,
        indisponibles=0, fallos=1, duplicados=0, fuera_mapeo=0,
        fila_esgotada=False, autenticado=True)
    assert r["completa"] is False