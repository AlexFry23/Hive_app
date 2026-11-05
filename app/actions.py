"""Жесты карточки -> реестр улья и база приложения (решение 12).

Запись в реестр — как в WORKFLOW.md улья: свежая ревизия записи, operation_id,
expected_revision; «сохранено» — только после обратного чтения. 409 — перечитать и
повторить; нет ответа — повторить тот же запрос с тем же operation_id (реестр
узнаёт его по журналу и не запишет дважды).
"""
import datetime as dt

from cards import AGENT_NAMES_RU, is_waiting
from registry import Conflict, RegistryError, Unconfirmed
from store import MSK, iso, msk_day_start, now

# Тот же путь, что у панели «Вопросы»: только так реестр шлёт штатную квитанцию «Принял ответ…».
SOURCE = {'path': 'Личный сайт — ввод Александра', 'line': 1, 'via': 'мини-приложение'}
AUTHOR = 'Александр'
REMOVE_REASON = 'Снято Александром: больше не спрашивать'
NO_DAYS = 3  # «Нет» = «не сейчас» = отложить на 3 дня (решение 4, 05.11)


class ActionError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _readback_ok(kind, rec, result):
    if (rec.get('revision') or 0) < (result.get('record_revision') or 0):
        return False
    qa = rec.get('humanqa') or {}
    if kind == 'remove':
        return rec.get('status') == 'archived' or bool(qa.get('withdrawn'))
    return qa.get('a') is not None


def write(registry, store, record_id, kind, build, check=None, require_waiting=True):
    """build(record) -> запрос без operation_id/expected_revision."""
    try:
        rec = registry.record(record_id)
    except RegistryError as e:
        raise ActionError('Реестр недоступен, ничего не записано: ' + str(e), 503)
    if require_waiting and not is_waiting(rec):
        return {'status': 'already', 'message': 'Вопрос уже закрыт в улье'}
    req = dict(build(rec), record_id=record_id)
    op = store.op_start(record_id, kind, req)
    req['operation_id'] = op
    result = None
    for attempt in range(3):
        req['expected_revision'] = rec.get('revision')
        try:
            result = registry.update(req)
            break
        except Conflict:
            rec = registry.record(record_id)
            if require_waiting and not is_waiting(rec):
                store.op_finish(op, 'failed', {'reason': 'вопрос закрыт другим путём'})
                return {'status': 'already', 'message': 'Вопрос уже закрыт в улье'}
            # новая ревизия -> другой запрос -> нужен новый operation_id
            store.op_finish(op, 'failed', {'reason': 'конфликт версии, повтор'})
            op = store.op_start(record_id, kind, req)
            req['operation_id'] = op
        except Unconfirmed:
            continue  # тот же operation_id и тот же запрос — безопасный повтор
        except RegistryError as e:
            store.op_finish(op, 'failed', {'error': str(e)})
            raise ActionError('Реестр отказал: ' + str(e), 502)
    if result is None:
        store.op_finish(op, 'pending', {'reason': 'нет подтверждения'})
        raise ActionError('Реестр не подтвердил запись. Ничего не повторяйте — проверю сам.', 503)
    if result.get('answer_accepted') is False or result.get('conflict'):
        store.op_finish(op, 'failed', result)
        raise ActionError('Реестр записал расхождение вместо ответа — Майкл разберёт', 409)
    try:
        back = registry.record(record_id)
    except RegistryError:
        store.op_finish(op, 'pending', {'result': result, 'reason': 'нет обратного чтения'})
        raise ActionError('Записано, но не подтверждено обратным чтением', 503)
    if not (check(back) if check else _readback_ok(kind, back, result)):
        store.op_finish(op, 'failed', {'result': result, 'reason': 'обратное чтение не совпало'})
        raise ActionError('Обратное чтение не подтвердило запись', 502)
    store.op_finish(op, 'saved', result)
    return {'status': 'saved', 'operation_id': op}


def stamp():
    return iso(now())


def assignee_line(assignee):
    """Исполнитель строкой в тексте ответа (решение 25): реестр не принимает поле исполнителя."""
    if not assignee:
        return ''
    if assignee == 'me':
        return 'Делаю сам.'
    if assignee not in AGENT_NAMES_RU:
        raise ActionError('Исполнитель — только из списка')
    return 'Делает: ' + AGENT_NAMES_RU[assignee] + '.'


def _join(*parts):
    out = ''
    for p in parts:
        p = (p or '').strip()
        if not p:
            continue
        if out and out[-1] not in '.!?':
            out += '.'
        out = (out + ' ' + p).strip()
    return out


