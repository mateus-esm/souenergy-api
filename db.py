# -*- coding: utf-8 -*-
"""Conexiones SQLite, migraciones versionadas, observaciones, promoción atómica,
histórico y consultas.

Plano SE-PRICES-002 §5:
- `sqlite3` da biblioteca padrão, `foreign_keys=ON` em todas as conexões,
  WAL e `busy_timeout` definido.
- Banco fora do repositório (configurable via `SOUENERGY_DB`).
- Datas UTC em ISO 8601. Valores monetários autoritativos em centavos inteiros.
- Observações persistidas incrementalmente; promoção atómica só após validar
  a execução inteira. Reexecutar promoção com o mesmo `scrape_id` não duplica
  histórico (UNIQUE(producto_id, scrape_id) + guard de status).
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MIGRATIONS_DIR = BASE_DIR / "migrations"

ESTADOS_SCRAPE = (
    'executando', 'ok', 'parcial', 'erro', 'login_falhou', 'rejeitado',
    'interrompido',
)


def _ahora() -> str:
    # Microsegundos: dos promoções no mesmo segundo não podem empatar
    # `finalizado_em` (a "última coleta aprovada" deve ser inequívoca).
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def conectar(db_path: str | os.PathLike, modo: str = "rw") -> sqlite3.Connection:
    """Abre conexión SQLite. `modo` = 'rw' (colector) | 'ro' (API).

    WAL e busy_timeout só se aplican en escritura; lectura usa mode=ro para
    que a API nunca bloquee nem escreva.
    """
    db_path = str(db_path)
    if modo == "ro":
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    else:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        busy = int(os.getenv("DB_BUSY_TIMEOUT_MS", "30000"))
        conn.execute(f"PRAGMA busy_timeout={busy}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def migrar(conn: sqlite3.Connection) -> list[str]:
    """Aplica migraciones versionadas de migrations/ (aditivas)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version TEXT PRIMARY KEY, aplicada_em TEXT NOT NULL)"
    )
    aplicadas = {r["version"] for r in conn.execute(
        "SELECT version FROM schema_migrations")}
    aplicadas_ahora = []
    for archivo in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = archivo.stem
        if version in aplicadas:
            continue
        sql = archivo.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, aplicada_em) VALUES (?, ?)",
            (version, _ahora()))
        aplicadas_ahora.append(version)
    conn.commit()
    return aplicadas_ahora


# ─── Scrapes ──────────────────────────────────────────────────────────────────

def crear_scrape(conn: sqlite3.Connection, *, scrape_id: str,
                 versao_coletor: str, contexto_preco: str,
                 autenticado: bool = False) -> None:
    conn.execute(
        "INSERT INTO scrapes (id, iniciado_em, status, versao_coletor,"
        " contexto_preco, autenticado) VALUES (?, ?, 'executando', ?, ?, ?)",
        (scrape_id, _ahora(), versao_coletor, contexto_preco,
         int(bool(autenticado))))
    conn.commit()


def guardar_observacion(conn: sqlite3.Connection, *, scrape_id: str, url: str,
                        status: str, dados: dict | None = None,
                        erro_codigo: str | None = None,
                        tentativas: int = 1) -> None:
    """Persiste una observación incrementalmente (sin transacción abierta)."""
    conn.execute(
        "INSERT OR REPLACE INTO observacoes"
        " (scrape_id, url, capturado_em, status, dados_json, erro_codigo,"
        "  tentativas) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (scrape_id, url, _ahora(), status,
         json.dumps(dados, ensure_ascii=False) if dados else None,
         erro_codigo, tentativas))
    conn.commit()


