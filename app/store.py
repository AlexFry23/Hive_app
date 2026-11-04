"""Своя база приложения (SQLite): судьба карточек, журнал записей в реестр, напоминания, задачи.
Реестр улья здесь не хранится — только то, чего в нём нет (решение 12)."""
import datetime as dt
import json
import sqlite3
import threading
import uuid

MSK = dt.timezone(dt.timedelta(hours=3))  # Москва без перехода на летнее время с 2014 г.

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
  record_id TEXT PRIMARY KEY,
  state TEXT NOT NULL,            -- snoozed | skipped | removed | answered
  until TEXT,                     -- ISO, до какого момента не показывать (snoozed/skipped)
  draft TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ops (
  operation_id TEXT PRIMARY KEY,
  record_id TEXT NOT NULL,
  kind TEXT NOT NULL,             -- yes | answer | remove
  request TEXT,
  status TEXT NOT NULL,           -- pending | saved | failed
  result TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  at TEXT NOT NULL,               -- ISO с часовым поясом
  sent_at TEXT,
  done INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  project_id TEXT,
  project_name TEXT,
  due TEXT,                       -- YYYY-MM-DD или пусто
  assignee TEXT,                  -- michael | pam | oscar | kevin | me
  done INTEGER NOT NULL DEFAULT 0,
  notified_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

ASSIGNEES = {'michael': 'Michael', 'pam': 'Pam', 'oscar': 'Oscar', 'kevin': 'Kevin', 'me': 'я сам'}


def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).isoformat(timespec='seconds')


def parse(value):
    if not value:
        return None
    t = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return t if t.tzinfo else t.replace(tzinfo=MSK)


def msk_day_start(day):
    return dt.datetime.combine(day, dt.time(0, 0), MSK)


class Store:
    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # --- карточки ---
    def set_card(self, record_id, state, until=None, draft=None):
        old = self.card(record_id)
        if draft is None and old:
            draft = old.get('draft')
        self._exec('INSERT INTO cards(record_id,state,until,draft,updated_at) VALUES(?,?,?,?,?) '
                   'ON CONFLICT(record_id) DO UPDATE SET state=excluded.state, until=excluded.until, '
                   'draft=excluded.draft, updated_at=excluded.updated_at',
                   (record_id, state, iso(until) if until else None, draft, iso(now())))

    def card(self, record_id):
        rows = self._all('SELECT * FROM cards WHERE record_id=?', (record_id,))
        return rows[0] if rows else None

    def card_states(self):
        return {r['record_id']: r for r in self._all('SELECT * FROM cards')}

    @staticmethod
    def hidden(row, at=None):
        if not row:
            return False
        if row['state'] in ('removed', 'answered'):
            return True
        until = parse(row.get('until'))
        return bool(until and until > (at or now()))

    # --- журнал записей в реестр ---
    def op_start(self, record_id, kind, request):
        op = uuid.uuid4().hex
        t = iso(now())
        self._exec('INSERT INTO ops VALUES(?,?,?,?,?,?,?,?)',
                   (op, record_id, kind, json.dumps(request, ensure_ascii=False), 'pending', None, t, t))
        return op

    def op_finish(self, op, status, result):
        self._exec('UPDATE ops SET status=?, result=?, updated_at=? WHERE operation_id=?',
                   (status, json.dumps(result, ensure_ascii=False), iso(now()), op))

    def ops(self, limit=50):
        return self._all('SELECT operation_id,record_id,kind,status,result,created_at,updated_at FROM ops '
                         'ORDER BY created_at DESC LIMIT ?', (limit,))

    # --- напоминания ---
    def add_reminder(self, text, at):
        rid = 'R-' + uuid.uuid4().hex[:12]
        self._exec('INSERT INTO reminders(id,text,at,created_at) VALUES(?,?,?,?)', (rid, text, iso(at), iso(now())))
        return rid

    def reminders(self):
        return self._all('SELECT * FROM reminders ORDER BY at')

    def reminder_done(self, rid, done=True):
        return self._exec('UPDATE reminders SET done=? WHERE id=?', (1 if done else 0, rid)).rowcount

    def delete_reminder(self, rid):
        return self._exec('DELETE FROM reminders WHERE id=?', (rid,)).rowcount

    def due_reminders(self, at=None):
        at = iso(at or now())
        return self._all('SELECT * FROM reminders WHERE sent_at IS NULL AND done=0 AND at<=? ORDER BY at', (at,))

    def mark_reminder_sent(self, rid):
        self._exec('UPDATE reminders SET sent_at=? WHERE id=?', (iso(now()), rid))

    # --- задачи ---
    def add_task(self, title, project_id=None, project_name=None, due=None, assignee=None):
        tid = 'A-' + uuid.uuid4().hex[:12]
        t = iso(now())
        self._exec('INSERT INTO tasks(id,title,project_id,project_name,due,assignee,created_at,updated_at) '
                   'VALUES(?,?,?,?,?,?,?,?)', (tid, title, project_id, project_name, due, assignee, t, t))
        return tid

    def update_task(self, tid, **fields):
        allowed = {'title', 'project_id', 'project_name', 'due', 'assignee', 'done'}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return 0
        if 'due' in fields:
            fields['notified_at'] = None  # новый срок — новое напоминание
        sets = ', '.join(k + '=?' for k in fields) + ', updated_at=?'
        return self._exec('UPDATE tasks SET ' + sets + ' WHERE id=?', (*fields.values(), iso(now()), tid)).rowcount

    def delete_task(self, tid):
        return self._exec('DELETE FROM tasks WHERE id=?', (tid,)).rowcount

    def tasks(self, project_id=None):
        if project_id is None:
            return self._all("SELECT * FROM tasks ORDER BY done, COALESCE(NULLIF(due, ''), '9999'), created_at")
        return self._all("SELECT * FROM tasks WHERE project_id=? ORDER BY done, COALESCE(NULLIF(due, ''), '9999'), created_at",
                         (project_id,))

    def task(self, tid):
        rows = self._all('SELECT * FROM tasks WHERE id=?', (tid,))
        return rows[0] if rows else None

    def tasks_due_to_notify(self, today):
        return self._all("SELECT * FROM tasks WHERE done=0 AND due IS NOT NULL AND due<>'' AND due<=? "
                         'AND notified_at IS NULL', (today.isoformat(),))

    def mark_task_notified(self, tid):
        self._exec('UPDATE tasks SET notified_at=? WHERE id=?', (iso(now()), tid))
