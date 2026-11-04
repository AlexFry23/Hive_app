// Этап 1: только чтение. Колода «Ждёт меня» листается кнопками; жесты — этап 2.
(function () {
  var tg = window.Telegram && window.Telegram.WebApp;
  var initData = tg ? tg.initData : '';
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.disableVerticalSwipes) tg.disableVerticalSwipes();
  }

  function $(id) { return document.getElementById(id); }

  function api(path) {
    return fetch(path, { headers: { Authorization: 'tma ' + initData } }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) throw new Error(body.error || ('Ошибка ' + r.status));
        return body;
      });
    });
  }

  // вкладки
  var tabs = document.querySelectorAll('.tabs button');
  tabs.forEach(function (b) {
    b.addEventListener('click', function () {
      tabs.forEach(function (x) { x.classList.toggle('active', x === b); });
      ['today', 'waiting', 'projects'].forEach(function (t) { $('tab-' + t).hidden = t !== b.dataset.tab; });
      if (b.dataset.tab === 'projects') loadProjects();
      if (tg && tg.HapticFeedback) tg.HapticFeedback.selectionChanged();
    });
  });

  // колода
  var cards = [], pos = 0;
  function show() {
    var c = cards[pos];
    $('card-avatar').src = '/avatars/' + c.agent + '.svg';
    $('card-agent').textContent = c.agent_name;
    $('card-question').textContent = c.question;
    $('card-project').hidden = !c.project;
    $('card-project').textContent = c.project || '';
    $('counter').textContent = (pos + 1) + ' из ' + cards.length;
    $('prev').disabled = pos === 0;
    $('next').disabled = pos === cards.length - 1;
  }
  $('prev').addEventListener('click', function () { if (pos > 0) { pos--; show(); } });
  $('next').addEventListener('click', function () { if (pos < cards.length - 1) { pos++; show(); } });

  function loadCards() {
    if (!initData) { $('deck-status').textContent = 'Откройте приложение кнопкой в боте Telegram.'; return; }
    api('/api/cards').then(function (body) {
      cards = body.cards || [];
      $('deck-status').textContent = '';
      if (!cards.length) { $('empty').hidden = false; return; }
      $('deck').hidden = false;
      pos = 0; show();
    }).catch(function (e) { $('deck-status').textContent = e.message; });
  }

  var projectsLoaded = false;
  function loadProjects() {
    if (projectsLoaded || !initData) {
      if (!initData) $('projects-status').textContent = 'Откройте приложение кнопкой в боте Telegram.';
      return;
    }
    api('/api/projects').then(function (body) {
      projectsLoaded = true;
      var list = $('projects'); list.innerHTML = '';
      (body.projects || []).forEach(function (p) {
        var li = document.createElement('li'); li.textContent = p.name.replace(/_/g, ' '); list.appendChild(li);
      });
      $('projects-status').textContent = (body.projects || []).length ? '' : 'Список проектов пока не получен.';
    }).catch(function (e) { $('projects-status').textContent = e.message; });
  }

  loadCards();
})();
