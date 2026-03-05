import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import db as db_module

os.environ['USE_DB'] = '1'
import app


class SearchFilterTests(unittest.TestCase):
    def setUp(self):
        self._old_sqlite_path = os.environ.get('SQLITE_PATH')
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / 'test_search_filters.db'
        os.environ['SQLITE_PATH'] = str(self.db_path)

        self.db = importlib.reload(db_module)
        self.db.bootstrap_sqlite_db(reset=True)

        app.USE_DB = True
        app.get_auctions = self.db.get_auctions
        app.app.config['TESTING'] = True
        self.client = app.app.test_client()

        self.seller_id = self.db.create_member('seller_search', 'Secret123!', email='seller@example.com')
        self.bidder_id = self.db.create_member('bidder_search', 'Secret123!', email='bidder@example.com')

        self.auction_camera, _ = self.db.create_item_and_auction(
            'Vintage Camera',
            'Classic camera in good condition',
            seller_id=self.seller_id,
            starting_price=200.0,
            end_date=datetime.utcnow() + timedelta(days=1),
            category=1,
        )
        self.auction_laptop, _ = self.db.create_item_and_auction(
            'Gaming Laptop',
            'High performance laptop',
            seller_id=self.seller_id,
            starting_price=850.0,
            end_date=datetime.utcnow() + timedelta(days=1),
            category=2,
        )
        self.auction_book, _ = self.db.create_item_and_auction(
            'Old Story Book',
            'Rare collection book',
            seller_id=self.seller_id,
            starting_price=40.0,
            end_date=datetime.utcnow() + timedelta(days=1),
            category=3,
        )

        self.db.place_bid(self.auction_camera, self.bidder_id, 260.0)
        self.db.place_bid(self.auction_laptop, self.bidder_id, 900.0)

        conn = self.db.get_connection()
        try:
            conn.execute("UPDATE auction SET a_status = 'closed' WHERE a_id = ?", (self.auction_laptop,))
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        if self._old_sqlite_path is None:
            os.environ.pop('SQLITE_PATH', None)
        else:
            os.environ['SQLITE_PATH'] = self._old_sqlite_path
        importlib.reload(db_module)
        self._tmpdir.cleanup()

    def test_db_filters_keyword_status_category_price(self):
        by_keyword = self.db.get_auctions(keyword='vintage', limit=50)
        self.assertEqual(len(by_keyword), 1)
        self.assertEqual(by_keyword[0]['title'], 'Vintage Camera')

        by_category = self.db.get_auctions(category='2', limit=50)
        self.assertEqual(len(by_category), 1)
        self.assertEqual(by_category[0]['title'], 'Gaming Laptop')

        by_status = self.db.get_auctions(status='closed', limit=50)
        self.assertEqual(len(by_status), 1)
        self.assertEqual(by_status[0]['title'], 'Gaming Laptop')

        by_price = self.db.get_auctions(min_price=200.0, max_price=300.0, limit=50)
        titles = {item['title'] for item in by_price}
        self.assertEqual(titles, {'Vintage Camera'})

    def test_search_route_applies_combined_filters(self):
        resp = self.client.get('/search?key_word=book&category=3&status=open&min_price=10&max_price=60')
        self.assertEqual(resp.status_code, 200)

        html = resp.get_data(as_text=True)
        self.assertIn('Old Story Book', html)
        self.assertNotIn('Vintage Camera', html)
        self.assertNotIn('Gaming Laptop', html)


if __name__ == '__main__':
    unittest.main()