def marcar_scrape(conn: sqlite3.Connection, *, scrape_id: str, status: str,
                  erro_codigo: str | None = None, **campos) -> None:
    assert status in ESTADOS_SCRAPE, f"Estado inválido: {status}"
    asignaciones = ["status = ?", "finalizado_em = ?"]
    valores = [status, _ahora()]
    for k, v in campos.items():
        if k in ("descobertos", "processados", "falhas", "alteracoes", "autenticado"):
            asignaciones.append(f"{k} = ?")
            valores.append(int(v))
        elif k == "cobertura_json":
            asignaciones.append("cobertura_json = ?")
            valores.append(json.dumps(v, ensure_ascii=False))
    if erro_codigo:
        asignaciones.append("erro_codigo = ?")
        valores.append(erro_codigo)
    valores.append(scrape_id)
    conn.execute(f"UPDATE scrapes SET {', '.join(asignaciones)} WHERE id = ?",
                 valores)
    conn.commit()


def marcar_interrompido(conn: sqlite3.Connection, *, scrape_id: str) -> None:
    """Recuperación: un proceso interrumpido mantiene observaciones de
    diagnóstico y se marca `interrompido` (plano §5)."""
    conn.execute(
        "UPDATE scrapes SET status = 'interrompido', finalizado_em = ?"
        " WHERE id = ? AND status = 'executando'",
        (_ahora(), scrape_id))
    conn.commit()


# ─── Promoción atómica ────────────────────────────────────────────────────────

def _upsert_producto(conn, scrape_id, obs, datos) -> int:
    """Upsert por URL. Devuelve 1 si hubo cambio de precio, 0 si no."""
    url = obs["url"]
    fila = conn.execute("SELECT * FROM productos WHERE url = ?", (url,)).fetchone()
    preco_nuevo = datos.get("preco_normalizado")
    preco_pix = datos.get("preco_pix")
    disponibilidad = datos.get("disponibilidade", "desconhecida")
    ahora = _ahora()

    if fila is None:
        conn.execute(
            "INSERT INTO productos (url, sku, nome, tipo_produto, marca,"
            " marca_modulo, potencia_kwp, fase, tipo_inversor, inversor,"
            " modulo, modulo_potencia_w, quantidade_modulos, estrutura,"
            " preco_pix, preco_normalizado, moeda, disponibilidade,"
            " origem_campos_json, motivo_nao_exportavel, primeira_visita,"
            " fecha_scrapeo, ultimo_scrape_id, ausencias_completas, ativo)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,1)",
            (url, datos.get("sku"), datos.get("nome", ""),
             datos.get("tipo_produto", "desconhecido"), datos.get("marca"),
             datos.get("marca_modulo"), datos.get("potencia_kwp"),
             datos.get("fase"), datos.get("tipo_inversor"),
             datos.get("inversor"), datos.get("modulo"),
             datos.get("modulo_potencia_w"), datos.get("quantidade_modulos"),
             datos.get("estrutura"), preco_pix, preco_nuevo,
             datos.get("moeda", "BRL"), disponibilidad,
             json.dumps(datos.get("origem_campos", {}), ensure_ascii=False),
             datos.get("motivo_nao_exportavel"), ahora, ahora, scrape_id))
        producto_id = conn.execute(
            "SELECT id FROM productos WHERE url = ?", (url,)).fetchone()["id"]
        if preco_nuevo is not None:
            conn.execute(
                "INSERT INTO historico_precios (producto_id, scrape_id, preco,"
                " preco_pix, fecha, motivo) VALUES (?,?,?,?,?,'inicial')",
                (producto_id, scrape_id, preco_nuevo, preco_pix, ahora))
            return 1
        return 0

    producto_id = fila["id"]
    cambio = 0
    preco_anterior = fila["preco_normalizado"]
    estaba_disponible = fila["disponibilidade"] == "disponible"

    if preco_nuevo is not None and preco_nuevo != preco_anterior:
        if estaba_disponible:
            motivo = "alterado"
        else:
            motivo = "restabelecido"
        conn.execute(
            "INSERT INTO historico_precios (producto_id, scrape_id, preco,"
            " preco_pix, fecha, motivo) VALUES (?,?,?,?,?,?)",
            (producto_id, scrape_id, preco_nuevo, preco_pix, ahora, motivo))
        cambio = 1
    elif preco_nuevo is None and preco_anterior is not None:
        # valor -> nulo confirmado (indisponible)
        conn.execute(
            "INSERT INTO historico_precios (producto_id, scrape_id, preco,"
            " preco_pix, fecha, motivo) VALUES (?,?,NULL,NULL,?,'indisponivel')",
            (producto_id, scrape_id, ahora))
        cambio = 1

    conn.execute(
        "UPDATE productos SET sku=?, nome=?, tipo_produto=?, marca=?,"
        " marca_modulo=?, potencia_kwp=?, fase=?, tipo_inversor=?, inversor=?,"
        " modulo=?, modulo_potencia_w=?, quantidade_modulos=?, estrutura=?,"
        " preco_pix=?, preco_normalizado=?, moeda=?, disponibilidade=?,"
        " origem_campos_json=?, motivo_nao_exportavel=?, fecha_scrapeo=?,"
        " ultimo_scrape_id=?, ausencias_completas=0, ativo=1 WHERE id=?",
        (datos.get("sku"), datos.get("nome", ""),
         datos.get("tipo_produto", "desconhecido"), datos.get("marca"),
         datos.get("marca_modulo"), datos.get("potencia_kwp"),
         datos.get("fase"), datos.get("tipo_inversor"), datos.get("inversor"),
         datos.get("modulo"), datos.get("modulo_potencia_w"),
         datos.get("quantidade_modulos"), datos.get("estrutura"), preco_pix,
         preco_nuevo, datos.get("moeda", "BRL"), disponibilidad,
         json.dumps(datos.get("origem_campos", {}), ensure_ascii=False),
         datos.get("motivo_nao_exportavel"), ahora, scrape_id, producto_id))
    return cambio


