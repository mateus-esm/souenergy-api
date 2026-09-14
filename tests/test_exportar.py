# -*- coding: utf-8 -*-
"""Testes del contrato precios.json (plano §6/§9): determinismo, cuatro
tablas, identidad estable, rechazo de ambigüedad, consistencia de moneda y
publicación idempotente."""
import json
import subprocess
from pathlib import Path

import db
import exportar_precos
from conftest import crear_scrape_con_observaciones, observacion

URL_MONO = "https://souenergy.com.br/kit-mono-7440.html"
URL_TRI = "https://souenergy.com.br/kit-tri-7440.html"
URL_MICRO620 = "https://souenergy.com.br/kit-micro-620.html"
URL_MICRO710 = "https://souenergy.com.br/kit-micro-710.html"
URL_HUAWEI = "https://souenergy.com.br/kit-huawei.html"
URL_EQUIPO = "https://souenergy.com.br/inversor-suelto.html"


def _catalogo_completo(conn):
    crear_scrape_con_observaciones(conn, [
        observacion(URL_MONO, preco=1234567, preco_pix="R$ 12.345,67",
                    marca="SOLPLANET", fase="mono", modulo_potencia_w=620,
                    potencia_kwp=7.44, quantidade=12),
        observacion(URL_TRI, preco=1300000, preco_pix="R$ 13.000,00",
                    marca="SOLPLANET", fase="tri", modulo_potencia_w=620,
                    potencia_kwp=7.44, quantidade=12),
        observacion(URL_MICRO620, preco=900000, preco_pix="R$ 9.000,00",
                    marca="HOYMILES", tipo_inversor="micro", fase=None,
                    modulo_potencia_w=620, potencia_kwp=7.44, quantidade=12),
        observacion(URL_MICRO710, preco=1050000, preco_pix="R$ 10.500,00",
                    marca="HOYMILES", tipo_inversor="micro", fase=None,
                    modulo_potencia_w=710, potencia_kwp=8.52, quantidade=12),
        observacion(URL_HUAWEI, preco=2000000, marca="HUAWEI",
                    tipo_inversor="string", fase="tri", modulo_potencia_w=620,
                    potencia_kwp=7.44, quantidade=12),
        observacion(URL_EQUIPO, preco=500000, marca="SOLPLANET",
                    tipo_inversor="string", fase="mono",
                    tipo_produto="equipamento"),
    ])


def test_cuatro_tablas_y_contenido(conn):
    """As quatro tabelas legadas continuam presentes e populadas (aditivo:
    o mapeamento ganhou famílias reais + catch-all, não perdeu as legadas)."""
    _catalogo_completo(conn)
    datos, reporte = exportar_precos.construir_precios_json(conn)
    tabelas_legadas = {"mono_solplanet_620", "tri_solplanet_620",
                       "micro_hoymiles_620", "micro_hoymiles_710"}
    assert tabelas_legadas <= set(datos["tabelas"])
    assert len(datos["tabelas"]["mono_solplanet_620"]) == 1
    assert len(datos["tabelas"]["tri_solplanet_620"]) == 1
    assert len(datos["tabelas"]["micro_hoymiles_620"]) == 1
    assert len(datos["tabelas"]["micro_hoymiles_710"]) == 1
    # HUAWEI 620W não bate com nenhuma família real (que exige 415W) nem com
    # as legadas (marca != SOLPLANET/HOYMILES) — cai no catch-all "outros"
    # em vez de ser descartado.
    assert len(datos["tabelas"]["outros"]) == 1
    assert datos["tabelas"]["outros"][0]["marca"] == "HUAWEI"
    # Só o "equipamento" (não é kit) fica de fora do contrato, reportado.
    assert len(reporte["no_mapeados"]) == 1
    assert reporte["no_mapeados"][0]["motivo"] == "no_es_kit"
    assert reporte["elegidos"] == 5


