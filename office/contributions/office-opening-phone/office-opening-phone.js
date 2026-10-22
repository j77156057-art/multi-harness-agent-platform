/*
 * office-opening-phone.js —— 手机开工向导（纯消费型组件）
 * 契约接缝：office-opening-preview/0.1（手机侧）
 *
 * 组件不读取目录 / 网络 / 真实成员状态；只消费宿主注入的 catalog（项目/实例/容量）
 * 与宿主回执（resolveConfirm）。点击与提交只发回调，不持有也不修改成员/owner/Claim/任务事实。
 * 文本一律 textContent/createTextNode 安全渲染；持续标注 mock/演示。
 *
 * 返修（2026-10-10 R01–R04）：
 *  - R01 演出改为「群聊时间线」：系统建群 / 加人 → 用户原创闹钟（1–2s 可见摆动动效）
 *        → 「开工了！」→ 管理员与各成员逐条回复气泡；减少动画为静态终态；不发声音/外部资源/真实消息。
 *  - R02 演出与「重开的群聊记录」共用同一份有序表现记录（state.record = 演出时间线的有序副本）；
 *        跳过 / 减少动画补齐记录但不重复发送、不改变业务状态。
 *  - R03 提交前用「当前 catalog」复校项目/管理员/打工人有效性；不可用或已移除的选择按钮禁用并说明；
 *        setCatalog 刷新后即时重算并作废失效选择，要求重选（前端校验不构成鉴权，宿主仍须独立校验）。
 *  - R04 传阅轮数用 valueAsNumber 保留原始输入，整数 + 宿主上下界校验；空/小数/越界均可见拒绝，不静默截断。
 */
