"""Проверки приложения. Запуск: python3 -m unittest discover -s tests"""
import copy
import datetime as dt
import hashlib
import hmac
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import cards  # noqa: E402
import notifier  # noqa: E402
import server  # noqa: E402
import today  # noqa: E402
from registry import Registry  # noqa: E402
from store import MSK, Store  # noqa: E402
from telegram_auth import AuthError, check_init_data, sign_init_data  # noqa: E402

TOKEN = '123456:TEST-TOKEN'
OWNER = 777
KEY = 'test-registry-key'


def init_data(user_id=OWNER, token=TOKEN, age=0):
    return sign_init_data({'auth_date': str(int(time.time()) - age),
                           'query_id': 'AAA',
                           'user': json.dumps({'id': user_id, 'first_name': 'А'})}, token)


RECORDS = [
    {'id': 'Q-1', 'type': 'question', 'status': 'open', 'waiting_on': 'alexander', 'owner': 'god', 'revision': 3,
     'title': 'Вардане: подавать ли заявку?', 'topic': 'Вардане — участки и подачи', 'humanqa': {'q': 'Подавать заявку по Вардане?'},
     'updated_at': '2026-11-03T10:00:00+00:00'},
    {'id': 'Q-2', 'type': 'question', 'status': 'doing', 'waiting_on': 'alexander', 'owner': 'oscar-mtmubnna', 'revision': 1,
     'title': 'Оплатить счёт?', 'topic': 'Финансы', 'humanqa': {}, 'updated_at': '2026-11-04T10:00:00+00:00'},
    {'id': 'Q-3', 'type': 'question', 'status': 'answered', 'waiting_on': 'alexander', 'owner': 'god', 'title': 'Уже отвечен', 'humanqa': {'a': 'да'}, 'revision': 2},
    {'id': 'Q-4', 'type': 'question', 'status': 'open', 'waiting_on': 'michael', 'title': 'Не Александру', 'revision': 1},
    {'id': 'T-5', 'type': 'task', 'status': 'open', 'waiting_on': 'alexander', 'title': 'Задача улья со сроком', 'revision': 1, 'due': None},
    {'id': 'Q-6', 'type': 'question', 'status': 'partial', 'waiting_on': 'alexander', 'title': 'Снятый', 'humanqa': {'withdrawn': 'x'}, 'revision': 1},
    {'id': 'Q-7', 'type': 'question', 'status': 'paused', 'waiting_on': 'alexander', 'title': 'На паузе', 'revision': 1},
    {'id': 'Q-8', 'type': 'question', 'status': 'open', 'waiting_on': 'alexander', 'owner': 'pam-x', 'revision': 1,
     'title': 'Старая таблица', 'humanqa': {'q': '| К2 | Аренда |'}, 'updated_at': '2026-11-01T10:00:00+00:00'},
]
PROJECTS = [{'name': 'Вардане', 'id': 'a'}, {'name': 'Дубли', 'id': 'b'}, {'name': '_Рынок_и_конкуренты', 'id': 'c'},
            {'name': 'ГК Основа', 'id': 'd'}]


