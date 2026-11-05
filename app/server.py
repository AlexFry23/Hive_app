#!/usr/bin/env python3
"""Служба мини-приложения Александра.

Слушает 127.0.0.1:PORT, снаружи — через Caddy (app.afhive.tech:8443).
Настройки — переменные окружения (systemd: EnvironmentFile=/opt/office-app/.env):
  BOT_TOKEN        токен отдельного бота приложения (решение 8) — СЕКРЕТ
  ALLOWED_USER_ID  Telegram id Александра; остальных не пускаем
  APP_URL          адрес страницы для кнопки меню, например https://app.afhive.tech:8443/
  PORT             по умолчанию 18780
  REGISTRY_URL     по умолчанию http://127.0.0.1:18768
  REGISTRY_KEY_FILE необязательно; иначе $CREDENTIALS_DIRECTORY/registry.key
  PROJECTS_FILE    по умолчанию /opt/office-app/data/projects.json
  DB_FILE          по умолчанию /opt/office-app/data/app.sqlite3
  SKIP_DAYS        через сколько дней вернуть пролистанную карточку (по умолчанию 3, решение 5)
  SET_MENU_BUTTON  1 (по умолчанию) — при запуске поставить кнопку меню бота
  NOTIFY           1 (по умолчанию) — слать напоминания и сроки в личку
"""
import datetime as dt
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import actions  # noqa: E402
import notifier  # noqa: E402
import today  # noqa: E402
from cards import ACTIVE_STATUSES, AGENTS, CLOSED_STATUSES, clean_projects, make_cards, record_project, summarize, full_text  # noqa: E402
from registry import Registry, RegistryError, load_key  # noqa: E402
from store import ASSIGNEES, MSK, Store, now  # noqa: E402
from telegram_auth import AuthError, check_init_data  # noqa: E402

WEB = (HERE.parent / 'web').resolve()
RECORD_ID = re.compile(r'^[A-Z]-[A-Za-z0-9_-]{1,80}$')
LOCAL_ID = re.compile(r'^[RA]-[0-9a-f]{12}$')


def log(*parts):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *parts, flush=True)


class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.bot_token = env.get('BOT_TOKEN', '').strip()
        self.allowed_user_id = env.get('ALLOWED_USER_ID', '').strip()
        self.app_url = env.get('APP_URL', '').strip()
        self.port = int(env.get('PORT', '18780'))
        self.registry_url = env.get('REGISTRY_URL', 'http://127.0.0.1:18768')
        self.registry_key_file = env.get('REGISTRY_KEY_FILE') or None
        self.projects_file = env.get('PROJECTS_FILE', '/opt/office-app/data/projects.json')
        self.db_file = env.get('DB_FILE', '/opt/office-app/data/app.sqlite3')
        self.skip_days = float(env.get('SKIP_DAYS', '3'))
        self.set_menu_button = env.get('SET_MENU_BUTTON', '1') == '1'
        self.notify = env.get('NOTIFY', '1') == '1'
        self.telegram_api = env.get('TELEGRAM_API', 'https://api.telegram.org')


def read_projects(path):
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return {'updated_at': data.get('updated_at'), 'projects': clean_projects(data.get('projects'))}
    except (OSError, ValueError, AttributeError):
        return {'updated_at': None, 'projects': []}


