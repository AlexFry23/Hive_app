"""Проверка подписи initData мини-приложения Telegram.

Алгоритм — по документации Telegram (https://core.telegram.org/bots/webapps,
раздел «Validating data received via the Mini App»):
secret = HMAC_SHA256(key="WebAppData", msg=bot_token);
hash   = hex(HMAC_SHA256(key=secret, msg=data_check_string)),
где data_check_string — все поля, кроме hash, «ключ=значение», по алфавиту, через \n.
"""
import hashlib
import hmac
import json
import time
import urllib.parse


class AuthError(Exception):
    pass


def check_init_data(init_data, bot_token, max_age_seconds=86400, now=None):
    """Вернуть словарь user из initData или поднять AuthError."""
    if not init_data or not bot_token:
        raise AuthError('нет данных входа')
    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    fields = dict(pairs)
    received = fields.pop('hash', None)
    if not received:
        raise AuthError('нет подписи')
    check_string = '\n'.join(k + '=' + v for k, v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError('подпись не сошлась')
    try:
        auth_date = int(fields.get('auth_date', '0'))
    except ValueError:
        raise AuthError('неверная дата входа')
    now = time.time() if now is None else now
    if max_age_seconds and now - auth_date > max_age_seconds:
        raise AuthError('вход устарел, откройте приложение заново')
    try:
        user = json.loads(fields.get('user') or '{}')
    except ValueError:
        raise AuthError('неверные данные пользователя')
    if not isinstance(user, dict) or 'id' not in user:
        raise AuthError('нет пользователя')
    return user


def sign_init_data(fields, bot_token):
    """Собрать подписанную строку initData (для проверок и локального запуска)."""
    fields = dict(fields)
    check_string = '\n'.join(k + '=' + v for k, v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)