(function (global, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  global.OfficeOpeningPhone = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var VERSION = 'office-opening-phone/0.1';
  var DEMO_LABEL = '演示 · MOCK';

  /* ---------------- 工具 ---------------- */
  function isObj(v) { return v !== null && typeof v === 'object'; }
  function deepFreeze(o) {
    if (isObj(o) || Array.isArray(o)) {
      Object.keys(o).forEach(function (k) { deepFreeze(o[k]); });
      Object.freeze(o);
    }
    return o;
  }
  function structuredCloneSafe(v) {
    try { if (typeof structuredClone === 'function') return structuredClone(v); } catch (e) {}
    return JSON.parse(JSON.stringify(v));
  }
  function deepCloneFreeze(v) { return deepFreeze(structuredCloneSafe(v)); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function truncate(s, max) {
    s = String(s == null ? '' : s);
    return s.length > max ? s.slice(0, max - 1) + '…' : s;
  }
  function safeHttpsHref(u) {
    try {
      var url = new URL(String(u));
      if (url.protocol !== 'https:') return null;
      if (!url.hostname || url.hostname === '') return null;
      return url.href;
    } catch (e) { return null; }
  }
  // 稳定种子 PRNG（用于可复现的闹钟文案与成员回复），不依赖真实随机
  function hashSeed(str) {
    var h = 2166136261 >>> 0;
    str = String(str);
    for (var i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h >>> 0;
  }
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function pick(arr, rnd) { return arr[Math.floor(rnd() * arr.length) % arr.length]; }
  function firstChar(name) { name = String(name == null ? '' : name); return name ? name.slice(0, 1) : '·'; }

  var DEFAULT_ALARMS = ['叮——开工了', '新的一天，开机', '闹钟响：集合'];
  var DEFAULT_REPLIES = ['收到，我上手', '就位，开始', '好的，先梳理', '在的，马上开工', '明白了，开工'];

  var DEFAULT_CFG = {
    goalMaxLen: 40,
    replyMaxLen: 30,
    stepDelayMs: 700,
    replyDelayMs: 520
  };

  /* ---------------- 构造 mount ---------------- */
  function mount(root, opts) {
    opts = opts || {};
    if (!root || !root.appendChild) throw new Error('mount: root required');
    var cfg = Object.assign({}, DEFAULT_CFG, opts.config || {});
    var callbacks = {
      onConfigConfirm: opts.callbacks && opts.callbacks.onConfigConfirm,
      onOpeningReady: opts.callbacks && opts.callbacks.onOpeningReady
    };
    if (typeof callbacks.onConfigConfirm !== 'function') throw new Error('mount: callbacks.onConfigConfirm required');
    if (typeof callbacks.onOpeningReady !== 'function') throw new Error('mount: callbacks.onOpeningReady required');

    var state = {
      stage: 'unmounted',      // unmounted|idle|connect|config|summary|performing|collapsed|record
      connected: false,
      catalog: null,           // 深冻结
      reduced: !!opts.reducedMotion,
      // UI 选择（仅表现层，不构成事实）
      sel: { projectId: null, adminRef: null, workerIds: [], goal: '', reviewRounds: 1 },
      // 提交流
      reqSeq: 0,
      inflight: null,          // {request_id, runToken}
      submitDisabled: false,
      runToken: 0,
      lastRun: null,           // {run_id, epoch, config}
      readySentForRun: null,   // run_id+epoch 已发过 onOpeningReady
      // 校验与回执
      errors: [],
      staleNote: null,         // setCatalog 后失效选择提示（表现层）
      // 演出与记录（R02：二者共用同一份有序表现记录）
      timeline: [],            // 本次演出的有序条目
      record: [],              // 归档 = 演出时间线有序副本
      // 计账（供验收）
      counts: { submit: 0, resolve: 0, rejectedLate: 0, ready: 0, replays: 0, droppedResult: 0, blockedResubmit: 0 },
      timers: [],
      destroy: false
    };

    var timers = [];
    function later(fn, ms) {
      var id = setTimeout(fn, state.reduced ? 0 : ms);
      timers.push(id);
      return id;
    }
    function clearTimers() { timers.forEach(clearTimeout); timers = []; }

    /* --------- 构建外壳 --------- */
    var layer = el('div', 'oop-root');
    layer.setAttribute('data-version', VERSION);
    if (state.reduced) layer.classList.add('oop-reduced');
    var frame = el('div', 'oop-frame');
    var header = el('div', 'oop-header');
    var hTitle = el('span', 'oop-title', '开工向导');
    var hDemo = el('span', 'oop-demo-badge', state.catalog && state.catalog.mode === 'formal' ? '正式平台' : DEMO_LABEL);
    header.appendChild(hTitle); header.appendChild(hDemo);
    var body = el('div', 'oop-body');
    var footer = el('div', 'oop-footer');
    frame.appendChild(header); frame.appendChild(body); frame.appendChild(footer);
    layer.appendChild(frame);
    root.innerHTML = '';
    root.appendChild(layer);

    function setStage(s) { state.stage = s; layer.setAttribute('data-stage', s); render(); }

    /* --------- catalog 查询 --------- */
    function findInstance(id) {
      if (!state.catalog) return null;
      for (var i = 0; i < state.catalog.instances.length; i++) {
        if (state.catalog.instances[i].instance_id === id) return state.catalog.instances[i];
      }
      return null;
    }
    function findProject(id) {
      if (!state.catalog) return null;
      for (var i = 0; i < state.catalog.projects.length; i++) {
        if (state.catalog.projects[i].project_id === id) return state.catalog.projects[i];
      }
      return null;
    }
    function displayName(id) { var it = findInstance(id); return it ? it.display_name : String(id); }
    function adminDisplayName() {
      if (!state.sel.adminRef) return '管理员';
      if (state.sel.adminRef.kind === 'local_agent') return '本机 Agent';
      return displayName(state.sel.adminRef.instance_id);
    }

    /* --------- 校验（纯 UI 规则；容量与有效性读宿主当前目录） --------- */
    function validate() {
      var errs = [];
      var c = state.catalog;
      if (!c) { return ['宿主未注入 catalog']; }
      var cap = c.capacity || {};
      if (typeof cap.max_workers !== 'number') errs.push('宿主未提供容量 capacity.max_workers（组件不写死）');

      // 项目：必须存在于「当前」目录
      if (!state.sel.projectId) errs.push('未选择项目');
      else if (!findProject(state.sel.projectId)) errs.push('所选项目已不在当前目录，请重选：' + state.sel.projectId);

      // 管理员：kind 合法 + 引用有效 + 可用
      if (!state.sel.adminRef) errs.push('未选择唯一管理员');
      else if (state.sel.adminRef.kind === 'local_agent') {
        if (c.local_agent === false) errs.push('当前目录不允许本机 Agent 担任管理员');
      } else if (state.sel.adminRef.kind === 'harness') {
        var ai = findInstance(state.sel.adminRef.instance_id);
        if (!ai) errs.push('所选管理员实例已不在当前目录，请重选：' + state.sel.adminRef.instance_id);
        else if (ai.available === false) errs.push('管理员实例不可用：' + ai.display_name);
      } else {
        errs.push('管理员 kind 非法：' + state.sel.adminRef.kind);
      }

      var adminId = state.sel.adminRef && state.sel.adminRef.kind === 'harness' ? state.sel.adminRef.instance_id : '__local_agent__';
      var seen = {};
      if (!state.sel.workerIds.length) errs.push('至少选择 1 名普通打工人');
      state.sel.workerIds.forEach(function (id) {
        if (seen[id]) { errs.push('打工人重复：' + id); }
        seen[id] = 1;
        if (id === adminId) errs.push('同一实例不得兼任管理员与打工人：' + id);
        var inst = findInstance(id);
        if (!inst) errs.push('实例已不在当前目录，请重选：' + id);
        else if (inst.available === false) errs.push('实例不可用：' + id);
      });
      if (typeof cap.max_workers === 'number' && state.sel.workerIds.length > cap.max_workers) {
        errs.push('打工人数量 ' + state.sel.workerIds.length + ' 超出宿主容量 ' + cap.max_workers);
      }
      if (!String(state.sel.goal).trim()) errs.push('未填写任务目标');
      else if (String(state.sel.goal).length > cfg.goalMaxLen) errs.push('任务目标过长（≤' + cfg.goalMaxLen + ' 字）');

      // 传阅轮数：空 / 小数 / 越界均可见拒绝（R04）
      var rrMax = typeof cap.review_rounds_max === 'number' ? cap.review_rounds_max : 9;
      var rrMin = typeof cap.review_rounds_min === 'number' ? cap.review_rounds_min : 1;
      var raw = state.sel.reviewRounds;
      if (raw === null || raw === undefined || raw === '' || (typeof raw === 'number' && Number.isNaN(raw))) {
        errs.push('传阅轮数不能为空');
      } else if (!Number.isInteger(Number(raw))) {
        errs.push('传阅轮数必须为整数（不接受小数：' + raw + '）');
      } else if (!(Number(raw) >= rrMin && Number(raw) <= rrMax)) {
        errs.push('传阅轮数需为 ' + rrMin + '–' + rrMax + ' 的整数');
      }
      return errs;
    }

    /* --------- 渲染各阶段 --------- */
    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

    function renderIdle() {
      clear(body); clear(footer);
      var quiet = el('div', 'oop-quiet');
      quiet.appendChild(el('div', 'oop-quiet-line', '安静的工作台'));
      var btn = el('button', 'oop-bigstart');
      btn.setAttribute('type', 'button');
      btn.setAttribute('data-action', 'start');
      btn.textContent = '开始任务';
      btn.addEventListener('click', function () { api.start(); });
      quiet.appendChild(btn);
      quiet.appendChild(el('div', 'oop-quiet-hint', state.catalog && state.catalog.mode === 'formal' ? '打开手机开工向导（正式平台）' : '打开手机开工向导（演示）'));
      body.appendChild(quiet);
    }

    function renderConnect() {
      clear(body); clear(footer);
      var panel = el('div', 'oop-form');
      panel.appendChild(el('div', 'oop-group', '开始配置前，先连接要参与本轮工作的 Harness。'));
      var formal = state.catalog && state.catalog.mode === 'formal';
      panel.appendChild(el('div', 'oop-stale', formal ? '正在检查正式平台注册表；仅在线 Harness 可参与派单。' : '当前为离线演示目录；此按钮只连接 mock，不会检测或启动真实 Harness。'));
      var connect = el('button', 'oop-btn oop-primary', formal ? '连接正式平台' : '连接演示 Harness');
      connect.type = 'button'; connect.setAttribute('data-action', 'connect-demo');
      connect.addEventListener('click', function () {
        state.connected = true;
        // 目录里只有一个项目时（本窗口的项目上下文），它就是此处唯一合理的选择；
        // 让用户再点一次这个没有别的竞争项的 chip 是多余的一步。
        if (!state.sel.projectId && state.catalog.projects.length === 1) {
          state.sel.projectId = state.catalog.projects[0].project_id;
        }
        setStage('config');
      });
      panel.appendChild(connect);
      body.appendChild(panel);
      var back = el('button', 'oop-btn', '返回');
      back.type = 'button'; back.addEventListener('click', function () { setStage('idle'); });
      footer.appendChild(back);
    }

    function renderConfig() {
      clear(body); clear(footer);
      if (!state.connected) { setStage('connect'); return; }
      var c = state.catalog;
      var form = el('div', 'oop-form');

      // 目录刷新后的失效选择提示（R03）
      if (state.staleNote) {
        var warn = el('div', 'oop-stale');
        warn.setAttribute('data-role', 'stale');
        warn.textContent = '⚠ ' + state.staleNote;
        form.appendChild(warn);
      }

      // 项目
      var gProj = el('div', 'oop-group');
      gProj.appendChild(el('label', 'oop-label', '项目'));
      var projList = el('div', 'oop-chips');
      c.projects.forEach(function (p) {
        var b = el('button', 'oop-chip' + (state.sel.projectId === p.project_id ? ' on' : ''), p.name);
        b.setAttribute('type', 'button');
        b.setAttribute('data-project', p.project_id);
        b.addEventListener('click', function () { state.sel.projectId = p.project_id; state.staleNote = null; renderConfig(); });
        projList.appendChild(b);
      });
      gProj.appendChild(projList);
      form.appendChild(gProj);

      // 管理员（唯一；按 instance_id 绑定；不可用实例禁用；R03）
      var gAdm = el('div', 'oop-group');
      gAdm.appendChild(el('label', 'oop-label', '唯一管理员实例（同 Harness 的其他实例可作为成员）'));
      var admList = el('div', 'oop-admins');
      if (c.local_agent !== false) {
        var la = el('button', 'oop-chip' + (state.sel.adminRef && state.sel.adminRef.kind === 'local_agent' ? ' on' : ''), '本机 Agent');
        la.setAttribute('type', 'button');
        la.setAttribute('data-admin', 'local_agent');
        la.addEventListener('click', function () {
          state.sel.adminRef = { kind: 'local_agent', instance_id: null };
          state.staleNote = null; renderConfig();
        });
        admList.appendChild(la);
      }
      c.instances.forEach(function (inst) {
        var isSel = state.sel.adminRef && state.sel.adminRef.kind === 'harness' && state.sel.adminRef.instance_id === inst.instance_id;
        var unavailable = inst.available === false;
        var b = el('button', 'oop-chip' + (isSel ? ' on' : '') + (unavailable ? ' dis' : ''),
          inst.display_name + ' (' + inst.brand + ')' + (unavailable ? ' · 不可用' : ''));
        b.setAttribute('type', 'button');
        b.setAttribute('data-admin', inst.instance_id);
        if (unavailable) {
          b.disabled = true;
          b.setAttribute('data-unavailable', '1');
          b.title = '该实例当前不可用，无法担任管理员';
        } else {
          b.addEventListener('click', function () {
            state.sel.adminRef = { kind: 'harness', instance_id: inst.instance_id };
            // 禁止同实例兼任打工人
            state.sel.workerIds = state.sel.workerIds.filter(function (x) { return x !== inst.instance_id; });
            state.staleNote = null; renderConfig();
          });
        }
        admList.appendChild(b);
      });
      gAdm.appendChild(admList);
      form.appendChild(gAdm);

      // 成员实例（多选；不含同一管理员实例；仅可用实例；按 instance_id 去重；容量）
      var gW = el('div', 'oop-group');
      var capTxt = (c.capacity && typeof c.capacity.max_workers === 'number') ? ('容量(宿主) ' + c.capacity.max_workers + ' · 已选 ' + state.sel.workerIds.length) : '容量：宿主未提供';
      gW.appendChild(el('label', 'oop-label', '成员实例（仅显示已注册窗口；同 Harness 的不同 instance_id 可同时加入） · ' + capTxt));
      var wList = el('div', 'oop-workers');
      var selectedAdminBrand = null;
      if (state.sel.adminRef && state.sel.adminRef.kind === 'harness') {
        var selectedAdmin = findInstance(state.sel.adminRef.instance_id);
        selectedAdminBrand = selectedAdmin && selectedAdmin.brand;
      }
      var hasOtherAdminBrandInstance = selectedAdminBrand !== null && c.instances.some(function (inst) {
        return inst.brand === selectedAdminBrand && inst.instance_id !== state.sel.adminRef.instance_id && inst.available !== false;
      });
      if (selectedAdminBrand !== null && !hasOtherAdminBrandInstance) {
        var instanceNote = el('div', 'oop-stale');
        instanceNote.textContent = '当前目录没有该 Harness 的第二个已注册窗口。要让同一 Harness 同时担任管理员和成员，请先注册另一个 instance_id（例如 codex-member-1）；不能重复使用当前管理员实例。';
        wList.appendChild(instanceNote);
      }
      c.instances.forEach(function (inst) {
        var isAdmin = state.sel.adminRef && state.sel.adminRef.kind === 'harness' && state.sel.adminRef.instance_id === inst.instance_id;
        var unavailable = inst.available === false;
        var on = state.sel.workerIds.indexOf(inst.instance_id) !== -1;
        var cls = 'oop-chip' + (on ? ' on' : '') + ((isAdmin || unavailable) ? ' dis' : '');
        var suffix = isAdmin ? ' · 当前管理员实例' : (unavailable ? ' · 不可用' : '');
        // Always expose the stable instance identity. Same-brand windows are
        // allowed when their IDs differ; the brand name alone is not enough.
        var identity = ' · ' + inst.instance_id;
        var b = el('button', cls, inst.display_name + identity + suffix);
        b.setAttribute('type', 'button');
        b.setAttribute('data-worker', inst.instance_id);
        if (isAdmin) {
          b.setAttribute('data-admin-blocked', '1');
          b.disabled = true;
          b.title = '同一注册实例不能同时担任管理员和成员；请先注册同 Harness 的另一个 instance_id';
          b.setAttribute('aria-label', inst.display_name + '，当前管理员实例；请先注册同 Harness 的另一个 instance_id');
        }
        if (unavailable) { b.disabled = true; b.setAttribute('data-unavailable', '1'); b.title = '该实例当前不可用，无法选为打工人'; }
        b.addEventListener('click', function () {
          if (isAdmin || unavailable) return; // 兼任 / 不可用一律无效
          var i = state.sel.workerIds.indexOf(inst.instance_id);
          if (i === -1) state.sel.workerIds.push(inst.instance_id);
          else state.sel.workerIds.splice(i, 1);
          state.staleNote = null; renderConfig();
        });
        wList.appendChild(b);
      });
      gW.appendChild(wList);
      form.appendChild(gW);

      // 任务目标
      var gGoal = el('div', 'oop-group');
      gGoal.appendChild(el('label', 'oop-label', '任务目标'));
      var ta = el('textarea', 'oop-goal');
      ta.setAttribute('data-field', 'goal');
      ta.setAttribute('maxlength', String(cfg.goalMaxLen));
      ta.placeholder = '例如：为本期活动写一页说明（' + cfg.goalMaxLen + ' 字以内）';
      ta.value = state.sel.goal;
      ta.addEventListener('input', function () { state.sel.goal = ta.value; });
      gGoal.appendChild(ta);
      form.appendChild(gGoal);

      // 传阅轮数（valueAsNumber 保留原始输入，不静默截断；R04）
      var gRR = el('div', 'oop-group');
      var rrMin = c.capacity && typeof c.capacity.review_rounds_min === 'number' ? c.capacity.review_rounds_min : 1;
      var rrMax = c.capacity && typeof c.capacity.review_rounds_max === 'number' ? c.capacity.review_rounds_max : 9;
      gRR.appendChild(el('label', 'oop-label', '传阅轮数（' + rrMin + '–' + rrMax + ' 的整数）'));
      var rr = el('input', 'oop-rounds');
      rr.type = 'number';
      rr.setAttribute('data-field', 'rounds');
      rr.setAttribute('step', '1');
      rr.min = String(rrMin);
      rr.max = String(rrMax);
      rr.value = Number.isFinite(state.sel.reviewRounds) ? String(state.sel.reviewRounds) : '';
      rr.addEventListener('input', function () {
        // 直接保留 valueAsNumber：空 -> NaN，小数 -> 1.5，交给 validate 可见拒绝
        state.sel.reviewRounds = rr.valueAsNumber;
      });
      gRR.appendChild(rr);
      form.appendChild(gRR);

      // 人数：所选成员一名都没留下时，先在这里说明为什么（全部禁用 / 都不可用），
      // 否则「查看配置摘要」后只得到一句「至少选择 1 名普通打工人」，看不出怎么修。
      var roster = el('div', 'oop-quiet-line');
      roster.setAttribute('data-role', 'roster');
      var rosterNotes = [];
      var adminInstanceId = state.sel.adminRef && state.sel.adminRef.kind === 'harness' ? state.sel.adminRef.instance_id : null;
      var adminOnly = c.instances.filter(function (inst) { return inst.instance_id === adminInstanceId; }).length;
      var blockedByAdmin = adminInstanceId ? c.instances.filter(function (inst) {
        return inst.instance_id === adminInstanceId && inst.available !== false;
      }).length : 0;
      var unavailableCount = c.instances.filter(function (inst) { return inst.available === false; }).length;
      if (!state.sel.workerIds.length) {
        if (c.instances.length - adminOnly - (unavailableCount - blockedByAdmin) <= 0) rosterNotes.push('目录里没有可担任成员的已注册窗口：当前只剩管理员实例本身' + (unavailableCount ? '与 ' + unavailableCount + ' 个不可用窗口' : '') + '。');
        else if (blockedByAdmin) rosterNotes.push('管理员实例不能同时担任成员；请再勾选至少 1 个其他已注册窗口。');
        else rosterNotes.push('还没有勾选任何成员：请至少勾选 1 个普通打工人。');
      }
      if (adminInstanceId) {
        var adminBrand = (findInstance(adminInstanceId) || {}).brand;
        var sameBrandOthers = c.instances.filter(function (inst) {
          return inst.brand === adminBrand && inst.instance_id !== adminInstanceId && inst.available !== false;
        }).length;
        if (!sameBrandOthers) rosterNotes.push('当前管理员属于 ' + (adminBrand || adminInstanceId) + '，该 Harness 没有第二个已注册窗口；需要它同时也是成员时，请先注册另一个 instance_id。');
      }
      roster.textContent = rosterNotes.join(' ');
      roster.hidden = !rosterNotes.length;
      form.appendChild(roster);

      // 错误
      var errBox = el('div', 'oop-errors');
      errBox.setAttribute('data-role', 'errors');
      state.errors.forEach(function (e) { errBox.appendChild(el('div', 'oop-err', '· ' + e)); });
      form.appendChild(errBox);

      body.appendChild(form);

      var next = el('button', 'oop-btn oop-primary');
      next.setAttribute('type', 'button');
      next.setAttribute('data-action', 'to-summary');
      next.textContent = '查看配置摘要';
      next.addEventListener('click', function () {
        state.errors = validate();
        if (state.errors.length) { renderConfig(); return; }
        setStage('summary');
      });
      var back = el('button', 'oop-btn', '返回首屏');
      back.setAttribute('type', 'button');
      back.addEventListener('click', function () { setStage('idle'); });
      footer.appendChild(back); footer.appendChild(next);
    }

    function summaryRows() {
      var proj = findProject(state.sel.projectId);
      var rows = [];
      rows.push(['项目', proj ? proj.name : '—']);
      var adminTxt;
      if (state.sel.adminRef && state.sel.adminRef.kind === 'local_agent') adminTxt = '本机 Agent（local_agent）';
      else if (state.sel.adminRef) {
        var inst = findInstance(state.sel.adminRef.instance_id);
        adminTxt = (inst ? inst.display_name + ' [' + inst.brand + ']' : state.sel.adminRef.instance_id) + '（harness）';
      } else adminTxt = '—';
      rows.push(['管理员', adminTxt]);
      rows.push(['普通打工人', state.sel.workerIds.map(function (id) {
        var it = findInstance(id); return it ? it.display_name : id;
      }).join('、') || '—']);
      rows.push(['传阅轮数', String(state.sel.reviewRounds)]);
      rows.push(['任务目标', truncate(state.sel.goal, cfg.goalMaxLen)]);
      return rows;
    }

    function renderSummary() {
      clear(body); clear(footer);
      var box = el('div', 'oop-summary');
      box.appendChild(el('div', 'oop-sum-title', '配置摘要（请宿主确认）'));
      var dl = el('dl', 'oop-sum-list');
      summaryRows().forEach(function (r) {
        dl.appendChild(el('dt', null, r[0]));
        dl.appendChild(el('dd', null, r[1]));
      });
      box.appendChild(dl);
      box.appendChild(el('div', 'oop-sum-note', state.catalog && state.catalog.mode === 'formal'
        ? '确认后会为每个选中的在线 Harness 创建正式任务；任一派单失败都不会进入场景。'
        : '以上为演示配置；确认后不会发送任何真实消息。'));
      body.appendChild(box);

      var statusBox = el('div', 'oop-status'); statusBox.setAttribute('data-role', 'status');
      footer.appendChild(statusBox);

      var edit = el('button', 'oop-btn', '修改配置');
      edit.setAttribute('type', 'button');
      edit.addEventListener('click', function () { if (!state.submitDisabled) setStage('config'); });

      var confirm = el('button', 'oop-btn oop-primary');
      confirm.setAttribute('type', 'button');
      confirm.setAttribute('data-action', 'confirm');
      confirm.textContent = '确认开工';
      confirm.disabled = state.submitDisabled;
      confirm.addEventListener('click', submitConfig);

      var cancel = el('button', 'oop-btn oop-ghost', '取消');
      cancel.setAttribute('type', 'button');
      cancel.setAttribute('data-action', 'cancel');
      cancel.addEventListener('click', function () { api.cancel(); });

      footer.appendChild(edit); footer.appendChild(cancel); footer.appendChild(confirm);

      if (state.submitDisabled && state.inflight) {
        statusBox.textContent = '等待宿主确认…（request_id=' + state.inflight.request_id + '）';
      } else if (state.lastReject) {
        statusBox.textContent = state.lastReject;
      }
    }

    function submitConfig() {
      // 反复点击不得重复提交
      if (state.submitDisabled) { state.counts.blockedResubmit++; return; }
      // 提交前用当前目录复校（含目录刷新导致的失效选择；R03）
      var errs = validate();
      if (errs.length) { state.errors = errs; setStage('config'); return; }
      state.runToken++;
      state.reqSeq++;
      var req = {
        request_id: 'req-' + state.runToken + '-' + state.reqSeq,
        project_id: state.sel.projectId,
        admin: state.sel.adminRef.kind === 'local_agent'
          ? { kind: 'local_agent' }
          : { kind: 'harness', instance_id: state.sel.adminRef.instance_id },
        workers: state.sel.workerIds.slice(),
        goal: String(state.sel.goal),
        review_rounds: state.sel.reviewRounds
      };
      state.inflight = { request_id: req.request_id, runToken: state.runToken };
      state.submitDisabled = true;
      state.lastReject = null;
      state.counts.submit++;
      renderSummary();
      callbacks.onConfigConfirm(deepCloneFreeze(structuredCloneSafe(req)));
    }

    // 宿主回执
    function resolveConfirm(result) {
      result = structuredCloneSafe(result);
      if (!result || typeof result !== 'object') throw new Error('resolveConfirm: result object required');
      // 迟到的 / 已取消的 request 丢弃
      if (!state.inflight || result.request_id !== state.inflight.request_id || state.stage !== 'summary') {
        state.counts.rejectedLate++;
        return { applied: false, reason: 'stale-or-cancelled' };
      }
      state.counts.resolve++;
      state.inflight = null;
      state.submitDisabled = false;

      if (result.status === 'rejected') {
        state.lastReject = '已拒绝：' + (result.reason || '配置不符合规则');
        state.errors = result.reason ? [result.reason] : state.errors;
        renderSummary();
        return { applied: true, status: 'rejected' };
      }
      if (result.status === 'failed') {
        state.lastReject = '临时失败：' + (result.reason || '宿主暂时无法处理') + '（可重试）';
        renderSummary();
        return { applied: true, status: 'failed' };
      }
      if (result.status === 'accepted') {
        if (!result.run_id) { state.lastReject = '宿主 accepted 但缺 run_id'; renderSummary(); return { applied: true, status: 'malformed' }; }
        state.lastRun = { run_id: result.run_id, epoch: result.epoch == null ? 0 : result.epoch, config: result.confirmed_config || summaryConfig() };
        state.lastReject = null;
        startPerform();
        return { applied: true, status: 'accepted' };
      }
      state.lastReject = '未知回执状态：' + result.status;
      renderSummary();
      return { applied: true, status: 'unknown' };
    }

    function summaryConfig() {
      return {
        project_id: state.sel.projectId,
        admin: state.sel.adminRef,
        workers: state.sel.workerIds.slice(),
        goal: String(state.sel.goal),
        review_rounds: state.sel.reviewRounds
      };
    }

    /* --------- 演出：群聊时间线（R01）+ 有序记录（R02） --------- */
    function startPerform() {
      state.record = [];
      state.staleNote = null;
      setStage('performing');
    }

    // 由 run_id#epoch 派生稳定种子，构建「有序表现记录」——演出与归档共用同一份（R02）
    function buildTimeline() {
      var run = state.lastRun;
      var seedBase = (run ? run.run_id + ':' + run.epoch : 'norange');
      var rndAlarm = mulberry32(hashSeed(seedBase + '|alarm|' + state.sel.projectId));
      var alarms = (state.catalog && state.catalog.alarms) || DEFAULT_ALARMS;
      var alarmText = truncate(pick(alarms, rndAlarm), cfg.goalMaxLen);

      var rndRep = mulberry32(hashSeed(seedBase + '|replies'));
      var pool = (state.catalog && state.catalog.reply_pool) || DEFAULT_REPLIES;

      var adminName = adminDisplayName();
      var workerNames = state.sel.workerIds.map(displayName);
      var memberNames = [adminName].concat(workerNames);

      var tl = [];
      tl.push({ kind: 'system', role: 'system', who: '系统', text: '系统：已创建开工群聊' });
      tl.push({ kind: 'system', role: 'system', who: '系统', text: '系统：已加入成员 ' + memberNames.join('、') });
      tl.push({ kind: 'alarm', role: 'self', who: '我', text: alarmText });
      tl.push({ kind: 'message', role: 'self', who: '我', text: '开工了！' });
      tl.push({ kind: 'message', role: 'admin', who: adminName, text: truncate(pick(pool, rndRep), cfg.replyMaxLen) });
      state.sel.workerIds.forEach(function (id) {
        tl.push({ kind: 'message', role: 'worker', who: displayName(id), text: truncate(pick(pool, rndRep), cfg.replyMaxLen) });
      });
      return tl;
    }

    // 一条表现记录 -> 一个群聊节点（演出逐条与重开记录复用同一渲染，保证视觉与顺序一致）
    function bubbleNode(entry, animate) {
      if (entry.kind === 'system') {
        var sys = el('div', 'oop-chat-sys', entry.text);
        sys.setAttribute('data-msg-kind', 'system');
        sys.setAttribute('data-msg-role', 'system');
        if (animate) sys.classList.add('oop-msg-enter');
        return sys;
      }
      var side = entry.role === 'self' ? 'self' : 'other';
      var row = el('div', 'oop-msg oop-msg--' + side);
      row.setAttribute('data-msg-kind', entry.kind);
      row.setAttribute('data-msg-role', entry.role);
      if (animate) row.classList.add('oop-msg-enter');
      row.appendChild(el('span', 'oop-avatar', firstChar(entry.who)));
      var col = el('div', 'oop-msg-col');
      col.appendChild(el('span', 'oop-msg-who', entry.who));
      var bubble = el('div', 'oop-bubble' + (entry.kind === 'alarm' ? ' oop-bubble-alarm' : ''));
      if (entry.kind === 'alarm') bubble.appendChild(el('span', 'oop-alarm-icon', '⏰'));
      bubble.appendChild(el('span', 'oop-bubble-text', entry.text));
      col.appendChild(bubble);
      row.appendChild(col);
      return row;
    }

    function renderPerform() {
      clear(body); clear(footer);
      var wrap = el('div', 'oop-perform');
      wrap.setAttribute('data-role', 'perform-wrap');
      wrap.appendChild(el('div', 'oop-perform-demo', state.catalog && state.catalog.mode === 'formal'
        ? '正式平台 · 已收到任务回执，下面的群聊仅展示开工通知'
        : DEMO_LABEL + ' · 群聊为演示，不会发送真实消息'));
      var chat = el('div', 'oop-chat');
      chat.setAttribute('data-role', 'chat');
      chat.setAttribute('aria-live', 'polite');
      wrap.appendChild(chat);
      body.appendChild(wrap);

      var cancel = el('button', 'oop-btn oop-ghost', '取消');
      cancel.setAttribute('type', 'button'); cancel.setAttribute('data-action', 'cancel');
      cancel.addEventListener('click', function () { api.cancel(); });
      var restart = el('button', 'oop-btn', '重播演出');
      restart.setAttribute('type', 'button'); restart.setAttribute('data-action', 'restart');
      restart.addEventListener('click', function () { state.counts.replays++; runChatSequence(); });
      var skip = el('button', 'oop-btn oop-primary', '跳过');
      skip.setAttribute('type', 'button'); skip.setAttribute('data-action', 'skip');
      skip.addEventListener('click', skipPerform);
      footer.appendChild(cancel); footer.appendChild(restart); footer.appendChild(skip);

      runChatSequence();
    }

    function runChatSequence() {
      clearTimers();
      var wrap = body.querySelector('[data-role="perform-wrap"]');
      if (!wrap) return;
      var chat = wrap.querySelector('[data-role="chat"]');
      clear(chat);
      var tl = state.timeline = buildTimeline();

      if (state.reduced) { // 减少动画：静态终态，一次性铺满
        tl.forEach(function (e) { chat.appendChild(bubbleNode(e, false)); });
        finishPerform();
        return;
      }
      var i = 0;
      (function step() {
        if (state.destroy || state.stage !== 'performing') return;
        if (i >= tl.length) { finishPerform(); return; }
        var e = tl[i];
        var node = bubbleNode(e, true);
        chat.appendChild(node);
        // 真正滚动的是 .oop-body（.oop-chat 随内容长高、自身不滚），滚动容器使最新气泡可见（R01 短屏修复）
        body.scrollTop = body.scrollHeight;
        i++;
        var wait = (e.kind === 'message' && e.role !== 'self') ? cfg.replyDelayMs : cfg.stepDelayMs;
        later(step, wait);
      })();
    }

    // 跳过：补齐整条有序表现记录，但不重发、不改变业务状态（R02）
    function skipPerform() {
      if (state.stage !== 'performing') return;
      clearTimers();
      var wrap = body.querySelector('[data-role="perform-wrap"]');
      var chat = wrap && wrap.querySelector('[data-role="chat"]');
      var tl = state.timeline = (state.timeline && state.timeline.length) ? state.timeline : buildTimeline();
      if (chat) { clear(chat); tl.forEach(function (e) { chat.appendChild(bubbleNode(e, false)); }); }
      finishPerform();
    }

    // 归档 = 演出时间线有序副本；随后向宿主交出入场请求（同 run/epoch 至多一次）
    function finishPerform() {
      if (state.stage !== 'performing') return;
      state.record = (state.timeline && state.timeline.length ? state.timeline : buildTimeline()).slice();
      fireOpeningReady();
    }

    function fireOpeningReady() {
      var run = state.lastRun;
      if (!run) return;
      var key = run.run_id + '#' + run.epoch;
      if (state.readySentForRun === key) return; // 同一运行最多一次
      state.readySentForRun = key;
      state.counts.ready++;
      setStage('collapsed');
      callbacks.onOpeningReady(deepCloneFreeze({ run_id: run.run_id, epoch: run.epoch }));
    }

    /* --------- 收起后的记录入口 --------- */
    function renderCollapsed() {
      clear(body); clear(footer);
      var box = el('div', 'oop-collapsed');
      box.appendChild(el('div', 'oop-collapsed-line', '开工流程已交宿主，手机已收起'));
      box.appendChild(el('div', 'oop-collapsed-run', 'run_id=' + (state.lastRun ? state.lastRun.run_id : '—') + ' · epoch=' + (state.lastRun ? state.lastRun.epoch : '—')));
      var open = el('button', 'oop-btn oop-primary');
      open.setAttribute('type', 'button');
      open.setAttribute('data-action', 'reopen');
      open.textContent = '打开群聊记录';
      open.addEventListener('click', function () { setStage('record'); open.focus(); });
      box.appendChild(open);
      body.appendChild(box);
    }

    // 重开：渲染与演出「同一份有序记录」，顺序/视觉一致（R02）
    function renderRecord() {
      clear(body); clear(footer);
      var box = el('div', 'oop-record');
      box.appendChild(el('div', 'oop-record-title', '群聊记录 · ' + DEMO_LABEL));
      var chat = el('div', 'oop-chat oop-record-chat');
      chat.setAttribute('data-role', 'record');
      if (!state.record.length) chat.appendChild(el('div', 'oop-record-empty', '（暂无记录）'));
      state.record.forEach(function (e) { chat.appendChild(bubbleNode(e, false)); });
      box.appendChild(chat);
      body.appendChild(box);

      var again = el('button', 'oop-btn', '再开一轮');
      again.setAttribute('type', 'button');
      again.addEventListener('click', function () { resetSelKeep(); state.connected = false; setStage('connect'); });
      var close = el('button', 'oop-btn oop-ghost', '收起');
      close.setAttribute('type', 'button');
      close.addEventListener('click', function () { setStage('collapsed'); });
      footer.appendChild(close); footer.appendChild(again);
    }

    function resetSelKeep() {
      state.submitDisabled = false; state.inflight = null; state.lastReject = null;
      state.errors = []; state.staleNote = null;
    }

    // setCatalog 后即时重算：作废已从目录移除/不可用/角色冲突的选择（R03）
    function reconcileSelections() {
      var c = state.catalog; var msgs = [];
      if (state.sel.projectId && !findProject(state.sel.projectId)) { state.sel.projectId = null; msgs.push('所选项目已从目录移除'); }
      if (state.sel.adminRef) {
        if (state.sel.adminRef.kind === 'local_agent') {
          if (c.local_agent === false) { state.sel.adminRef = null; msgs.push('本机 Agent 不再被允许担任管理员'); }
        } else if (state.sel.adminRef.kind === 'harness') {
          var ai = findInstance(state.sel.adminRef.instance_id);
          if (!ai) { state.sel.adminRef = null; msgs.push('所选管理员实例已从目录移除'); }
          else if (ai.available === false) { state.sel.adminRef = null; msgs.push('所选管理员实例已不可用'); }
        } else { state.sel.adminRef = null; msgs.push('管理员 kind 非法'); }
      }
      var adminId = state.sel.adminRef && state.sel.adminRef.kind === 'harness' ? state.sel.adminRef.instance_id : '__local_agent__';
      var kept = [], dropped = 0;
      state.sel.workerIds.forEach(function (id) {
        var it = findInstance(id);
        if (it && it.available !== false && id !== adminId) kept.push(id); else dropped++;
      });
      if (dropped) { state.sel.workerIds = kept; msgs.push('有 ' + dropped + ' 个打工人实例已失效'); }
      return msgs;
    }

    function render() {
      switch (state.stage) {
        case 'idle': renderIdle(); break;
        case 'connect': renderConnect(); break;
        case 'config': renderConfig(); break;
        case 'summary': renderSummary(); break;
        case 'performing': renderPerform(); break;
        case 'collapsed': renderCollapsed(); break;
        case 'record': renderRecord(); break;
        default: renderIdle();
      }
    }

    /* --------- 公共接口 --------- */
    var api = {
      version: VERSION,
      create: function () { if (state.stage === 'unmounted') setStage('idle'); return api; },
      start: function () {
        if (!state.catalog) throw new Error('start: catalog required');
        resetSelKeep();
        state.connected = false;
        setStage('connect');
        return api;
      },
      cancel: function () {
        clearTimers();
        state.inflight = null;
        state.submitDisabled = false;
        state.lastReject = null;
        state.errors = [];
        // 取消后旧 run 不再发 ready；迟到回执因 stage 不再是 summary 而被丢弃
        if (state.stage === 'performing' || state.stage === 'summary') {
          setStage('idle');
        }
        return api;
      },
      destroy: function () {
        clearTimers();
        state.destroy = true;
        state.stage = 'unmounted';
        if (root && layer.parentNode === root) root.removeChild(layer);
      },
      setCatalog: function (catalog) {
        if (!catalog || !Array.isArray(catalog.projects) || !Array.isArray(catalog.instances)) {
          throw new Error('setCatalog: projects[] 与 instances[] 必填');
        }
        state.catalog = deepCloneFreeze(catalog);
        state.catalogFrozen = Object.isFrozen(state.catalog);
        hDemo.textContent = state.catalog.mode === 'formal' ? '正式平台' : DEMO_LABEL;
        var invalidated = reconcileSelections();
        if (invalidated.length) state.staleNote = invalidated.join('；') + '，请重新选择';
        if (state.stage === 'config') renderConfig();
        return api;
      },
      resolveConfirm: function (result) { return resolveConfirm(result); },
      setReducedMotion: function (b) {
        state.reduced = !!b;
        layer.classList.toggle('oop-reduced', state.reduced);
        if (state.stage === 'performing') { runChatSequence(); }
        return api;
      },
      setViewport: function (vp) {
        if (!vp) return api;
        var w = Number(vp.width) || 0;
        var narrow = w < 380;
        layer.classList.toggle('oop-narrow', narrow);
        layer.setAttribute('data-scene-width', String(w));
        return api;
      },
      getStage: function () { return state.stage; },
      debug: function () {
        return {
          version: VERSION,
          stage: state.stage,
          connected: state.connected,
          catalogFrozen: !!state.catalogFrozen,
          capacity: state.catalog && state.catalog.capacity ? state.catalog.capacity : null,
          errors: state.errors.slice(),
          staleNote: state.staleNote,
          sel: {
            projectId: state.sel.projectId,
            adminRef: state.sel.adminRef ? structuredCloneSafe(state.sel.adminRef) : null,
            workerIds: state.sel.workerIds.slice(),
            reviewRounds: state.sel.reviewRounds
          },
          reviewRounds: state.sel.reviewRounds,
          submitDisabled: state.submitDisabled,
          inflightRequestId: state.inflight ? state.inflight.request_id : null,
          lastRun: state.lastRun ? { run_id: state.lastRun.run_id, epoch: state.lastRun.epoch } : null,
          readySentForRun: state.readySentForRun,
          recordCount: state.record.length,
          recordSeq: state.record.map(function (e) { return e.kind + ':' + e.role; }),
          counts: JSON.parse(JSON.stringify(state.counts)),
          reduced: state.reduced
        };
      }
    };

    if (opts.catalog) api.setCatalog(opts.catalog);
    api.create();
    return api;
  }

  return { version: VERSION, mount: mount, safeHttpsHref: safeHttpsHref, hashSeed: hashSeed };
});
