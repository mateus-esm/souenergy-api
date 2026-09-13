# -*- coding: utf-8 -*-
"""Testes de descoberta (plano §9): raiz con productos y subcategorias, dos
hermanos productivos, tres niveles, más de cinco páginas, paginación propia de
subcategoría, producto sin kWp, equipamento avulso, marcas adicionales,
duplicata entre categorías, variante por URL, página repetida, listagem vazia
real y timeout."""
from scraper import Limites, recorrer_catalogo
from conftest import (PaginaFalsa, card, pagina_listado, pagina_producto)

BASE = "https://souenergy.com.br"


def _recorrer(paginas, entrada_url=f"{BASE}/raiz.html", **limites):
    page = PaginaFalsa(paginas)
    lim = Limites(max_paginas=limites.get("max_paginas", 100),
                  max_tiempo_min=limites.get("max_tiempo_min", 60))
    return recorrer_catalogo(page, {"nome": "test", "url": entrada_url,
                                    "marca": "TEST"}, lim)


def test_raiz_con_productos_y_subcategorias():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html",
            cards=[card("Kit A", f"{BASE}/kit-a.html", True),
                   card("Kit B", f"{BASE}/kit-b.html", True)],
            enlaces_categoria=[f"{BASE}/inversores-e-microinversores/solplanet.html"]),
        f"{BASE}/inversores-e-microinversores/solplanet.html": pagina_listado(
            f"{BASE}/inversores-e-microinversores/solplanet.html",
            cards=[card("Kit C", f"{BASE}/kit-c.html", True)]),
        f"{BASE}/kit-a.html": pagina_producto(f"{BASE}/kit-a.html"),
        f"{BASE}/kit-b.html": pagina_producto(f"{BASE}/kit-b.html"),
        f"{BASE}/kit-c.html": pagina_producto(f"{BASE}/kit-c.html"),
    }
    res = _recorrer(paginas)
    assert res["metricas"]["productos_descubiertos"] == 3
    assert res["metricas"]["detalles_validos"] == 3
    assert res["metricas"]["fallos"] == 0
    assert res["metricas"]["fila_esgotada"] is True


def test_dos_hermanos_productivos():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html",
            enlaces_categoria=[f"{BASE}/categorias/cat-a.html",
                               f"{BASE}/categorias/cat-b.html"]),
        f"{BASE}/categorias/cat-a.html": pagina_listado(
            f"{BASE}/categorias/cat-a.html",
            cards=[card("Kit A1", f"{BASE}/a1.html", True)]),
        f"{BASE}/categorias/cat-b.html": pagina_listado(
            f"{BASE}/categorias/cat-b.html",
            cards=[card("Kit B1", f"{BASE}/b1.html", True)]),
        f"{BASE}/a1.html": pagina_producto(f"{BASE}/a1.html"),
        f"{BASE}/b1.html": pagina_producto(f"{BASE}/b1.html"),
    }
    res = _recorrer(paginas)
    assert res["metricas"]["productos_descubiertos"] == 2
    assert res["metricas"]["detalles_validos"] == 2


def test_tres_niveles():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html",
            enlaces_categoria=[f"{BASE}/categorias/nivel1.html"]),
        f"{BASE}/categorias/nivel1.html": pagina_listado(
            f"{BASE}/categorias/nivel1.html",
            enlaces_categoria=[f"{BASE}/categorias/nivel2.html"]),
        f"{BASE}/categorias/nivel2.html": pagina_listado(
            f"{BASE}/categorias/nivel2.html",
            cards=[card("Kit profundo", f"{BASE}/profundo.html", True)]),
        f"{BASE}/profundo.html": pagina_producto(f"{BASE}/profundo.html"),
    }
    res = _recorrer(paginas)
    assert res["metricas"]["productos_descubiertos"] == 1
    assert res["metricas"]["categorias_visitadas"] == 3


