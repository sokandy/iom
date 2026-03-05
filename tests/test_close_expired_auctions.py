import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import db as db_module


class TestCloseExpiredAuctions(unittest.TestCase):
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

    def _create_auction(self, end_date: datetime) -> int:
        item_id = self.db.create_item(title="T", description="D", starting_price=10.0)
        return self.db.create_auction(item_id=item_id, starting_price=10.0, end_date=end_date)

    def test_close_only_expired_open_auctions(self):
        now = datetime.utcnow()
        expired_open = self._create_auction(now - timedelta(minutes=5))
        future_open = self._create_auction(now + timedelta(hours=2))
        expired_cancelled = self._create_auction(now - timedelta(minutes=10))
        self.db.update_auction_housekeeping(expired_cancelled, "cancel")

        closed_count = self.db.close_expired_auctions(now=now)

        self.assertEqual(closed_count, 1)
        self.assertEqual(self.db.get_auction(expired_open)["status"], "closed")
        self.assertEqual(self.db.get_auction(future_open)["status"], "open")
        self.assertEqual(self.db.get_auction(expired_cancelled)["status"], "cancelled")

    def test_close_expired_is_idempotent(self):
        now = datetime.utcnow()
        expired_open = self._create_auction(now - timedelta(minutes=1))

        first = self.db.close_expired_auctions(now=now)
        second = self.db.close_expired_auctions(now=now)

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(self.db.get_auction(expired_open)["status"], "closed")


if __name__ == "__main__":
    unittest.main()