class FakeRegistryState:
    """Повторяет нужные правила ядра реестра: ревизия записи, идемпотентность по operation_id."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.records = {r['id']: copy.deepcopy(r) for r in RECORDS}
        self.history = {}
        self.revision = 100
        self.posts = []
        self.fail_next_409 = 0
        self.fail_after_commit_503 = 0


REG = FakeRegistryState()


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

    def reply(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = urllib.parse.urlsplit(self.path)
        if p.path == '/health':
            return self.reply(200, {'ok': True})
        if not self._ok_cookie():
            return self.reply(401, {'error': 'нет входа'})
        if p.path == '/api/state':
            return self.reply(200, {'revision': REG.revision, 'records': list(REG.records.values())})
        if p.path == '/api/record':
            rid = urllib.parse.parse_qs(p.query)['id'][0]
            return self.reply(200, REG.records[rid])
        return self.reply(200, [])

    def do_POST(self):
        if not self._ok_cookie():
            return self.reply(401, {'error': 'Нужен вход'})
        if self.headers.get('Origin') != 'https://qa.afhive.tech:8443' or self.headers.get('Content-Type') != 'application/json':
            return self.reply(403, {'error': 'Запрос должен исходить со страницы реестра'})
        req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        REG.posts.append(req)
        op = req['operation_id']
        h = json.dumps(req, sort_keys=True)
        if op in REG.history:
            if REG.history[op]['hash'] != h:
                return self.reply(409, {'error': 'operation_id уже использован для другого запроса'})
            return self.reply(200, {'ok': True, 'idempotent': True, 'revision': REG.revision})
        r = REG.records[req['record_id']]
        if REG.fail_next_409:
            REG.fail_next_409 -= 1
            r['revision'] += 1  # кто-то изменил запись
        if req.get('expected_revision') != r['revision']:
            return self.reply(409, {'error': 'Запись уже изменена.'})
        assert req['source']['path'] == 'Личный сайт — ввод Александра'
        assert req['source_date']
        r.setdefault('humanqa', {})
        if req['action'] == 'answer':
            assert req['author'] == 'Александр' and req['coverage'] == 'full'
            r['humanqa']['a'] = req['text']
            r['status'] = 'answered'
        elif req['action'] == 'withdraw_question':
            assert req['reason']
            r['humanqa']['withdrawn'] = req['reason']
            r['status'] = 'archived'
        r['revision'] += 1
        REG.revision += 1
        REG.history[op] = {'hash': h}
        if REG.fail_after_commit_503:
            REG.fail_after_commit_503 -= 1
            return self.reply(503, {'error': 'Запись не подтверждена'})
        return self.reply(200, {'record_id': r['id'], 'record_revision': r['revision'], 'answer_accepted': True,
                                'conflict': False, 'readback_revision': REG.revision})


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
        self.assertEqual([c['id'] for c in got], ['Q-2', 'Q-1', 'Q-8'])

    def test_fields(self):
        c = {x['id']: x for x in cards.make_cards(RECORDS, PROJECTS)}
        self.assertEqual(c['Q-1']['agent_name'], 'Michael')
        self.assertEqual(c['Q-1']['question'], 'Подавать заявку по Вардане?')
        self.assertEqual(c['Q-1']['project'], 'Вардане')
        self.assertEqual(c['Q-2']['agent_name'], 'Oscar')
        self.assertIsNone(c['Q-2']['project'])
        self.assertEqual(c['Q-8']['question'], 'Старая таблица')
        self.assertEqual(c['Q-8']['agent_name'], 'Pam')

    def test_agents(self):
        self.assertEqual(cards.agent_key('kevin-mudo1gxe'), 'kevin')
        self.assertEqual(cards.agent_key('dwight-mtbceykx'), 'dwight')
        self.assertEqual(cards.agent_key(None), 'michael')

    def test_not_projects(self):
        self.assertEqual([p['name'] for p in cards.clean_projects(PROJECTS)], ['Вардане', 'ГК Основа'])


class TodayTest(unittest.TestCase):
    def test_window_and_order(self):
        at = dt.datetime(2026, 11, 4, 12, 0, tzinfo=MSK)  # среда
        reminders = [
            {'id': 'R-1', 'text': 'В окне', 'at': '2026-11-06T10:00:00+03:00', 'done': 0},
            {'id': 'R-2', 'text': 'Следующая среда', 'at': '2026-11-11T09:00:00+03:00', 'done': 0},
            {'id': 'R-3', 'text': 'Слишком поздно', 'at': '2026-11-12T09:00:00+03:00', 'done': 0},
            {'id': 'R-4', 'text': 'Просрочено', 'at': '2026-11-03T09:00:00+03:00', 'done': 0},
            {'id': 'R-5', 'text': 'Сделано', 'at': '2026-11-05T09:00:00+03:00', 'done': 1},
        ]
        tasks = [{'id': 'A-1', 'title': 'Задача', 'due': '2026-11-04', 'done': 0},
                 {'id': 'A-2', 'title': 'Без срока', 'due': None, 'done': 0}]
        records = [{'id': 'T-9', 'waiting_on': 'alexander', 'status': 'todo', 'title': 'Улей', 'due': '2026-11-05'}]
        out = today.build(at, reminders, tasks, records)
        self.assertEqual((out['from'], out['to']), ('2026-11-04', '2026-11-11'))
        self.assertEqual([i['id'] for i in out['items']], ['R-4', 'A-1', 'T-9', 'R-1', 'R-2'])


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = ThreadingHTTPServer(('127.0.0.1', 0), FakeRegistry)
        threading.Thread(target=cls.reg.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        pf = Path(cls.tmp.name) / 'projects.json'
        pf.write_text(json.dumps({'updated_at': '2026-11-02T21:30:00+03:00', 'source': 'test', 'projects': PROJECTS}))
        cls.cfg = server.Config({'BOT_TOKEN': TOKEN, 'ALLOWED_USER_ID': str(OWNER), 'PROJECTS_FILE': str(pf),
                                 'DB_FILE': str(Path(cls.tmp.name) / 'app.sqlite3'),
                                 'REGISTRY_URL': 'http://127.0.0.1:%d' % cls.reg.server_port})
        cls.registry = Registry(cls.cfg.registry_url, KEY, cache_seconds=0)
        cls.store = Store(cls.cfg.db_file)
        cls.status = {'registry': cls.registry.health()}
        cls.app = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(cls.cfg, cls.registry, cls.status, cls.store))
        threading.Thread(target=cls.app.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d' % cls.app.server_port

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown(); cls.reg.shutdown(); cls.tmp.cleanup()

    def setUp(self):
        REG.reset()
        for t in ('cards', 'ops', 'reminders', 'tasks'):
            self.store._exec('DELETE FROM ' + t)

    def call(self, path, body=None, auth=True):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data)
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        if auth:
            req.add_header('Authorization', 'tma ' + (auth if isinstance(auth, str) else init_data()))
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get('Content-Type', '').startswith('application/json') else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'{}')

    def ids(self):
        return [c['id'] for c in self.call('/api/cards')[1]['cards']]


class ServerTest(Base):
    def test_registry_health_with_key(self):
        self.assertTrue(self.status['registry'])

    def test_health(self):
        code, body = self.call('/health', auth=False)
        self.assertEqual(code, 200)
        self.assertTrue(body['ok'])

    def test_page(self):
        code, body = self.call('/', auth=False)
        self.assertEqual(code, 200)
        self.assertIn('Ждёт меня'.encode(), body)

    def test_traversal(self):
        self.assertEqual(self.call('/../app/server.py', auth=False)[0], 404)
        self.assertEqual(self.call('/%2e%2e/app/server.py', auth=False)[0], 404)

    def test_needs_auth(self):
        self.assertEqual(self.call('/api/cards', auth=False)[0], 401)
        self.assertEqual(self.call('/api/cards', auth=init_data(user_id=1))[0], 401)
        self.assertEqual(self.call('/api/cards/Q-1/yes', {}, auth=init_data(user_id=1))[0], 401)
        self.assertEqual(REG.posts, [])

    def test_cards(self):
        self.assertEqual(self.ids(), ['Q-2', 'Q-1', 'Q-8'])


class GesturesTest(Base):
    def test_yes_with_text(self):
        code, body = self.call('/api/cards/Q-1/yes', {'text': 'подавай до пятницы'})
        self.assertEqual((code, body['status']), (200, 'saved'))
        self.assertEqual(REG.records['Q-1']['humanqa']['a'], 'Да. подавай до пятницы')
        self.assertEqual(REG.posts[0]['expected_revision'], 3)
        self.assertNotIn('Q-1', self.ids())
        self.assertEqual(self.store.ops()[0]['status'], 'saved')

    def test_yes_plain(self):
        self.call('/api/cards/Q-2/yes', {})
        self.assertEqual(REG.records['Q-2']['humanqa']['a'], 'Да')

    def test_answer_text(self):
        code, _ = self.call('/api/cards/Q-1/answer', {'text': 'Только после суда'})
        self.assertEqual(code, 200)
        self.assertEqual(REG.records['Q-1']['humanqa']['a'], 'Только после суда')
        self.assertEqual(self.call('/api/cards/Q-2/answer', {'text': '  '})[0], 400)

    def test_remove(self):
        self.call('/api/cards/Q-1/remove', {})
        self.assertEqual(REG.records['Q-1']['status'], 'archived')
        self.assertEqual(REG.records['Q-1']['humanqa']['withdrawn'], 'Снято Александром: больше не спрашивать')
        self.assertNotIn('Q-1', self.ids())

    def test_no_is_local_snooze_7_days(self):
        code, body = self.call('/api/cards/Q-1/no', {'draft': 'черновик'})
        self.assertEqual(code, 200)
        self.assertEqual(REG.posts, [])
        self.assertNotIn('Q-1', self.ids())
        row = self.store.card('Q-1')
        self.assertEqual(row['draft'], 'черновик')
        until = dt.datetime.fromisoformat(row['until'])
        days = (until.astimezone(MSK).date() - dt.datetime.now(MSK).date()).days
        self.assertEqual(days, 7)
        self.assertFalse(self.store.hidden(row, until + dt.timedelta(seconds=1)))

    def test_snooze(self):
        self.assertEqual(self.call('/api/cards/Q-1/snooze', {'days': 3})[0], 200)
        self.assertNotIn('Q-1', self.ids())
        future = (dt.datetime.now(MSK).date() + dt.timedelta(days=10)).isoformat()
        self.assertEqual(self.call('/api/cards/Q-2/snooze', {'date': future})[0], 200)
        today_ = dt.datetime.now(MSK).date().isoformat()
        self.assertEqual(self.call('/api/cards/Q-8/snooze', {'date': today_})[0], 400)
        self.assertEqual(self.call('/api/cards/Q-8/snooze', {'days': 0})[0], 400)
        self.assertEqual(REG.posts, [])

    def test_skip_returns_after_3_days(self):
        self.call('/api/cards/Q-1/skip', {})
        row = self.store.card('Q-1')
        until = dt.datetime.fromisoformat(row['until'])
        self.assertAlmostEqual((until - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400, 3, places=2)

    def test_conflict_retry(self):
        REG.fail_next_409 = 1
        code, body = self.call('/api/cards/Q-1/yes', {})
        self.assertEqual((code, body['status']), (200, 'saved'))
        self.assertEqual([p['expected_revision'] for p in REG.posts], [3, 4])
        self.assertNotEqual(REG.posts[0]['operation_id'], REG.posts[1]['operation_id'])

    def test_unconfirmed_retry_same_operation(self):
        REG.fail_after_commit_503 = 1
        code, body = self.call('/api/cards/Q-1/yes', {})
        self.assertEqual((code, body['status']), (200, 'saved'))
        self.assertEqual(len(REG.posts), 2)
        self.assertEqual(REG.posts[0]['operation_id'], REG.posts[1]['operation_id'])
        self.assertEqual(REG.records['Q-1']['revision'], 4)  # записано один раз

    def test_already_closed(self):
        REG.records['Q-1']['humanqa']['a'] = 'ответили в панели'
        code, body = self.call('/api/cards/Q-1/yes', {})
        self.assertEqual((code, body['status']), (200, 'already'))
        self.assertEqual(REG.posts, [])

    def test_bad_id(self):
        self.assertEqual(self.call('/api/cards/..%2Fx/yes', {})[0], 400)
        self.assertEqual(REG.posts, [])


class TodayAndTasksTest(Base):
    def test_reminders(self):
        at = (dt.datetime.now(MSK) + dt.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
        code, body = self.call('/api/reminders', {'text': 'Позвонить юристу', 'at': at})
        self.assertEqual(code, 200)
        items = self.call('/api/today')[1]['items']
        self.assertEqual([i['title'] for i in items], ['Позвонить юристу'])
        self.call('/api/reminders/' + body['id'] + '/done', {})
        self.assertEqual(self.call('/api/today')[1]['items'], [])
        self.assertEqual(self.call('/api/reminders', {'text': '', 'at': at})[0], 400)

    def test_tasks(self):
        code, body = self.call('/api/tasks', {'title': 'Подать заявку', 'project_id': 'a',
                                              'due': dt.date.today().isoformat(), 'assignee': 'pam'})
        self.assertEqual(code, 200)
        tid = body['id']
        tasks = self.call('/api/projects/a/tasks')[1]['tasks']
        self.assertEqual([t['title'] for t in tasks], ['Подать заявку'])
        self.assertEqual(tasks[0]['project_name'], 'Вардане')
        projects = {p['id']: p for p in self.call('/api/projects')[1]['projects']}
        self.assertEqual(projects['a']['open_tasks'], 1)
        self.assertNotIn('b', projects)
        self.assertEqual(self.call('/api/tasks/' + tid, {'assignee': 'me', 'done': True})[0], 200)
        self.assertEqual(self.store.task(tid)['assignee'], 'me')
        self.assertEqual(self.call('/api/tasks/' + tid, {'assignee': 'creed'})[0], 400)
        self.assertEqual(self.call('/api/tasks', {'title': 'x', 'project_id': 'b'})[0], 400)  # «Дубли» не проект
        self.assertEqual(self.call('/api/tasks', {'title': '', 'project_id': 'a'})[0], 400)
        self.assertEqual(self.call('/api/tasks/' + tid + '/delete', {})[0], 200)
        self.assertEqual(self.call('/api/projects/a/tasks')[1]['tasks'], [])


class NotifierTest(unittest.TestCase):
    def test_tick(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / 'db.sqlite3')
            at = dt.datetime(2026, 11, 4, 9, 30, tzinfo=MSK)
            s.add_reminder('Прошло', at - dt.timedelta(minutes=1))
            s.add_reminder('Ещё рано', at + dt.timedelta(hours=1))
            s.add_task('Срок сегодня', 'a', 'Вардане', '2026-11-04', 'me')
            s.add_task('Срок завтра', 'a', 'Вардане', '2026-11-05', 'me')
            sent = []
            ok = lambda cfg, text: sent.append(text) or True  # noqa: E731
            self.assertEqual(notifier.tick(None, s, ok, at), 2)
            self.assertEqual(sent, ['Напоминание: Прошло', 'Срок сегодня: Срок сегодня — Вардане'])
            self.assertEqual(notifier.tick(None, s, ok, at), 0)  # второй раз не шлём
            early = dt.datetime(2026, 11, 5, 8, 0, tzinfo=MSK)
            self.assertEqual(notifier.tick(None, s, ok, early), 1)  # напоминание да, срок завтрашней задачи — только с 09:00
            self.assertEqual(sent[-1], 'Напоминание: Ещё рано')
            failed = lambda cfg, text: False  # noqa: E731
            s.add_reminder('Telegram недоступен', at - dt.timedelta(minutes=1))
            self.assertEqual(notifier.tick(None, s, failed, at), 0)
            self.assertEqual(len(s.due_reminders(at)), 1)  # не отмечено, повторим


if __name__ == '__main__':
    unittest.main()
