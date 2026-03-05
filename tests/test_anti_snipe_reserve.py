import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import db as db_module


class AntiSnipeReserveTests(unittest.TestCase):
    def setUp(self):
        self._old_sqlite_path = os.environ.get('SQLITE_PATH')
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / 'test_anti_snipe.db'
        os.environ['SQLITE_PATH'] = str(self.db_path)
        self.db = importlib.reload(db_module)
        self.db.bootstrap_sqlite_db(reset=True)

    def tearDown(self):
        if self._old_sqlite_path is None:
            os.environ.pop('SQLITE_PATH', None)
        else:
            os.environ['SQLITE_PATH'] = self._old_sqlite_path
        importlib.reload(db_module)
        self._tmpdir.cleanup()

    def test_reserve_price_persisted(self):
        seller_id = self.db.create_member('seller_reserve', 'Secret123!', email='seller@example.com')
        item_id = self.db.create_item('Item', 'Desc', owner_id=seller_id, starting_price=10.0)
        auction_id = self.db.create_auction(
            item_id=item_id,
            seller_id=seller_id,
            starting_price=10.0,
            end_date=datetime.utcnow() + timedelta(days=1),
            reserve_price=120.0,
        )
        auc = self.db.get_auction(auction_id)
        self.assertEqual(float(auc['reserve_price_raw']), 120.0)

    def test_anti_snipe_extends_end_time_and_caps(self):
        seller_id = self.db.create_member('seller_anti', 'Secret123!', email='seller2@example.com')
        bidder_id = self.db.create_member('bidder_anti', 'Secret123!', email='bidder@example.com')
        item_id = self.db.create_item('Anti Snipe Item', 'Desc', owner_id=seller_id, starting_price=10.0)
        auction_id = self.db.create_auction(
            item_id=item_id,
            seller_id=seller_id,
            starting_price=10.0,
            end_date=datetime.utcnow() + timedelta(minutes=1),
            anti_snipe_minutes=5,
            anti_snipe_extend_minutes=2,
            anti_snipe_max_extend=1,
        )

        before = self.db.get_auction(auction_id)
        before_end = before['end_time']

        self.assertTrue(self.db.place_bid(auction_id, bidder_id, 20.0))
        after_first = self.db.get_auction(auction_id)
        self.assertEqual(int(after_first['anti_snipe_extend_count']), 1)
        self.assertGreater(after_first['end_time'], before_end)

        self.assertTrue(self.db.place_bid(auction_id, bidder_id, 30.0))
        after_second = self.db.get_auction(auction_id)
        self.assertEqual(int(after_second['anti_snipe_extend_count']), 1)


if __name__ == '__main__':
    unittest.main()
