"""Отбор вопросов «Ждёт меня» из сводки реестра и подготовка карточек. Без сети и файлов."""
import re

# Статусы, при которых вопрос реально ждёт Александра.
# needs_review (спорный повторный ответ) и paused/answered/archived в колоду не идут.
OPEN_STATUSES = {'open', 'doing', 'partial', 'proposed'}

# Пять текущих агентов (решение 20). Ключ — имя файла аватара в web/avatars/.
AGENTS = {
    'michael': 'Michael',
    'pam': 'Pam',
    'oscar': 'Oscar',
    'kevin': 'Kevin',
    'dwight': 'Dwight',
}

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
    if r.get('status') not in OPEN_STATUSES:
        return False
    qa = r.get('humanqa') or {}
    if qa.get('a') or qa.get('withdrawn'):
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


def project_label(r, projects):
    """Метка проекта — только если название папки проекта прямо встречается в теме или заголовке.
    Нет совпадения — без метки (решение 17)."""
    hay = ' ' + _norm(str(r.get('topic') or '') + ' ' + str(r.get('title') or '')) + ' '
    best = None
    for p in projects:
        n = _norm(p['name'])
        if n and (' ' + n + ' ') in hay and (best is None or len(n) > len(_norm(best))):
            best = p['name']
    return best


def question_text(r):
    qa = r.get('humanqa') or {}
    return str(qa.get('q') or r.get('title') or '').strip()


def make_cards(records, projects=()):
    projects = clean_projects(projects)
    cards = []
    for r in records:
        if not is_waiting(r):
            continue
        key = agent_key(r.get('owner'))
        cards.append({
            'id': r.get('id'),
            'agent': key,
            'agent_name': AGENTS[key],
            'question': question_text(r),
            'project': project_label(r, projects),
            'status': r.get('status'),
            'updated_at': r.get('updated_at') or r.get('waiting_on_at'),
        })
    cards.sort(key=lambda c: str(c.get('updated_at') or ''), reverse=True)
    return cards
