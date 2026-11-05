"""Отбор вопросов «Ждёт меня» из сводки реестра и подготовка карточек. Без сети и файлов."""
import datetime as dt
import re

# Статусы, при которых вопрос ждёт Александра (этап 4.1: плюс paused и needs_review).
OPEN_STATUSES = {'open', 'doing', 'partial', 'proposed', 'paused', 'needs_review'}
ACTIVE_STATUSES = {'open', 'doing', 'todo', 'partial', 'proposed', 'blocked', 'paused', 'needs_review', 'active', 'accepted'}
CLOSED_STATUSES = {'done', 'done_reported', 'answered', 'archived', 'reference'}

# Пять текущих агентов (решение 20). Ключ — имя файла аватара в web/img/.
AGENTS = {
    'michael': 'Michael',
    'pam': 'Pam',
    'oscar': 'Oscar',
    'kevin': 'Kevin',
    'dwight': 'Dwight',
}
# Имена для текста ответа в улей — как в квитанциях реестра.
AGENT_NAMES_RU = {'michael': 'Майкл', 'pam': 'Пэм', 'oscar': 'Оскар', 'kevin': 'Кевин', 'dwight': 'Дуайт'}

NOT_PROJECTS = {'дубли', '_рынок_и_конкуренты'}


def agent_key(owner):
    """id владельца из реестра -> ключ агента. god = Michael. Неизвестный -> Michael (оркестратор)."""
    o = str(owner or '').strip().lower()
    if o == 'god' or o.startswith('michael'):
        return 'michael'
    for key in AGENTS:
        if o.startswith(key):
            return key
    return 'michael'


def is_waiting(r):
    if r.get('type') != 'question' or r.get('waiting_on') != 'alexander':
        return False
    status = r.get('status')
    if status not in OPEN_STATUSES:
        return False
    qa = r.get('humanqa') or {}
    if qa.get('withdrawn'):
        return False
    if qa.get('a') and status != 'needs_review':  # needs_review — ответ есть, но спорный: спросить снова
        return False
    return True


def _norm(s):
    return ' '.join(re.findall(r'\w+', str(s or '').casefold().replace('ё', 'е').replace('_', ' ')))


def clean_projects(projects):
    out = []
    for p in projects or []:
        name = str((p or {}).get('name') or '').strip()
        if not name or name.casefold() in NOT_PROJECTS:
            continue
        out.append({'name': name, 'id': (p or {}).get('id')})
    return sorted(out, key=lambda p: p['name'].casefold())


def guess_project(r, projects):
    """Название папки проекта прямо встречается в теме или заголовке. Нет совпадения — None."""
    hay = ' ' + _norm(str(r.get('topic') or '') + ' ' + str(r.get('title') or '')) + ' '
    best = None
    for p in projects:
        n = _norm(p['name'])
        if n and (' ' + n + ' ') in hay and (best is None or len(n) > len(_norm(best['name']))):
            best = p
    return best


def record_project(r, projects, local=None):
    """Проект записи: поле `project` реестра (решение 26) -> выбор в приложении -> угадывание по названию."""
    by_id = {p['id']: p for p in projects}
    for pid in (r.get('project'), (local or {}).get(r.get('id'))):
        if pid and pid in by_id:
            return by_id[pid], 'registry' if pid == r.get('project') else 'app'
    p = guess_project(r, projects)
    return (p, 'guess') if p else (None, None)


# ---------- текст карточки (этап 4.1, п. 2) ----------
ID_RE = re.compile(r'\b(?:MCA|[QTMDISUAR])-[A-Za-z0-9][A-Za-z0-9_-]{2,}\b')
PATH_RE = re.compile(r'(?:/Users/|/opt/|/home/|~/|shared/|scratch/|bin/|agents/)\S*')
FROM_CARD_RE = re.compile(r'\(?\s*(?:из|по) карточк[еиу]\s*[^\n),.;]*\)?', re.I)
OPTION_RE = re.compile(r'^\s*(?:[-•*–]|\d{1,2}[.)]|[а-яa-z][.)])\s+(.+)$', re.I)


