#!/usr/bin/env python3
"""Close all expired open auctions."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db import close_expired_auctions


def main() -> None:
    parser = argparse.ArgumentParser(description="Close expired auctions")
    parser.add_argument(
        "--at",
        type=str,
        default=None,
        help="Reference UTC datetime in ISO format (default: now)",
    )
    args = parser.parse_args()

    ref = datetime.fromisoformat(args.at) if args.at else None
    closed = close_expired_auctions(now=ref)
    print(f"Closed {closed} expired auction(s).")


if __name__ == "__main__":
    main()