def promover_scrape(conn: sqlite3.Connection, *, scrape_id: str) -> int:
    """Promueve una coleta validada a catálogo aprobado (transacción atómica).

    Solo actúa si el scrape está en 'executando' (idempotente). Devuelve el
    número de cambios de precio registrados.
    """
    scrape = conn.execute(
        "SELECT * FROM scrapes WHERE id = ?", (scrape_id,)).fetchone()
    if scrape is None or scrape["status"] != "executando":
        return 0

    observaciones = conn.execute(
        "SELECT * FROM observacoes WHERE scrape_id = ?", (scrape_id,)).fetchall()
    alteracoes = 0
    urls_observadas = set()

    try:
        conn.execute("BEGIN")
        for obs in observaciones:
            urls_observadas.add(obs["url"])
            if obs["status"] == "ok" and obs["dados_json"]:
                datos = json.loads(obs["dados_json"])
                alteracoes += _upsert_producto(conn, scrape_id, obs, datos)
            elif obs["status"] == "indisponivel":
                fila = conn.execute(
                    "SELECT * FROM productos WHERE url = ?",
                    (obs["url"],)).fetchone()
                if fila is not None and fila["preco_normalizado"] is not None:
                    conn.execute(
                        "INSERT INTO historico_precios (producto_id, scrape_id,"
                        " preco, preco_pix, fecha, motivo)"
                        " VALUES (?,?,NULL,NULL,?,'indisponivel')",
                        (fila["id"], scrape_id, _ahora()))
                    alteracoes += 1
                if fila is not None:
                    conn.execute(
                        "UPDATE productos SET disponibilidade='indisponible',"
                        " preco_pix=NULL, preco_normalizado=NULL,"
                        " ultimo_scrape_id=?, ausencias_completas=0 WHERE id=?",
                        (scrape_id, fila["id"]))

        # Produtos não observados nesta coleta completa: ausência consecutiva
        for fila in conn.execute(
                "SELECT id, url FROM productos WHERE ultimo_scrape_id != ?",
                (scrape_id,)):
            if fila["url"] in urls_observadas:
                continue
            conn.execute(
                "UPDATE productos SET ausencias_completas ="
                " ausencias_completas + 1, ativo = CASE WHEN"
                " ausencias_completas + 1 >= 2 THEN 0 ELSE ativo END"
                " WHERE id = ?", (fila["id"],))

        conn.execute(
            "UPDATE scrapes SET status='ok', finalizado_em=?, alteracoes=?"
            " WHERE id = ?", (_ahora(), alteracoes, scrape_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return alteracoes


# ─── Consultas (API / exportação) ─────────────────────────────────────────────

def obtener_ultimo_scrape_aprobado(conn: sqlite3.Connection) -> sqlite3.Row | None:
    # `rowid DESC` desempata timestamps idénticos (datos legados con precisión
    # de segundos): el más recientemente insertado es el último aprobado.
    return conn.execute(
        "SELECT * FROM scrapes WHERE status = 'ok'"
        " ORDER BY finalizado_em DESC, rowid DESC LIMIT 1").fetchone()


def obtener_catalogo_aprobado(conn: sqlite3.Connection,
                              scrape_id: str | None = None) -> list[sqlite3.Row]:
    """Produtos da última coleta aprovada (ativos)."""
    if scrape_id is None:
        scrape = obtener_ultimo_scrape_aprobado(conn)
        if scrape is None:
            return []
        scrape_id = scrape["id"]
    return conn.execute(
        "SELECT * FROM productos WHERE ultimo_scrape_id = ? AND ativo = 1"
        " ORDER BY potencia_kwp, url", (scrape_id,)).fetchall()


def coleta_desatualizada(scrape) -> bool:
    """Uma semana mais 24 horas de tolerância para execução e recuperação."""
    if scrape is None:
        return True
    data = datetime.fromisoformat(scrape["finalizado_em"])
    return (datetime.now(timezone.utc) - data).total_seconds() > 192 * 3600


def obtener_precios_por_potencia(conn: sqlite3.Connection,
                                 potencia: float) -> dict:
    """Compatibilidade legada de GET /precos: produtos com potencia >= alvo,
    agrupados por marca, com nomes/campos legados."""
    scrape = obtener_ultimo_scrape_aprobado(conn)
    if scrape is None:
        return {"source": "database", "coleta_aprovada_em": None,
                "desactualizado": True, "solplanet": [], "hoymiles": []}
    filas = conn.execute(
        "SELECT * FROM productos WHERE ultimo_scrape_id = ? AND ativo = 1"
        " AND potencia_kwp > 0 ORDER BY potencia_kwp, url",
        (scrape["id"],)).fetchall()
    solplanet, hoymiles = [], []
    selecionadas = []
    for marca in ("SOLPLANET", "HOYMILES"):
        grupo = [f for f in filas if (f["marca"] or "").upper() == marca]
        if not grupo:
            continue
        acima = [f["potencia_kwp"] for f in grupo if f["potencia_kwp"] >= potencia]
        alvo = min(acima) if acima else max(f["potencia_kwp"] for f in grupo)
        selecionadas.extend(f for f in grupo if abs(f["potencia_kwp"] - alvo) <= 0.15)
    for f in selecionadas:
        item = {
            "nome": f["nome"],
            "potencia": f["potencia_kwp"],
            "preco_pix": f["preco_pix"],
            "preco_normalizado": f["preco_normalizado"],
            "inversor": f["inversor"],
            "modulo": f["modulo"],
            "estrutura": f["estrutura"],
            "fase": f["fase"],
            "url": f["url"],
        }
        if (f["marca"] or "").upper() == "HOYMILES":
            hoymiles.append(item)
        else:
            solplanet.append(item)
    return {
        "potencia_alvo_kwp": potencia,
        "solplanet": solplanet,
        "hoymiles": hoymiles,
        "source": "database",
        "coleta_aprovada_em": scrape["finalizado_em"],
        "desactualizado": coleta_desatualizada(scrape),
    }


def obtener_historico(conn: sqlite3.Connection, url: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT h.* FROM historico_precios h JOIN productos p ON"
        " p.id = h.producto_id WHERE p.url = ? ORDER BY h.fecha",
        (url,)).fetchall()


def obtener_cambios_scrape(conn: sqlite3.Connection,
                           scrape_id: str) -> list[sqlite3.Row]:
    """Cambios de precio de un scrape con su valor anterior (para alertas de
    variación anormal, plano §7)."""
    return conn.execute(
        "SELECT h.producto_id, h.preco, h.preco_pix, h.motivo, p.url, p.nome,"
        " (SELECT preco FROM historico_precios h2"
        "   WHERE h2.producto_id = h.producto_id AND h2.id < h.id"
        "   ORDER BY h2.id DESC LIMIT 1) AS preco_anterior"
        " FROM historico_precios h JOIN productos p ON p.id = h.producto_id"
        " WHERE h.scrape_id = ? AND h.motivo IN ('alterado','restabelecido')",
        (scrape_id,)).fetchall()


# ─── Publicaciones ────────────────────────────────────────────────────────────

def registrar_publicacion(conn: sqlite3.Connection, *, scrape_id: str,
                          hash_conteudo: str) -> int:
    conn.execute(
        "INSERT INTO publicacoes (scrape_id, hash_conteudo, status, criado_em)"
        " VALUES (?, ?, 'pendente', ?) ON CONFLICT(hash_conteudo) DO NOTHING",
        (scrape_id, hash_conteudo, _ahora()))
    conn.commit()
    return conn.execute(
        "SELECT id FROM publicacoes WHERE hash_conteudo = ?",
        (hash_conteudo,)).fetchone()["id"]


def actualizar_publicacion(conn: sqlite3.Connection, *, publicacion_id: int,
                           status: str, commit_sha: str | None = None,
                           deploy_id: str | None = None,
                           erro_codigo: str | None = None) -> None:
    conn.execute(
        "UPDATE publicacoes SET status=?, commit_sha=COALESCE(?, commit_sha),"
        " deploy_id=COALESCE(?, deploy_id), confirmado_em=?, erro_codigo=?"
        " WHERE id=?",
        (status, commit_sha, deploy_id,
         _ahora() if status in ("enviado", "implantado", "erro", "revertido")
         else None, erro_codigo, publicacion_id))
    conn.commit()


def hash_ya_publicado(conn: sqlite3.Connection, hash_conteudo: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM publicacoes WHERE hash_conteudo = ?"
        " AND status IN ('enviado','implantado')",
        (hash_conteudo,)).fetchone() is not None


# ─── Jobs de consulta (compatibilidade API) ───────────────────────────────────

def crear_job_consulta(conn: sqlite3.Connection, *, job_id: str,
                       potencia: float, resultado: dict,
                       scrape_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO jobs_consulta (id, potencia, criado_em, scrape_id, status,"
        " resultado_json) VALUES (?, ?, ?, ?, 'done', ?)",
        (job_id, potencia, _ahora(), scrape_id,
         json.dumps(resultado, ensure_ascii=False)))
    conn.commit()


def obtener_job_consulta(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM jobs_consulta WHERE id = ?", (job_id,)).fetchone()


# ─── Backup / integridad ──────────────────────────────────────────────────────

def backup(conn: sqlite3.Connection, destino: str | os.PathLike) -> None:
    """Backup consistente via API de backup SQLite (WAL seguro)."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(destino) as d:
        conn.backup(d)


def integrity_check(conn: sqlite3.Connection) -> list[str]:
    return [r["integrity_check"] for r in conn.execute("PRAGMA integrity_check")]
