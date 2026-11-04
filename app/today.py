"""Вкладка «Сегодня»: сроки и напоминания от сегодня до того же дня недели через неделю
включительно (решение 9: «сегодня среда — до следующей среды»). Просроченное невыполненное
тоже показывается — сверху, с пометкой."""
import datetime as dt

from store import MSK, parse

ACTIVE = {'open', 'doing', 'todo', 'partial', 'proposed', 'blocked', 'paused', 'needs_review'}


def window(at):
    today = at.astimezone(MSK).date()
    return today, today + dt.timedelta(days=7)


def _day(value):
    if not value:
        return None
    s = str(value)
    try:
        if len(s) == 10:
            return dt.date.fromisoformat(s)
        return parse(s).astimezone(MSK).date()
    except ValueError:
        return None


def build(at, reminders, tasks, records, projects_by_id=None):
    start, end = window(at)
    items = []
    for r in reminders:
        if r.get('done'):
            continue
        t = parse(r['at'])
        d = t.astimezone(MSK).date()
        if d <= end:
            items.append({'kind': 'reminder', 'id': r['id'], 'title': r['text'], 'day': d.isoformat(),
                          'time': t.astimezone(MSK).strftime('%H:%M'), 'overdue': t < at, 'sent': bool(r.get('sent_at'))})
    for t in tasks:
        d = _day(t.get('due'))
        if t.get('done') or not d or d > end:
            continue
        items.append({'kind': 'task', 'id': t['id'], 'title': t['title'], 'day': d.isoformat(), 'time': None,
                      'project': t.get('project_name'), 'assignee': t.get('assignee'), 'overdue': d < start})
    for r in records:
        if r.get('waiting_on') != 'alexander' or r.get('status') not in ACTIVE:
            continue
        d = _day(r.get('due'))
        if not d or d < start or d > end:
            continue
        items.append({'kind': 'hive', 'id': r.get('id'), 'title': r.get('title') or '', 'day': d.isoformat(),
                      'time': None, 'overdue': False})
    items.sort(key=lambda i: (not i['overdue'], i['day'], i['time'] or '99:99'))
    return {'from': start.isoformat(), 'to': end.isoformat(), 'items': items}
