"""Планёрка (решение 29): все записи улья на паузе — вопросы Александру и дела улья.
У каждой должна быть дата возврата, иначе пауза никогда не снимется.
Порядок: «пора решить» (дата наступила) -> «без даты» -> «с датой» (по дате)."""
import datetime as dt

from cards import AGENTS, agent_key, deferred_until, full_text, summarize
from store import MSK, parse


def is_paused_item(r):
    if r.get('status') != 'paused':
        return False
    if r.get('type') == 'task':
        return True
    if r.get('type') == 'question' and r.get('waiting_on') == 'alexander':
        qa = r.get('humanqa') or {}
        return not qa.get('withdrawn') and not qa.get('a')
    return False


def build(records, states, at):
    items = []
    for r in records:
        if not is_paused_item(r):
            continue
        row = states.get(r.get('id')) or {}
        if row.get('state') in ('removed', 'answered'):
            continue
        app_date = parse(row.get('until')) if row.get('state') in ('snoozed', 'skipped') else None
        hive_date = deferred_until(r, dt.datetime.min.replace(tzinfo=dt.timezone.utc))
        date = app_date or hive_date
        if not date:
            bucket = 'nodate'
        elif date <= at:
            bucket = 'due'
        else:
            bucket = 'dated'
        key = agent_key(r.get('owner'))
        head, _ = summarize(full_text(r), r.get('title'))
        raw = r.get('_raw') or {}
        items.append({
            'id': r.get('id'), 'type': r.get('type'), 'agent': key, 'agent_name': AGENTS[key],
            'title': head, 'bucket': bucket,
            'date': date.astimezone(MSK).date().isoformat() if date else None,
            'date_from': 'app' if app_date else ('hive' if hive_date else None),
            'pause_reason': raw.get('pause_reason') or r.get('pause_reason'),
        })
    order = {'due': 0, 'nodate': 1, 'dated': 2}
    items.sort(key=lambda i: (order[i['bucket']], i['date'] or '', i['title']))
    counts = {b: sum(1 for i in items if i['bucket'] == b) for b in order}
    return {'items': items, 'counts': counts}