def test_mas_de_cinco_paginas():
    paginas = {f"{BASE}/raiz.html": pagina_listado(
        f"{BASE}/raiz.html", cards=[card("Kit 0", f"{BASE}/k0.html", True)],
        siguiente=f"{BASE}/raiz.html?p=2")}
    for i in range(2, 8):
        url = f"{BASE}/raiz.html?p={i}"
        sig = f"{BASE}/raiz.html?p={i + 1}" if i < 7 else None
        paginas[url] = pagina_listado(
            url, cards=[card(f"Kit {i - 1}", f"{BASE}/k{i - 1}.html", True)],
            siguiente=sig)
    for i in range(0, 7):
        paginas[f"{BASE}/k{i}.html"] = pagina_producto(f"{BASE}/k{i}.html")
    res = _recorrer(paginas)
    assert res["metricas"]["paginas_visitadas"] == 7  # sin teto de 5
    assert res["metricas"]["productos_descubiertos"] == 7
    assert res["metricas"]["detalles_validos"] == 7


def test_paginacion_propia_de_subcategoria():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html",
            enlaces_categoria=[f"{BASE}/categorias/sub.html"]),
        f"{BASE}/categorias/sub.html": pagina_listado(
            f"{BASE}/categorias/sub.html",
            cards=[card("S1", f"{BASE}/s1.html", True)],
            siguiente=f"{BASE}/categorias/sub.html?p=2"),
        f"{BASE}/categorias/sub.html?p=2": pagina_listado(
            f"{BASE}/categorias/sub.html?p=2",
            cards=[card("S2", f"{BASE}/s2.html", True)]),
        f"{BASE}/s1.html": pagina_producto(f"{BASE}/s1.html"),
        f"{BASE}/s2.html": pagina_producto(f"{BASE}/s2.html"),
    }
    res = _recorrer(paginas)
    assert res["metricas"]["productos_descubiertos"] == 2


def test_producto_sin_kwp():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html",
            cards=[card("Kit sin potencia", f"{BASE}/sin-kwp.html", True)]),
        f"{BASE}/sin-kwp.html": pagina_producto(
            f"{BASE}/sin-kwp.html", nome="Kit sin potencia",
            titulo="Kit sin potencia", ficha="Inversor SOLPLANET"),
    }
    res = _recorrer(paginas)
    assert res["metricas"]["productos_descubiertos"] == 1
    assert res["metricas"]["detalles_validos"] == 1
    obs = res["observaciones"][0]
    assert obs["status"] == "ok"
    assert obs["datos"]["potencia_kwp"] is None  # no es fallo de cobertura


def test_equipamento_avulso():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html",
            cards=[card("Inversor suelto", f"{BASE}/inversor.html", True)]),
        f"{BASE}/inversor.html": pagina_producto(
            f"{BASE}/inversor.html", nome="Inversor suelto",
            titulo="Inversor suelto", ficha="Inversor SOLPLANET 5 kW"),
    }
    res = _recorrer(paginas)
    obs = res["observaciones"][0]
    assert obs["datos"]["tipo_produto"] == "equipamento"


def test_marcas_adicionales():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html",
            cards=[card("Kit Huawei", f"{BASE}/huawei.html", True)]),
        f"{BASE}/huawei.html": pagina_producto(
            f"{BASE}/huawei.html", nome="Kit Huawei",
            titulo="Kit Huawei", ficha="Inversor HUAWEI"),
    }
    res = _recorrer(paginas)
    assert res["observaciones"][0]["datos"]["marca"] == "HUAWEI"


