/* ============================================================
 * LiaraPilot — frontend logic
 * Vanilla JS, single IIFE, no globals, no dependencies.
 * API CONTRACT v1 (+v2):
 *   intake (zip | github)  ->  assessment  ->  run (SSE timeline,
 *   human approval gates)  ->  report  ->  star agent (periodic care)
 * All technical content (logs, commands, URLs, paths, env keys,
 * cost numbers) renders inside .ltr-block so it stays readable
 * inside the RTL page. Realtime: SSE first, snapshot-polling
 * fallback when a proxy eats the stream (id-dedupe makes replay
 * idempotent).
 * ============================================================ */
(function () {
  'use strict';

  /* ----------------------------------------------------------
   * Tiny helpers
   * ---------------------------------------------------------- */

  function $(sel, root) { return (root || document).querySelector(sel); }

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  var FA_DIGITS = '۰۱۲۳۴۵۶۷۸۹';
  function faDigits(v) { return String(v).replace(/[0-9]/g, function (d) { return FA_DIGITS[Number(d)]; }); }

  function faNum(v) {
    var n = Number(v);
    if (!Number.isFinite(n)) return '—';
    try { return n.toLocaleString('fa-IR'); }
    catch (e) { return faDigits(String(n)); }
  }

  function faMoney(v) { return faNum(v) + ' تومان'; }

  function shallowCopy(obj) {
    var out = {};
    for (var k in obj) { if (Object.prototype.hasOwnProperty.call(obj, k)) out[k] = obj[k]; }
    return out;
  }

  /** Create an element; `html` is innerHTML (caller must pre-escape). */
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  var UNVERIFIED_BADGE = 'قیمت تخمینی — نیازمند تأیید';

  /* ----------------------------------------------------------
   * HTTP + API (contract v1)
   * ---------------------------------------------------------- */

  function req(method, url, body) {
    var opts = { method: method, headers: {} };
    if (body instanceof FormData) {
      opts.body = body; // browser sets the multipart boundary itself
    } else if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
        if (!res.ok) {
          var detail = (data && data.detail != null) ? String(data.detail) : '';
          if (res.status === 404 && /session_expired|expired|not_found|not found/i.test(detail)) {
            showExpired();
            var exp = new Error('session_expired');
            exp.kind = 'expired'; exp.status = 404; exp.detail = detail;
            throw exp;
          }
          var err = new Error(detail || ('http_' + res.status));
          err.kind = 'http'; err.status = res.status; err.detail = detail;
          throw err;
        }
        return data;
      });
    }, function () {
      var net = new Error('network_error');
      net.kind = 'network';
      throw net;
    });
  }

  function isExpired(e) { return !!(e && e.kind === 'expired'); }

  var api = {
    upload: function (fd) { return req('POST', '/api/upload', fd); },
    github: function (body) { return req('POST', '/api/github', body); },
    account: function () { return req('GET', '/api/account'); },
    accountCheck: function (body) { return req('POST', '/api/account/check', body); },
    demo: function (body) { return req('POST', '/api/sessions/demo', body); },
    assessment: function () { return req('GET', '/api/assessment'); },
    answers: function (id, body) { return req('POST', '/api/sessions/' + encodeURIComponent(id) + '/answers', body); },
    start: function (id) { return req('POST', '/api/sessions/' + encodeURIComponent(id) + '/start'); },
    snapshot: function (id) { return req('GET', '/api/sessions/' + encodeURIComponent(id)); },
    approve: function (id, body) { return req('POST', '/api/sessions/' + encodeURIComponent(id) + '/approve', body); },
    level: function (id, body) { return req('POST', '/api/sessions/' + encodeURIComponent(id) + '/level', body); },
    cancel: function (id) { return req('POST', '/api/sessions/' + encodeURIComponent(id) + '/cancel'); },
    ask: function (id, body) { return req('POST', '/api/sessions/' + encodeURIComponent(id) + '/ask', body); },
    ops: function (id, body) { return req('POST', '/api/sessions/' + encodeURIComponent(id) + '/ops', body); },
    monitor: function (id) { return req('GET', '/api/sessions/' + encodeURIComponent(id) + '/monitor'); },
    careState: function (id) { return req('GET', '/api/sessions/' + encodeURIComponent(id) + '/care'); },
    careSet: function (id, body) { return req('POST', '/api/sessions/' + encodeURIComponent(id) + '/care', body); },
    careRun: function (id) { return req('POST', '/api/sessions/' + encodeURIComponent(id) + '/care/run'); },
    careBrief: function (id) { return req('POST', '/api/sessions/' + encodeURIComponent(id) + '/care/brief', {}); }
  };

  /* ----------------------------------------------------------
   * State
   * ---------------------------------------------------------- */

  var store = {
    screen: 'upload',
    sessionId: null,
    demo: false,
    level: 'beginner',
    answers: {},
    questions: [],
    expertSkip: {},
    eventsSeen: false,
    maxI: 0,
    report: null,
    hasPlan: false,
    planEl: null,
    deployGroupEl: null,
    risks: [],
    cancelled: false,
    pendingApproval: null,
    care: null,
    careShowAll: false,
    es: null,
    sseTimer: null,
    sseWatchdog: null,
    sseAttempts: 0,
    sseGot: 0,
    pollTimer: null,
    pollBusy: false,
    monitorTimer: null,
    careTimer: null,
    hydrating: false
  };

  /* ----------------------------------------------------------
   * Screens / top bar
   * ---------------------------------------------------------- */

  var SCREENS = ['upload', 'assessment', 'run', 'report', 'expired'];

  function showScreen(name) {
    store.screen = name;
    SCREENS.forEach(function (s) {
      var node = $('#screen-' + s);
      if (node) node.hidden = (s !== name);
    });
    if (name === 'run') bindAsk();
    window.scrollTo(0, 0);
  }

  function updateSampleBadge() { $('#sample-badge').hidden = (store.demo !== true); }

  function showExpired() {
    if (store.screen === 'expired') return;
    stopSSE();
    stopCarePolling();
    stopMonitorPolling();
    closeModal();
    showScreen('expired');
  }

  function disableInputs(dis) {
    $('#btn-cancel').disabled = dis || store.cancelled;
    $('#lvl-beginner').disabled = dis;
    $('#lvl-expert').disabled = dis;
  }

  /* ----------------------------------------------------------
   * Account chip — real Liara account status (read-only)
   * ---------------------------------------------------------- */

  function loadAccount() {
    api.account().then(function (a) {
      var chip = $('#account-chip');
      store.account = a || null;
      if (!a || a.connected !== true) { chip.hidden = true; return; }
      var parts = ['لیارا · متصل'];
      if (a.balance_toman != null) parts.push('موجودی ' + faMoney(a.balance_toman));
      $('#account-text').textContent = parts.join(' — ');
      if (a.default_plan && a.plan_price_hourly_toman != null) {
        chip.title = 'پلن پیش‌فرض: ' + a.default_plan +
          ' — ' + faNum(a.plan_price_hourly_toman) + ' تومان در ساعت (قیمت واقعی از API)';
      }
      chip.hidden = false;
    }).catch(function () { $('#account-chip').hidden = true; });
  }

  /* ----------------------------------------------------------
   * A) Intake screen
   * ---------------------------------------------------------- */

  var DZ_TITLE = 'فایل zip پروژه را اینجا رها کن';
  var DZ_TITLE_BUSY = 'در حال آپلود پروژه…';

  function setUploadLoading(on) {
    var dz = $('#dropzone');
    if (dz) {
      dz.classList.toggle('loading', on);
      $('#dz-spinner').hidden = !on;
      $('#dz-title').textContent = on ? DZ_TITLE_BUSY : DZ_TITLE;
    }
    var chips = document.querySelectorAll('.chip');
    for (var i = 0; i < chips.length; i++) chips[i].disabled = on;
    var gh = $('#btn-github');
    if (gh) gh.disabled = on;
  }

  function showUploadError(prefix, detail) {
    var err = $('#upload-error');
    err.innerHTML = '';
    err.appendChild(document.createTextNode(prefix));
    if (detail) err.appendChild(el('span', 'ltr-block', esc(detail)));
    err.hidden = false;
  }

  function intakeReady(data) {
    store.sessionId = data.session_id;
    if (data.demo === true) { store.demo = true; updateSampleBadge(); }
    goAssessment();
  }

  function intakeFail(e, prefix) {
    setUploadLoading(false);
    if (isExpired(e)) return;
    var msg = (e && e.detail && e.detail.length < 300) ? e.detail : '';
    if (!msg && e && e.kind === 'network') msg = 'اتصال به سرور برقرار نشد.';
    showUploadError(prefix, msg);
  }

  function uploadFile(file) {
    if (!file) return;
    $('#upload-error').hidden = true;
    var isZip = /\.zip$/i.test(file.name) || /zip/i.test(file.type || '');
    if (!isZip) {
      showUploadError('فقط فایل zip پذیرفته می‌شود.', null);
      return;
    }
    setUploadLoading(true);
    var fd = new FormData();
    fd.append('file', file, file.name);
    api.upload(fd).then(intakeReady).catch(function (e) {
      intakeFail(e, 'آپلود ناموفق بود. دوباره تلاش کن.');
    });
  }

  function submitGithub(ev) {
    ev.preventDefault();
    $('#upload-error').hidden = true;
    var input = $('#github-url');
    var raw = String(input.value || '').trim();
    if (!raw) {
      showUploadError('آدرس ریپو را وارد کن — مثلاً: username/my-project', null);
      input.focus();
      return;
    }
    setUploadLoading(true);
    var btn = $('#btn-github');
    btn.disabled = true;
    btn.textContent = 'در حال دریافت…';
    api.github({ url: raw }).then(function (data) {
      btn.disabled = false;
      btn.textContent = 'تحویل پروژه';
      input.value = '';
      intakeReady(data);
    }).catch(function (e) {
      btn.disabled = false;
      btn.textContent = 'تحویل پروژه';
      intakeFail(e, 'دریافت از گیت‌هاب ناموفق بود.');
    });
  }

  function demoFixture(key, chipBtn) {
    $('#upload-error').hidden = true;
    var chips = document.querySelectorAll('.chip');
    for (var i = 0; i < chips.length; i++) chips[i].disabled = true;
    if (chipBtn) chipBtn.classList.add('loading');
    api.demo({ fixture: key }).then(function (data) {
      if (chipBtn) chipBtn.classList.remove('loading');
      intakeReady(data);
    }).catch(function (e) {
      if (chipBtn) chipBtn.classList.remove('loading');
      for (var j = 0; j < chips.length; j++) chips[j].disabled = false;
      intakeFail(e, 'ساخت پروژهٔ نمونه ناموفق بود.');
    });
  }

  function wireTabs() {
    var tabGh = $('#tab-github'), tabZip = $('#tab-zip');
    var paneGh = $('#pane-github'), paneZip = $('#pane-zip');
    function pick(which) {
      var gh = which === 'github';
      tabGh.classList.toggle('is-active', gh);
      tabZip.classList.toggle('is-active', !gh);
      tabGh.setAttribute('aria-selected', String(gh));
      tabZip.setAttribute('aria-selected', String(!gh));
      paneGh.hidden = !gh;
      paneZip.hidden = gh;
    }
    tabGh.addEventListener('click', function () { pick('github'); });
    tabZip.addEventListener('click', function () { pick('zip'); });
  }

  function wireUpload() {
    wireTabs();
    $('#github-form').addEventListener('submit', submitGithub);

    var dz = $('#dropzone');
    dz.addEventListener('click', function () {
      if (!dz.classList.contains('loading')) $('#file-input').click();
    });
    dz.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar') {
        ev.preventDefault();
        if (!dz.classList.contains('loading')) $('#file-input').click();
      }
    });
    ['dragenter', 'dragover'].forEach(function (t) {
      dz.addEventListener(t, function (ev) { ev.preventDefault(); dz.classList.add('drag'); });
    });
    ['dragleave', 'drop'].forEach(function (t) {
      dz.addEventListener(t, function (ev) { ev.preventDefault(); dz.classList.remove('drag'); });
    });
    dz.addEventListener('drop', function (ev) {
      var f = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
      if (f) uploadFile(f);
    });
    $('#file-input').addEventListener('change', function (ev) {
      var f = ev.target.files && ev.target.files[0];
      if (f) uploadFile(f);
      ev.target.value = '';
    });
    var chips = document.querySelectorAll('.chip');
    for (var i = 0; i < chips.length; i++) {
      (function (chip) {
        chip.addEventListener('click', function () { demoFixture(chip.dataset.fixture, chip); });
      })(chips[i]);
    }
  }

  /* ----------------------------------------------------------
   * B) Assessment screen
   * ---------------------------------------------------------- */

  function goAssessment() {
    showScreen('assessment');
    var box = $('#questions');
    var err = $('#assess-error');
    err.hidden = true;
    $('#btn-start').disabled = true;
    $('#btn-expert-skip').disabled = true;
    box.innerHTML = '';
    box.appendChild(el('div', 'assess-loading',
      '<span class="spinner" aria-hidden="true"></span><span>در حال گرفتن سؤال‌ها…</span>'));

    return api.assessment().then(function (data) {
      store.questions = (data && Array.isArray(data.questions)) ? data.questions : [];
      store.expertSkip = (data && data.expert_skip) || {};
      var skipBtn = $('#btn-expert-skip');
      skipBtn.textContent = store.expertSkip.label || 'رد کردن سؤال‌ها (حالت کارشناس)';
      skipBtn.disabled = false;
      box.innerHTML = '';
      if (!store.questions.length) {
        box.appendChild(el('p', 'muted', 'سؤالی برای تو ندارم؛ مستقیم شروع می‌کنیم.'));
      }
      store.questions.forEach(function (q) { box.appendChild(questionCard(q)); });
      updateStartState();
    }).catch(function (e) {
      if (isExpired(e)) return;
      box.innerHTML = '';
      var errBox = $('#assess-error');
      errBox.innerHTML = '';
      errBox.appendChild(document.createTextNode('دریافت سؤال‌ها ناموفق بود. '));
      var retry = el('button', 'btn btn-ghost btn-sm', 'تلاش دوباره');
      retry.type = 'button';
      retry.addEventListener('click', function () { goAssessment(); });
      errBox.appendChild(retry);
      errBox.hidden = false;
    });
  }

  function questionCard(q) {
    var card = el('div', 'card q-card');
    card.setAttribute('role', 'radiogroup');
    card.setAttribute('aria-label', q.text || 'سؤال');
    card.appendChild(el('p', 'q-text', esc(q.text || '')));
    var opts = el('div', 'q-options');
    (q.options || []).forEach(function (o) {
      var b = el('button', 'opt-btn');
      b.type = 'button';
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', 'false');
      b.dataset.value = (o.value != null) ? String(o.value) : '';
      b.textContent = (o.label != null) ? String(o.label) : b.dataset.value;
      b.addEventListener('click', function () {
        var siblings = opts.querySelectorAll('.opt-btn');
        for (var i = 0; i < siblings.length; i++) siblings[i].setAttribute('aria-checked', 'false');
        b.setAttribute('aria-checked', 'true');
        store.answers[q.id] = b.dataset.value; // exactly one option per question
        updateStartState();
      });
      opts.appendChild(b);
    });
    card.appendChild(opts);
    return card;
  }

  function updateStartState() {
    var qs = store.questions;
    var all = qs.length === 0 || qs.every(function (q) { return store.answers[q.id] !== undefined; });
    $('#btn-start').disabled = !all;
    $('#assess-hint').textContent = all
      ? 'هر لحظه آماده‌ام — دکمهٔ «شروع بررسی» را بزن.'
      : 'برای شروع، به همهٔ سؤال‌ها پاسخ بده.';
  }

  function submitAnswers(skipExpert) {
    var btn = skipExpert ? $('#btn-expert-skip') : $('#btn-start');
    btn.disabled = true;
    var payload = skipExpert
      ? { answers: {}, skip_expert: true }
      : { answers: shallowCopy(store.answers), skip_expert: false };
    api.answers(store.sessionId, payload).then(function (res) {
      if (res && (res.level === 'beginner' || res.level === 'expert')) store.level = res.level;
      return enterRun();
    }).catch(function (e) {
      if (isExpired(e)) return;
      btn.disabled = false;
      var errBox = $('#assess-error');
      errBox.innerHTML = '';
      errBox.appendChild(document.createTextNode('ثبت پاسخ‌ها ناموفق بود. دوباره تلاش کن.'));
      if (e.detail) errBox.appendChild(el('span', 'ltr-block', esc(e.detail)));
      errBox.hidden = false;
    });
  }

  /* ----------------------------------------------------------
   * C) Run screen — entry, hydration, SSE
   * ---------------------------------------------------------- */

  function enterRun() {
    showScreen('run');
    setLevelUI();
    disableInputs(false);
    $('#stage-title').textContent = 'در حال آماده‌سازی…';

    return hydrate().then(function (snap) {
      if (store.screen !== 'run') return; // redirected (expired / report)
      var st = snap ? String(snap.status || '').toLowerCase() : '';

      // Session already finished before we got here.
      if (store.report && /done|success|finish|complete/.test(st)) { goReport(); return; }
      if (/cancel/.test(st)) {
        store.cancelled = true;
        addNote('لغو شد');
        disableInputs(true);
        return;
      }

      openSSE();

      // Only start the agent for a fresh session (no prior events, no
      // running/approval status in the snapshot).
      var resumed = store.eventsSeen || /run|deploy|assess|inspect|plan|approv|start/.test(st);
      if (!resumed) {
        api.start(store.sessionId).catch(function (e) {
          if (isExpired(e)) return;
          if (e.status === 409 || /already/i.test(String(e.detail || ''))) return; // already started
          rError({ message: e.detail || ('start failed: ' + (e.status || 'network')) });
        });
      }
    });
  }

  /** GET snapshot once, replay stored events oldest-first. */
  function hydrate() {
    store.hydrating = true;
    return api.snapshot(store.sessionId).then(function (snap) {
      snap = snap || {};
      if (snap.demo === true) { store.demo = true; updateSampleBadge(); }
      if (snap.level === 'beginner' || snap.level === 'expert') {
        store.level = snap.level;
        setLevelUI();
      }
      var evs = Array.isArray(snap.events) ? snap.events.slice() : [];
      evs.sort(function (a, b) { return (a && a.i ? a.i : 0) - (b && b.i ? b.i : 0); });
      store.eventsSeen = evs.length > 0;

      var approvalPayload = null;
      var seenApproval = false;
      var afterApproval = false;

      evs.forEach(function (ev) {
        if (!ev || !ev.type) return;
        if (ev.type === 'approval_required') {
          approvalPayload = ev.payload || {};
          seenApproval = true;
          afterApproval = false;
        } else if (seenApproval) {
          afterApproval = true;
        }
        handleEvent(ev.i, ev.type, ev.payload || {});
      });

      // Fallbacks for data the snapshot exposes outside the event list.
      if (snap.plan && !store.hasPlan) handleEvent(null, 'plan', snap.plan);
      if (snap.report) store.report = snap.report;

      // Pending approval: snapshot field wins (its note is not yet shown);
      // otherwise an approval_required that is still the newest event means
      // the user has not decided yet (its note was already added by the
      // event route during replay above — do not duplicate it).
      var st = String(snap.status || '').toLowerCase();
      if (snap.approval) {
        addNote('خلبان برای ادامه نیاز به تأیید تو دارد.');
        openApprovalModal(snap.approval);
      } else if (approvalPayload && !afterApproval && !store.cancelled && !/done|cancel|success/.test(st)) {
        openApprovalModal(approvalPayload);
      }

      store.hydrating = false;
      scrollTimeline();
      return snap;
    }).catch(function (e) {
      store.hydrating = false;
      if (isExpired(e)) return null;
      // Non-fatal: SSE replay (no Last-Event-ID yet -> full replay) covers us.
      addNote('دریافت وضعیت قبلی ناموفق بود؛ ادامه می‌دهیم.');
      return null;
    });
  }

  /**
   * SSE with manual reconnect + a POLLING FALLBACK.
   * The Next.js dev proxy compresses (gzip) proxied responses whenever the
   * browser sends Accept-Encoding — and the compressed SSE stream then never
   * delivers chunks to the browser. So: if the EventSource stays silent for
   * 5s, we tear it down and poll the snapshot endpoint instead —
   * handleEvent() dedupes by integer id, so replay through polling is
   * idempotent. On a direct deployment (liara.run, no proxy) SSE stays live.
   */
  function openSSE() {
    stopSSE();
    if (!store.sessionId || store.screen === 'report' || store.screen === 'expired') return;
    var es = new EventSource('/api/events/' + encodeURIComponent(store.sessionId));
    store.es = es;
    store.sseAttempts = 0;
    store.sseGot = 0;
    if (store.sseWatchdog) clearTimeout(store.sseWatchdog);
    store.sseWatchdog = setTimeout(function () {
      if (store.sseGot === 0 && store.es === es) {
        try { es.close(); } catch (e) { /* noop */ }
        store.es = null;
        startPolling(); // proxy ate the stream -> poll snapshots
      }
    }, 5000);

    es.onopen = function () { store.sseAttempts = 0; };

    es.onmessage = function (ev) {
      store.sseGot += 1;
      stopPolling(); // SSE proved alive again — polling not needed
      var msg = null;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (!msg || typeof msg !== 'object' || !msg.type) return;
      var payload = shallowCopy(msg);
      delete payload.type;
      handleEvent(ev.lastEventId, msg.type, payload);
    };

    es.onerror = function () {
      // Native reconnect retries forever; we take over to cap attempts.
      try { es.close(); } catch (e) { /* noop */ }
      if (store.es === es) store.es = null;
      store.sseAttempts += 1;
      if (store.sseAttempts >= 10) { showExpired(); return; }
      store.sseTimer = setTimeout(openSSE, 2000);
    };
  }

  /** Snapshot polling fallback: same event routing as hydration replay. */
  function startPolling() {
    if (store.pollTimer) return;
    store.pollTimer = setInterval(pollOnce, 1500);
    pollOnce();
  }

  function stopPolling() {
    if (store.pollTimer) { clearInterval(store.pollTimer); store.pollTimer = null; }
    store.pollBusy = false;
  }

  function pollOnce() {
    if (!store.sessionId || store.pollBusy || store.screen === 'expired') return;
    if (store.screen === 'report') return; // report screen polls /monitor + /care
    store.pollBusy = true;
    api.snapshot(store.sessionId).then(function (snap) {
      store.pollBusy = false;
      if (!snap || store.screen === 'report' || store.screen === 'expired') return;
      var evs = Array.isArray(snap.events) ? snap.events : [];
      evs.forEach(function (ev) {
        if (ev && ev.type) handleEvent(ev.i, ev.type, ev.payload || {});
      });
      if (snap.plan && !store.hasPlan) handleEvent(null, 'plan', snap.plan);
      if (snap.report && !store.report) store.report = snap.report;
      if (snap.approval && !store.pendingApproval) openApprovalModal(snap.approval);
      var st = String(snap.status || '').toLowerCase();
      if (/cancel/.test(st) && !store.cancelled) {
        store.cancelled = true;
        addNote('لغو شد');
        disableInputs(true);
      }
      if (store.report && /done|success|finish|complete/.test(st) && store.screen === 'run') goReport();
    }).catch(function () {
      store.pollBusy = false;
    });
  }

  function stopSSE() {
    if (store.sseTimer) { clearTimeout(store.sseTimer); store.sseTimer = null; }
    if (store.sseWatchdog) { clearTimeout(store.sseWatchdog); store.sseWatchdog = null; }
    if (store.es) {
      try { store.es.close(); } catch (e) { /* noop */ }
      store.es = null;
    }
    stopPolling();
  }

  /** Central event gate: dedupes by integer id, then routes by type. */
  function handleEvent(rawId, type, payload) {
    if (rawId !== undefined && rawId !== null && String(rawId) !== '') {
      var n = Number(rawId);
      if (Number.isFinite(n)) {
        if (n <= store.maxI) return; // already rendered (hydration / replay)
        store.maxI = n;
      }
    }
    var fn = ROUTES[type];
    if (fn) fn(payload || {});
  }

  /* ----------------------------------------------------------
   * Timeline building blocks
   * ---------------------------------------------------------- */

  function timelineEl() { return $('#timeline'); }

  function tlItem(cls, dotCls) {
    var li = el('li', 'tl-entry ' + (cls || ''));
    li.appendChild(el('span', 'tl-dot ' + (dotCls || '')));
    return li;
  }

  function scrollTimeline() {
    var tl = timelineEl();
    var last = tl.lastElementChild;
    if (last && typeof last.scrollIntoView === 'function') {
      try { last.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
      catch (e) { last.scrollIntoView(); }
    }
  }

  function addNote(text) {
    var li = tlItem('tl-note', '');
    li.appendChild(el('span', 'note-text', esc(text)));
    timelineEl().appendChild(li);
    scrollTimeline();
  }

  function ensureDeployGroup() {
    if (store.deployGroupEl && store.deployGroupEl.isConnected) return store.deployGroupEl;
    var li = el('li', 'tl-entry tl-group');
    li.appendChild(el('span', 'tl-dot dot-amber'));
    var box = el('div', 'group-box');
    box.appendChild(el('div', 'group-title', 'استقرار — لاگ زنده'));
    var list = el('ol', 'group-list');
    box.appendChild(list);
    li.appendChild(box);
    timelineEl().appendChild(li);
    store.deployGroupEl = list;
    return list;
  }

  /* ----------------------------------------------------------
   * SSE event route handlers (one per server event type)
   * ---------------------------------------------------------- */

  function rStage(p) {
    if (p.fa) $('#stage-title').textContent = p.fa;
    syncStepper(p.name);
    var li = tlItem('tl-stage', 'dot-amber');
    var box = el('div', 'stage-box');
    box.appendChild(el('span', 'stage-fa', esc(p.fa || p.name || '')));
    if (p.name) box.appendChild(el('span', 'stage-name ltr-block', esc(p.name)));
    li.appendChild(box);
    timelineEl().appendChild(li);
    scrollTimeline();
  }

  function rLog(p) {
    var li = tlItem('tl-log', '');
    var line = el('div', 'log-line ltr-block' + logLevelCls(p.text));
    line.textContent = (p.text != null) ? String(p.text) : '';
    li.appendChild(line);
    timelineEl().appendChild(li);
    scrollTimeline();
  }

  function rDeployLog(p) {
    var list = ensureDeployGroup();
    var row = el('li', 'log-line deploy-line ltr-block' + logLevelCls(p.text));
    row.textContent = (p.text != null) ? String(p.text) : '';
    list.appendChild(row);
    scrollTimeline();
  }

  var SEV = {
    high:   { label: 'بالا',   badgeCls: 'badge-red',   dotCls: 'dot-red' },
    medium: { label: 'متوسط', badgeCls: 'badge-amber', dotCls: 'dot-amber' },
    low:    { label: 'کم',    badgeCls: 'badge-zinc',  dotCls: '' }
  };

  function riskBodyText(p) {
    return (store.level === 'expert')
      ? (p.expert_text || p.detail || '')
      : (p.beginner_text || p.detail || '');
  }

  function rRisk(p) {
    var sev = SEV[p.severity] || SEV.low;
    var riskId = (p.id != null) ? String(p.id) : null;

    // Upsert: same risk id twice -> refresh body only.
    for (var i = 0; i < store.risks.length; i++) {
      if (riskId !== null && store.risks[i].id === riskId) {
        store.risks[i].payload = p;
        if (store.risks[i].bodyEl && store.risks[i].bodyEl.isConnected) {
          store.risks[i].bodyEl.textContent = riskBodyText(p);
        }
        return;
      }
    }

    var li = tlItem('tl-card', sev.dotCls);
    if (riskId) li.dataset.riskId = riskId;
    var card = el('div', 'card risk-card' + (p.severity ? ' sev-' + p.severity : ''));

    var head = el('div', 'risk-head');
    head.appendChild(el('span', 'badge ' + sev.badgeCls, sev.label));
    if (p.evidence_file) head.appendChild(el('span', 'evidence ltr-block', esc(p.evidence_file)));
    card.appendChild(head);

    var body = el('p', 'risk-body');
    body.setAttribute('data-risk-body', '');
    body.textContent = riskBodyText(p);
    card.appendChild(body);

    if (Array.isArray(p.concept_keys) && p.concept_keys.length) {
      var keys = el('div', 'chip-wrap risk-keys');
      p.concept_keys.forEach(function (k) {
        keys.appendChild(el('span', 'key-chip ltr-block', esc(k)));
      });
      card.appendChild(keys);
    }

    li.appendChild(card);
    timelineEl().appendChild(li);
    store.risks.push({ id: riskId, payload: p, bodyEl: body });
    scrollTimeline();
  }

  /** Re-render risk bodies after a level change (beginner <-> expert). */
  function renderRiskBodies() {
    store.risks.forEach(function (r) {
      if (r.bodyEl && r.bodyEl.isConnected) r.bodyEl.textContent = riskBodyText(r.payload);
    });
  }

  /** Real-price grid (live from the Liara API) or the fallback cost line. */
  function costBlock(p) {
    var wrap = el('div');
    if (p && p.price_hourly_toman != null && p.plan_id) {
      var grid = el('div', 'cost-grid');
      [['پلن پیشنهادی', String(p.plan_id), 'بر اساس پلتفرم پروژه'],
       ['هر ساعت', faNum(p.price_hourly_toman), 'تومان'],
       ['هر ماه', faNum(p.price_monthly_toman), 'تومان (۷۳۰ ساعت)']
      ].forEach(function (row) {
        var cell = el('div', 'cost-cell');
        cell.appendChild(el('span', 'cost-cell-label', esc(row[0])));
        cell.appendChild(el('span', 'cost-cell-val ltr-block', esc(row[1])));
        cell.appendChild(el('span', 'cost-cell-unit', esc(row[2])));
        grid.appendChild(cell);
      });
      wrap.appendChild(grid);
      var live = el('span', 'cost-live-badge');
      live.innerHTML = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></svg> قیمت زنده از API لیارا';
      wrap.appendChild(live);
      return wrap;
    }
    var row = el('div', 'cost-row');
    row.appendChild(el('span', 'cost-label', 'هزینهٔ تخمینی:'));
    row.appendChild(el('span', 'cost-val ltr-block', esc(p && p.estimated_cost != null ? p.estimated_cost : '—')));
    if (p && p.cost_unverified) row.appendChild(el('span', 'badge badge-amber', UNVERIFIED_BADGE));
    wrap.appendChild(row);
    return wrap;
  }

  function rPlan(p) {
    store.hasPlan = true;
    if (store.planEl && store.planEl.isConnected) store.planEl.remove(); // replace, never duplicate

    var li = tlItem('tl-card', 'dot-amber');
    var card = el('div', 'card plan-card');
    card.appendChild(el('div', 'plan-title', 'برنامهٔ استقرار'));

    var meta = el('div', 'plan-meta');
    if (p.platform) {
      meta.appendChild(el('span', 'meta-item', 'پلتفرم: '));
      meta.appendChild(el('span', 'meta-val ltr-block', esc(p.platform)));
    }
    if (p.port != null && p.port !== '') {
      meta.appendChild(el('span', 'meta-item', 'پورت: '));
      meta.appendChild(el('span', 'meta-val ltr-block', esc(p.port)));
    }
    if (meta.childElementCount) card.appendChild(meta);

    if (Array.isArray(p.steps) && p.steps.length) {
      var ol = el('ol', 'plan-steps');
      p.steps.forEach(function (s, i) {
        var item = el('li', 'plan-step');
        item.appendChild(el('span', 'step-num', faDigits(i + 1)));
        var body = el('div', 'plan-step-body');
        var title = (s && (s.title_fa || s.title)) || ('مرحلهٔ ' + faDigits(i + 1));
        body.appendChild(el('span', 'plan-step-title', esc(title)));
        if (s && s.detail) body.appendChild(el('span', 'plan-step-detail', esc(s.detail)));
        item.appendChild(body);
        ol.appendChild(item);
      });
      card.appendChild(ol);
    }

    card.appendChild(costBlock(p));

    var envItems = normalizeEnv(p.env_needed);
    if (envItems.length) {
      var envWrap = el('div', 'chip-wrap');
      envWrap.appendChild(el('span', 'sec-inline-label', 'متغیرهای محیطی موردنیاز:'));
      envItems.forEach(function (it) {
        envWrap.appendChild(el('span', 'key-chip ltr-block', esc(it.key)));
      });
      card.appendChild(envWrap);
    }

    if (Array.isArray(p.prepared_files) && p.prepared_files.length) {
      card.appendChild(el('span', 'sec-inline-label', 'فایل‌های آماده‌شده:'));
      card.appendChild(preparedFilesBlock(p.prepared_files));
    }

    li.appendChild(card);
    timelineEl().appendChild(li);
    store.planEl = li;
    scrollTimeline();
  }

  function rApproval(p) {
    addNote('خلبان برای ادامه نیاز به تأیید تو دارد.');
    setStep(3); // stepper: تأیید
    openApprovalModal(p);
  }

  function rFix(p) {
    setStep(5); // stepper: اصلاح
    var li = tlItem('tl-card', 'dot-amber');
    var card = el('div', 'card fix-card');
    var attempt = (p.attempt != null) ? faDigits(p.attempt) : '؟';
    var maxA = (p.max_attempts != null) ? faDigits(p.max_attempts) : '؟';
    card.appendChild(el('div', 'fix-title', 'تلاش خودکار ' + attempt + ' از ' + maxA));
    if (p.reason_fa) card.appendChild(el('p', 'fix-reason', esc(p.reason_fa)));
    if (p.action) {
      var act = el('div', 'fix-action ltr-block');
      act.textContent = String(p.action);
      card.appendChild(act);
    }
    // Risk tier badge: every action shows its tier + context note.
    if (p.risk_tier && p.risk_tier.tier) card.appendChild(tierBadge(p.risk_tier));
    if (p.risk_note_fa) card.appendChild(el('span', 'risk-note muted small', esc(p.risk_note_fa)));
    // Ranked causes for unscripted failures: quote-only evidence.
    if (Array.isArray(p.causes_ranked) && p.causes_ranked.length) {
      var cwrap = el('ul', 'causes-list');
      p.causes_ranked.forEach(function (c, i) {
        var item = el('li', 'cause-item');
        item.appendChild(el('span', 'cause-rank', faDigits(i + 1)));
        item.appendChild(el('span', 'cause-text', esc((c && c.cause_fa) || '')));
        if (c && c.evidence) item.appendChild(el('span', 'cause-evidence ltr-block', esc(c.evidence)));
        cwrap.appendChild(item);
      });
      card.appendChild(cwrap);
    }
    li.appendChild(card);
    timelineEl().appendChild(li);
    scrollTimeline();
  }

  var TIER_BADGE_CLS = { auto: 'badge-emerald', approval: 'badge-amber', forbidden: 'badge-red' };

  function tierBadge(rt) {
    var cls = TIER_BADGE_CLS[rt.tier] || 'badge-zinc';
    var fa = rt.tier_fa || rt.tier || '';
    var b = el('span', 'badge ' + cls + ' tier-badge', esc(fa));
    if (rt.tier_level && fa.indexOf('سطح') === -1) {
      b.appendChild(el('span', 'tier-level', 'سطح ' + faDigits(rt.tier_level)));
    }
    return b;
  }

  function rOps(p) {
    var ok = !!p.ok;
    var li = tlItem('tl-card', ok ? 'dot-emerald' : 'dot-red');
    var card = el('div', 'card ops-card');
    var head = el('div', 'risk-head');
    head.appendChild(el('span', 'ops-title', 'عملیات: ' + esc(p.action || '')));
    if (p.risk_tier && p.risk_tier.tier) head.appendChild(tierBadge(p.risk_tier));
    card.appendChild(head);
    card.appendChild(el('span', 'badge ' + (ok ? 'badge-emerald' : 'badge-red'), ok ? 'انجام شد' : 'ناموفق'));
    if (p.message_fa) card.appendChild(el('span', 'ops-msg ltr-block', esc(p.message_fa)));
    li.appendChild(card);
    timelineEl().appendChild(li);
    scrollTimeline();
  }

  function rOpsDenied(p) {
    var li = tlItem('tl-card', 'dot-red');
    var card = el('div', 'card ops-card ops-denied');
    var head = el('div', 'risk-head');
    head.appendChild(el('span', 'ops-title', 'اقدام رد شد: ' + esc(p.action || '')));
    if (p.risk_tier && p.risk_tier.tier) head.appendChild(tierBadge(p.risk_tier));
    card.appendChild(head);
    card.appendChild(el('p', 'fix-reason', esc(p.reason_fa || 'این اقدام بدون تأیید اجرا نمی‌شود.')));
    li.appendChild(card);
    timelineEl().appendChild(li);
    scrollTimeline();
  }

  function rMonitorAlert(p) {
    var li = tlItem('tl-card', 'dot-red');
    var card = el('div', 'card monitor-alert-card');
    card.appendChild(el('div', 'fix-title', 'هشدار پایش'));
    if (p.message_fa) card.appendChild(el('p', 'fix-reason', esc(p.message_fa)));
    if (p.label) card.appendChild(el('span', 'badge badge-amber', esc(p.label)));
    var sug = p.suggested_action;
    if (sug && sug.tier) {
      var row = el('div', 'monitor-row');
      row.appendChild(el('span', 'muted small', 'اقدام پیشنهادی:'));
      row.appendChild(el('span', 'ops-title', esc(sug.action)));
      row.appendChild(tierBadge(sug));
      card.appendChild(row);
      card.appendChild(el('p', 'muted small', 'اجرای آن فقط با تأیید تو انجام می‌شود.'));
    }
    li.appendChild(card);
    timelineEl().appendChild(li);
    scrollTimeline();
  }

  function rCareScheduleNote(p) {
    var fa = { daily: 'هر روز', weekly: 'هر هفته', monthly: 'هر ماه' }[p.schedule || ''] || '';
    addNote(fa ? ('ایجنت ستاره فعال شد — پایش ' + fa + '. اولین بررسی همین حالا انجام می‌شود.') : 'پایش دوره‌ای ایجنت ستاره خاموش شد.');
  }

  function rCareReportNote(p) {
    var score = (p.score != null) ? faDigits(p.score) : '؟';
    addNote('ایجنت ستاره گزارش داد: نمرهٔ سلامت ' + score + ' از ۱۰۰ — ' + (p.verdict_fa || ''));
  }

  function rVerify(p) {
    setStep(6); // stepper: صحت‌سنجی
    var ok = !!p.ok;
    var li = tlItem('tl-card', ok ? 'dot-emerald' : 'dot-red');
    var card = el('div', 'card verify-card ' + (ok ? 'verify-ok' : 'verify-fail'));
    card.appendChild(el('div', 'verify-title', ok ? 'بررسی نهایی موفق' : 'بررسی نهایی ناموفق'));
    if (p.status != null && p.status !== '') {
      card.appendChild(el('span', 'badge ' + (ok ? 'badge-emerald' : 'badge-red'), 'HTTP ' + p.status));
    }
    if (p.url) {
      var a = el('a', 'live-link ltr-block');
      a.href = p.url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = p.url;
      card.appendChild(a);
    }
    li.appendChild(card);
    timelineEl().appendChild(li);
    scrollTimeline();
  }

  function rReport(p) { store.report = p || {}; }

  function rDone() {
    stopSSE();
    completeStepper();
    disableInputs(true);
    addNote('کار تمام شد.');
    goReport();
  }

  function rError(p) {
    errorStepper();
    var li = tlItem('tl-error', 'dot-red');
    var card = el('div', 'card error-card');
    card.appendChild(el('span', 'error-label', 'خطا'));
    var msg = el('div', 'error-msg ltr-block');
    msg.textContent = (p.message != null) ? String(p.message) : '';
    card.appendChild(msg);
    li.appendChild(card);
    timelineEl().appendChild(li);
    scrollTimeline();
  }

  function rCancelled() {
    store.cancelled = true;
    addNote('لغو شد');
    disableInputs(true);
  }

  function rLevelChanged(p) {
    var lvl = (p && p.level === 'expert') ? 'expert' : 'beginner';
    store.level = lvl;
    setLevelUI();
    renderRiskBodies();
  }

  var ROUTES = {
    stage: rStage,
    log: rLog,
    risk: rRisk,
    plan: rPlan,
    approval_required: rApproval,
    deploy_log: rDeployLog,
    fix: rFix,
    verify: rVerify,
    report: rReport,
    done: rDone,
    error: rError,
    cancelled: rCancelled,
    level_changed: rLevelChanged,
    ops: rOps,
    ops_denied: rOpsDenied,
    monitor_alert: rMonitorAlert,
    care_schedule: rCareScheduleNote,
    care_report: rCareReportNote
  };

  /* ----------------------------------------------------------
   * Stepper (presentational)
   * ---------------------------------------------------------- */

  var STAGE_TO_STEP = {
    assess: 0,
    inspect: 1,
    explain_risks: 2,
    make_plan: 3,
    prepare_files: 3,
    deploy: 4,
    read_logs: 5,
    verify: 6,
    teach_summary: 7,
    escalate: 3
  };

  var stepperEl = null;
  var stepEls = [];

  function initStepper() {
    stepperEl = $('#stepper');
    if (!stepperEl) return;
    stepEls = [];
    var nodes = stepperEl.querySelectorAll('.step');
    for (var i = 0; i < nodes.length; i++) stepEls.push(nodes[i]);
  }

  function setStep(idx) {
    if (!stepEls.length) return;
    for (var i = 0; i < stepEls.length; i++) {
      var li = stepEls[i];
      li.classList.toggle('done', i < idx);
      li.classList.toggle('active', i === idx);
      li.classList.remove('error');
      if (i === idx) li.setAttribute('aria-current', 'step');
      else li.removeAttribute('aria-current');
    }
    if (stepperEl) stepperEl.classList.remove('complete');
  }

  function syncStepper(stageName) {
    if (stageName == null) return;
    var idx = STAGE_TO_STEP[String(stageName)];
    if (idx != null) setStep(idx);
  }

  function completeStepper() {
    if (!stepEls.length) return;
    for (var i = 0; i < stepEls.length; i++) {
      stepEls[i].classList.remove('active', 'error');
      stepEls[i].classList.add('done');
      stepEls[i].removeAttribute('aria-current');
    }
  }

  function errorStepper() {
    if (!stepEls.length) return;
    for (var i = 0; i < stepEls.length; i++) {
      if (stepEls[i].classList.contains('active')) stepEls[i].classList.add('error');
    }
  }

  function logLevelCls(text) {
    var t = String(text == null ? '' : text).toLowerCase();
    if (/error|fail|fatal|exception|traceback|refused|denied|crash|✗|✘/.test(t)) return ' log-err';
    if (/warn|deprecat|fallback|retry|unverified|skip|not found|not verified|escalat/.test(t)) return ' log-warn';
    if (/(^|\W)(ok|success|passed|healthy|live|done)(\W|$)|✓|200 ok/.test(t)) return ' log-ok';
    return '';
  }

  /* ----------------------------------------------------------
   * Run screen controls: level toggle + cancel
   * ---------------------------------------------------------- */

  function setLevelUI() {
    $('#lvl-beginner').setAttribute('aria-pressed', String(store.level === 'beginner'));
    $('#lvl-expert').setAttribute('aria-pressed', String(store.level === 'expert'));
  }

  function onToggleLevel(lvl) {
    if (store.level === lvl || store.cancelled) return;
    var prev = store.level;
    store.level = lvl;           // optimistic
    setLevelUI();
    renderRiskBodies();
    api.level(store.sessionId, { level: lvl }).then(function (res) {
      if (res && (res.level === 'beginner' || res.level === 'expert') && res.level !== store.level) {
        store.level = res.level;
        setLevelUI();
        renderRiskBodies();
      }
    }).catch(function (e) {
      if (isExpired(e)) return;
      store.level = prev;        // revert on failure
      setLevelUI();
      renderRiskBodies();
      addNote('تغییر سطح توضیح اعمال نشد.');
    });
  }

  function onCancel() {
    if (store.cancelled) return;
    var btn = $('#btn-cancel');
    btn.disabled = true;
    btn.textContent = 'در حال لغو…';
    api.cancel(store.sessionId).then(function () {
      addNote('درخواست لغو ثبت شد؛ در حال توقف…');
    }).catch(function (e) {
      if (isExpired(e)) return;
      btn.disabled = false;
      btn.textContent = 'لغو عملیات';
      addNote('لغو عملیات ناموفق بود؛ دوباره تلاش کن.');
    });
  }

  /** Mid-run Q&A: free question -> ONE LLM call on the server -> Q/A pair
   *  rendered in the timeline (client-side only; never enters SSE events). */
  function bindAsk() {
    var form = $('#ask-form');
    if (!form || form.dataset.bound) return;
    form.dataset.bound = '1';
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var input = $('#ask-input');
      var btn = $('#btn-ask');
      var q = String(input.value || '').trim();
      if (!q || !store.sessionId) return;
      btn.disabled = true;
      input.disabled = true;
      btn.textContent = 'در حال پاسخ…';
      addQA(q, null); // question first, answer appended on arrival
      api.ask(store.sessionId, { question: q }).then(function (res) {
        addQA(null, (res && res.answer_fa) ? res.answer_fa : '—');
        input.value = '';
      }).catch(function (e) {
        if (!isExpired(e)) addQA(null, 'پاسخ گرفتن ناموفق بود؛ دوباره تلاش کن.');
      }).finally(function () {
        btn.disabled = false;
        input.disabled = false;
        btn.textContent = 'پرسیدن';
        input.focus();
      });
    });
  }

  function addQA(question, answer) {
    var tl = timelineEl();
    if (!tl) return;
    var li = el('li', 'tl-item tl-note');
    if (question != null) {
      var q = el('div', 'ask-q', '<span class="ask-badge">تو</span>');
      var qText = document.createElement('span');
      qText.textContent = question; // textContent: user input is never HTML
      q.appendChild(qText);
      li.appendChild(q);
      li.dataset.pending = '1';
      tl.appendChild(li);
      tl.scrollTop = tl.scrollHeight;
      return;
    }
    // answer: attach to the newest pending question, else standalone
    var last = tl.querySelector('li[data-pending="1"]');
    var target = last || li;
    if (last) delete last.dataset.pending;
    var a = el('div', 'ask-a', '<span class="ask-badge ask-badge-agent">خلبان</span>');
    var aText = document.createElement('span');
    aText.textContent = answer || '';
    a.appendChild(aText);
    target.appendChild(a);
    tl.scrollTop = tl.scrollHeight;
  }

  /* ----------------------------------------------------------
   * Approval modal (cost gate + env values + prepared files)
   * and the ask_user question modal.
   * ---------------------------------------------------------- */

  function normalizeEnv(env) {
    if (!env) return [];
    var arr = Array.isArray(env)
      ? env
      : Object.keys(env).map(function (k) { return { key: k, value: env[k] }; });
    return arr.map(function (it) {
      if (typeof it === 'string') return { key: it, value: '' };
      var key = it.key || it.name || it.env || it.id || '';
      var value = (it.value != null) ? it.value
        : (it.default != null) ? it.default
        : (it.example != null) ? it.example : '';
      return { key: String(key), value: String(value) };
    }).filter(function (x) { return x.key; });
  }

  function envRowsBlock(env) {
    var wrap = el('div', 'env-table');
    normalizeEnv(env).forEach(function (it, i) {
      var row = el('div', 'env-row');
      var label = el('label', 'env-key ltr-block');
      label.setAttribute('for', 'env-in-' + i);
      label.textContent = it.key;
      var input = el('input', 'env-input');
      input.type = 'text';
      input.id = 'env-in-' + i;
      input.dir = 'ltr';
      input.value = it.value;
      input.dataset.key = it.key;
      input.autocomplete = 'off';
      input.spellcheck = false;
      row.appendChild(label);
      row.appendChild(input);
      wrap.appendChild(row);
    });
    return wrap;
  }

  function preparedFilesBlock(files) {
    var wrap = el('div', 'files-list');
    (files || []).forEach(function (f, idx) {
      var d = el('details', 'file-item');
      var name = (f && f.name) ? f.name : 'file-' + (idx + 1);
      d.appendChild(el('summary', 'file-name ltr-block', esc(name)));
      var pre = el('pre', 'file-content ltr-block');
      pre.textContent = (f && f.content != null) ? String(f.content) : '';
      d.appendChild(pre);
      wrap.appendChild(d);
    });
    return wrap;
  }

  /* ----------------------------------------------------------
   * Liara account connect — the user's OWN token, checked live.
   * The token lives in RAM only (store.liaraToken) and travels
   * with the approve call so the deploy runs on the user's own
   * account. Nothing is ever echoed back.
   * ---------------------------------------------------------- */

  function invoiceBlock(a) {
    var wrap = el('div', 'cost-grid');
    var rows = [
      ['پلن پیشنهادی', String(a.default_plan || '—'), 'بر اساس پلتفرم پروژه'],
      ['هر ساعت', a.plan_price_hourly_toman != null ? faNum(a.plan_price_hourly_toman) : '—', 'تومان'],
      ['هر ماه', a.plan_price_monthly_toman != null ? faNum(a.plan_price_monthly_toman) : '—', 'تومان (۷۳۰ ساعت)']
    ];
    rows.forEach(function (row) {
      var cell = el('div', 'cost-cell');
      cell.appendChild(el('span', 'cost-cell-label', esc(row[0])));
      cell.appendChild(el('span', 'cost-cell-val ltr-block', esc(row[1])));
      cell.appendChild(el('span', 'cost-cell-unit', esc(row[2])));
      wrap.appendChild(cell);
    });
    if (a.balance_toman != null && a.plan_price_hourly_toman) {
      var hours = a.balance_toman / a.plan_price_hourly_toman;
      var note;
      if (hours >= 720) note = 'موجودی تو برای بیش از ' + faNum(Math.floor(hours / 720)) + ' ماه روشن‌ماندن این پلن کافی است.';
      else if (hours >= 48) note = 'موجودی تو برای حدود ' + faNum(Math.floor(hours / 24)) + ' روز روشن‌ماندن این پلن کافی است.';
      else if (hours >= 24) note = 'موجودی تو برای حدود ' + faNum(Math.floor(hours)) + ' ساعت کار با این پلن کافی است — بهتر است شارژ کنی.';
      else {
        note = 'موجودی برای ۲۴ ساعت روشن‌ماندن کافی نیست — اول از پنل لیارا اعتبار شارژ کن.';
        wrap.classList.add('is-low');
      }
      wrap.appendChild(el('p', 'cost-note' + (wrap.classList.contains('is-low') ? ' cost-note-warn' : ''), note));
    }
    return wrap;
  }

  function liaraAccountSection() {
    var wrap = el('div', 'acct-connect');
    wrap.appendChild(el('h4', 'modal-sec-title', 'اتصال حساب لیارا'));
    wrap.appendChild(el('p', 'muted small',
      'استقرار روی حسابِ خودِ تو ساخته می‌شود و هزینه از اعتبار حسابِ تو کم می‌شود. توکن API لیارای خودت را بده:'));

    var steps = el('ol', 'acct-steps');
    [
      ['console.liara.ir', 'باز کن — ثبت‌نام رایگان است.'],
      ['از منوی «API»', 'یک توکن جدید بساز و کپی کن.'],
      ['اعتبار', 'مطمئن شو حساب تو شارژ شده باشد.']
    ].forEach(function (t, i) {
      var li = el('li', 'acct-step');
      li.appendChild(el('span', 'acct-step-num', faDigits(i + 1)));
      li.appendChild(el('span', 'acct-step-text', ' '));
      var b = el('b', 'ltr-block-inline', t[0]);
      li.appendChild(b);
      li.appendChild(document.createTextNode(' ' + t[1]));
      steps.appendChild(li);
    });
    wrap.appendChild(steps);

    var row = el('div', 'acct-row');
    var input = el('input', 'env-input acct-token');
    input.type = 'password';
    input.dir = 'ltr';
    input.placeholder = 'توکن API لیارا (eyJ…)';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.setAttribute('aria-label', 'توکن API لیارا');
    var btn = el('button', 'btn btn-ghost btn-sm acct-check', 'بررسی حساب');
    btn.type = 'button';
    row.appendChild(input);
    row.appendChild(btn);
    wrap.appendChild(row);

    if (store.account && store.account.connected === true) {
      wrap.appendChild(el('p', 'muted small acct-hint',
        'بدون توکن، استقرار از حساب پیش‌فرضِ سرویس انجام می‌شود — با توکن، همه‌چیز مال حساب خودت می‌شود.'));
    } else {
      wrap.appendChild(el('p', 'muted small acct-hint acct-hint-warn',
        'برای استقرار واقعی، توکن حساب خودت را وارد و بررسی کن.'));
    }

    var out = el('div', 'acct-result');
    out.hidden = true;
    wrap.appendChild(out);

    var aerr = el('div', 'error-box');
    aerr.hidden = true;
    aerr.setAttribute('role', 'alert');
    wrap.appendChild(aerr);

    btn.addEventListener('click', function () {
      var tok = input.value.trim();
      aerr.hidden = true;
      out.hidden = true;
      if (tok.length < 20) {
        aerr.textContent = 'توکن خیلی کوتاه است — توکن کامل را از کنسول لیارا کپی کن.';
        aerr.hidden = false;
        return;
      }
      btn.disabled = true;
      btn.textContent = 'در حال بررسی…';
      api.accountCheck({ token: tok }).then(function (a) {
        btn.disabled = false;
        btn.textContent = 'بررسی حساب';
        store.liaraToken = tok;
        store.userAccount = a || null;
        out.innerHTML = '';
        var okrow = el('div', 'acct-okrow');
        okrow.appendChild(el('span', 'badge badge-emerald',
          '✓ حساب متصل' + (a && a.team_name ? ' — تیم «' + a.team_name + '»' : '')));
        if (a && a.apps_count != null) {
          okrow.appendChild(el('span', 'muted small', faNum(a.apps_count) + ' برنامه در حساب تو'));
        }
        out.appendChild(okrow);
        if (a && a.default_plan && a.plan_price_hourly_toman != null) {
          out.appendChild(el('p', 'sec-inline-label', 'فاکتور استقرار — با قیمت زنده از حساب تو'));
          out.appendChild(invoiceBlock(a));
        }
        out.hidden = false;
      }).catch(function (e) {
        btn.disabled = false;
        btn.textContent = 'بررسی حساب';
        store.liaraToken = '';
        store.userAccount = null;
        aerr.textContent = (e && e.detail) ? String(e.detail) : 'بررسی حساب ناموفق بود — دوباره تلاش کن.';
        aerr.hidden = false;
      });
    });
    return wrap;
  }

  function openApprovalModal(p) {
    p = p || {};
    store.pendingApproval = p;
    var modal = $('#modal');
    modal.innerHTML = '';
    modal.classList.remove('is-approval', 'is-question', 'is-ops');

    var isQuestion = p.kind === 'question';
    modal.classList.add(isQuestion ? 'is-question' : 'is-approval');

    var title = el('h3', 'modal-title', isQuestion ? 'خلبان یک سؤال دارد' : 'نیاز به تأیید تو');
    title.id = 'modal-title';
    modal.appendChild(title);
    modal.appendChild(el('p', 'muted modal-sub',
      isQuestion ? 'خلبان از روی لاگ‌ها به چیزی نیاز دارد که فقط تو می‌توانی بدهی.'
                 : 'هیچ کار هزینه‌داری بدون اجازهٔ تو انجام نمی‌شود. هزینه را ببین، تصمیم بگیر.'));

    if (isQuestion) {
      modal.appendChild(el('p', 'question-text', String(p.question_fa || '')));
      var reply = el('input', 'env-input');
      reply.type = 'text';
      reply.id = 'ask-reply';
      reply.placeholder = 'پاسخ‌ات را همین‌جا بنویس…';
      modal.appendChild(reply);

      var errBoxQ = el('div', 'error-box');
      errBoxQ.hidden = true;
      errBoxQ.setAttribute('role', 'alert');
      modal.appendChild(errBoxQ);

      var actionsQ = el('div', 'modal-actions');
      var sendBtn = el('button', 'btn btn-primary', 'ارسال پاسخ');
      sendBtn.type = 'button';
      sendBtn.id = 'btn-approve';
      var skipBtn = el('button', 'btn btn-ghost', 'بدون پاسخ');
      skipBtn.type = 'button';
      skipBtn.id = 'btn-reject';
      actionsQ.appendChild(sendBtn);
      actionsQ.appendChild(skipBtn);
      modal.appendChild(actionsQ);

      sendBtn.addEventListener('click', function () {
        sendReply(String(reply.value || ''), sendBtn, skipBtn, errBoxQ);
      });
      skipBtn.addEventListener('click', function () {
        sendReply('', skipBtn, sendBtn, errBoxQ);
      });

      $('#modal-overlay').hidden = false;
      reply.focus();
      return;
    }

    modal.appendChild(el('h4', 'modal-sec-title', 'فاکتور استقرار'));
    modal.appendChild(costBlock(p));

    modal.appendChild(liaraAccountSection());

    var envItems = normalizeEnv(p.env_needed);
    if (envItems.length) {
      modal.appendChild(el('h4', 'modal-sec-title', 'متغیرهای محیطی'));
      modal.appendChild(el('p', 'muted small', 'مقادیر را وارد کن؛ فقط برای همین استقرار استفاده می‌شوند و هرگز جایی نمایش یا ذخیره نمی‌شوند.'));
      modal.appendChild(envRowsBlock(p.env_needed));
    }

    if (Array.isArray(p.prepared_files) && p.prepared_files.length) {
      modal.appendChild(el('h4', 'modal-sec-title', 'فایل‌های آماده‌شده'));
      modal.appendChild(preparedFilesBlock(p.prepared_files));
    }

    var errBox = el('div', 'error-box');
    errBox.hidden = true;
    errBox.setAttribute('role', 'alert');
    modal.appendChild(errBox);

    var actions = el('div', 'modal-actions');
    var okBtn = el('button', 'btn btn-primary', 'تأیید و استقرار');
    okBtn.type = 'button';
    okBtn.id = 'btn-approve';
    var noBtn = el('button', 'btn btn-ghost', 'لغو');
    noBtn.type = 'button';
    noBtn.id = 'btn-reject';
    actions.appendChild(okBtn);
    actions.appendChild(noBtn);
    modal.appendChild(actions);

    okBtn.addEventListener('click', function () { sendApproval(true, okBtn, noBtn, errBox); });
    noBtn.addEventListener('click', function () { sendApproval(false, okBtn, noBtn, errBox); });

    $('#modal-overlay').hidden = false;
    okBtn.focus();
  }

  /** Answer an ask_user interrupt: a free-text reply for the agent. */
  function sendReply(text, okBtn, noBtn, errBox) {
    errBox.hidden = true;
    okBtn.disabled = true;
    noBtn.disabled = true;
    okBtn.textContent = 'در حال ارسال پاسخ…';
    var replied = store.pendingApproval; // the question this reply answers
    api.approve(store.sessionId, { approved: true, user_reply: text }).then(function () {
      // The server may resume the graph before this POST resolves; if the NEXT
      // interrupt already re-opened the modal, keep it instead of closing it.
      if (!store.pendingApproval || store.pendingApproval === replied) closeModal();
      addNote('پاسخ تو به خلبان رسید؛ ادامه‌ی اصلاح…');
    }).catch(function (err) {
      okBtn.disabled = false;
      noBtn.disabled = false;
      okBtn.textContent = 'ارسال پاسخ';
      errBox.hidden = false;
      errBox.textContent = (err && err.message) ? err.message : 'ارسال پاسخ ناموفق بود.';
    });
  }

  function collectEnvValues() {
    var out = {};
    var inputs = $('#modal').querySelectorAll('.env-input');
    for (var i = 0; i < inputs.length; i++) {
      if (inputs[i].dataset.key) out[inputs[i].dataset.key] = inputs[i].value.trim();
    }
    return out;
  }

  function sendApproval(approved, okBtn, noBtn, errBox) {
    errBox.hidden = true;
    okBtn.disabled = true;
    noBtn.disabled = true;
    if (approved) okBtn.textContent = 'در حال ارسال تأیید…';

    var body = approved
      ? { approved: true, cost_ok: true, env_values: collectEnvValues() }
      : { approved: false };
    // The user's own Liara token (checked in the modal) rides along so the
    // deployer rebinds to the USER'S account — never the service default.
    if (approved && store.liaraToken) body.liara_token = store.liaraToken;
    api.approve(store.sessionId, body).then(function () {
      closeModal();
      if (approved) {
        addNote('تأیید دریافت شد؛ استقرار آغاز شد.');
      } else {
        store.cancelled = true;
        disableInputs(true);
        addNote('درخواست استقرار لغو شد.');
      }
    }).catch(function (e) {
      if (isExpired(e)) return;
      errBox.innerHTML = '';
      errBox.appendChild(document.createTextNode('ارسال پاسخ ناموفق بود. دوباره تلاش کن.'));
      if (e.detail) errBox.appendChild(el('span', 'ltr-block', esc(e.detail)));
      errBox.hidden = false;
      okBtn.disabled = false;
      noBtn.disabled = false;
      okBtn.textContent = 'تأیید و استقرار';
    });
  }

  function closeModal() {
    $('#modal-overlay').hidden = true;
    $('#modal').innerHTML = '';
    $('#modal').classList.remove('is-approval', 'is-question', 'is-ops');
    store.pendingApproval = null;
  }

  /** Minimal focus trap so Tab cannot escape the dialog. */
  function wireModalTrap() {
    $('#modal-overlay').addEventListener('keydown', function (ev) {
      if (ev.key !== 'Tab') return;
      var focusables = $('#modal').querySelectorAll('button, input, summary, textarea, a[href]');
      if (!focusables.length) return;
      var first = focusables[0];
      var last = focusables[focusables.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault();
        first.focus();
      }
    });
  }

  /* ----------------------------------------------------------
   * D) Report screen
   * ---------------------------------------------------------- */

  function goReport() {
    stopSSE();
    disableInputs(true);
    showScreen('report');
    renderReport();
    if (store.report && (store.report.url || store.report.live_url)) {
      startMonitorPolling();
      startCarePolling(); // استارت ایجنت: وضعیت و گزارش‌های زنده
    }
  }

  /* ----------------------------------------------------------
   * Continuous monitoring panel — poll /monitor on the report screen.
   * Source labels: liara_api values stay UNVERIFIED-badged until a live
   * read confirms units; inference is always labelled «استنتاج».
   * ---------------------------------------------------------- */

  function startMonitorPolling() {
    if (store.monitorTimer) return;
    renderMonitor(); // immediate first paint
    store.monitorTimer = setInterval(renderMonitor, 15000);
  }

  function stopMonitorPolling() {
    if (store.monitorTimer) { clearInterval(store.monitorTimer); store.monitorTimer = null; }
  }

  function monitorSourceBadge(sample) {
    if (sample.source === 'liara_api') return el('span', 'badge badge-amber monitor-src', UNVERIFIED_BADGE);
    return el('span', 'badge badge-zinc monitor-src', 'استنتاج');
  }

  function renderMonitor() {
    var box = $('#monitor-box');
    if (!box || !store.sessionId) return;
    api.monitor(store.sessionId).then(function (data) {
      box.innerHTML = '';
      var s = (data && data.sample) || {};
      var alerting = !!(data && data.alerting);

      if (alerting) {
        var alert = el('div', 'monitor-alert', 'هشدار: سلامت برنامه افت کرده است. پیشنهاد خلبان: ری‌استارت (فقط با تأیید تو).');
        var ab = el('button', 'btn btn-ghost btn-sm ops-btn', 'ری‌استارت برنامه');
        ab.type = 'button';
        ab.addEventListener('click', function () { openOpsApprovalModal('restart'); });
        alert.appendChild(ab);
        box.appendChild(alert);
      }

      var row = el('div', 'monitor-row');
      var ok = (s.healthy !== undefined) ? !!s.healthy : (s.probe && s.probe.ok);
      row.appendChild(el('span', 'badge ' + (ok ? 'badge-emerald' : 'badge-red'), ok ? 'سالم' : 'ناسالم'));
      row.appendChild(monitorSourceBadge(s));
      if (s.probe && s.probe.response_ms != null) {
        row.appendChild(el('span', 'muted small', 'زمان پاسخ: ' + faDigits(s.probe.response_ms) + ' میلی‌ثانیه'));
      }
      if (s.plan_id) {
        row.appendChild(el('span', 'muted small', 'پلن زنده: '));
        row.appendChild(el('span', 'ltr-block', esc(s.plan_id)));
      }
      box.appendChild(row);

      var hist = (data && data.history) || [];
      if (hist.length) {
        var ul = el('ul', 'monitor-history');
        hist.forEach(function (h, idx) {
          var li = el('li', (h.healthy ? '' : 'bad') + (idx < hist.length - 12 ? ' old' : ''));
          li.title = (h.healthy ? 'سالم' : 'ناسالم') + ' — ' + new Date((h.ts || 0) * 1000).toLocaleTimeString('fa-IR');
          ul.appendChild(li);
        });
        box.appendChild(ul);
      }
    }).catch(function (e) {
      if (isExpired(e)) { stopMonitorPolling(); }
    });
  }

  /* ----------------------------------------------------------
   * Star Agent (ایجنت ستاره) — the permanent care agent.
   * Polls GET /care every 10s on the report screen; schedule picker
   * (daily/weekly/monthly/off), manual check, care reports (score,
   * findings with severity, actionable tips) and the copyable brief
   * the user can hand to their own agent.
   * ---------------------------------------------------------- */

  var CARE_FA = { daily: 'هر روز', weekly: 'هر هفته', monthly: 'هر ماه', off: 'خاموش', '': 'خاموش' };

  function startCarePolling() {
    if (store.careTimer) return;
    loadCare(); // immediate first paint
    store.careTimer = setInterval(loadCare, 10000);
  }

  function stopCarePolling() {
    if (store.careTimer) { clearInterval(store.careTimer); store.careTimer = null; }
  }

  function loadCare() {
    if (!store.sessionId || store.screen !== 'report') return;
    api.careState(store.sessionId).then(function (data) {
      store.care = data || {};
      renderCare();
    }).catch(function (e) {
      if (isExpired(e)) stopCarePolling();
    });
  }

  function careScoreBadge(score) {
    var n = Number(score);
    if (!Number.isFinite(n)) return el('span', 'badge badge-zinc', '—');
    var cls = n >= 80 ? 'badge-emerald' : (n >= 60 ? 'badge-amber' : 'badge-red');
    return el('span', 'badge ' + cls, 'نمرهٔ سلامت: ' + faDigits(n) + ' از ۱۰۰');
  }

  var FINDING_SEV = {
    critical: { label: 'بحرانی', cls: 'badge-red' },
    warning:  { label: 'هشدار',  cls: 'badge-amber' },
    info:     { label: 'نکته',   cls: 'badge-zinc' }
  };

  function careTriggerLabel(t) {
    if (t === 'first') return 'اولین بررسی';
    if (t === 'scheduled') return 'پایش دوره‌ای';
    if (t === 'manual') return 'بررسی فوری';
    return 'بررسی';
  }

  function faClock(tsSeconds) {
    try {
      return new Date(tsSeconds * 1000).toLocaleString('fa-IR',
        { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch (e) { return ''; }
  }

  function faDuration(seconds) {
    var s = Math.max(0, Math.round(seconds));
    if (s < 90) return faDigits(s) + ' ثانیه بعد';
    var m = Math.round(s / 60);
    if (m < 90) return faDigits(m) + ' دقیقه بعد';
    var h = Math.round(m / 60);
    if (h < 36) return faDigits(h) + ' ساعت بعد';
    var d = Math.round(h / 24);
    return faDigits(d) + ' روز بعد';
  }

  function careReportCard(rec, idx) {
    var li = el('li', 'care-report' + (idx === 0 ? ' care-report-new' : ''));
    if (idx > 2 && !store.careShowAll) li.hidden = true;

    var head = el('div', 'care-report-head');
    head.appendChild(careScoreBadge(rec.score));
    head.appendChild(el('span', 'badge badge-zinc', careTriggerLabel(rec.trigger)));
    if (rec.ts) head.appendChild(el('span', 'muted small', faClock(rec.ts)));
    li.appendChild(head);

    if (rec.health) {
      var hrow = el('div', 'care-health-row');
      var ok = rec.health.ok;
      hrow.appendChild(el('span', 'badge ' + (ok ? 'badge-emerald' : 'badge-red'), ok ? 'HTTP سالم' : 'پاسخ سالم نداد'));
      if (rec.health.avg_ms != null) {
        hrow.appendChild(el('span', 'muted small', 'میانگین زمان پاسخ: ' + faDigits(rec.health.avg_ms) + ' میلی‌ثانیه'));
      }
      if (rec.health.worst_ms != null && rec.health.n > 1) {
        hrow.appendChild(el('span', 'muted small', 'کندترین: ' + faDigits(rec.health.worst_ms) + 'ms'));
      }
      li.appendChild(hrow);
    }

    if (rec.verdict_fa) li.appendChild(el('p', 'care-verdict', esc(rec.verdict_fa)));

    if (Array.isArray(rec.findings) && rec.findings.length) {
      var ul = el('ul', 'care-findings');
      rec.findings.forEach(function (f) {
        var sev = FINDING_SEV[f.severity] || FINDING_SEV.info;
        var item = el('li', 'care-finding');
        var headF = el('div', 'care-finding-head');
        headF.appendChild(el('span', 'badge ' + sev.cls, sev.label));
        headF.appendChild(el('span', 'care-finding-title', esc(f.title_fa || '')));
        item.appendChild(headF);
        if (f.detail_fa) item.appendChild(el('p', 'care-finding-detail', esc(f.detail_fa)));
        if (f.evidence) item.appendChild(el('span', 'care-finding-evidence ltr-block', esc(f.evidence)));
        ul.appendChild(item);
      });
      li.appendChild(ul);
    }

    if (Array.isArray(rec.tips_fa) && rec.tips_fa.length) {
      var tips = el('ul', 'checklist care-tips');
      rec.tips_fa.forEach(function (t) { tips.appendChild(el('li', '', esc(t))); });
      li.appendChild(tips);
    }
    return li;
  }

  function careSection() {
    var sec = el('section', 'card report-sec care-card');
    sec.id = 'care-box';
    return sec;
  }

  function renderCare() {
    var box = $('#care-box');
    if (!box) return;
    var c = store.care || {};
    var reports = Array.isArray(c.reports) ? c.reports.slice().reverse() : []; // newest first
    box.innerHTML = '';

    // ---- header
    var head = el('div', 'care-head');
    var icon = el('span', 'care-icon',
      '<svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 3 1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3z"/></svg>');
    icon.setAttribute('aria-hidden', 'true');
    head.appendChild(icon);
    var titles = el('div', 'care-head-titles');
    titles.appendChild(el('h4', 'sec-title care-title', 'استارت ایجنت'));
    titles.appendChild(el('p', 'muted small',
      'بعد از استقرار می‌مانم و مثل یک کارشناس ارزیاب، دوره‌ای سایتت را چک می‌کنم — سرعت، سلامت و لاگ‌ها. اگر چیزی افت کند می‌گویم چرا و چطور درستش کنی.'));
    head.appendChild(titles);
    if (c.schedule && c.schedule !== 'off') {
      head.appendChild(el('span', 'badge badge-emerald', 'فعال: ' + (CARE_FA[c.schedule] || c.schedule)));
    } else {
      head.appendChild(el('span', 'badge badge-zinc', 'غیرفعال'));
    }
    box.appendChild(head);

    // ---- schedule picker
    var pick = el('div', 'care-pick');
    pick.setAttribute('role', 'group');
    pick.setAttribute('aria-label', 'زمان‌بندی پایش');
    pick.appendChild(el('span', 'sec-inline-label', 'هر چند وقت یک بار سایتت را چک کنم؟'));
    var chips = el('div', 'chip-wrap care-chips');
    ['daily', 'weekly', 'monthly'].forEach(function (v) {
      var active = c.schedule === v;
      var b = el('button', 'chip care-chip' + (active ? ' care-chip-on' : ''), CARE_FA[v]);
      b.type = 'button';
      b.setAttribute('aria-pressed', String(active));
      b.addEventListener('click', function () { setCareSchedule(v); });
      chips.appendChild(b);
    });
    var offBtn = el('button', 'chip care-chip care-chip-off' + (!c.schedule ? ' care-chip-on' : ''), 'خاموش');
    offBtn.type = 'button';
    offBtn.setAttribute('aria-pressed', String(!c.schedule));
    offBtn.addEventListener('click', function () { setCareSchedule('off'); });
    chips.appendChild(offBtn);
    pick.appendChild(chips);
    box.appendChild(pick);

    // ---- status row + actions
    var row = el('div', 'care-actions');
    if (c.running) {
      var st = el('span', 'badge badge-amber care-status');
      st.innerHTML = '<span class="spinner spinner-sm" aria-hidden="true"></span> در حال بررسی سایت…';
      row.appendChild(st);
    } else if (c.schedule && c.next_ts) {
      row.appendChild(el('span', 'care-status muted small',
        'پایش بعدی: ' + faDuration(c.next_ts - Date.now() / 1000)));
    }
    var btnRun = el('button', 'btn btn-ghost btn-sm ops-btn', 'پایش فوری');
    btnRun.type = 'button';
    btnRun.disabled = !!c.running;
    btnRun.addEventListener('click', function () {
      btnRun.disabled = true;
      btnRun.textContent = 'در حال شروع…';
      api.careRun(store.sessionId).then(function () {
        c.running = true;
        renderCare();
      }).catch(function (e) {
        if (!isExpired(e)) addNote('پایش فوری شروع نشد؛ دوباره تلاش کن.');
        renderCare();
      });
    });
    row.appendChild(btnRun);

    var btnBrief = el('button', 'btn btn-ghost btn-sm ops-btn', 'خلاصه برای ایجنت من');
    btnBrief.type = 'button';
    btnBrief.addEventListener('click', openBriefModal);
    row.appendChild(btnBrief);
    box.appendChild(row);

    // ---- reports
    var listWrap = el('div', 'care-list-wrap');
    if (!reports.length) {
      listWrap.appendChild(el('p', 'muted small', 'هنوز گزارشی نیست. یک زمان‌بندی انتخاب کن یا «پایش فوری» را بزن — اولین گزارش چند ثانیه بعد می‌آید.'));
    } else {
      listWrap.appendChild(el('span', 'sec-inline-label', 'گزارش‌های استارت ایجنت'));
      var ul = el('ul', 'care-reports');
      reports.forEach(function (r, i) { ul.appendChild(careReportCard(r, i)); });
      listWrap.appendChild(ul);
      if (reports.length > 3) {
        var more = el('button', 'btn btn-ghost btn-sm', store.careShowAll
          ? 'نمایش کمتر'
          : 'نمایش ' + faDigits(reports.length - 3) + ' گزارش قدیمی‌تر');
        more.type = 'button';
        more.addEventListener('click', function () {
          store.careShowAll = !store.careShowAll;
          var items = ul.children;
          for (var i = 0; i < items.length; i++) items[i].hidden = (i > 2) && !store.careShowAll;
          more.textContent = store.careShowAll
            ? 'نمایش کمتر'
            : 'نمایش ' + faDigits(reports.length - 3) + ' گزارش قدیمی‌تر';
        });
        listWrap.appendChild(more);
      }
    }
    box.appendChild(listWrap);
  }

  function setCareSchedule(v) {
    var box = $('#care-box');
    var chips = box ? box.querySelectorAll('.care-chip') : [];
    for (var i = 0; i < chips.length; i++) { chips[i].disabled = true; chips[i].classList.remove('care-chip-on'); }
    api.careSet(store.sessionId, { schedule: v }).then(function () {
      // 202 accepted; the first check starts server-side. Optimistic state:
      store.care = store.care || {};
      store.care.schedule = (v === 'off') ? '' : v;
      if (v !== 'off') store.care.running = true; // first check fires immediately
      renderCare();
    }).catch(function (e) {
      if (!isExpired(e)) addNote('تغییر زمان‌بندی اعمال نشد؛ دوباره تلاش کن.');
      for (var j = 0; j < chips.length; j++) chips[j].disabled = false;
      loadCare();
    });
  }

  /** The copyable brief the user hands to their own agent. */
  function openBriefModal() {
    var modal = $('#modal');
    modal.innerHTML = '';
    modal.classList.remove('is-approval', 'is-question', 'is-ops');
    modal.classList.add('is-ops');
    var title = el('h3', 'modal-title', 'خلاصه برای ایجنت تو');
    title.id = 'modal-title';
    modal.appendChild(title);
    modal.appendChild(el('p', 'muted modal-sub',
      'این متن را کپی کن و به هر ایجنتی که داری بده (چت‌بات، ابزار کدنویسی…) — می‌داند نگهبان این سایت چیست و هر دوره چه چیزهایی را چک کند.'));
    var pre = el('textarea', 'brief-text');
    pre.id = 'brief-text';
    pre.readOnly = true;
    pre.rows = 12;
    pre.dir = 'rtl';
    pre.textContent = 'در حال ساخت خلاصه…';
    modal.appendChild(pre);

    var errBox = el('div', 'error-box');
    errBox.hidden = true;
    errBox.setAttribute('role', 'alert');
    modal.appendChild(errBox);

    var actions = el('div', 'modal-actions');
    var copyBtn = el('button', 'btn btn-primary', 'کپی متن');
    copyBtn.type = 'button';
    var closeBtn = el('button', 'btn btn-ghost', 'بستن');
    closeBtn.type = 'button';
    actions.appendChild(copyBtn);
    actions.appendChild(closeBtn);
    modal.appendChild(actions);

    copyBtn.addEventListener('click', function () {
      var text = pre.value;
      var done = function () {
        copyBtn.textContent = 'کپی شد ✓';
        setTimeout(function () { copyBtn.textContent = 'کپی متن'; }, 1800);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(function () { copyFallback(pre, done); });
      } else {
        copyFallback(pre, done);
      }
    });
    closeBtn.addEventListener('click', closeModal);
    $('#modal-overlay').hidden = false;
    copyBtn.focus();

    api.careBrief(store.sessionId).then(function (res) {
      pre.value = (res && res.brief) || 'خلاصه‌ای ساخته نشد — دوباره تلاش کن.';
    }).catch(function (e) {
      pre.value = '';
      errBox.hidden = false;
      errBox.textContent = isExpired(e) ? '' : 'ساخت خلاصه ناموفق بود؛ دوباره تلاش کن.';
    });
  }

  function copyFallback(textarea, done) {
    try {
      textarea.select();
      document.execCommand('copy');
      done();
    } catch (e) { /* best-effort */ }
  }

  /* ----------------------------------------------------------
   * Report rendering
   * ---------------------------------------------------------- */

  function reportStatus(r) {
    if (store.cancelled) return { label: 'لغو شد', cls: 'st-off' };
    var s = String(r.status || '').toLowerCase();
    if (/decision|approval|await|need|confirm/.test(s)) return { label: 'نیاز به تصمیم تو', cls: 'st-warn' };
    if (/cancel|abort|stop/.test(s)) return { label: 'لغو شد', cls: 'st-off' };
    if (/success|done|deployed|live|complete|finish|ok/.test(s)) return { label: 'موفق — سایتت زنده است', cls: 'st-ok' };
    var url = r.url || r.live_url || (r.deployment && r.deployment.url);
    if (url) return { label: 'موفق — سایتت زنده است', cls: 'st-ok' };
    if (!r.status) return { label: 'نیاز به تصمیم تو', cls: 'st-warn' };
    return { label: 'نیاز به تصمیم تو', cls: 'st-warn' };
  }

  function sectionCard(title) {
    var sec = el('section', 'card report-sec');
    sec.appendChild(el('h4', 'sec-title', esc(title)));
    return sec;
  }

  function checklist(items) {
    var ul = el('ul', 'checklist');
    items.forEach(function (t) {
      var text = (typeof t === 'string') ? t : ((t && (t.text || t.title || t.detail)) || '');
      ul.appendChild(el('li', '', esc(text)));
    });
    return ul;
  }

  /* Post-deploy operations section — the three-tier legend + gated buttons. */
  function opsSection() {
    var sec = sectionCard('عملیات بعد از استقرار');
    var legend = el('div', 'chip-wrap risk-legend');
    legend.appendChild(tierBadge({ tier: 'auto', tier_fa: 'خودکار — فقط خواندنی', tier_level: 1 }));
    legend.appendChild(tierBadge({ tier: 'approval', tier_fa: 'نیازمند تأیید تو', tier_level: 2 }));
    legend.appendChild(tierBadge({ tier: 'forbidden', tier_fa: 'فقط دستی — هرگز اجرا نمی‌شود', tier_level: 3 }));
    sec.appendChild(legend);
    sec.appendChild(el('p', 'muted small',
      'خواندن وضعیت/لاگ/مصرف خودکار است؛ ری‌استارت و تغییر پلن فقط با تأیید تو؛ حذف اپ یا دیسک هرگز توسط خلبان انجام نمی‌شود.'));
    var row = el('div', 'ops-actions');
    var btnRestart = el('button', 'btn btn-ghost ops-btn', 'ری‌استارت برنامه');
    btnRestart.type = 'button';
    btnRestart.id = 'btn-ops-restart';
    btnRestart.appendChild(tierBadge({ tier: 'approval', tier_fa: 'سطح ۲', tier_level: 2 }));
    btnRestart.addEventListener('click', function () { openOpsApprovalModal('restart'); });
    row.appendChild(btnRestart);
    sec.appendChild(row);
    var mwrap = el('div', 'monitor-box');
    mwrap.id = 'monitor-box';
    sec.appendChild(mwrap);
    return sec;
  }

  /** Tier-2 approval modal for a post-deploy operation (restart for now). */
  function openOpsApprovalModal(action) {
    var modal = $('#modal');
    modal.innerHTML = '';
    modal.classList.remove('is-approval', 'is-question', 'is-ops');
    modal.classList.add('is-ops');
    var title = el('h3', 'modal-title', 'اقدام سطح ۲ — نیازمند تأیید تو');
    title.id = 'modal-title';
    modal.appendChild(title);
    var text = (action === 'restart')
      ? 'ری‌استارت برنامه باعث قطع موقت سرویس می‌شود و بعد از آن برنامه دوباره بالا می‌آید. ادامه می‌دهی؟'
      : 'این اقدام سطح ۲ است و فقط با تأیید تو اجرا می‌شود. ادامه می‌دهی؟';
    modal.appendChild(el('p', 'modal-sub', text));
    var errBox = el('div', 'error-box');
    errBox.hidden = true;
    errBox.setAttribute('role', 'alert');
    modal.appendChild(errBox);
    var actions = el('div', 'modal-actions');
    var okBtn = el('button', 'btn btn-primary', 'تأیید و اجرا');
    okBtn.type = 'button';
    var noBtn = el('button', 'btn btn-ghost', 'لغو');
    noBtn.type = 'button';
    actions.appendChild(okBtn);
    actions.appendChild(noBtn);
    modal.appendChild(actions);
    okBtn.addEventListener('click', function () {
      errBox.hidden = true;
      okBtn.disabled = true; noBtn.disabled = true;
      okBtn.textContent = 'در حال اجرا…';
      api.ops(store.sessionId, { action: action, approved: true }).then(function (res) {
        closeModal();
        // The report screen has no live timeline — show the result in the ops panel.
        var box = $('#monitor-box');
        var text2 = (action === 'restart' ? 'ری‌استارت: ' : 'عملیات: ')
          + ((res && (res.message_fa || res.message)) ? (res.message_fa || res.message) : 'ثبت شد.');
        if (box) {
          box.innerHTML = '';
          var note = el('div', 'monitor-row ops-result');
          note.appendChild(el('span', 'badge badge-emerald', 'انجام شد'));
          note.appendChild(el('span', 'ltr-block', esc(text2)));
          box.appendChild(note);
        } else {
          addNote(text2);
        }
      }).catch(function (e) {
        okBtn.disabled = false; noBtn.disabled = false;
        okBtn.textContent = 'تأیید و اجرا';
        errBox.hidden = false;
        errBox.textContent = (e && e.detail === 'approval_required')
          ? 'بدون تأیید صریح اجرا نمی‌شود.'
          : 'اجرای اقدام ناموفق بود. دوباره تلاش کن.';
      });
    });
    noBtn.addEventListener('click', function () { closeModal(); });
    $('#modal-overlay').hidden = false;
    okBtn.focus();
  }

  function renderReport() {
    var root = $('#report-root');
    root.innerHTML = '';
    var r = store.report || {};

    if (!store.report) {
      root.appendChild(el('p', 'muted', 'گزارشی برای نمایش ثبت نشد.'));
      return;
    }

    // Status header
    var st = reportStatus(r);
    var banner = el('div', 'report-status ' + st.cls);
    banner.appendChild(el('span', 'status-label', st.label));
    root.appendChild(banner);

    // Live URL
    var url = r.url || r.live_url || (r.deployment && r.deployment.url);
    if (url) {
      var secUrl = sectionCard('آدرس زندهٔ سایتت');
      var box = el('div', 'url-box');
      var a = el('a', 'live-link ltr-block');
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = url;
      box.appendChild(a);
      secUrl.appendChild(box);
      root.appendChild(secUrl);

      // Post-deploy operations: each action shows its risk tier.
      root.appendChild(opsSection());

      // Star Agent (ایجنت ستاره) dashboard.
      root.appendChild(careSection());
      renderCare(); // initial paint (poll keeps it fresh)
    }

    // Steps taken
    var rawSteps = r.steps || r.steps_taken || [];
    var steps = rawSteps.map(function (s) {
      if (typeof s === 'string') return { title: s, detail: '' };
      return {
        title: (s && (s.title_fa || s.title || s.name)) || '',
        detail: (s && (s.detail || s.detail_fa || s.text)) || ''
      };
    }).filter(function (x) { return x.title || x.detail; });
    if (steps.length) {
      var secSteps = sectionCard('مرحله‌های انجام‌شده');
      var ol = el('ol', 'steps-list');
      steps.forEach(function (s) {
        var li = el('li', 'step-item');
        li.appendChild(el('span', 'step-title', esc(s.title)));
        if (s.detail) li.appendChild(el('span', 'step-detail', esc(s.detail)));
        ol.appendChild(li);
      });
      secSteps.appendChild(ol);
      root.appendChild(secSteps);
    }

    // Risks summary chips
    var rawRisks = r.risks || [];
    if (rawRisks.length) {
      var secRisks = sectionCard('ریسک‌های شناسایی‌شده');
      var wrap = el('div', 'chip-wrap');
      rawRisks.forEach(function (x) {
        var item = (typeof x === 'string')
          ? { sev: null, text: x }
          : {
              sev: x && x.severity,
              text: (x && (x.evidence_file || x.beginner_text || x.detail || x.id)) || ''
            };
        var sev = SEV[item.sev];
        var chip = el('span', 'risk-chip');
        if (sev) chip.appendChild(el('span', 'sev-dot ' + sev.dotCls));
        chip.appendChild(el('span', 'ltr-block', esc(item.text)));
        wrap.appendChild(chip);
      });
      secRisks.appendChild(wrap);
      root.appendChild(secRisks);
    }

    // Decisions (audit)
    var rawDecisions = r.decisions || r.audit || [];
    if (rawDecisions.length) {
      var secDec = sectionCard('تصمیم‌ها (حسابرسی)');
      var ul = el('ul', 'decisions-list');
      rawDecisions.forEach(function (d) {
        var text = (typeof d === 'string')
          ? d
          : ((d && (d.action || d.detail || d.summary || d.text || d.title_fa)) || JSON.stringify(d));
        ul.appendChild(el('li', 'decision-item ltr-block', esc(text)));
      });
      secDec.appendChild(ul);
      root.appendChild(secDec);
    }

    // Cost
    if (r.price_hourly_toman != null || r.estimated_cost != null || r.cost_unverified) {
      var secCost = sectionCard('هزینه');
      secCost.appendChild(costBlock(r));
      root.appendChild(secCost);
    }

    // Beginner "what you learned" checklist
    if (Array.isArray(r.learned) && r.learned.length) {
      var secLearn = el('section', 'card report-sec learned-card');
      secLearn.appendChild(el('h4', 'sec-title learned-title', 'چه چیزهایی یاد گرفتی'));
      secLearn.appendChild(checklist(r.learned));
      root.appendChild(secLearn);
    }

    // "Next time" checklist card
    if (Array.isArray(r.next_time) && r.next_time.length) {
      var secNext = el('section', 'card report-sec next-card');
      secNext.appendChild(el('h4', 'sec-title', 'برای دفع بعد'));
      secNext.appendChild(checklist(r.next_time));
      root.appendChild(secNext);
    }
  }

  /* ----------------------------------------------------------
   * Bootstrap
   * ---------------------------------------------------------- */

  function init() {
    wireUpload();
    wireModalTrap();
    initStepper();

    $('#btn-expert-skip').addEventListener('click', function () { submitAnswers(true); });
    $('#btn-start').addEventListener('click', function () { submitAnswers(false); });
    $('#btn-cancel').addEventListener('click', onCancel);
    $('#lvl-beginner').addEventListener('click', function () { onToggleLevel('beginner'); });
    $('#lvl-expert').addEventListener('click', function () { onToggleLevel('expert'); });
    $('#btn-new').addEventListener('click', function () { location.reload(); });
    $('#btn-restart').addEventListener('click', function () { location.reload(); });

    updateSampleBadge();
    showScreen('upload');
    loadAccount(); // real Liara account status in the header
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