def test_familias_reais_415w_aditivo(conn):
    """O catálogo real (módulos 415W MAXEON) é publicado nas novas famílias
    aditivas, lado a lado com as quatro tabelas legadas (mesmo vazias)."""
    crear_scrape_con_observaciones(conn, [
        observacion("https://souenergy.com.br/kit-solplanet-415.html",
                    preco=1800000, marca="SOLPLANET", fase="tri",
                    modulo_potencia_w=415, potencia_kwp=9.96, quantidade=24),
        observacion("https://souenergy.com.br/kit-hoymiles-micro-415.html",
                    preco=270048, marca="HOYMILES", tipo_inversor="micro",
                    fase=None, modulo_potencia_w=415, potencia_kwp=1.66,
                    quantidade=4),
        observacion("https://souenergy.com.br/kit-huawei-415.html",
                    preco=1200000, marca="HUAWEI", fase="tri",
                    modulo_potencia_w=415, potencia_kwp=5.81, quantidade=14),
        observacion("https://souenergy.com.br/kit-sungrow-415.html",
                    preco=900000, marca="SUNGROW", fase="tri",
                    modulo_potencia_w=415, potencia_kwp=4.15, quantidade=10),
        # Sem marca detectada: representado em "outros", não descartado.
        observacion("https://souenergy.com.br/kit-sem-marca-415.html",
                    preco=850000, marca=None, fase="tri",
                    modulo_potencia_w=415, potencia_kwp=9.96, quantidade=24),
    ])
    datos, reporte = exportar_precos.construir_precios_json(conn)
    assert len(datos["tabelas"]["solplanet_string_415"]) == 1
    assert len(datos["tabelas"]["hoymiles_micro_415"]) == 1
    assert len(datos["tabelas"]["huawei_string_415"]) == 1
    assert len(datos["tabelas"]["sungrow_string_415"]) == 1
    assert len(datos["tabelas"]["outros"]) == 1
    assert datos["tabelas"]["outros"][0]["marca"] is None
    # As quatro tabelas legadas continuam no contrato, mesmo vazias.
    for legada in ("mono_solplanet_620", "tri_solplanet_620",
                  "micro_hoymiles_620", "micro_hoymiles_710"):
        assert datos["tabelas"][legada] == []
    assert reporte["elegidos"] == 5
    assert exportar_precos.validar_contrato(datos) == []


def test_familias_reais_nao_bloqueiam_por_fase_diferente(conn):
    """Duas fases distintas (mono e tri) com a mesma potência não podem ser
    tratadas como 'ambíguas' só porque a família real agrega várias fases
    numa tabela só (sem `fase` em reglas) — cada uma é um produto legítimo."""
    crear_scrape_con_observaciones(conn, [
        observacion("https://souenergy.com.br/kit-tri-9-96.html",
                    preco=1800000, marca="SOLPLANET", fase="tri",
                    modulo_potencia_w=415, potencia_kwp=9.96, quantidade=24,
                    inversor="Solplanet Tri X", modulo="Painel A"),
        observacion("https://souenergy.com.br/kit-mono-9-96.html",
                    preco=1750000, marca="SOLPLANET", fase="mono",
                    modulo_potencia_w=415, potencia_kwp=9.96, quantidade=24,
                    inversor="Solplanet Mono Y", modulo="Painel A"),
    ])
    datos, reporte = exportar_precos.construir_precios_json(conn)
    assert reporte["conflictos"] == []
    assert len(datos["tabelas"]["solplanet_string_415"]) == 2
    fases = {f["fase"] for f in datos["tabelas"]["solplanet_string_415"]}
    assert fases == {"tri", "mono"}


def test_validacion_contrato_ok(conn):
    _catalogo_completo(conn)
    datos, _ = exportar_precos.construir_precios_json(conn)
    assert exportar_precos.validar_contrato(datos) == []


def test_determinismo_y_identidad_estable(conn):
    _catalogo_completo(conn)
    datos1, _ = exportar_precos.construir_precios_json(conn)
    datos2, _ = exportar_precos.construir_precios_json(conn)
    assert datos1 == datos2
    fila = datos1["tabelas"]["mono_solplanet_620"][0]
    assert fila["produto_id"].startswith("souenergy:")
    assert fila["preco_pix_centavos"] == 1234567
    assert fila["preco_pix"] == 12345.67


def test_hash_comercial_ignora_timestamps():
    base = {
        "schema_version": 1, "tipo_preco": "pix_kit",
        "tabelas": {"mono_solplanet_620": [{"url": "https://x/a",
                                            "preco_pix_centavos": 100}]},
    }
    h1 = exportar_precos.hash_comercial({**base, "coleta_aprovada_em": "a"})
    h2 = exportar_precos.hash_comercial({**base, "coleta_aprovada_em": "b"})
    assert h1 == h2
    assert h1 != exportar_precos.hash_comercial(
        {**base, "tabelas": {"mono_solplanet_620": [{"url": "https://x/a",
                                                     "preco_pix_centavos": 101}]}})


def test_ambiguedad_bloquea_linea(conn):
    """Dos productos con la misma potencia, marca, fase, W, inversor y
    modulo, pero distinta estrutura, no se eligen silenciosamente: divergen
    en un campo que não distingue variantes legítimas, então se bloquea la
    línea y se reporta conflicto."""
    crear_scrape_con_observaciones(conn, [
        observacion(URL_MONO, preco=1234567, marca="SOLPLANET", fase="mono",
                    modulo_potencia_w=620, potencia_kwp=7.44, quantidade=12,
                    inversor="Solplanet X1", modulo="Painel A",
                    estrutura="Estrutura A"),
        observacion("https://souenergy.com.br/kit-mono-b.html", preco=1200000,
                    marca="SOLPLANET", fase="mono", modulo_potencia_w=620,
                    potencia_kwp=7.44, quantidade=12, inversor="Solplanet X1",
                    modulo="Painel A", estrutura="Estrutura B"),
    ])
    datos, reporte = exportar_precos.construir_precios_json(conn)
    assert datos["tabelas"]["mono_solplanet_620"] == []
    assert len(reporte["conflictos"]) == 2


