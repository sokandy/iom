#!/usr/bin/env python3
"""List members stored in the SQLite database."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List members from the SQLite database")
    parser.add_argument(
        "--db",
        dest="db_path",
        help="Path to SQLite database file (overrides SQLITE_PATH)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.db_path:
        os.environ["SQLITE_PATH"] = str(Path(args.db_path).expanduser())

    from db import get_connection  # noqa: E402

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT m_id, m_login_id, m_email, m_status, m_is_admin, m_role FROM member ORDER BY m_id"
        ).fetchall()
        if not rows:
            print("No members found")
            return
        for row in rows:
            print(
                f"{row['m_id']:>3}  {row['m_login_id']:<15}  {row['m_email'] or '-':<25}  "
                f"status={row['m_status']}  admin={bool(row['m_is_admin'])}  role={row['m_role'] or '-'}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
