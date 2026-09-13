# -*- coding: utf-8 -*-
"""Normalização de campos extraídos do site SouEnergy.

Regras (plano SE-PRICES-002 §5):
- Dinheiro: valores autoritativos em centavos inteiros (BRL). Rechaza texto de
  parcelas, múltiplos valores, formato ambíguo e moeda inesperada.
- Potência: distingue kWp (fotovoltaica) de kW (nominal do inversor). Tanto
  "7,44 kWp" como "7.44 kWp" resultam em 7.44.
- Fase: mono/tri/bi com precedência documentada; conflito ou ausência -> None.
- Microinversor é TIPO de equipamento, não fase elétrica.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# ─── Dinheiro ─────────────────────────────────────────────────────────────────

# Rechaza "12x R$ 1.234,56" (parcelas) e "R$ 1.234,56 - R$ 1.567,89" (múltiplos)
_PARCELAS_RE = re.compile(r'\d+\s*[xX×]\s*R?\$', re.IGNORECASE)
_MULTIPLOS_RE = re.compile(r'R?\$.*R?\$', re.IGNORECASE)
_MONEDA_RE = re.compile(r'R?\$|US\$|USD|EUR|€')
_ESPACIOS_RE = re.compile(r'[\s\u00a0\u2009\u202f]+')

# Formatos aceitos (após remover símbolo e espaços):
#   1234            -> reais inteiros (1234,00)
#   1234,56         -> decimal com vírgula
#   1.234,56        -> milhar com ponto + decimal com vírgula
#   12345.67        -> decimal estruturado com ponto (parser separado)
_RE_INTEIRO = re.compile(r'^\d+$')
_RE_DEC_COMA = re.compile(r'^\d+,\d{2}$')
_RE_MILHAR = re.compile(r'^\d{1,3}(\.\d{3})+(,\d{2})$')
_RE_DEC_PUNTO = re.compile(r'^\d+\.\d{2}$')


def normalizar_precio(texto: str | None) -> int | None:
    """Convierte un texto de precio a centavos BRL, o None si no es válido.

    "R$ 12.345,67" -> 1234567  (centavos)
    "12345.67"     -> 1234567  (formato estruturado com ponto decimal)
    "12x R$ 1.234" -> None     (parcelas)
    "R$ 1.234"     -> None     (ambíguo: milhar ou reais sem decimales)
    """
    if not texto:
        return None
    t = str(texto).strip()
    if not t:
        return None
    if _PARCELAS_RE.search(t) or _MULTIPLOS_RE.search(t):
        return None
    # Remove símbolo de moeda e espaços especiais
    t = re.sub(r'R?\$', '', t, flags=re.IGNORECASE)
    t = _ESPACIOS_RE.sub('', t)
    if not t:
        return None
    try:
        # Formato estruturado com ponto decimal: no requiere símbolo
        # (viene del parser interno del sitio, plano §5)
        if _RE_DEC_PUNTO.fullmatch(t):
            return int((Decimal(t) * 100).to_integral_value())
        # Los formatos brasileños requieren moneda explícita. Se comprueba
        # sobre el texto ORIGINAL (antes de quitar el símbolo).
        if not _MONEDA_RE.search(texto):
            return None
        if _RE_INTEIRO.fullmatch(t):
            return int(t) * 100
        if _RE_DEC_COMA.fullmatch(t):
            return int((Decimal(t.replace(',', '.')) * 100).to_integral_value())
        if _RE_MILHAR.fullmatch(t):
            t = t.replace('.', '').replace(',', '.')
            return int((Decimal(t) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None
    return None


def centavos_a_reales(centavos: int | None) -> float | None:
    """Convierte centavos a reais numéricos (solo para exportação)."""
    if centavos is None:
        return None
    return round(centavos / 100, 2)


# ─── Potência ─────────────────────────────────────────────────────────────────

_NUM_RE = r'\d+[\.,]\d+|\d+'
_KWP_RE = re.compile(rf'({_NUM_RE})\s*kWp', re.IGNORECASE)
_KW_RE = re.compile(rf'({_NUM_RE})\s*kW\b', re.IGNORECASE)
_W_RE = re.compile(rf'({_NUM_RE})\s*W\b', re.IGNORECASE)


def _parsear_numero(texto: str) -> float | None:
    """'7,44' -> 7.44 ; '7.44' -> 7.44 ; '744' -> 744.0"""
    try:
        return float(texto.replace(',', '.'))
    except ValueError:
        return None


def normalizar_potencia_kwp(texto: str | None) -> float | None:
    """Potência fotovoltaica total em kWp. '5 kW' (inversor) não basta."""
    if not texto:
        return None
    m = _KWP_RE.search(texto)
    if m:
        return _parsear_numero(m.group(1))
    return None


def normalizar_potencia_kw(texto: str | None) -> float | None:
    """Potência nominal em kW (inversor). Não confundir com kWp."""
    if not texto:
        return None
    m = _KW_RE.search(texto)
    if m:
        return _parsear_numero(m.group(1))
    return None


def normalizar_potencia_modulo_w(texto: str | None) -> int | None:
    """Potência unitária do painel em W (620, 710, ...)."""
    if not texto:
        return None
    m = _W_RE.search(texto)
    if m:
        val = _parsear_numero(m.group(1))
        if val is not None and float(val).is_integer():
            return int(val)
    return None


def calcular_potencia_total(quantidade: int | None, potencia_w: int | None) -> float | None:
    """kWp = quantidade × W / 1000. Solo com composición comprovada."""
    if not quantidade or not potencia_w or quantidade <= 0 or potencia_w <= 0:
        return None
    return round(quantidade * potencia_w / 1000, 3)


# ─── Fase ─────────────────────────────────────────────────────────────────────

_FASE_MONO = re.compile(r'mono\s*fas|monof[aá]s', re.IGNORECASE)
_FASE_TRI = re.compile(r'tri\s*fas|trif[aá]s', re.IGNORECASE)
_FASE_BI = re.compile(r'bi\s*fas|bif[aá]s', re.IGNORECASE)


def extrair_fase(texto: str | None) -> str | None:
    """Extrae fase elétrica. Precedência: ficha técnica > título (ver chamada).

    Conflito (mono e tri presentes) -> None. Microinversor não é fase.
    """
    if not texto:
        return None
    t = str(texto)
    mono = bool(_FASE_MONO.search(t))
    tri = bool(_FASE_TRI.search(t))
    bi = bool(_FASE_BI.search(t))
    if (mono and tri) or (mono and bi) or (tri and bi):
        return None
    if mono:
        return 'mono'
    if tri:
        return 'tri'
    if bi:
        return 'bi'
    return None


# ─── Marcas / tipo de inversor ─────────────────────────────────────────────────

_MARCAS_INVERSORES = [
    'SOLPLANET', 'HOYMILES', 'HUAWEI', 'DEYE', 'GROWATT', 'GOODWE',
    'SUNGROW', 'FRONIUS', 'ENPHASE', 'AP SYSTEMS', 'BENY', 'DAH',
]

# SOLPLANET queda fuera a propósito: es marca de inversor en este catálogo y
# no debe inferirse como marca de módulo (plano §5, separación de campos).
_MARCAS_MODULOS = [
    'JA SOLAR', 'LONGI', 'JINKO', 'TRINA', 'CANADIAN',
    'ASTRONERGY', 'RISEN', 'LUXEN', 'TW SOLAR', 'MEYER BURGER',
]


def extrair_marca_inversor(texto: str | None) -> str | None:
    """Marca do inversor quando comprovada no texto (ficha/título)."""
    if not texto:
        return None
    t = str(texto).upper()
    for marca in _MARCAS_INVERSORES:
        if marca in t:
            return marca
    return None


def extrair_marca_modulo(texto: str | None) -> str | None:
    """Marca do painel (módulo) quando comprovada. Separada da marca do
    inversor: não pressupor módulo SOLPLANET por o inversor ter essa marca."""
    if not texto:
        return None
    t = str(texto).upper()
    for marca in _MARCAS_MODULOS:
        if marca in t:
            return marca
    return None


def extrair_tipo_inversor(texto: str | None) -> str | None:
    """'micro' si es microinversor, 'string' si es inversor de string, 'outro'."""
    if not texto:
        return None
    t = str(texto).lower()
    if 'microinversor' in t or 'micro inversor' in t:
        return 'micro'
    if 'inversor' in t:
        return 'string'
    return None


# ─── Quantidade de módulos ─────────────────────────────────────────────────────

_QTY_RE = re.compile(r'(\d+)\s*[xX×]\s*\d+\s*W', re.IGNORECASE)
_QTY_PALABRA_RE = re.compile(r'(\d+)\s*(?:m[oó]dulos?|paines?|paneles?|pain[eé]is)', re.IGNORECASE)


def extrair_quantidade_modulos(texto: str | None) -> int | None:
    """Quantidade de módulos do kit ("12x 620W", "12 módulos")."""
    if not texto:
        return None
    t = str(texto)
    m = _QTY_RE.search(t)
    if m:
        return int(m.group(1))
    m = _QTY_PALABRA_RE.search(t)
    if m:
        return int(m.group(1))
    return None
