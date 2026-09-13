# -*- coding: utf-8 -*-
"""Backup consistente del SQLite via API de backup (WAL seguro), con rotación.

Plano SE-PRICES-002 §5/§10: no copiar solo el archivo principal mientras WAL
está activo; probar restauración e integrity_check. Rotación a 30 backups
diarios. Uso:
    python ops/backup.py --db /var/lib/souenergy/catalogo.sqlite3 \
        --dest /var/lib/souenergy/backups --keep 30
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backup SQLite con rotación")
    parser.add_argument("--db", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--keep", type=int, default=30)
    args = parser.parse_args(argv)

    destino_dir = Path(args.dest)
    destino_dir.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destino = destino_dir / f"catalogo-{fecha}.sqlite3"

    conn = db.conectar(args.db, modo="ro")
    try:
        db.backup(conn, destino)
        backups = sorted(destino_dir.glob("catalogo-*.sqlite3"))
        for viejo in backups[:-args.keep]:
            viejo.unlink()
        print(f"Backup ok: {destino}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())