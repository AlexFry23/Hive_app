"""Сообщения в личку от бота приложения: напоминания — в их время, сроки задач — в 09:00 по Москве
в день срока (просроченные — один раз). Отметка «отправлено» — только после ответа Telegram ok."""
import datetime as dt
import json
import threading
import time
import urllib.request

from store import MSK, now

TASK_HOUR = 9


def send_message(cfg, text):
    if not (cfg.bot_token and cfg.allowed_user_id):
        return False
    body = json.dumps({'chat_id': int(cfg.allowed_user_id), 'text': text}).encode()
    req = urllib.request.Request(cfg.telegram_api + '/bot' + cfg.bot_token + '/sendMessage', data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return bool(json.loads(resp.read().decode()).get('ok'))
    except Exception:
        return False


def tick(cfg, store, send=send_message, at=None):
    at = at or now()
    sent = 0
    for r in store.due_reminders(at):
        if send(cfg, 'Напоминание: ' + r['text']):
            store.mark_reminder_sent(r['id'])
            sent += 1
    local = at.astimezone(MSK)
    if local.hour >= TASK_HOUR:
        for t in store.tasks_due_to_notify(local.date()):
            when = 'сегодня' if t['due'] == local.date().isoformat() else 'просрочено (' + t['due'] + ')'
            text = 'Срок ' + when + ': ' + t['title'] + (' — ' + t['project_name'] if t.get('project_name') else '')
            if send(cfg, text):
                store.mark_task_notified(t['id'])
                sent += 1
    return sent


def run(cfg, store, log, period=30):
    def loop():
        while True:
            try:
                tick(cfg, store)
            except Exception as e:
                log('напоминания: ошибка', type(e).__name__)
            time.sleep(period)
    threading.Thread(target=loop, daemon=True).start()
