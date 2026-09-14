# -*- coding: utf-8 -*-
"""Testes de normalização (plano §9): dinheiro, potência, fase, marcas,
módulos. Verificar que fallback genérico não vira PIX."""
from normalizacao import (calcular_potencia_total, centavos_a_reales,
                          extrair_fase, extrair_marca_inversor,
                          extrair_marca_modulo, extrair_quantidade_modulos,
                          extrair_tipo_inversor, normalizar_potencia_kwp,
                          normalizar_potencia_kw, normalizar_potencia_modulo_w,
                          normalizar_precio)


# ─── Dinheiro ─────────────────────────────────────────────────────────────────

def test_precio_milhar_centavos():
    assert normalizar_precio("R$ 12.345,67") == 1234567
    assert centavos_a_reales(1234567) == 12345.67


def test_precio_sin_milhar():
    assert normalizar_precio("R$ 1234,56") == 123456


def test_precio_estructurado_punto():
    assert normalizar_precio("12345.67") == 1234567


def test_precio_entero_reais():
    assert normalizar_precio("R$ 1234") == 123400


def test_precio_espacios_especiales():
    assert normalizar_precio("R$\u00a012.345,67") == 1234567


def test_precio_parcelas_rechazado():
    assert normalizar_precio("12x R$ 1.234,56") is None


def test_precio_multiple_rechazado():
    assert normalizar_precio("R$ 1.234,56 - R$ 1.567,89") is None


def test_precio_ambiguo_rechazado():
    # "R$ 1.234" sin decimales: milhar o reais? -> ambíguo
    assert normalizar_precio("R$ 1.234") is None


def test_precio_moneda_inesperada_rechazada():
    assert normalizar_precio("USD 100") is None
    assert normalizar_precio("100 EUR") is None


def test_precio_vacio_none():
    assert normalizar_precio(None) is None
    assert normalizar_precio("") is None


# ─── Potência ─────────────────────────────────────────────────────────────────

def test_potencia_virgula_y_punto():
    assert normalizar_potencia_kwp("7,44 kWp") == 7.44
    assert normalizar_potencia_kwp("7.44 kWp") == 7.44


def test_kw_no_es_kwp():
    # "5 kW" del inversor no basta para potencia fotovoltaica
    assert normalizar_potencia_kwp("Inversor 5 kW") is None
    assert normalizar_potencia_kw("Inversor 5 kW") == 5.0


def test_potencia_modulo_w():
    assert normalizar_potencia_modulo_w("Painel 620W") == 620
    assert normalizar_potencia_modulo_w("Painel 710 W") == 710
    assert normalizar_potencia_modulo_w("Painel 7,44 kWp") is None


def test_calcular_potencia_total():
    assert calcular_potencia_total(12, 620) == 7.44
    assert calcular_potencia_total(None, 620) is None
    assert calcular_potencia_total(0, 620) is None


# ─── Fase ─────────────────────────────────────────────────────────────────────

def test_fase_mono_tri():
    assert extrair_fase("Inversor monofásico") == "mono"
    assert extrair_fase("Inversor trifásico") == "tri"
    assert extrair_fase("Inversor bifásico") == "bi"


def test_fase_conflito_none():
    assert extrair_fase("monofásico y trifásico") is None


def test_fase_ausente_none():
    assert extrair_fase("Inversor híbrido") is None
    assert extrair_fase(None) is None


def test_microinversor_no_es_fase():
    assert extrair_fase("Microinversor Hoymiles") is None


# ─── Marcas / tipo ────────────────────────────────────────────────────────────

def test_marca_inversor():
    assert extrair_marca_inversor("Inversor SOLPLANET X1") == "SOLPLANET"
    assert extrair_marca_inversor("Microinversor HOYMILES") == "HOYMILES"
    assert extrair_marca_inversor("Inversor genérico") is None


def test_marca_modulo_separada():
    assert extrair_marca_modulo("Painel JA SOLAR 620W") == "JA SOLAR"
    # No pressuponer módulo SOLPLANET por el inversor tener esa marca
    assert extrair_marca_modulo("Inversor SOLPLANET") is None


def test_tipo_inversor():
    assert extrair_tipo_inversor("Microinversor Hoymiles") == "micro"
    assert extrair_tipo_inversor("Inversor SOLPLANET") == "string"
    assert extrair_tipo_inversor("Cable") is None


def test_quantidade_modulos():
    assert extrair_quantidade_modulos("Kit 12x 620W") == 12
    assert extrair_quantidade_modulos("Kit 8 módulos") == 8
    assert extrair_quantidade_modulos("Kit solar") is None


def test_quantidade_modulos_campo_painel_real():
    """Casos reais do site: marca/modelo entre a quantidade e o W."""
    assert extrair_quantidade_modulos(
        "26 x PAINEL MAXEON 415W (SPR-MAX3-415-R)") == 26
    assert extrair_quantidade_modulos(
        "144 x PAINEL MAXEON 415W") == 144
    assert extrair_quantidade_modulos(
        "20 x PAINEL HANERSUN 710W BIFACIAL N-TYPE TOPCON "
        "ALUMÍNIO (HN21N-66HT 30MM)") == 20


def test_quantidade_nao_casa_kit_fixacao_nem_ano():
    """'4 PAINÉIS' do kit de fixação e anos davam quantidades falsas."""
    assert extrair_quantidade_modulos(
        "KIT DE FIXAÇÃO P/ 4 PAINÉIS: 1 x PRISIONEIRO") is None
    assert extrair_quantidade_modulos(
        "Previsto a partir de: 30/09/2026") is None
    assert extrair_quantidade_modulos(
        "GERADOR DE ENERGIA HOYMILES 2,66kWp") is None


def test_quantidade_paineis_exige_qualificador():
    assert extrair_quantidade_modulos("8 painéis fotovoltaicos") == 8
    assert extrair_quantidade_modulos("4 PAINÉIS") is None