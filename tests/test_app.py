"""Проверки этапа 1. Запуск: python3 -m unittest discover -s tests"""
import hashlib
import hmac
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import cards  # noqa: E402
import server  # noqa: E402
from registry import Registry  # noqa: E402
from telegram_auth import AuthError, check_init_data, sign_init_data  # noqa: E402

TOKEN = '123456:TEST-TOKEN'
OWNER = 777
KEY = 'test-registry-key'


def init_data(user_id=OWNER, token=TOKEN, age=0):
    return sign_init_data({'auth_date': str(int(time.time()) - age),
                           'query_id': 'AAA',
                           'user': json.dumps({'id': user_id, 'first_name': 'А'})}, token)


RECORDS = [
    {'id': 'Q-1', 'type': 'question', 'status': 'open', 'waiting_on': 'alexander', 'owner': 'god',
     'title': 'Вардане: подавать ли заявку?', 'topic': 'Вардане — участки и подачи', 'humanqa': {'q': 'Подавать заявку по Вардане?'},
     'updated_at': '2026-11-03T10:00:00+00:00'},
    {'id': 'Q-2', 'type': 'question', 'status': 'doing', 'waiting_on': 'alexander', 'owner': 'oscar-mtmubnna',
     'title': 'Оплатить счёт?', 'topic': 'Финансы', 'humanqa': {}, 'updated_at': '2026-11-04T10:00:00+00:00'},
    {'id': 'Q-3', 'type': 'question', 'status': 'answered', 'waiting_on': 'alexander', 'owner': 'god', 'title': 'Уже отвечен', 'humanqa': {'a': 'да'}},
    {'id': 'Q-4', 'type': 'question', 'status': 'open', 'waiting_on': 'michael', 'title': 'Не Александру'},
    {'id': 'T-5', 'type': 'task', 'status': 'open', 'waiting_on': 'alexander', 'title': 'Задача'},
    {'id': 'Q-6', 'type': 'question', 'status': 'partial', 'waiting_on': 'alexander', 'title': 'Снятый', 'humanqa': {'withdrawn': 'x'}},
    {'id': 'Q-7', 'type': 'question', 'status': 'paused', 'waiting_on': 'alexander', 'title': 'На паузе'},
]
PROJECTS = [{'name': 'Вардане', 'id': 'a'}, {'name': 'Дубли', 'id': 'b'}, {'name': '_Рынок_и_конкуренты', 'id': 'c'}]


class AuthTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(check_init_data(init_data(), TOKEN)['id'], OWNER)

    def test_wrong_token(self):
        with self.assertRaises(AuthError):
            check_init_data(init_data(token='999:OTHER'), TOKEN)

    def test_tampered(self):
        with self.assertRaises(AuthError):
            check_init_data(init_data().replace('777', '778'), TOKEN)

    def test_expired(self):
        with self.assertRaises(AuthError):
            check_init_data(init_data(age=2 * 86400), TOKEN)

    def test_empty(self):
        with self.assertRaises(AuthError):
            check_init_data('', TOKEN)


class CardsTest(unittest.TestCase):
    def test_filter_and_order(self):
        got = cards.make_cards(RECORDS, PROJECTS)
        self.assertEqual([c['id'] for c in got], ['Q-2', 'Q-1'])

    def test_fields(self):
        c = {x['id']: x for x in cards.make_cards(RECORDS, PROJECTS)}
        self.assertEqual(c['Q-1']['agent_name'], 'Michael')
        self.assertEqual(c['Q-1']['question'], 'Подавать заявку по Вардане?')
        self.assertEqual(c['Q-1']['project'], 'Вардане')
        self.assertEqual(c['Q-2']['agent_name'], 'Oscar')
        self.assertEqual(c['Q-2']['question'], 'Оплатить счёт?')
        self.assertIsNone(c['Q-2']['project'])

    def test_agents(self):
        self.assertEqual(cards.agent_key('kevin-mudo1gxe'), 'kevin')
        self.assertEqual(cards.agent_key('dwight-mtbceykx'), 'dwight')
        self.assertEqual(cards.agent_key(None), 'michael')

    def test_not_projects(self):
        self.assertEqual([p['name'] for p in cards.clean_projects(PROJECTS)], ['Вардане'])


class FakeRegistry(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ok_cookie(self):
        c = SimpleCookie()
        c.load(self.headers.get('Cookie', ''))
        if 'hive_session' not in c:
            return False
        exp, sig = c['hive_session'].value.split('.')
        return int(exp) > time.time() and hmac.compare_digest(sig, hmac.new(KEY.encode(), exp.encode(), hashlib.sha256).hexdigest())

    def do_GET(self):
        if self.path == '/health':
            body = {'ok': True}
        elif not self._ok_cookie():
            self.send_response(401); self.end_headers(); return
        elif self.path.startswith('/api/state'):
            body = {'revision': 42, 'records': RECORDS}
        else:
            body = []
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = ThreadingHTTPServer(('127.0.0.1', 0), FakeRegistry)
        threading.Thread(target=cls.reg.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        pf = Path(cls.tmp.name) / 'projects.json'
        pf.write_text(json.dumps({'updated_at': '2026-11-02T21:30:00+03:00', 'source': 'test', 'projects': PROJECTS}))
        cfg = server.Config({'BOT_TOKEN': TOKEN, 'ALLOWED_USER_ID': str(OWNER), 'PROJECTS_FILE': str(pf),
                             'REGISTRY_URL': 'http://127.0.0.1:%d' % cls.reg.server_port})
        registry = Registry(cfg.registry_url, KEY)
        status = {'registry': registry.health()}
        cls.app = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(cfg, registry, status))
        threading.Thread(target=cls.app.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d' % cls.app.server_port
        cls.status = status

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown(); cls.reg.shutdown(); cls.tmp.cleanup()

    def get(self, path, auth=None):
        req = urllib.request.Request(self.base + path)
        if auth is not None:
            req.add_header('Authorization', 'tma ' + auth)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_registry_health_with_key(self):
        self.assertTrue(self.status['registry'])

    def test_health(self):
        code, body = self.get('/health')
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)['ok'])

    def test_page(self):
        code, body = self.get('/')
        self.assertEqual(code, 200)
        self.assertIn('Ждёт меня'.encode(), body)

    def test_traversal(self):
        code, _ = self.get('/../app/server.py')
        self.assertEqual(code, 404)
        code, _ = self.get('/%2e%2e/app/server.py')
        self.assertEqual(code, 404)

    def test_cards_needs_auth(self):
        self.assertEqual(self.get('/api/cards')[0], 401)
        self.assertEqual(self.get('/api/cards', init_data(user_id=1))[0], 401)

    def test_cards(self):
        code, body = self.get('/api/cards', init_data())
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data['revision'], 42)
        self.assertEqual([c['id'] for c in data['cards']], ['Q-2', 'Q-1'])

    def test_projects(self):
        code, body = self.get('/api/projects', init_data())
        self.assertEqual(code, 200)
        self.assertEqual([p['name'] for p in json.loads(body)['projects']], ['Вардане'])


if __name__ == '__main__':
    unittest.main()
