#!/usr/bin/env python3
"""Set a single auction to expired (closed) state."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db import set_auction_expired


def main() -> None:
    parser = argparse.ArgumentParser(description="Set one auction as expired")
    parser.add_argument("auction_id", type=int, help="Auction id to mark as expired")
    parser.add_argument(
        "--at",
        type=str,
        default=None,
        help="Reference UTC datetime in ISO format (default: now)",
    )
    args = parser.parse_args()

    ref = datetime.fromisoformat(args.at) if args.at else None
    changed = set_auction_expired(args.auction_id, now=ref)
    if changed:
        print(f"Auction {args.auction_id} marked as expired.")
    else:
        print(f"Auction {args.auction_id} was not changed (not found or not open).")


if __name__ == "__main__":
    main()
