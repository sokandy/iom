import unittest

from app import app, ts


class SmokeTests(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()

    def test_core_paths_status(self):
        paths = [
            '/',
            '/search?key_word=test',
            '/user_login',
            '/register',
            '/logout',
            '/user_menu',
            '/browse',
            '/sell',
            '/help',
            '/static/app.js',
            '/static/styles.css',
        ]
        for p in paths:
            r = self.client.get(p, follow_redirects=True)
            self.assertEqual(r.status_code, 200, msg=f"{p} did not return 200")

    def test_admin_flow_and_content(self):
        # log in as demo admin
        r = self.client.post('/user_login', data={'username': 'admin', 'password': 'adminpass'}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)

        # admin panel should be accessible and contain the header
        r = self.client.get('/admin', follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Admin Panel', r.data)

        # test resend and unlock POST endpoints respond and return to admin panel
        r = self.client.post('/admin/resend/1', follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Admin Panel', r.data)

        r = self.client.post('/admin/unlock/1', follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Admin Panel', r.data)

    def test_confirm_token_page(self):
        # generate a token and request the confirmation page
        token = ts.dumps({'m_id': 1})
        r = self.client.get(f'/confirm/{token}', follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        # register template includes 'Register' as heading
        self.assertIn(b'Register', r.data)

    def test_admin_demo_fallback_in_db_mode(self):
        # In DB mode, if no admin row exists, demo admin credential should still work for legacy flows.
        r = self.client.post('/user_login', data={'username': 'admin', 'password': 'adminpass'}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        r = self.client.get('/admin', follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Admin Panel', r.data)


if __name__ == '__main__':
    unittest.main()