def tidy(s):
    s = str(s or '')
    s = FROM_CARD_RE.sub('', s)
    s = re.sub(r'`[^`\n]*`', lambda m: '' if PATH_RE.search(m.group(0)) or ID_RE.search(m.group(0)) else m.group(0)[1:-1], s)
    s = s.replace('`', '')
    s = PATH_RE.sub('', s)
    s = ID_RE.sub('', s)
    s = s.replace('**', '').replace('__', '')
    s = re.sub(r'^\s*#+\s*', '', s, flags=re.M)
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r'\(\s*\)|\[\s*\]', '', s)
    s = re.sub(r'\s+([,.;:!?])', r'\1', s)
    return re.sub(r'\n{3,}', '\n\n', s).strip(' \t\n—-:;,')


def full_text(r):
    """Полный текст вопроса: humanqa.q (если не строка таблицы) -> content -> title."""
    qa = r.get('humanqa') or {}
    q = str(qa.get('q') or '').strip()
    if q and not q.startswith('|'):
        return q
    content = str(r.get('content') or '').strip()
    if content and not content.startswith('|'):
        return content
    return str(r.get('title') or '').strip()


def summarize(raw, title=''):
    """Суть: первая жирная строка (или первая непустая строка) + варианты ответа."""
    raw = str(raw or '')
    bold = re.search(r'\*\*(.+?)\*\*', raw, re.S)
    lines = [l for l in raw.splitlines() if l.strip()]
    head = bold.group(1) if bold else (lines[0] if lines else title)
    head = tidy(head) or tidy(title)
    options = []
    for l in lines:
        m = OPTION_RE.match(l)
        if m:
            o = tidy(m.group(1))
            if o and o != head and o not in options:
                options.append(o[:160])
        if len(options) >= 6:
            break
    if len(head) > 280:
        head = head[:277].rstrip() + '…'
    return head, options


def deferred_until(r, at):
    """Отсрочка самого улья — как vopros_gate.deferred_until улья: поле not_before или
    continuation.waiting_until ПОЛНОЙ записи (r['_raw'], берётся через /api/record).
    В сводке /api/state continuation.waiting_until — это срок due, его не используем."""
    raw = r.get('_raw') or {}
    cont = raw.get('continuation') if isinstance(raw.get('continuation'), dict) else {}
    for value in (raw.get('not_before') or r.get('not_before'), cont.get('waiting_until')):
        if not value:
            continue
        s = str(value)
        try:
            t = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
            if not t.tzinfo:
                t = t.replace(tzinfo=dt.timezone(dt.timedelta(hours=3)))
        except ValueError:
            try:
                t = dt.datetime.combine(dt.date.fromisoformat(s[:10]), dt.time(), dt.timezone(dt.timedelta(hours=3)))
            except ValueError:
                continue
        if t > at:
            return t
    return None


def make_cards(records, projects=(), local_projects=None, at=None):
    """Карточки и отложенные улья. Возвращает (cards, hive_deferred[(id, until)])."""
    at = at or dt.datetime.now(dt.timezone.utc)
    projects = clean_projects(projects)
    cards, deferred = [], []
    for r in records:
        if not is_waiting(r):
            continue
        until = deferred_until(r, at)
        if until:
            deferred.append((r.get('id'), until))
            continue
        key = agent_key(r.get('owner'))
        full = full_text(r)
        head, options = summarize(full, r.get('title'))
        p, how = record_project(r, projects, local_projects)
        full_clean = tidy(full)
        cards.append({
            'id': r.get('id'),
            'agent': key,
            'agent_name': AGENTS[key],
            'question': head,
            'options': options,
            'full': full_clean if full_clean != head else '',
            'project': p['name'] if p else None,
            'project_id': p['id'] if p else None,
            'project_how': how,
            'status': r.get('status'),
            'paused': r.get('status') == 'paused',  # на паузе в улье без даты возврата
            'updated_at': r.get('updated_at') or r.get('waiting_on_at'),
        })
    # свежие сверху; паузы улья без даты — отдельной стопкой в конце (предложение Майкла 05.11, В-2)
    cards.sort(key=lambda c: str(c.get('updated_at') or ''), reverse=True)
    cards.sort(key=lambda c: c['paused'])
    return cards, deferred
