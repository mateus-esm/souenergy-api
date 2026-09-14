# -*- coding: utf-8 -*-
"""Proyección determinística del catálogo aprobado a las cuatro tablas de
solo-prices y validación del contrato `precios.json` v1.

Plano SE-PRICES-002 §6:
- Exportar solo produtos ativos, disponibles, con PIX autenticado válido y
  composición suficiente. El resto queda en DB con motivo y en el reporte.
- Potência total es clave de selección DENTRO de la tabla, no criterio de
  descubrimiento. Entre equivalentes (misma composición) elegir menor PIX,
  desempate por URL. Sin equivalencia comprovada -> bloquear la línea y
  reportar conflicto (no elegir silenciosamente).
- IDs públicos estables (SKU o hash de URL canónica), sin depender del entero
  local de SQLite. Hash comercial cubre selección/composición/precio/versión
  de mapeo; excluye timestamps y dataset_id.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import jsonschema

import db
from normalizacao import centavos_a_reales

BASE_DIR = Path(__file__).resolve().parent
SCHEMA = json.loads((BASE_DIR / "schemas" / "precios.schema.json")
                    .read_text(encoding="utf-8"))
MAPEO = json.loads((BASE_DIR / "config" / "mapeamento_solo_prices.json")
                   .read_text(encoding="utf-8"))

TOLERANCIA_KWP = 0.05


def cargar_mapeo() -> dict:
    return MAPEO


def _id_estable(fila) -> str:
    if fila["sku"]:
        return f"souenergy:{fila['sku']}"
    digest = hashlib.sha256(fila["url"].encode("utf-8")).hexdigest()[:16]
    return f"souenergy:{digest}"


def _tabla_para(fila, mapeo: dict) -> str | None:
    for clave, reglas in mapeo["tabelas"].items():
        if (fila["marca"] == reglas["marca"]
                and fila["tipo_inversor"] == reglas["tipo_inversor"]
                and fila["modulo_potencia_w"] == reglas["modulo_potencia_w"]
                and ("fase" not in reglas or fila["fase"] == reglas["fase"])):
            return clave
    return None


def _fila_json(fila, scrape) -> dict:
    return {
        "produto_id": _id_estable(fila),
        "url": fila["url"],
        "nome": fila["nome"],
        "marca": fila["marca"],
        "marca_modulo": fila["marca_modulo"],
        "fase": fila["fase"],
        "tipo_inversor": fila["tipo_inversor"],
        "potencia_kwp": fila["potencia_kwp"],
        "modulo_potencia_w": fila["modulo_potencia_w"],
        "quantidade_modulos": fila["quantidade_modulos"],
        "inversor": fila["inversor"],
        "modulo": fila["modulo"],
        "estrutura": fila["estrutura"],
        "preco_pix_centavos": fila["preco_normalizado"],
        "preco_pix": centavos_a_reales(fila["preco_normalizado"]),
        "capturado_em": fila["fecha_scrapeo"],
    }


def proyectar_catalogo(conn, mapeo: dict | None = None) -> tuple[dict, dict]:
    """Devuelve (tabelas, reporte). No escribe nada."""
    mapeo = mapeo or cargar_mapeo()
    scrape = db.obtener_ultimo_scrape_aprobado(conn)
    if scrape is None:
        return {k: [] for k in mapeo["tabelas"]}, {
            "elegidos": 0, "no_mapeados": [], "conflictos": [],
            "por_tabla": {k: 0 for k in mapeo["tabelas"]},
        }

    elegibles = []
    no_mapeados = []
    for f in db.obtener_catalogo_aprobado(conn, scrape["id"]):
        if (f["disponibilidade"] != "disponible"
                or f["preco_normalizado"] is None):
            continue  # permanece en DB con motivo; no entra al contrato
        if f["motivo_nao_exportavel"]:
            no_mapeados.append({"url": f["url"], "motivo": f["motivo_nao_exportavel"]})
            continue
        if f["tipo_produto"] != "kit":
            no_mapeados.append({"url": f["url"], "motivo": "no_es_kit"})
            continue
        if (f["potencia_kwp"] is None or f["quantidade_modulos"] is None
                or f["modulo_potencia_w"] is None):
            no_mapeados.append({"url": f["url"],
                                "motivo": "composicion_incompleta"})
            continue
        tabla = _tabla_para(f, mapeo)
        if tabla is None:
            no_mapeados.append({"url": f["url"], "motivo": "fuera_mapeo"})
            continue
        elegibles.append((tabla, f))

    # Agrupar por (tabla, potencia_kwp); composición = identidad comercial
    grupos: dict[tuple[str, float], list] = {}
    for tabla, f in elegibles:
        grupos.setdefault((tabla, f["potencia_kwp"]), []).append(f)

    conflictos = []
    tabelas = {k: [] for k in mapeo["tabelas"]}
    for (tabla, _potencia), grupo in sorted(grupos.items(),
                                            key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        composiciones = {}
        for f in grupo:
            comp = (f["inversor"], f["modulo"], f["estrutura"],
                    f["quantidade_modulos"], f["fase"])
            composiciones.setdefault(comp, []).append(f)
        if len(composiciones) > 1 and mapeo["reglas"].get(
                "bloquear_sin_equivalencia", True):
            conflictos.extend(grupo)
            continue
        elegido = min(grupo, key=lambda f: (f["preco_normalizado"], f["url"]))
        tabelas[tabla].append(_fila_json(elegido, scrape))

    reporte = {
        "elegidos": sum(len(v) for v in tabelas.values()),
        "no_mapeados": no_mapeados,
        "conflictos": [{"url": f["url"], "potencia_kwp": f["potencia_kwp"]}
                       for f in conflictos],
        "por_tabla": {k: len(v) for k, v in tabelas.items()},
    }
    return tabelas, reporte


def hash_comercial(datos: dict, mapeo_version: int | None = None) -> str:
    """Hash del contenido comercial (selección, composición, disponibilidad,
    precio y versión de mapeo, plano §6.3). Excluye timestamps y dataset_id."""
    comercial = {
        "schema_version": datos["schema_version"],
        "tipo_preco": datos["tipo_preco"],
        "mapeo_version": mapeo_version,
        "tabelas": {k: [{campo: valor for campo, valor in fila.items()
                         if campo != "capturado_em"} for fila in filas]
                    for k, filas in datos["tabelas"].items()},
    }
    canon = json.dumps(comercial, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def validar_contrato(datos: dict) -> list[str]:
    """Valida schema + consistencias (reais/centavos, qty×W vs kWp,
    unicidad de produto_id/url). Devuelve lista de errores (vacía = ok)."""
    errores = []
    try:
        jsonschema.validate(datos, SCHEMA, format_checker=jsonschema.FormatChecker())
    except jsonschema.ValidationError as e:
        return [f"schema: {e.message}"]

    datas = [datos["coleta_aprovada_em"], datos["conteudo_alterado_em"]]
    datas.extend(f["capturado_em"] for filas in datos["tabelas"].values() for f in filas)
    for valor in datas:
        try:
            if datetime.fromisoformat(valor.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError()
        except ValueError:
            errores.append("Data inválida ou sem fuso horário")
    vistos_id, vistos_url = set(), set()
    for tabla, filas in datos["tabelas"].items():
        for f in filas:
            if f["produto_id"] in vistos_id:
                errores.append(f"produto_id duplicado: {f['produto_id']}")
            vistos_id.add(f["produto_id"])
            if f["url"] in vistos_url:
                errores.append(f"url duplicada: {f['url']}")
            vistos_url.add(f["url"])
            if abs(f["preco_pix"] - round(f["preco_pix_centavos"] / 100, 2)) > 0.005:
                errores.append(f"reais/centavos inconsistentes: {f['url']}")
            calculada = f["quantidade_modulos"] * f["modulo_potencia_w"] / 1000
            if abs(calculada - f["potencia_kwp"]) > TOLERANCIA_KWP:
                errores.append(
                    f"qty×W vs kWp divergente: {f['url']} "
                    f"({calculada} vs {f['potencia_kwp']})")
    return errores


def construir_precios_json(conn, mapeo: dict | None = None) -> tuple[dict, dict]:
    mapeo = mapeo or cargar_mapeo()
    scrape = db.obtener_ultimo_scrape_aprobado(conn)
    if scrape is None:
        raise ValueError("Sin coleta aprovada — no se puede exportar")
    tabelas, reporte = proyectar_catalogo(conn, mapeo)
    datos = {
        "schema_version": 1,
        "dataset_id": "",
        "fonte": "SouEnergy",
        "moeda": "BRL",
        "coleta_aprovada_em": scrape["finalizado_em"],
        "conteudo_alterado_em": scrape["finalizado_em"],
        "tipo_preco": "pix_kit",
        "tabelas": tabelas,
    }
    datos["dataset_id"] = hash_comercial(
        datos, mapeo_version=mapeo.get("schema_version"))
    return datos, reporte


def exportar_precios(conn, directorio: str | os.PathLike | None = None,
                     mapeo: dict | None = None) -> tuple[Path, str, dict]:
    """Construye, valida y escribe precios.json (atómico). Devuelve
    (ruta, hash_comercial, reporte)."""
    datos, reporte = construir_precios_json(conn, mapeo)
    errores = validar_contrato(datos)
    if errores:
        raise ValueError(f"Contrato inválido: {'; '.join(errores[:5])}")
    directorio = Path(directorio or os.getenv("EXPORT_DIR", "./solo-prices"))
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / "precios.json"
    fd, tmp = tempfile.mkstemp(dir=str(directorio), suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, ruta)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return ruta, datos["dataset_id"], reporte