def yes(registry, store, record_id, text='', assignee=None):
    answer = _join('Да', text, assignee_line(assignee))
    out = write(registry, store, record_id, 'yes', lambda rec: {
        'action': 'answer', 'text': answer, 'coverage': 'full', 'author': AUTHOR,
        'source': SOURCE, 'source_date': stamp()})
    store.set_card(record_id, 'answered', draft='')
    return out


def answer(registry, store, record_id, text, assignee=None):
    text = (text or '').strip()
    if not text:
        raise ActionError('Пустой ответ')
    full = _join(text, assignee_line(assignee))
    out = write(registry, store, record_id, 'answer', lambda rec: {
        'action': 'answer', 'text': full, 'coverage': 'full', 'author': AUTHOR,
        'source': SOURCE, 'source_date': stamp()})
    store.set_card(record_id, 'answered', draft='')
    return out


def remove(registry, store, record_id):
    out = write(registry, store, record_id, 'remove', lambda rec: {
        'action': 'withdraw_question', 'reason': REMOVE_REASON, 'source': SOURCE, 'source_date': stamp()})
    store.set_card(record_id, 'removed')
    return out


def set_project(registry, store, record_id, project_id):
    """Проект карточки: сначала в базу приложения (решение 26), затем попытка записать в улей
    (`edit`, `changes.project` — правку ядра делает Майкл, решение 28). Пока улей не принимает —
    остаётся только в приложении, без ошибки для Александра."""
    store.set_record_project(record_id, project_id)
    try:
        out = write(registry, store, record_id, 'project', lambda rec: {
            'action': 'edit', 'changes': {'project': project_id}, 'source': SOURCE,
            'reason': 'Проект выбран Александром в мини-приложении'},
            check=lambda rec: rec.get('project') == project_id)
    except ActionError as e:
        return {'status': 'saved_local', 'project_id': project_id, 'registry': str(e)}
    if out.get('status') == 'saved':
        return {'status': 'saved', 'project_id': project_id, 'registry': 'записано в улей'}
    return {'status': 'saved_local', 'project_id': project_id, 'registry': out.get('message')}


def plan_date(registry, store, record_id, days=None, date=None):
    """Планёрка (решение 29): дата, когда вернуться к записи на паузе. Сначала в базу приложения —
    вопрос вернётся в колоду, дело улья — в «Пора решить» Планёрки. Затем попытка записать в улей
    `not_before` (его уже понимает шлюз вопросов улья); пока улей не принимает — только в приложении."""
    until = snooze_until(days=days, date=date)
    store.set_card(record_id, 'snoozed', until=until)
    day = until.astimezone(MSK).date().isoformat()
    try:
        out = write(registry, store, record_id, 'plan', lambda rec: {
            'action': 'edit', 'changes': {'not_before': day}, 'source': SOURCE,
            'reason': 'Планёрка: Александр назначил дату возврата'},
            check=lambda rec: str(rec.get('not_before') or '')[:10] == day, require_waiting=False)
        where = 'saved' if out.get('status') == 'saved' else 'saved_local'
    except ActionError:
        where = 'saved_local'
    return {'status': where, 'until': iso(until), 'day': day}


def plan_now(store, record_id):
    """Планёрка: «Решить сейчас» — убрать дату приложения, вопрос сразу в колоду."""
    store.clear_card(record_id)
    return {'status': 'now'}


def snooze_until(days=None, date=None, at=None):
    """«Через N дней» и «выбрать дату» — карточка возвращается в начале того дня (по Москве)."""
    at = at or now()
    if date:
        try:
            day = dt.date.fromisoformat(str(date)[:10])
        except ValueError:
            raise ActionError('Неверная дата')
    else:
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise ActionError('Неверный срок')
        if days < 1 or days > 366:
            raise ActionError('Неверный срок')
        day = at.astimezone(MSK).date() + dt.timedelta(days=days)
    if day <= at.astimezone(MSK).date():
        raise ActionError('Дата должна быть позже сегодняшней')
    return msk_day_start(day)


def no(store, record_id, draft=None):
    until = snooze_until(days=NO_DAYS)
    store.set_card(record_id, 'snoozed', until=until, draft=draft)
    return {'status': 'snoozed', 'until': iso(until)}


def snooze(store, record_id, days=None, date=None, draft=None):
    until = snooze_until(days=days, date=date)
    store.set_card(record_id, 'snoozed', until=until, draft=draft)
    return {'status': 'snoozed', 'until': iso(until)}


def skip(store, record_id, skip_days, draft=None):
    until = now() + dt.timedelta(days=skip_days)
    store.set_card(record_id, 'skipped', until=until, draft=draft)
    return {'status': 'skipped', 'until': iso(until)}
