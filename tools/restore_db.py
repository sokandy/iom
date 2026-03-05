#!/usr/bin/env python3
"""Restore SQLite database from a backup file."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import db  # noqa: E402


def restore_database(backup_file: Path, target: Path, force: bool = False) -> Path:
    if not backup_file.exists():
        raise FileNotFoundError(f"Backup file does not exist: {backup_file}")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        raise FileExistsError(f"Target DB already exists: {target} (use --force to overwrite)")

    shutil.copy2(backup_file, target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore SQLite DB from backup")
    parser.add_argument("backup_file", type=Path, help="Backup file path")
    parser.add_argument("--target", type=Path, default=Path(db.DB_PATH), help="Path to target SQLite DB")
    parser.add_argument("--force", action="store_true", help="Overwrite existing target DB")
    args = parser.parse_args()

    restored = restore_database(args.backup_file, args.target, force=args.force)
    print(f"Database restored to: {restored}")


if __name__ == "__main__":
    main()
