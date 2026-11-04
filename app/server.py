#!/usr/bin/env python3
"""Служба мини-приложения Александра (этап 1: только чтение).

Слушает 127.0.0.1:PORT, снаружи — через Caddy (app.afhive.tech:8443).
Настройки — переменные окружения (systemd: EnvironmentFile=/opt/office-app/.env):
  BOT_TOKEN        токен отдельного бота приложения (решение 8) — СЕКРЕТ
  ALLOWED_USER_ID  Telegram id Александра; остальных не пускаем
  APP_URL          адрес страницы для кнопки меню, например https://app.afhive.tech:8443/
  PORT             по умолчанию 18780
  REGISTRY_URL     по умолчанию http://127.0.0.1:18768
  REGISTRY_KEY_FILE необязательно; иначе $CREDENTIALS_DIRECTORY/registry.key
  PROJECTS_FILE    по умолчанию /opt/office-app/data/projects.json
  SET_MENU_BUTTON  1 (по умолчанию) — при запуске поставить кнопку меню бота
"""
import json
import mimetypes
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cards import clean_projects, make_cards  # noqa: E402
from registry import Registry, RegistryError, load_key  # noqa: E402
from telegram_auth import AuthError, check_init_data  # noqa: E402

WEB = (HERE.parent / 'web').resolve()


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
        self.set_menu_button = env.get('SET_MENU_BUTTON', '1') == '1'
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


def make_handler(cfg, registry, status):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'office-app'

        def log_message(self, *args):
            pass  # не пишем запросы: в них initData

        def send(self, code, body, ctype='application/json'):
            if not isinstance(body, bytes):
                body = (json.dumps(body, ensure_ascii=False) if ctype == 'application/json' else body).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith(('text/', 'application/json', 'application/javascript', 'image/svg')) else ''))
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

        def static(self, rel):
            target = (WEB / rel).resolve()
            if WEB not in target.parents or not target.is_file():
                return self.send(404, {'error': 'нет такого адреса'})
            ctype = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
            return self.send(200, target.read_bytes(), ctype)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == '/health':
                return self.send(200, dict(status, ok=True))
            if path == '/':
                return self.static('index.html')
            if not path.startswith('/api/'):
                return self.static(path.lstrip('/'))
            try:
                self.user()
            except AuthError as e:
                return self.send(401, {'error': str(e)})
            if path == '/api/cards':
                projects = read_projects(cfg.projects_file)['projects']
                try:
                    state = registry.state()
                except RegistryError as e:
                    log('реестр:', e)
                    return self.send(503, {'error': 'Реестр улья сейчас недоступен'})
                cards = make_cards(state.get('records') or [], projects)
                return self.send(200, {'cards': cards, 'revision': state.get('revision')})
            if path == '/api/projects':
                return self.send(200, read_projects(cfg.projects_file))
            return self.send(404, {'error': 'нет такого адреса'})

    return Handler


def main():
    cfg = Config()
    key = load_key(cfg.registry_key_file)
    registry = Registry(cfg.registry_url, key)
    status = {'bot_token': bool(cfg.bot_token), 'allowed_user': bool(cfg.allowed_user_id),
              'registry_key': bool(key), 'registry': None, 'menu_button': None}

    def startup_checks():
        try:
            status['registry'] = registry.health()
        except RegistryError as e:
            status['registry'] = False
            log('реестр:', e)
        if cfg.set_menu_button:
            status['menu_button'] = set_menu_button(cfg)

    threading.Thread(target=startup_checks, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', cfg.port), make_handler(cfg, registry, status))
    log('office-app слушает 127.0.0.1:' + str(cfg.port),
        '| токен:', 'есть' if cfg.bot_token else 'НЕТ',
        '| владелец:', 'задан' if cfg.allowed_user_id else 'НЕ задан',
        '| ключ реестра:', 'есть' if key else 'НЕТ')
    server.serve_forever()


if __name__ == '__main__':
    main()