def set_menu_button(cfg):
    """Кнопка меню бота открывает приложение. Ставится для всех чатов бота;
    чужие всё равно не пройдут проверку initData."""
    if not (cfg.bot_token and cfg.app_url):
        log('кнопка меню: пропущено (нет BOT_TOKEN или APP_URL)')
        return False
    body = json.dumps({'menu_button': {'type': 'web_app', 'text': 'Открыть',
                                       'web_app': {'url': cfg.app_url}}}).encode()
    req = urllib.request.Request(cfg.telegram_api + '/bot' + cfg.bot_token + '/setChatMenuButton',
                                 data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            ok = bool(json.loads(resp.read().decode()).get('ok'))
    except Exception as e:  # сеть, ответ Telegram — токен в лог не пишем
        log('кнопка меню: ошибка', type(e).__name__)
        return False
    log('кнопка меню:', 'поставлена' if ok else 'Telegram отказал')
    return ok


def parse_local_datetime(value):
    """'YYYY-MM-DDTHH:MM' из поля формы — время по Москве."""
    try:
        t = dt.datetime.fromisoformat(str(value))
    except ValueError:
        raise actions.ActionError('Неверные дата и время')
    return t if t.tzinfo else t.replace(tzinfo=MSK)


def parse_due(value):
    if value in (None, ''):
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        raise actions.ActionError('Неверная дата срока')


class App:
    def __init__(self, cfg, registry, store, status):
        self.cfg, self.registry, self.store, self.status = cfg, registry, store, status

    def projects(self):
        return read_projects(self.cfg.projects_file)

    def cards(self):
        projects = self.projects()['projects']
        state = self.registry.state()
        records = state.get('records') or []
        # отсрочка самого улья видна только в полной записи — читаем её для paused (кэш по ревизии)
        for rec in records:
            if rec.get('status') == 'paused' and rec.get('type') == 'question' and rec.get('waiting_on') == 'alexander':
                try:
                    rec['_raw'] = self.registry.record_cached(rec['id'], rec.get('revision'))
                except RegistryError:
                    pass
        at = now()
        made, hive_deferred = make_cards(records, projects, self.store.record_projects(), at)
        states = self.store.card_states()
        out, later = [], [u for _, u in hive_deferred]
        for c in made:
            row = states.get(c['id'])
            if self.store.hidden(row, at):
                if row and row['state'] in ('snoozed', 'skipped') and row.get('until'):
                    later.append(dt.datetime.fromisoformat(row['until']))
                continue
            c['draft'] = (row or {}).get('draft') or ''
            out.append(c)
        nxt = min(later) if later else None
        return {'cards': out, 'revision': state.get('revision'),
                'deferred': {'count': len(later), 'next': nxt.astimezone(MSK).date().isoformat() if nxt else None}}

    def card_action(self, record_id, verb, body):
        s, r = self.store, self.registry
        draft = body.get('draft')
        if verb == 'yes':
            return actions.yes(r, s, record_id, body.get('text', ''), body.get('assignee'))
        if verb == 'answer':
            return actions.answer(r, s, record_id, body.get('text', ''), body.get('assignee'))
        if verb == 'project':
            pid = body.get('project_id') or None
            if pid and not self.project_by_id(pid):
                raise actions.ActionError('Нет такого проекта')
            return actions.set_project(r, s, record_id, pid)
        if verb == 'remove':
            return actions.remove(r, s, record_id)
        if verb == 'no':
            return actions.no(s, record_id, draft)
        if verb == 'snooze':
            return actions.snooze(s, record_id, body.get('days'), body.get('date'), draft)
        if verb == 'skip':
            return actions.skip(s, record_id, self.cfg.skip_days, draft)
        raise actions.ActionError('Нет такого действия', 404)

    def today(self):
        try:
            records = self.registry.state().get('records') or []
        except RegistryError:
            records = []
        return today.build(now(), self.store.reminders(), self.store.tasks(), records)

    def project_items(self):
        """Записи реестра по проектам (этап 4.1, п. 3): ждут Александра, открытые, закрытые за 7 дней."""
        projects = self.projects()['projects']
        try:
            records = self.registry.state().get('records') or []
        except RegistryError:
            records = []
        local = self.store.record_projects()
        week_ago = now() - dt.timedelta(days=7)
        groups = {}
        for rec in records:
            if rec.get('type') not in ('question', 'task'):
                continue
            p, how = record_project(rec, projects, local)
            if not p:
                continue
            status = rec.get('status')
            qa = rec.get('humanqa') or {}
            if status in CLOSED_STATUSES:
                when = None
                for v in (qa.get('answeredAt'), qa.get('withdrawnAt'), rec.get('waiting_on_at'), rec.get('accepted_at')):
                    try:
                        when = dt.datetime.fromisoformat(str(v).replace('Z', '+00:00')) if v else None
                    except ValueError:
                        when = None
                    if when and when.tzinfo:
                        break
                    when = None
                if not when or when < week_ago:
                    continue
                bucket = 'closed'
            elif status in ACTIVE_STATUSES:
                bucket = 'waiting' if rec.get('waiting_on') == 'alexander' else 'open'
            else:
                continue
            head, _ = summarize(full_text(rec), rec.get('title'))
            groups.setdefault(p['id'], {'waiting': [], 'open': [], 'closed': []})[bucket].append({
                'id': rec.get('id'), 'type': rec.get('type'), 'title': head, 'status': status,
                'due': rec.get('due'), 'how': how})
        return groups

    def project_by_id(self, pid):
        return next((p for p in self.projects()['projects'] if p.get('id') == pid), None)

    def task_fields(self, body, partial=False):
        out = {}
        if 'title' in body or not partial:
            title = str(body.get('title') or '').strip()
            if not title:
                raise actions.ActionError('Нужно название задачи')
            out['title'] = title[:500]
        if 'project_id' in body or not partial:
            p = self.project_by_id(body.get('project_id'))
            if not p:
                raise actions.ActionError('Нет такого проекта')
            out['project_id'], out['project_name'] = p['id'], p['name']
        if 'due' in body:
            out['due'] = parse_due(body.get('due'))
        if 'assignee' in body:
            a = body.get('assignee') or None
            if a is not None and a not in ASSIGNEES:
                raise actions.ActionError('Исполнитель — только из списка')
            out['assignee'] = a
        if 'done' in body:
            out['done'] = 1 if body.get('done') else 0
        return out


def make_handler(cfg, registry, status, store=None):
    app = App(cfg, registry, store, status)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'office-app'

        def log_message(self, *args):
            pass  # не пишем запросы: в них initData

        def send(self, code, body, ctype='application/json'):
            if not isinstance(body, bytes):
                body = (json.dumps(body, ensure_ascii=False) if ctype == 'application/json' else body).encode()
            self.send_response(code)
            text = ctype.startswith(('text/', 'application/json', 'application/javascript', 'image/svg'))
            self.send_header('Content-Type', ctype + ('; charset=utf-8' if text else ''))
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)

        def user(self):
            header = self.headers.get('Authorization', '')
            init_data = header[4:] if header.startswith('tma ') else ''
            user = check_init_data(init_data, cfg.bot_token)
            if not cfg.allowed_user_id or str(user.get('id')) != cfg.allowed_user_id:
                raise AuthError('доступ только для владельца')
            return user

        def body(self):
            n = int(self.headers.get('Content-Length') or 0)
            if n > 100_000:
                raise actions.ActionError('Слишком большой запрос', 413)
            if not n:
                return {}
            try:
                data = json.loads(self.rfile.read(n).decode('utf-8'))
            except ValueError:
                raise actions.ActionError('Неверный запрос')
            if not isinstance(data, dict):
                raise actions.ActionError('Неверный запрос')
            return data

        def static(self, rel):
            target = (WEB / rel).resolve()
            if WEB not in target.parents or not target.is_file():
                return self.send(404, {'error': 'нет такого адреса'})
            ctype = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
            return self.send(200, target.read_bytes(), ctype)

        def guarded(self, fn):
            try:
                self.user()
            except AuthError as e:
                return self.send(401, {'error': str(e)})
            try:
                return fn()
            except actions.ActionError as e:
                return self.send(e.code, {'error': str(e)})
            except RegistryError as e:
                log('реестр:', e)
                return self.send(503, {'error': 'Реестр улья сейчас недоступен'})

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == '/health':
                return self.send(200, dict(status, ok=True))
            if path == '/':
                return self.static('index.html')
            if not path.startswith('/api/'):
                return self.static(path.lstrip('/'))
            return self.guarded(lambda: self.route_get(path))

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            return self.guarded(lambda: self.route_post(path))

        def route_get(self, path):
            if path == '/api/meta':
                return self.send(200, {'agents': AGENTS, 'assignees': ASSIGNEES, 'skip_days': cfg.skip_days})
            if path == '/api/cards':
                return self.send(200, app.cards())
            if path == '/api/today':
                return self.send(200, app.today())
            if path == '/api/projects':
                data = app.projects()
                counts = {}
                for t in store.tasks():
                    if not t['done']:
                        counts[t['project_id']] = counts.get(t['project_id'], 0) + 1
                items = app.project_items()
                for p in data['projects']:
                    g = items.get(p['id'], {})
                    p['open_tasks'] = counts.get(p['id'], 0)
                    p['waiting'] = len(g.get('waiting', []))
                    p['open'] = len(g.get('open', []))
                    p['closed'] = len(g.get('closed', []))
                    p['empty'] = not (p['open_tasks'] or p['waiting'] or p['open'] or p['closed']
                                      or any(t['project_id'] == p['id'] for t in store.tasks()))
                return self.send(200, data)
            m = re.fullmatch(r'/api/projects/([^/]+)/tasks', path)
            if m:
                pid = urllib.parse.unquote(m.group(1))
                if not app.project_by_id(pid):
                    raise actions.ActionError('Нет такого проекта', 404)
                g = app.project_items().get(pid, {'waiting': [], 'open': [], 'closed': []})
                return self.send(200, {'tasks': store.tasks(pid), 'registry': g})
            return self.send(404, {'error': 'нет такого адреса'})

        def route_post(self, path):
            body = self.body()
            m = re.fullmatch(r'/api/cards/([^/]+)/(yes|answer|no|snooze|skip|remove|project)', path)
            if m:
                rid = urllib.parse.unquote(m.group(1))
                if not RECORD_ID.match(rid):
                    raise actions.ActionError('Неверная карточка')
                return self.send(200, app.card_action(rid, m.group(2), body))
            if path == '/api/reminders':
                text = str(body.get('text') or '').strip()
                if not text:
                    raise actions.ActionError('Нужен текст напоминания')
                at = parse_local_datetime(body.get('at'))
                return self.send(200, {'id': store.add_reminder(text[:1000], at)})
            m = re.fullmatch(r'/api/reminders/([^/]+)/(done|delete)', path)
            if m and LOCAL_ID.match(m.group(1)):
                n = store.reminder_done(m.group(1)) if m.group(2) == 'done' else store.delete_reminder(m.group(1))
                return self.send(200 if n else 404, {'ok': bool(n)})
            if path == '/api/tasks':
                f = app.task_fields(body)
                return self.send(200, {'id': store.add_task(**{k: f.get(k) for k in (
                    'title', 'project_id', 'project_name', 'due', 'assignee')})})
            m = re.fullmatch(r'/api/tasks/([^/]+)(/delete)?', path)
            if m and LOCAL_ID.match(m.group(1)):
                tid = m.group(1)
                if m.group(2):
                    n = store.delete_task(tid)
                else:
                    n = store.update_task(tid, **app.task_fields(body, partial=True))
                return self.send(200 if n else 404, {'ok': bool(n), 'task': store.task(tid)})
            return self.send(404, {'error': 'нет такого адреса'})

    return Handler


def main():
    cfg = Config()
    key = load_key(cfg.registry_key_file)
    registry = Registry(cfg.registry_url, key)
    store = Store(cfg.db_file)
    status = {'bot_token': bool(cfg.bot_token), 'allowed_user': bool(cfg.allowed_user_id),
              'registry_key': bool(key), 'registry': None, 'menu_button': None, 'db': True}

    def startup_checks():
        try:
            status['registry'] = registry.health()
        except RegistryError as e:
            status['registry'] = False
            log('реестр:', e)
        if cfg.set_menu_button:
            status['menu_button'] = set_menu_button(cfg)

    threading.Thread(target=startup_checks, daemon=True).start()
    if cfg.notify:
        notifier.run(cfg, store, log)
    server = ThreadingHTTPServer(('127.0.0.1', cfg.port), make_handler(cfg, registry, status, store))
    log('office-app слушает 127.0.0.1:' + str(cfg.port),
        '| токен:', 'есть' if cfg.bot_token else 'НЕТ',
        '| владелец:', 'задан' if cfg.allowed_user_id else 'НЕ задан',
        '| ключ реестра:', 'есть' if key else 'НЕТ')
    server.serve_forever()


if __name__ == '__main__':
    main()
