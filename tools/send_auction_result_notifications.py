#!/usr/bin/env python3
"""Send winner/seller auction result emails for closed auctions.

Idempotency is enforced via db.auction_notification_log.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import app, send_auction_result_email
from db import (
    get_auction_highest_bidder,
    list_closed_auctions_for_result_notifications,
    mark_auction_notification_sent,
)


def _auction_url(auction_id: int) -> str:
    try:
        return f"/auction/{int(auction_id)}"
    except Exception:
        return "/auctions"


def process(limit: int = 200) -> dict:
    sent = {
        "winner": 0,
        "seller_winner": 0,
        "seller_no_sale": 0,
        "skipped": 0,
    }

    with app.app_context():
        auctions = list_closed_auctions_for_result_notifications(limit=limit)
        for auc in auctions:
            auction_id = auc.get("auction_id")
            title = auc.get("title") or f"Auction #{auction_id}"
            auction_url = _auction_url(auction_id)

            highest = get_auction_highest_bidder(auction_id)

            if highest and highest.get("email"):
                if mark_auction_notification_sent(auction_id, highest["email"], "winner"):
                    ok = send_auction_result_email(
                        to_email=highest["email"],
                        auction_title=title,
                        auction_url=auction_url,
                        recipient_role="winner",
                        winning_amount=highest.get("amount"),
                    )
                    if ok:
                        sent["winner"] += 1
                    else:
                        sent["skipped"] += 1

            seller_email = auc.get("seller_email")
            if seller_email:
                role = "seller_winner" if highest else "seller_no_sale"
                if mark_auction_notification_sent(auction_id, seller_email, role):
                    ok = send_auction_result_email(
                        to_email=seller_email,
                        auction_title=title,
                        auction_url=auction_url,
                        recipient_role=role,
                        winning_amount=highest.get("amount") if highest else None,
                    )
                    if ok:
                        sent[role] += 1
                    else:
                        sent["skipped"] += 1

    return sent


def main() -> None:
    parser = argparse.ArgumentParser(description="Send auction result notifications")
    parser.add_argument("--limit", type=int, default=200, help="Max closed auctions to process")
    args = parser.parse_args()

    res = process(limit=max(1, args.limit))
    print(
        "Sent notifications - "
        f"winner: {res['winner']}, "
        f"seller_winner: {res['seller_winner']}, "
        f"seller_no_sale: {res['seller_no_sale']}, "
        f"skipped: {res['skipped']}"
    )


if __name__ == "__main__":
    main()
