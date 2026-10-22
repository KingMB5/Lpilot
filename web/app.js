/* ============================================================
 * LiaraPilot — frontend logic
 * Vanilla JS, single IIFE, no globals, no dependencies.
 * Implements API CONTRACT v1 (see worklog.md):
 *   A) upload  ->  B) assessment  ->  C) run (SSE timeline)  ->  D) report
 * All technical content (logs, commands, URLs, paths, env keys,
 * cost numbers) renders inside .ltr-block so it stays readable
 * inside the RTL page.
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

  /** Create an element; `html` is innerHTML (caller must pre-escape). */
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  var cssEscape = (window.CSS && CSS.escape)
    ? function (s) { return CSS.escape(s); }
    : function (s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, function (c) { return '\\' + c; }); };

  var UNVERIFIED_BADGE = 'قیمت تخمینی — نیازمند تأیید (UNVERIFIED)';

  /* ----------------------------------------------------------
   * Store (single mutable state object)
   * ---------------------------------------------------------- */

  var store = {
    sessionId: null,
    demo: false,          // demo-mode flag -> amber badge in top bar
    level: 'beginner',    // 'beginner' | 'expert'
    screen: 'upload',
    questions: [],
    answers: {},          // {qid: value}
    risks: [],            // [{id, payload, bodyEl}] for level re-render
    report: null,
    maxI: -1,             // highest rendered event id (hydration + SSE dedupe)
    eventsSeen: false,    // snapshot had >= 1 event -> session already started
    hasPlan: false,
    planEl: null,         // plan card <li> (replaced, never duplicated)
    deployGroupEl: null,  // <ol> inside the "استقرار" group
    monitorTimer: null,   // T6 report-screen poll timer
    es: null,             // EventSource
    sseTimer: null,
    sseAttempts: 0,
    cancelled: false,
    hydrating: false,
    pendingApproval: null
  };

  /* ----------------------------------------------------------
   * API layer — every call funnels through req() so session
   * expiry (404 {detail:"session_expired"}) is handled once.
   * ---------------------------------------------------------- */

  function req(method, url, body) {
    var opts = { method: method, headers: {} };
    if (body instanceof FormData) {
      opts.body = body; // browser sets multipart boundary itself
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
    monitor: function (id) { return req('GET', '/api/sessions/' + encodeURIComponent(id) + '/monitor'); }
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
    if (name === 'run') bindAsk();  // mid-run Q&A input (T15)
    window.scrollTo(0, 0);
  }

  function updateDemoBadge() { $('#demo-badge').hidden = (store.demo !== true); }

  function showExpired() {
    if (store.screen === 'expired') return;
    stopSSE();
    closeModal();
    showScreen('expired');
  }

  function disableInputs(dis) {
    $('#btn-cancel').disabled = dis || store.cancelled;
    $('#lvl-beginner').disabled = dis;
    $('#lvl-expert').disabled = dis;
  }

  /* ----------------------------------------------------------
   * A) Upload screen
   * ---------------------------------------------------------- */

  var DZ_TITLE = 'فایل zip پروژه را اینجا رها کن';
  var DZ_TITLE_BUSY = 'در حال آپلود پروژه…';

  function setUploadLoading(on) {
    var dz = $('#dropzone');
    dz.classList.toggle('loading', on);
    $('#dz-spinner').hidden = !on;
    $('#dz-title').textContent = on ? DZ_TITLE_BUSY : DZ_TITLE;
    var chips = document.querySelectorAll('.chip');
    for (var i = 0; i < chips.length; i++) chips[i].disabled = on;
  }

  function showUploadError(prefix, detail) {
    var err = $('#upload-error');
    err.innerHTML = '';
    err.appendChild(document.createTextNode(prefix));
    if (detail) err.appendChild(el('span', 'ltr-block', esc(detail)));
    err.hidden = false;
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
    api.upload(fd).then(function (data) {
      store.sessionId = data.session_id;
      if (data.demo === true) { store.demo = true; updateDemoBadge(); }
      return goAssessment();
    }).catch(function (e) {
      setUploadLoading(false);
      if (isExpired(e)) return;
      showUploadError('آپلود ناموفق بود. دوباره تلاش کن.', e.detail || (e.kind === 'network' ? 'اتصال به سرور برقرار نشد.' : ''));
    });
  }

  function demoFixture(key, chipBtn) {
    $('#upload-error').hidden = true;
    var chips = document.querySelectorAll('.chip');
    for (var i = 0; i < chips.length; i++) chips[i].disabled = true;
    if (chipBtn) chipBtn.classList.add('loading');
    api.demo({ fixture: key }).then(function (data) {
      store.sessionId = data.session_id;
      if (data.demo === true) { store.demo = true; updateDemoBadge(); }
      return goAssessment();
    }).catch(function (e) {
      for (var j = 0; j < chips.length; j++) chips[j].disabled = false;
      if (chipBtn) chipBtn.classList.remove('loading');
      if (isExpired(e)) return;
      showUploadError('ساخت پروژهٔ نمونه ناموفق بود.', e.detail || (e.kind === 'network' ? 'اتصال به سرور برقرار نشد.' : ''));
    });
  }

  function wireUpload() {
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

  function shallowCopy(obj) {
    var out = {};
    for (var k in obj) { if (Object.prototype.hasOwnProperty.call(obj, k)) out[k] = obj[k]; }
    return out;
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
      if (snap.demo === true) { store.demo = true; updateDemoBadge(); }
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

      // Pending approval: snapshot field wins (note not yet shown for it);
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
   * SSE with manual reconnect: close on error, wait 2s, reopen the SAME
   * URL (never add cache-busters — that would defeat Last-Event-ID replay
   * semantics server-side). A freshly reopened EventSource has no
   * Last-Event-ID, so the server may replay old messages; handleEvent()
   * drops anything with id <= store.maxI, so nothing renders twice.
   */
  function openSSE() {
    stopSSE();
    if (!store.sessionId || store.screen === 'report' || store.screen === 'expired') return;
    var es = new EventSource('/api/events/' + encodeURIComponent(store.sessionId));
    store.es = es;
    store.sseAttempts = 0;

    es.onopen = function () { store.sseAttempts = 0; };

    es.onmessage = function (ev) {
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

  function stopSSE() {
    if (store.sseTimer) { clearTimeout(store.sseTimer); store.sseTimer = null; }
    if (store.es) {
      try { store.es.close(); } catch (e) { /* noop */ }
      store.es = null;
    }
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
    if (store.hydrating) return; // batch scroll once after hydration
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
    box.appendChild(el('div', 'group-title', 'استقرار'));
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
    var line = el('div', 'log-line ltr-block');
    line.textContent = (p.text != null) ? String(p.text) : '';
    li.appendChild(line);
    timelineEl().appendChild(li);
    scrollTimeline();
  }

  function rDeployLog(p) {
    var list = ensureDeployGroup();
    var row = el('li', 'log-line deploy-line ltr-block');
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
    var card = el('div', 'card risk-card');

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

    var costRow = el('div', 'cost-row');
    costRow.appendChild(el('span', 'cost-label', 'هزینهٔ تخمینی:'));
    costRow.appendChild(el('span', 'cost-val ltr-block', esc(p.estimated_cost != null ? p.estimated_cost : '—')));
    if (p.cost_unverified) costRow.appendChild(el('span', 'badge badge-amber', UNVERIFIED_BADGE));
    card.appendChild(costRow);

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
    openApprovalModal(p);
  }

  function rFix(p) {
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
    // Risk tier badge (T4): every action shows its tier + context note.
    if (p.risk_tier && p.risk_tier.tier) card.appendChild(tierBadge(p.risk_tier));
    if (p.risk_note_fa) card.appendChild(el('span', 'risk-note muted small', esc(p.risk_note_fa)));
    // Ranked causes for unscripted failures (T3): quote-only evidence.
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

  var TIER_BADGE_CLS = { auto: 'badge-zinc', approval: 'badge-amber', forbidden: 'badge-red' };

  function tierBadge(rt) {
    var cls = TIER_BADGE_CLS[rt.tier] || 'badge-zinc';
    var b = el('span', 'badge ' + cls + ' tier-badge', esc(rt.tier_fa || rt.tier || ''));
    if (rt.tier_level) {
      var lbl = el('span', 'tier-level', 'سطح ' + faDigits(rt.tier_level));
      lbl.style.marginInlineStart = '6px';
      b.appendChild(lbl);
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
      card.appendChild(el('p', 'muted small', 'اجرای آن فقط با تأیید تو انجام می‌شود (ماتریس ریسک سطح ۲).'));
    }
    li.appendChild(card);
    timelineEl().appendChild(li);
    scrollTimeline();
  }

  function rVerify(p) {
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
    disableInputs(true);
    addNote('کار تمام شد.');
    goReport();
  }

  function rError(p) {
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
    monitor_alert: rMonitorAlert
  };

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
      // sync from server response if it echoes the level
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

  /** Mid-run Q&A (T15): free question -> ONE llm call on the server -> Q/A pair
   *  rendered in the timeline (client-side only; answers never enter SSE events). */
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
   * Approval modal
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

  function openApprovalModal(p) {
    p = p || {};
    store.pendingApproval = p;
    var modal = $('#modal');
    modal.innerHTML = '';

    var isQuestion = p.kind === 'question';

    var title = el('h3', 'modal-title', isQuestion ? 'خلبان یک سؤال دارد' : 'نیاز به تأیید تو');
    title.id = 'modal-title';
    modal.appendChild(title);
    modal.appendChild(el('p', 'muted modal-sub',
      isQuestion ? 'خلبان از روی لاگ‌ها به چیزی نیاز دارد که فقط تو می‌توانی بدهی.'
                 : 'خلبان پیش از هر کار هزینه‌دار، اجازهٔ تو را می‌گیرد.'));

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

    var costRow = el('div', 'cost-row');
    costRow.appendChild(el('span', 'cost-label', 'هزینهٔ تخمینی:'));
    costRow.appendChild(el('span', 'cost-val ltr-block', esc(p.estimated_cost != null ? p.estimated_cost : '—')));
    if (p.cost_unverified) costRow.appendChild(el('span', 'badge badge-amber', UNVERIFIED_BADGE));
    modal.appendChild(costRow);

    var envItems = normalizeEnv(p.env_needed);
    if (envItems.length) {
      modal.appendChild(el('h4', 'modal-sec-title', 'متغیرهای محیطی'));
      modal.appendChild(el('p', 'muted small', 'مقادیر را وارد کن؛ خلبان آن‌ها را فقط برای همین استقرار استفاده می‌کند.'));
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

  /** Answer an ask_user interrupt (T14): a free-text reply for the agent. */
  function sendReply(text, okBtn, noBtn, errBox) {
    errBox.hidden = true;
    okBtn.disabled = true;
    noBtn.disabled = true;
    okBtn.textContent = 'در حال ارسال پاسخ…';
    api.approve(store.sessionId, { approved: true, user_reply: text }).then(function () {
      closeModal();
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
      out[inputs[i].dataset.key] = inputs[i].value.trim();
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
    store.pendingApproval = null;
  }

  /** Minimal focus trap so Tab cannot escape the approval dialog. */
  function wireModalTrap() {
    $('#modal-overlay').addEventListener('keydown', function (ev) {
      if (ev.key !== 'Tab') return;
      var focusables = $('#modal').querySelectorAll('button, input, summary, a[href]');
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
    if (store.report && (store.report.url || store.report.live_url)) startMonitorPolling();
  }

  /* ----------------------------------------------------------
   * Continuous monitoring panel (T6) — poll /monitor on the report screen.
   * Source labels: liara_api values stay UNVERIFIED-badged until a live read
   * confirms units; inference is always labelled «استنتاج».
   * ---------------------------------------------------------- */

  function startMonitorPolling() {
    if (store.monitorTimer) return;
    renderMonitor(); // immediate first paint
    store.monitorTimer = setInterval(renderMonitor, 15000);
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
        var alert = el('div', 'monitor-alert', 'هشدار: سلامت برنامه افت کرده است. پیشنهاد خلبان: ری‌استارت (سطح ۲ — با تأیید تو).');
        var ab = el('button', 'btn btn-ghost ops-btn', 'ری‌استارت برنامه');
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
      if (s.source === 'liara_api' && s.metrics) {
        row.appendChild(el('span', 'muted small', 'متریک از API لیارا (مقیاس مقادیر UNVERIFIED)'));
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

  function stopMonitorPolling() {
    if (store.monitorTimer) { clearInterval(store.monitorTimer); store.monitorTimer = null; }
  }

  function reportStatus(r) {
    if (store.cancelled) return { label: 'لغو شد', cls: 'st-off' };
    var s = String(r.status || '').toLowerCase();
    if (/decision|approval|await|need|confirm/.test(s)) return { label: 'نیاز به تصمیم تو', cls: 'st-warn' };
    if (/cancel|abort|stop/.test(s)) return { label: 'لغو شد', cls: 'st-off' };
    if (/success|done|deployed|live|complete|finish|ok/.test(s)) return { label: 'موفق', cls: 'st-ok' };
    var url = r.url || r.live_url || (r.deployment && r.deployment.url);
    if (url) return { label: 'موفق', cls: 'st-ok' };
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

  /* Post-deploy operations section (T4) — the three-tier legend + gated buttons. */
  function opsSection() {
    var sec = sectionCard('عملیات بعد از استقرار');
    var legend = el('div', 'chip-wrap risk-legend');
    legend.appendChild(tierBadge({ tier: 'auto', tier_fa: 'خودکار — فقط خواندنی', tier_level: 1 }));
    legend.appendChild(tierBadge({ tier: 'approval', tier_fa: 'نیازمند تأیید تو', tier_level: 2 }));
    legend.appendChild(tierBadge({ tier: 'forbidden', tier_fa: 'فقط دستی — ایجنت هرگز اجرا نمی‌کند', tier_level: 3 }));
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
        // The report screen has no live SSE any more — show the result here.
        addNote((action === 'restart' ? 'ری‌استارت: ' : 'عملیات: ')
          + ((res && res.message_fa) ? res.message_fa : 'ثبت شد.'));
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
      var secUrl = sectionCard('آدرس زنده');
      var box = el('div', 'url-box');
      var a = el('a', 'live-link ltr-block');
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = url;
      box.appendChild(a);
      secUrl.appendChild(box);
      root.appendChild(secUrl);

      // Post-deploy operations (T4): each action shows its risk tier.
      root.appendChild(opsSection());
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
    var cost = (r.estimated_cost != null) ? r.estimated_cost
      : (r.cost != null) ? r.cost
      : (r.cost_estimate != null) ? r.cost_estimate : null;
    if (cost != null || r.cost_unverified) {
      var secCost = sectionCard('هزینه');
      var costRow = el('div', 'cost-row');
      costRow.appendChild(el('span', 'cost-label', 'هزینهٔ تخمینی:'));
      costRow.appendChild(el('span', 'cost-val ltr-block', esc(cost != null ? cost : '—')));
      if (r.cost_unverified) costRow.appendChild(el('span', 'badge badge-amber', UNVERIFIED_BADGE));
      secCost.appendChild(costRow);
      root.appendChild(secCost);
    }

    // Beginner "what you learned" checklist (emerald card)
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

    $('#btn-expert-skip').addEventListener('click', function () { submitAnswers(true); });
    $('#btn-start').addEventListener('click', function () { submitAnswers(false); });
    $('#btn-cancel').addEventListener('click', onCancel);
    $('#lvl-beginner').addEventListener('click', function () { onToggleLevel('beginner'); });
    $('#lvl-expert').addEventListener('click', function () { onToggleLevel('expert'); });
    $('#btn-new').addEventListener('click', function () { location.reload(); });
    $('#btn-restart').addEventListener('click', function () { location.reload(); });

    updateDemoBadge();
    showScreen('upload');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
