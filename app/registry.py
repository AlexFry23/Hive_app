"""Чтение реестра улья через его HTTP-API (127.0.0.1:18768). Этап 1 — только чтение.

Вход в API — кука hive_session той же формы, что ставит сам реестр после ввода ключа:
«<срок>.<HMAC_SHA256(ключ, срок)>» (см. registry_service.py улья, функции session/valid).
Ключ служба получает от systemd (LoadCredential=registry.key:...), права улья не меняются.
"""
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class RegistryError(Exception):
    pass


def load_key(path=None):
    candidates = []
    if path:
        candidates.append(path)
    cred_dir = os.environ.get('CREDENTIALS_DIRECTORY')
    if cred_dir:
        candidates.append(os.path.join(cred_dir, 'registry.key'))
    for p in candidates:
        try:
            with open(p) as f:
                key = f.read().strip()
            if key:
                return key
        except OSError:
            continue
    return None


def session_cookie(key, lifetime=3600, now=None):
    expiry = str(int((time.time() if now is None else now) + lifetime))
    return expiry + '.' + hmac.new(key.encode(), expiry.encode(), hashlib.sha256).hexdigest()


class Registry:
    def __init__(self, base_url, key, cache_seconds=60, timeout=30):
        self.base = base_url.rstrip('/')
        self.key = key
        self.cache_seconds = cache_seconds
        self.timeout = timeout
        self._lock = threading.Lock()
        self._state = None
        self._at = 0.0

    def _get(self, path, auth=True):
        req = urllib.request.Request(self.base + path)
        if auth:
            if not self.key:
                raise RegistryError('нет ключа реестра')
            req.add_header('Cookie', 'hive_session=' + session_cookie(self.key))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            raise RegistryError('реестр ответил ' + str(e.code))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RegistryError('реестр недоступен: ' + type(e).__name__)

    def health(self):
        """Реестр жив и ключ подходит (дешёвый запрос с авторизацией)."""
        alive = bool(self._get('/health', auth=False).get('ok'))
        self._get('/api/search?' + urllib.parse.urlencode({'q': 'office-app-health-check-zzz'}))
        return alive

    def state(self):
        with self._lock:
            if self._state is not None and time.time() - self._at < self.cache_seconds:
                return self._state
        data = self._get('/api/state')
        with self._lock:
            self._state, self._at = data, time.time()
        return data
