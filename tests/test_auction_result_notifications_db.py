import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import db as db_module


class AuctionResultNotificationDbTests(unittest.TestCase):
    def setUp(self):
        self._old_sqlite_path = os.environ.get("SQLITE_PATH")
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_iom.db"
        os.environ["SQLITE_PATH"] = str(self.db_path)
        self.db = importlib.reload(db_module)
        self.db.bootstrap_sqlite_db(reset=True)

    def tearDown(self):
        if self._old_sqlite_path is None:
            os.environ.pop("SQLITE_PATH", None)
        else:
            os.environ["SQLITE_PATH"] = self._old_sqlite_path
        importlib.reload(db_module)
        self._tmpdir.cleanup()

    def _member(self, username, email):
        return self.db.create_member(username, "Secret123!", email=email)

    def test_list_closed_auctions_only(self):
        seller_id = self._member("seller1", "seller1@example.com")
        item_closed = self.db.create_item("Closed Item", "d", owner_id=seller_id, starting_price=10.0)
        auction_closed = self.db.create_auction(item_closed, seller_id=seller_id, starting_price=10.0, end_date=datetime.utcnow() - timedelta(days=1))
        self.db.update_auction_housekeeping(auction_closed, "close")

        item_open = self.db.create_item("Open Item", "d", owner_id=seller_id, starting_price=10.0)
        self.db.create_auction(item_open, seller_id=seller_id, starting_price=10.0, end_date=datetime.utcnow() + timedelta(days=1))

        rows = self.db.list_closed_auctions_for_result_notifications(limit=50)
        ids = {r["auction_id"] for r in rows}
        self.assertIn(auction_closed, ids)
        self.assertEqual(len([x for x in rows if x["auction_id"] == auction_closed]), 1)

    def test_highest_bidder_and_notification_dedup(self):
        seller_id = self._member("seller2", "seller2@example.com")
        bidder_a = self._member("biddera", "a@example.com")
        bidder_b = self._member("bidderb", "b@example.com")

        item_id = self.db.create_item("Watch", "desc", owner_id=seller_id, starting_price=10.0)
        auction_id = self.db.create_auction(item_id, seller_id=seller_id, starting_price=10.0, end_date=datetime.utcnow() + timedelta(hours=1))

        self.assertTrue(self.db.place_bid(auction_id, bidder_a, 20.0))
        self.assertTrue(self.db.place_bid(auction_id, bidder_b, 30.0))
        self.db.update_auction_housekeeping(auction_id, "close")

        highest = self.db.get_auction_highest_bidder(auction_id)
        self.assertIsNotNone(highest)
        self.assertEqual(highest["member_id"], bidder_b)
        self.assertEqual(highest["email"], "b@example.com")
        self.assertEqual(highest["amount"], 30.0)

        first = self.db.mark_auction_notification_sent(auction_id, "b@example.com", "winner")
        second = self.db.mark_auction_notification_sent(auction_id, "b@example.com", "winner")
        self.assertTrue(first)
        self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()