def test_duplicata_entre_categorias():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html",
            enlaces_categoria=[f"{BASE}/categorias/cat-a.html",
                               f"{BASE}/categorias/cat-b.html"]),
        f"{BASE}/categorias/cat-a.html": pagina_listado(
            f"{BASE}/categorias/cat-a.html",
            cards=[card("Kit X", f"{BASE}/kit-x.html", True)]),
        f"{BASE}/categorias/cat-b.html": pagina_listado(
            f"{BASE}/categorias/cat-b.html",
            cards=[card("Kit X", f"{BASE}/kit-x.html", True)]),
        f"{BASE}/kit-x.html": pagina_producto(f"{BASE}/kit-x.html"),
    }
    res = _recorrer(paginas)
    assert res["metricas"]["productos_descubiertos"] == 1
    assert res["metricas"]["duplicados"] == 1
    # Mantiene ambas asociaciones de categoría
    inventario = res["inventario"][0]
    assert len(inventario["categorias"]) == 2


def test_variante_por_url():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html",
            cards=[card("Kit 7,44 kWp", f"{BASE}/kit-7440.html", True),
                   card("Kit 7,44 kWp", f"{BASE}/kit-7440-b.html", True)]),
        f"{BASE}/kit-7440.html": pagina_producto(f"{BASE}/kit-7440.html"),
        f"{BASE}/kit-7440-b.html": pagina_producto(f"{BASE}/kit-7440-b.html"),
    }
    res = _recorrer(paginas)
    # No deduplicar por nombre: dos URLs distintas = dos variantes
    assert res["metricas"]["productos_descubiertos"] == 2


def test_pagina_repetida_detiene_paginacion():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html", cards=[card("Kit 1", f"{BASE}/k1.html", True)],
            siguiente=f"{BASE}/raiz.html?p=2"),
        f"{BASE}/raiz.html?p=2": pagina_listado(
            f"{BASE}/raiz.html?p=2", cards=[card("Kit 1", f"{BASE}/k1.html", True)],
            siguiente=f"{BASE}/raiz.html?p=3"),
        f"{BASE}/raiz.html?p=3": pagina_listado(
            f"{BASE}/raiz.html?p=3", cards=[card("Kit 1", f"{BASE}/k1.html", True)]),
        f"{BASE}/k1.html": pagina_producto(f"{BASE}/k1.html"),
    }
    res = _recorrer(paginas)
    # La firma repetida corta la paginación: p=3 no se visita
    assert res["metricas"]["paginas_visitadas"] == 2
    assert res["metricas"]["productos_descubiertos"] == 1


def test_listagem_vazia_real():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html",
            enlaces_categoria=[f"{BASE}/categorias/vacia.html"]),
        f"{BASE}/categorias/vacia.html": pagina_listado(
            f"{BASE}/categorias/vacia.html", vacio=True, body="No hay productos"),
    }
    res = _recorrer(paginas)
    assert res["metricas"]["fallos"] == 0
    assert res["metricas"]["fila_esgotada"] is True


def test_timeout_es_error_no_catalogo_vacio():
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(f"{BASE}/raiz.html", timeout=True),
    }
    res = _recorrer(paginas)
    assert res["metricas"]["fallos"] == 1
    assert res["metricas"]["fila_esgotada"] is True  # fallos bloquean promoção


def test_detalle_que_resulta_categoria():
    """Un candidato ambíguo que al abrirlo es categoría se re-enfileira."""
    paginas = {
        f"{BASE}/raiz.html": pagina_listado(
            f"{BASE}/raiz.html",
            cards=[card("Ambiguo", f"{BASE}/ambiguo.html", False)]),
        # "ambiguo.html" es en realidad un listado con un producto
        f"{BASE}/ambiguo.html": pagina_listado(
            f"{BASE}/ambiguo.html",
            cards=[card("Kit real", f"{BASE}/kit-real.html", True)]),
        f"{BASE}/kit-real.html": pagina_producto(f"{BASE}/kit-real.html"),
    }
    res = _recorrer(paginas)
    assert res["metricas"]["productos_descubiertos"] == 1
    assert res["metricas"]["detalles_validos"] == 1
    assert res["observaciones"][0]["url"] == f"{BASE}/kit-real.html"