def test_variantes_de_inversor_nao_bloqueiam(conn):
    """Duas variantes legítimas que só diferem no modelo do inversor (mesma
    potência/marca/tipo/fase/W/modulo/estrutura/quantidade) não podem ser
    tratadas como ambíguas: cada uma vira sua própria linha no export,
    reproduzindo o caso real HOYMILES HMS-2250DW-4T vs HMS-2000DW-4T."""
    crear_scrape_con_observaciones(conn, [
        observacion(URL_MONO, preco=270048, marca="HOYMILES",
                    tipo_inversor="micro", fase=None, modulo_potencia_w=415,
                    potencia_kwp=1.66, quantidade=4,
                    inversor="HOYMILES 2250W 220V (HMS-2250DW-4T)",
                    modulo="4 x MAXEON 415W", estrutura="MESA SOLO"),
        observacion("https://souenergy.com.br/kit-hoymiles-2000w.html",
                    preco=255054, marca="HOYMILES", tipo_inversor="micro",
                    fase=None, modulo_potencia_w=415, potencia_kwp=1.66,
                    quantidade=4,
                    inversor="HOYMILES 2000W 220V (HMS-2000DW-4T)",
                    modulo="4 x MAXEON 415W", estrutura="MESA SOLO"),
    ])
    datos, reporte = exportar_precos.construir_precios_json(conn)
    assert reporte["conflictos"] == []
    assert len(datos["tabelas"]["hoymiles_micro_415"]) == 2


def test_equivalentes_elige_menor_pix(conn):
    """Misma composición y potencia: elegir menor PIX, desempate por URL."""
    crear_scrape_con_observaciones(conn, [
        observacion(URL_MONO, preco=1234567, marca="SOLPLANET", fase="mono",
                    modulo_potencia_w=620, potencia_kwp=7.44, quantidade=12),
        observacion("https://souenergy.com.br/kit-mono-b.html", preco=1200000,
                    marca="SOLPLANET", fase="mono", modulo_potencia_w=620,
                    potencia_kwp=7.44, quantidade=12),
    ])
    datos, _ = exportar_precos.construir_precios_json(conn)
    filas = datos["tabelas"]["mono_solplanet_620"]
    assert len(filas) == 1
    assert filas[0]["preco_pix_centavos"] == 1200000


def test_exportar_escribe_archivo(conn, tmp_path):
    _catalogo_completo(conn)
    ruta, hash_json, reporte = exportar_precos.exportar_precios(
        conn, directorio=tmp_path / "solo-prices")
    assert ruta.exists()
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos["dataset_id"] == hash_json
    assert exportar_precos.validar_contrato(datos) == []


def test_publicacion_idempotente(conn, tmp_path):
    """Publicar el mismo artefacto dos veces no duplica commit."""
    _catalogo_completo(conn)
    ruta, hash_json, _ = exportar_precos.exportar_precios(
        conn, directorio=tmp_path / "solo-prices")

    # Repo "remoto" bare (como GitHub) con un commit base
    base = tmp_path / "base"
    base.mkdir()
    subprocess.run(["git", "init", "-b", "master", str(base)], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(base), "config", "user.email", "t@t"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(base), "config", "user.name", "test"],
                   check=True, capture_output=True)
    (base / "solo-prices").mkdir()
    (base / "solo-prices" / "precios.json").write_text("{}")
    subprocess.run(["git", "-C", str(base), "add", "."], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(base), "commit", "-m", "base"],
                   check=True, capture_output=True)
    repo = tmp_path / "propostas"
    subprocess.run(["git", "clone", "--bare", str(base), str(repo)],
                   check=True, capture_output=True)

    import publicar_precos
    commit1 = publicar_precos.publicar(conn, ruta, hash_json, repo=repo,
                                       branch="master")
    assert commit1
    # Segunda vez: mismo hash -> ya publicado, sin nuevo commit
    commit2 = publicar_precos.publicar(conn, ruta, hash_json, repo=repo,
                                       branch="master")
    assert commit2 is None
    # El artefacto validado está en el remoto (verificado en copia limpia)
    verif = tmp_path / "verif"
    subprocess.run(["git", "clone", str(repo), str(verif)], check=True,
                   capture_output=True)
    publicado = json.loads((verif / "solo-prices" / "precios.json")
                           .read_text(encoding="utf-8"))
    assert publicado["dataset_id"] == hash_json