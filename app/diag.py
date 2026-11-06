#!/usr/bin/env python3
"""Диагностика колоды «Ждёт меня» — ТОЛЬКО ЧТЕНИЕ, ничего не пишет ни в реестр, ни в базу.
Показывает по слоям, где отсеиваются вопросы: реестр -> отбор приложения -> отсрочки улья ->
отметки в базе приложения. Тексты вопросов не печатает, только счётчики и id.

Запуск на VPS от root, без копирования файлов (код приложения уже лежит в /opt/office-app/app):
  ssh "$VPS" 'cd /opt/office-app/app && python3 - --key /opt/hive-registry/access.key \
     --db /opt/office-app/data/app.sqlite3 --projects /opt/office-app/data/projects.json' < app/diag.py
"""
import argparse
import collections
import datetime as dt
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.getcwd())

import cards  # noqa: E402
import planerka  # noqa: E402
from registry import Registry  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://127.0.0.1:18768')
    ap.add_argument('--key', required=True)
    ap.add_argument('--db', required=True)
    ap.add_argument('--projects', default='')
    a = ap.parse_args()
    key = open(a.key).read().strip()
    reg = Registry(a.url, key, cache_seconds=0)
    at = dt.datetime.now(dt.timezone.utc)
    out = {'at': at.isoformat(timespec='seconds')}

    state = reg.state()
    recs = state.get('records') or []
    out['registry_revision'] = state.get('revision')
    out['records_total'] = len(recs)
    sample = recs[0] if recs else {}
    out['summary_has_fields'] = {k: (k in sample) for k in ('waiting_on', 'humanqa', 'not_before', 'project', 'updated_at', 'question_direction')}

    qs = [r for r in recs if r.get('type') == 'question']
    out['questions_total'] = len(qs)
    out['questions_by_waiting_on'] = dict(collections.Counter(str(r.get('waiting_on')) for r in qs).most_common())
    out['questions_waiting_alexander_by_status'] = dict(collections.Counter(
        str(r.get('status')) for r in qs if r.get('waiting_on') == 'alexander').most_common())
    out['questions_open_like_by_waiting_on'] = dict(collections.Counter(
        str(r.get('waiting_on')) for r in qs if r.get('status') in cards.OPEN_STATUSES).most_common())
    out['questions_open_like_by_direction'] = dict(collections.Counter(
        str(r.get('question_direction')) for r in qs if r.get('status') in cards.OPEN_STATUSES).most_common())
    out['tasks_waiting_alexander_by_status'] = dict(collections.Counter(
        str(r.get('status')) for r in recs if r.get('type') == 'task' and r.get('waiting_on') == 'alexander').most_common())

    # почему вопрос, ждущий Александра, не прошёл отбор приложения
    why = collections.Counter()
    for r in qs:
        if r.get('waiting_on') != 'alexander':
            continue
        qa = r.get('humanqa') or {}
        if r.get('status') not in cards.OPEN_STATUSES:
            why['статус ' + str(r.get('status'))] += 1
        elif qa.get('withdrawn'):
            why['снят (withdrawn)'] += 1
        elif qa.get('a') and r.get('status') != 'needs_review':
            why['уже есть ответ'] += 1
        else:
            why['ПРОШЁЛ отбор'] += 1
    out['waiting_alexander_filter'] = dict(why.most_common())

    # отсрочки самого улья (полная запись только для paused)
    for r in recs:
        if planerka.is_paused_item(r):
            try:
                r['_raw'] = reg.record(r['id'])
            except Exception as e:
                r['_raw'] = {}
                out.setdefault('raw_errors', []).append(type(e).__name__)
    projects = []
    if a.projects and os.path.exists(a.projects):
        projects = (json.load(open(a.projects, encoding='utf-8')) or {}).get('projects') or []
    db = sqlite3.connect('file:' + a.db + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    states = {row['record_id']: dict(row) for row in db.execute('SELECT * FROM cards')}
    local_projects = {row['record_id']: row['project_id'] for row in db.execute('SELECT * FROM record_projects')} \
        if db.execute("SELECT name FROM sqlite_master WHERE name='record_projects'").fetchone() else {}
    made, hive_deferred = cards.make_cards(recs, projects, local_projects, at)
    out['after_filter'] = len(made)
    out['hive_deferred'] = len(hive_deferred)

    hidden = collections.Counter()
    until_days = collections.Counter()
    visible = []
    for c in made:
        row = states.get(c['id'])
        if row and row['state'] in ('removed', 'answered'):
            hidden['в базе приложения: ' + row['state']] += 1
        elif row and row.get('until') and dt.datetime.fromisoformat(row['until']) > at:
            hidden['в базе приложения: ' + row['state'] + ' до даты'] += 1
            until_days[row['until'][:10]] += 1
        else:
            visible.append(c['id'])
    out['hidden_by_app'] = dict(hidden.most_common())
    out['app_snoozed_until'] = dict(sorted(until_days.items()))
    out['VISIBLE_IN_DECK'] = len(visible)
    out['visible_ids'] = visible[:20]
    out['app_db_cards_by_state'] = dict(collections.Counter(r['state'] for r in states.values()).most_common())
    out['app_db_ops_by_status'] = dict(collections.Counter(
        r['status'] for r in db.execute('SELECT status FROM ops')).most_common())
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
