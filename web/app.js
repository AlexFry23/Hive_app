// Мини-приложение Александра. Без сборки и библиотек.
(function () {
  var tg = window.Telegram && window.Telegram.WebApp;
  var initData = tg ? tg.initData : '';
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); // свайп вниз не сворачивает приложение
  }
  var meta = { assignees: {}, agents: {} };

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function haptic(kind) {
    if (!tg || !tg.HapticFeedback) return;
    if (kind === 'ok') tg.HapticFeedback.notificationOccurred('success');
    else if (kind === 'err') tg.HapticFeedback.notificationOccurred('error');
    else if (kind === 'tick') tg.HapticFeedback.selectionChanged();
    else tg.HapticFeedback.impactOccurred('medium');
  }
  var toastTimer;
  function toast(text) {
    var t = $('toast'); t.textContent = text; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.hidden = true; }, 2600);
  }
  function confirmBox(text, cb) {
    if (tg && tg.showConfirm) { try { tg.showConfirm(text, cb); return; } catch (e) { /* старый клиент */ } }
    cb(window.confirm(text));
  }

  function api(path, body) {
    var opt = { headers: { Authorization: 'tma ' + initData } };
    if (body !== undefined) {
      opt.method = 'POST';
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        if (!r.ok) throw new Error(b.error || ('Ошибка ' + r.status));
        return b;
      });
    });
  }

  // ---------- даты ----------
  var TZ = 'Europe/Moscow';
  function dayDate(iso) { var p = iso.split('-'); return new Date(Date.UTC(+p[0], p[1] - 1, +p[2], 12)); }
  function dayLabel(iso, todayIso) {
    var s = dayDate(iso).toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'long', timeZone: TZ });
    if (iso === todayIso) return 'Сегодня, ' + s;
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  // время всегда по Москве, независимо от часового пояса телефона (сервер считает так же)
  var mskFmt = new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  function mskInput(ms) { return mskFmt.format(new Date(ms)).replace(' ', 'T').slice(0, 16); }
  function tomorrowIso() { return mskInput(Date.now() + 86400000).slice(0, 10); }

  // ---------- листы ----------
  var openSheet = null;
  function showSheet(id) { closeSheet(); openSheet = $(id); openSheet.hidden = false; $('sheet-bg').hidden = false; }
  function closeSheet() { if (openSheet) openSheet.hidden = true; openSheet = null; $('sheet-bg').hidden = true; }
  $('sheet-bg').addEventListener('click', closeSheet);
  document.querySelectorAll('.sheet .close').forEach(function (b) { b.addEventListener('click', closeSheet); });

  // ---------- вкладки ----------
  var tabs = document.querySelectorAll('.tabs button');
  tabs.forEach(function (b) {
    b.addEventListener('click', function () {
      tabs.forEach(function (x) { x.classList.toggle('active', x === b); });
      ['today', 'waiting', 'projects'].forEach(function (t) { $('tab-' + t).hidden = t !== b.dataset.tab; });
      if (b.dataset.tab === 'today') loadToday();
      if (b.dataset.tab === 'projects') loadProjects();
      haptic('tick');
    });
  });

  // ---------- «Ждёт меня» ----------
  var cards = [], busy = false;
  var card = $('card'), answer = $('card-answer');

  function current() { return cards[0]; }
  function render() {
    $('deck-status').textContent = '';
    if (!cards.length) { $('deck').hidden = true; $('empty').hidden = false; return; }
    $('empty').hidden = true; $('deck').hidden = false;
    var c = current();
    $('card-avatar').src = '/avatars/' + c.agent + '.svg';
    $('card-agent').textContent = c.agent_name;
    $('card-question').textContent = c.question;
    $('card-project').hidden = !c.project;
    $('card-project').textContent = c.project || '';
    answer.value = c.draft || '';
    $('send').disabled = !answer.value.trim();
    $('counter').textContent = 'осталось ' + cards.length;
    card.className = 'card'; card.style.transform = ''; card.style.opacity = '';
    $('stamp').hidden = true;
  }
  answer.addEventListener('input', function () { $('send').disabled = !answer.value.trim(); });

  function done(msg) {
    cards.shift(); haptic('ok'); if (msg) toast(msg);
    render();
  }
  function fail(e) { haptic('err'); toast(e.message); card.className = 'card back'; card.style.transform = ''; card.style.opacity = ''; $('stamp').hidden = true; }
  function fly(dx, dy) { card.className = 'card fly'; card.style.transform = 'translate(' + dx + 'px,' + dy + 'px) rotate(' + (dx / 20) + 'deg)'; card.style.opacity = '0'; }

  function act(verb, body, okMsg, flyTo) {
    if (busy || !current()) return;
    busy = true;
    var c = current();
    if (flyTo) fly(flyTo[0], flyTo[1]);
    api('/api/cards/' + encodeURIComponent(c.id) + '/' + verb, body || {}).then(function (r) {
      busy = false;
      done(r.status === 'already' ? 'Вопрос уже закрыт в улье' : okMsg);
    }).catch(function (e) { busy = false; fail(e); });
  }
  function doYes() { act('yes', { text: answer.value }, 'Записано: «Да»', [600, 0]); }
  function doNo() { act('no', { draft: answer.value }, 'Вернётся через 7 дней', [-600, 0]); }
  function doSnooze(body) { closeSheet(); body.draft = answer.value; act('snooze', body, 'Отложено', [0, -800]); }
  $('btn-yes').addEventListener('click', doYes);
  $('btn-no').addEventListener('click', doNo);
  $('btn-later').addEventListener('click', function () { $('snooze-date').min = tomorrowIso(); showSheet('sheet-snooze'); });
  $('btn-skip').addEventListener('click', function () { act('skip', { draft: answer.value }, 'Вернётся через ' + meta.skip_days + ' дн.', [0, 0]); });
  $('send').addEventListener('click', function () {
    if (!answer.value.trim()) return;
    act('answer', { text: answer.value }, 'Ответ записан', [600, 0]);
  });
  document.querySelectorAll('#sheet-snooze [data-days]').forEach(function (b) {
    b.addEventListener('click', function () { doSnooze({ days: +b.dataset.days }); });
  });
  $('snooze-date-ok').addEventListener('click', function () {
    var v = $('snooze-date').value; if (!v) { toast('Выберите дату'); return; }
    doSnooze({ date: v });
  });
  $('card-menu').addEventListener('click', function () { showSheet('sheet-menu'); });
  $('menu-remove').addEventListener('click', function () {
    closeSheet();
    confirmBox('Снять вопрос и больше не спрашивать?', function (ok) { if (ok) act('remove', {}, 'Снято', [0, 600]); });
  });

  // жесты: вправо — да, влево — нет, вверх — отложить; вниз не используется
  var TH = 90, start = null;
  function stamp(kind, text) { var s = $('stamp'); s.hidden = !kind; s.className = 'stamp ' + (kind || ''); s.textContent = text || ''; }
  card.addEventListener('pointerdown', function (e) {
    if (busy || e.target.closest('textarea,button')) return;
    start = { x: e.clientX, y: e.clientY, id: e.pointerId };
    card.setPointerCapture(e.pointerId); card.className = 'card';
  });
  card.addEventListener('pointermove', function (e) {
    if (!start || e.pointerId !== start.id) return;
    var dx = e.clientX - start.x, dy = Math.min(0, e.clientY - start.y);
    card.style.transform = 'translate(' + dx + 'px,' + dy + 'px) rotate(' + (dx / 20) + 'deg)';
    if (dx > TH && Math.abs(dx) > -dy) stamp('yes', 'ДА');
    else if (dx < -TH && Math.abs(dx) > -dy) stamp('no', 'НЕТ');
    else if (-dy > TH) stamp('later', 'ОТЛОЖИТЬ');
    else stamp(null);
  });
  function release(e) {
    if (!start || e.pointerId !== start.id) return;
    var dx = e.clientX - start.x, dy = Math.min(0, e.clientY - start.y); start = null;
    if (dx > TH && Math.abs(dx) > -dy) return doYes();
    if (dx < -TH && Math.abs(dx) > -dy) return doNo();
    stamp(null); card.className = 'card back'; card.style.transform = '';
    if (-dy > TH) { haptic(); $('snooze-date').min = tomorrowIso(); showSheet('sheet-snooze'); }
  }
  card.addEventListener('pointerup', release);
  card.addEventListener('pointercancel', release);

  function loadCards() {
    api('/api/cards').then(function (b) { cards = b.cards || []; render(); })
      .catch(function (e) { $('deck-status').textContent = e.message; });
  }

  // ---------- «Сегодня» ----------
  function loadToday() {
    $('today-status').textContent = 'Загружаю…';
    api('/api/today').then(function (b) {
      var list = $('today-list'); list.innerHTML = '';
      $('today-range').textContent = 'Неделя до ' + dayDate(b.to).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', timeZone: TZ });
      $('today-status').textContent = b.items.length ? '' : 'На неделю ничего нет.';
      var lastDay = null;
      b.items.forEach(function (it) {
        var head = it.overdue ? 'Просрочено' : dayLabel(it.day, b.from);
        if (head !== lastDay) { list.appendChild(el('li', 'day', head)); lastDay = head; }
        var li = el('li', it.overdue ? 'overdue' : '');
        if (it.kind === 'reminder') {
          var tick = el('button', 'tick', '✓'); tick.type = 'button';
          tick.addEventListener('click', function (ev) {
            ev.stopPropagation();
            api('/api/reminders/' + it.id + '/done', {}).then(function () { haptic('ok'); loadToday(); }).catch(function (e) { toast(e.message); });
          });
          li.appendChild(tick);
        }
        var main = el('div', 'main');
        main.appendChild(el('div', '', it.title));
        var sub = [];
        if (it.time) sub.push(it.time);
        if (it.kind === 'task') { sub.push('задача'); if (it.project) sub.push(it.project.replace(/_/g, ' ')); if (it.assignee) sub.push(meta.assignees[it.assignee] || it.assignee); }
        if (it.kind === 'hive') sub.push('срок в улье');
        if (it.overdue && it.day) sub.push(dayDate(it.day).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', timeZone: TZ }));
        main.appendChild(el('div', 'sub', sub.join(' · ')));
        li.appendChild(main);
        list.appendChild(li);
      });
    }).catch(function (e) { $('today-status').textContent = e.message; });
  }
  $('add-reminder').addEventListener('click', function () {
    $('rem-text').value = ''; $('rem-at').value = mskInput(Date.now() + 3600000).slice(0, 14) + '00';
    showSheet('sheet-reminder'); $('rem-text').focus();
  });
  $('rem-save').addEventListener('click', function () {
    var text = $('rem-text').value.trim(), at = $('rem-at').value;
    if (!text || !at) { toast('Нужны текст и время'); return; }
    api('/api/reminders', { text: text, at: at }).then(function () { closeSheet(); haptic('ok'); toast('Напомню'); loadToday(); })
      .catch(function (e) { toast(e.message); });
  });

  // ---------- «Проекты» ----------
  var project = null, editing = null;
  function loadProjects() {
    $('project-view').hidden = true; $('projects-view').hidden = false;
    api('/api/projects').then(function (b) {
      var list = $('projects'); list.innerHTML = '';
      (b.projects || []).forEach(function (p) {
        var li = el('li');
        li.appendChild(el('div', 'main', p.name.replace(/_/g, ' ')));
        if (p.open_tasks) li.appendChild(el('span', 'tag', String(p.open_tasks)));
        li.addEventListener('click', function () { openProject(p); });
        list.appendChild(li);
      });
      $('projects-status').textContent = (b.projects || []).length ? '' : 'Список проектов пока не получен.';
    }).catch(function (e) { $('projects-status').textContent = e.message; });
  }
  function openProject(p) {
    project = p;
    $('projects-view').hidden = true; $('project-view').hidden = false;
    $('project-title').textContent = p.name.replace(/_/g, ' ');
    loadTasks();
  }
  function loadTasks() {
    $('tasks-status').textContent = 'Загружаю…';
    api('/api/projects/' + encodeURIComponent(project.id) + '/tasks').then(function (b) {
      var list = $('tasks'); list.innerHTML = '';
      $('tasks-status').textContent = b.tasks.length ? '' : 'Задач нет.';
      b.tasks.forEach(function (t) {
        var li = el('li', t.done ? 'done' : '');
        var main = el('div', 'main');
        main.appendChild(el('div', '', t.title));
        var sub = [];
        if (t.due) sub.push('срок ' + dayDate(t.due).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', timeZone: TZ }));
        if (t.assignee) sub.push(meta.assignees[t.assignee] || t.assignee);
        main.appendChild(el('div', 'sub', sub.join(' · ')));
        li.appendChild(main);
        li.addEventListener('click', function () { editTask(t); });
        list.appendChild(li);
      });
    }).catch(function (e) { $('tasks-status').textContent = e.message; });
  }
  $('back').addEventListener('click', loadProjects);
  function fillAssignees(value) {
    var s = $('task-assignee'); s.innerHTML = '';
    s.appendChild(new Option('—', ''));
    Object.keys(meta.assignees).forEach(function (k) { s.appendChild(new Option(meta.assignees[k], k)); });
    s.value = value || '';
  }
  function editTask(t) {
    editing = t;
    $('task-sheet-title').textContent = t ? 'Задача' : 'Новая задача';
    $('task-title').value = t ? t.title : '';
    $('task-due').value = t && t.due ? t.due : '';
    fillAssignees(t ? t.assignee : '');
    $('task-done').checked = !!(t && t.done);
    $('task-done').parentNode.hidden = !t;
    $('task-delete').hidden = !t;
    showSheet('sheet-task');
  }
  $('add-task').addEventListener('click', function () { editTask(null); $('task-title').focus(); });
  $('task-save').addEventListener('click', function () {
    var body = { title: $('task-title').value.trim(), due: $('task-due').value, assignee: $('task-assignee').value };
    if (!body.title) { toast('Нужно название'); return; }
    var req;
    if (editing) { body.done = $('task-done').checked; req = api('/api/tasks/' + editing.id, body); }
    else { body.project_id = project.id; req = api('/api/tasks', body); }
    req.then(function () { closeSheet(); haptic('ok'); loadTasks(); }).catch(function (e) { toast(e.message); });
  });
  $('task-delete').addEventListener('click', function () {
    confirmBox('Удалить задачу?', function (ok) {
      if (!ok) return;
      api('/api/tasks/' + editing.id + '/delete', {}).then(function () { closeSheet(); loadTasks(); }).catch(function (e) { toast(e.message); });
    });
  });

  // ---------- запуск ----------
  if (!initData) {
    $('deck-status').textContent = 'Откройте приложение кнопкой в боте Telegram.';
    return;
  }
  api('/api/meta').then(function (m) { meta = m; }).catch(function () {});
  loadCards();
})();
