#!/usr/bin/env python3
"""Initialize or reset the local SQLite database used by the IOM website."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import db  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the local SQLite database")
    parser.add_argument("--reset", action="store_true", help="Delete the existing database before recreating")
    parser.add_argument("--path", type=Path, default=None, help="Override SQLITE_PATH")
    args = parser.parse_args()

    if args.path:
        os.environ["SQLITE_PATH"] = str(args.path)

    db_path = Path(db.bootstrap_sqlite_db(reset=args.reset))
    print(f"SQLite database ready at: {db_path}")


if __name__ == "__main__":
    main()
