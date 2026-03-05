#!/usr/bin/env python3
"""Backup SQLite database to timestamped file."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import db  # noqa: E402


def backup_database(source: Path, output_dir: Path) -> Path:
    if not source.exists():
        raise FileNotFoundError(f"Source DB does not exist: {source}")

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    destination = output_dir / f"iom_backup_{stamp}.db"
    shutil.copy2(source, destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup SQLite DB")
    parser.add_argument("--source", type=Path, default=Path(db.DB_PATH), help="Path to source SQLite DB")
    parser.add_argument("--output-dir", type=Path, default=Path("tools/backups"), help="Directory to write backup file")
    args = parser.parse_args()

    backup_path = backup_database(args.source, args.output_dir)
    print(f"Backup created: {backup_path}")


if __name__ == "__main__":
    main()
