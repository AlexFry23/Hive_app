// Мини-приложение Александра. Без сборки и библиотек.
(function () {
  var tg = window.Telegram && window.Telegram.WebApp;
  var initData = tg ? tg.initData : '';
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); // свайп вниз не сворачивает приложение
  }
  var meta = { assignees: {}, agents: {}, skip_days: 3 };
  var ASSIGNEE_ORDER = ['michael', 'pam', 'oscar', 'kevin', 'dwight', 'me'];

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function nice(name) { return String(name || '').replace(/_/g, ' '); }
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
  function store(key, value) { try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { return null; } }

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

  // ---------- даты (всегда по Москве) ----------
  var TZ = 'Europe/Moscow';
  function dayDate(iso) { var p = iso.split('-'); return new Date(Date.UTC(+p[0], p[1] - 1, +p[2], 12)); }
  function shortDay(iso) {
    var o = { day: 'numeric', month: 'long', timeZone: TZ };
    if (iso.slice(0, 4) !== mskInput(Date.now()).slice(0, 4)) o.year = 'numeric';
    return dayDate(iso).toLocaleDateString('ru-RU', o);
  }
  function dayLabel(iso, todayIso) {
    var s = dayDate(iso).toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'long', timeZone: TZ });
    if (iso === todayIso) return 'Сегодня, ' + s;
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  var mskFmt = new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  function mskInput(ms) { return mskFmt.format(new Date(ms)).replace(' ', 'T').slice(0, 16); }
  function tomorrowIso() { return mskInput(Date.now() + 86400000).slice(0, 10); }

  // ---------- листы ----------
  var openSheet = null;
  function showSheet(id) { closeSheet(); openSheet = $(id); openSheet.hidden = false; $('sheet-bg').hidden = false; }
  function closeSheet() { if (openSheet) openSheet.hidden = true; openSheet = null; $('sheet-bg').hidden = true; }
  $('sheet-bg').addEventListener('click', closeSheet);
  document.querySelectorAll('.sheet .close').forEach(function (b) { b.addEventListener('click', closeSheet); });
  $('help').addEventListener('click', function () { showSheet('sheet-help'); });

  // ---------- вкладки ----------
  var tabs = document.querySelectorAll('.tabs button');
  tabs.forEach(function (b) {
    b.addEventListener('click', function () {
      tabs.forEach(function (x) { x.classList.toggle('active', x === b); });
      ['today', 'waiting', 'projects'].forEach(function (t) { $('tab-' + t).hidden = t !== b.dataset.tab; });
      if (b.dataset.tab === 'today') loadToday();
      if (b.dataset.tab === 'projects') loadProjects();
      if (b.dataset.tab === 'waiting') loadCards();
      haptic('tick');
    });
  });

  // ---------- «Ждёт меня» ----------
  var cards = [], deferred = { count: 0, next: null }, busy = false, chosen = null, projectList = null;
  var card = $('card'), answer = $('card-answer');

  function current() { return cards[0]; }
  function deferredText() {
    if (!deferred.count) return '';
    var t = 'Ещё отложено: ' + deferred.count + (deferred.next ? ', следующий вернётся ' + shortDay(deferred.next) : '');
    return /\.$/.test(t) ? t : t + '.';
  }
  function renderAssignees(c) {
    var box = $('assignees'); box.innerHTML = '';
    chosen = c.assignee || c.agent;
    ASSIGNEE_ORDER.forEach(function (k) {
      var b = el('button'); b.type = 'button';
      if (k === 'me') b.appendChild(el('span', 'me', 'Я'));
      else { var i = el('img'); i.src = '/img/' + k + '.webp'; i.alt = ''; b.appendChild(i); }
      b.appendChild(el('span', '', k === 'me' ? 'сам' : (meta.agents[k] || k)));
      b.classList.toggle('on', k === chosen);
      b.addEventListener('click', function () {
        chosen = k; c.assignee = k;
        box.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); });
        haptic('tick');
      });
      box.appendChild(b);
    });
  }
  function render() {
    $('deck-status').textContent = '';
    $('counter').textContent = cards.length ? 'Ждут ответа: ' + cards.length : '';
    if (!cards.length) {
      $('deck').hidden = true; $('empty').hidden = false;
      $('empty-deferred').textContent = deferredText() || 'Отложенных нет.';
      return;
    }
    $('empty').hidden = true; $('deck').hidden = false;
    var c = current();
    $('card-avatar').src = '/img/' + c.agent + '.webp';
    $('card-agent').textContent = c.agent_name;
    $('card-question').textContent = c.question;
    var ol = $('card-options'); ol.innerHTML = '';
    (c.options || []).forEach(function (o) { ol.appendChild(el('li', '', o)); });
    $('more').hidden = !c.full; $('card-full').hidden = true; $('card-full').textContent = c.full || ''; $('more').textContent = 'подробнее';
    var chip = $('card-project');
    chip.textContent = c.project ? nice(c.project) : '+ Проект';
    chip.className = 'chip' + (c.project ? '' : ' empty');
    $('card-paused').hidden = !c.paused;
    renderAssignees(c);
    answer.value = c.draft || '';
    $('send').disabled = !answer.value.trim();
    $('deferred').textContent = deferredText();
    card.className = 'card'; card.style.transform = ''; card.style.opacity = '';
    $('stamp').hidden = true;
  }
  answer.addEventListener('input', function () { $('send').disabled = !answer.value.trim(); });
  $('more').addEventListener('click', function () {
    var f = $('card-full'); f.hidden = !f.hidden; $('more').textContent = f.hidden ? 'подробнее' : 'свернуть';
  });

  function done(msg, later) {
    cards.shift(); haptic('ok'); if (msg) toast(msg);
    if (later) { deferred.count++; if (!deferred.next || later < deferred.next) deferred.next = later; }
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
      var later = r.until ? mskInput(Date.parse(r.until)).slice(0, 10) : null;
      done(r.status === 'already' ? 'Вопрос уже закрыт в улье' : okMsg, later);
    }).catch(function (e) { busy = false; fail(e); });
  }
  function doYes() { act('yes', { text: answer.value, assignee: chosen }, 'Записано: «Да»', [600, 0]); }
  function doNo() { act('no', { draft: answer.value }, 'Не сейчас — вернётся через 3 дня', [-600, 0]); }
  function doSnooze(body) { closeSheet(); body.draft = answer.value; act('snooze', body, 'Отложено', [0, -800]); }
  function askSnooze() { $('snooze-date').min = tomorrowIso(); showSheet('sheet-snooze'); }
  $('btn-yes').addEventListener('click', doYes);
  $('btn-no').addEventListener('click', doNo);
  $('btn-later').addEventListener('click', askSnooze);
  $('send').addEventListener('click', function () {
    if (!answer.value.trim()) return;
    act('answer', { text: answer.value, assignee: chosen }, 'Ответ записан', [600, 0]);
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

  // выбор проекта на карточке (хранится в приложении, решение 26)
  function withProjects(cb) {
    if (projectList) return cb(projectList);
    api('/api/projects').then(function (b) { projectList = b.projects || []; cb(projectList); }).catch(function (e) { toast(e.message); });
  }
  $('card-project').addEventListener('click', function () {
    var c = current(); if (!c) return;
    withProjects(function (list) {
      var ul = $('project-choices'); ul.innerHTML = '';
      var none = el('li', c.project_id ? '' : 'on'); none.appendChild(el('div', 'main', 'Без проекта')); ul.appendChild(none);
      none.addEventListener('click', function () { setProject(c, null, null); });
      list.forEach(function (p) {
        var li = el('li', p.id === c.project_id ? 'on' : ''); li.appendChild(el('div', 'main', nice(p.name)));
        li.addEventListener('click', function () { setProject(c, p.id, p.name); });
        ul.appendChild(li);
      });
      showSheet('sheet-project');
    });
  });
  function setProject(c, id, name) {
    api('/api/cards/' + encodeURIComponent(c.id) + '/project', { project_id: id }).then(function (r) {
      c.project_id = id; c.project = name; closeSheet(); haptic('ok'); render();
      toast(r.status === 'saved' ? 'Проект записан в улей' : 'Проект сохранён в приложении');
    }).catch(function (e) { toast(e.message); });
  }

  // жесты: вправо — да, влево — нет, вверх — отложить; вниз не используется
  var TH = 90, start = null;
  function stamp(kind, text) { var s = $('stamp'); s.hidden = !kind; s.className = 'stamp ' + (kind || ''); s.textContent = text || ''; }
  card.addEventListener('pointerdown', function (e) {
    if (busy || e.target.closest('textarea,button,.full')) return;
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
    if (-dy > TH) { haptic(); askSnooze(); }
  }
  card.addEventListener('pointerup', release);
  card.addEventListener('pointercancel', release);

  function loadCards() {
    if (busy) return;
    api('/api/cards').then(function (b) {
      cards = b.cards || []; deferred = b.deferred || { count: 0, next: null }; render();
    }).catch(function (e) { $('deck-status').textContent = e.message; });
  }

  // ---------- «Сегодня» ----------
  function loadToday() {
    $('today-status').textContent = 'Загружаю…';
    api('/api/today').then(function (b) {
      var list = $('today-list'); list.innerHTML = '';
      $('today-range').textContent = 'Неделя до ' + shortDay(b.to);
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
        if (it.kind === 'task') { sub.push('задача'); if (it.project) sub.push(nice(it.project)); if (it.assignee) sub.push(meta.assignees[it.assignee] || it.assignee); }
        if (it.kind === 'hive') sub.push('срок в улье');
        if (it.overdue && it.day) sub.push(shortDay(it.day));
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
  function projectRow(p) {
    var li = el('li');
    var main = el('div', 'main');
    main.appendChild(el('div', '', nice(p.name)));
    var sub = [];
    if (p.open) sub.push('в работе ' + p.open);
    if (p.closed) sub.push('закрыто за неделю ' + p.closed);
    if (p.open_tasks) sub.push('задач ' + p.open_tasks);
    if (sub.length) main.appendChild(el('div', 'sub', sub.join(' · ')));
    li.appendChild(main);
    if (p.waiting) li.appendChild(el('span', 'tag hot', 'ждут ' + p.waiting));
    li.addEventListener('click', function () { openProject(p); });
    return li;
  }
  function loadProjects() {
    $('project-view').hidden = true; $('projects-view').hidden = false;
    api('/api/projects').then(function (b) {
      projectList = b.projects || [];
      var list = $('projects'), others = $('others'); list.innerHTML = ''; others.innerHTML = '';
      var rest = 0;
      projectList.forEach(function (p) {
        if (p.empty) { others.appendChild(projectRow(p)); rest++; } else list.appendChild(projectRow(p));
      });
      $('others-toggle').hidden = !rest;
      $('others-toggle').textContent = (others.hidden ? 'Остальные' : 'Скрыть остальные') + ' (' + rest + ')';
      $('projects-status').textContent = projectList.length ? (list.children.length ? '' : 'Пока ни в одном проекте нет вопросов и задач.') : 'Список проектов пока не получен.';
    }).catch(function (e) { $('projects-status').textContent = e.message; });
  }
  $('others-toggle').addEventListener('click', function () {
    var o = $('others'); o.hidden = !o.hidden;
    $('others-toggle').textContent = (o.hidden ? 'Остальные' : 'Скрыть остальные') + ' (' + o.children.length + ')';
  });
  function openProject(p) {
    project = p;
    $('projects-view').hidden = true; $('project-view').hidden = false;
    $('project-title').textContent = nice(p.name);
    loadTasks();
  }
  function section(title, items, render) {
    if (!items.length) return;
    var box = $('project-sections');
    box.appendChild(el('h4', '', title + ' · ' + items.length));
    var ul = el('ul', 'list'); items.forEach(function (i) { ul.appendChild(render(i)); }); box.appendChild(ul);
  }
  function hiveRow(i) {
    var li = el('li'); li.style.cursor = 'default';
    var main = el('div', 'main'); main.appendChild(el('div', '', i.title));
    var sub = [i.type === 'question' ? 'вопрос' : 'задача улья'];
    if (i.due) sub.push('срок ' + shortDay(String(i.due).slice(0, 10)));
    main.appendChild(el('div', 'sub', sub.join(' · ')));
    li.appendChild(main);
    return li;
  }
  function taskRow(t) {
    var li = el('li', t.done ? 'done' : '');
    var main = el('div', 'main');
    main.appendChild(el('div', '', t.title));
    var sub = [];
    if (t.due) sub.push('срок ' + shortDay(t.due));
    if (t.assignee) sub.push(meta.assignees[t.assignee] || t.assignee);
    main.appendChild(el('div', 'sub', sub.join(' · ')));
    li.appendChild(main);
    li.addEventListener('click', function () { editTask(t); });
    return li;
  }
  function loadTasks() {
    $('tasks-status').textContent = 'Загружаю…'; $('project-sections').innerHTML = '';
    api('/api/projects/' + encodeURIComponent(project.id) + '/tasks').then(function (b) {
      var g = b.registry || { waiting: [], open: [], closed: [] };
      var total = b.tasks.length + g.waiting.length + g.open.length + g.closed.length;
      $('tasks-status').textContent = total ? '' : 'Здесь пока пусто.';
      section('Ждут тебя', g.waiting, hiveRow);
      section('В работе в улье', g.open, hiveRow);
      section('Мои задачи', b.tasks, taskRow);
      section('Закрыто за 7 дней', g.closed, hiveRow);
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
  api('/api/meta').then(function (m) { meta = m; if (cards.length) render(); }).catch(function () {});
  loadCards();
  if (!store('help-seen')) { showSheet('sheet-help'); store('help-seen', '1'); }
})();
