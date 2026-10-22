/**
 * 办公室 · 数据版（flat office）
 *
 * 3D 场景那套（角色、动作、动画适配）先搁置：它要做的事很多，而**屏幕上
 * 真正要回答的问题**其实只有几个——
 *
 *   · 现在有几条活，谁在做，卡在哪
 *   · 哪条活失败了，**为什么**失败
 *   · 平台现在有谁能用、额度池里还有什么
 *   · 有没有一份方案在等我（人）的最终审核
 *   · 我（人）现在能做什么
 *
 * 所以这一版把**平台真状态**直接铺在页面上：任务表、选中任务的详情与事件
 * 轨迹、CLI 原话、平台状态、以及**人自己的动作**。3D 场景仍在
 * `?scene=3d` 里（后期慢慢打磨），两版共用同一套数据源。
 *
 * 规矩不变：这是**观察面**。除了标注为"人"的动作，这里不替任何人改状态。
 */

import './flat-office.css';
import { isAdministratorHarness, isPlatformAssistantHarness } from './harness-roles.js';
import { createOfficeReadRuntime } from './office-read-runtime.js';
import { createOfficeReadFixture } from './office-read-fixture.js';

const STATE_LABEL = {
  draft: '草稿',
  ready: '待领取',
  queued: '排队中',
  claimed: '已领取',
  in_progress: '执行中',
  verifying: '校验中',
  awaiting_review: '待审核',
  changes_requested: '需返修',
  approved: '审核通过',
  merging: '合并中',
  merged: '已合入',
  abandoned: '已放弃',
  stale: '租约失效',
  cancelled: '已取消',
  escalated: '待人工',
  awaiting_human: '等人回答',
  reclaiming: '重新领取中',
  plan_drift: '方案漂移',
  impl_drift: '实现漂移',
  blocked: '受阻',
  needs_help: '求助中',
};

const FAILED_STATES = new Set(['abandoned', 'stale', 'cancelled']);
const WAITING_STATES = new Set(['draft', 'ready', 'queued']);

/** 状态的"紧急度"：越靠前越该先被人看见。 */
const STATUS_RANK = {
  abandoned: 0, stale: 0, blocked: 0, needs_help: 0, escalated: 0, awaiting_human: 0,
  changes_requested: 1, plan_drift: 1, impl_drift: 1, awaiting_review: 1,
  claimed: 2, in_progress: 2, verifying: 2, reclaiming: 2,
  ready: 3, queued: 3, draft: 3,
  merging: 4, approved: 4, merged: 4, cancelled: 4,
};

/** payload 里的字段名（认识的给中文，不认识的照实写英文键）。 */
const FIELD_LABEL = {
  attempt_id: '哪一次 attempt',
  attempt_no: '第几次尝试',
  attempt_budget_override: '给了预算豁免',
  branch: '分支',
  budget_override_by: '谁给的豁免',
  claim_request_id: '领取请求',
  commit: 'commit',
  completion: '成员自述',
  decided_by: '谁决定的',
  decision_id: '裁决',
  failure_reason: '失败原因',
  fencing_epoch: 'fencing 代次',
  findings_hash: '问题清单哈希',
  previous_status: '上一步状态',
  receipt_id: '回执',
  registration_version: '注册版本',
  reminder_no: '第几次提醒',
  interval_minutes: '提醒间隔（分钟）',
  plan_dependencies_unmet: '未满足的依赖',
  reason: '原因',
  registration_version: '注册版本',
  review_id: '评审',
  review_revision: '评审第几轮',
  reviewed_commit: '被审的 commit',
  revert_commit: '回滚用的 commit',
  reverted_commit: '被回滚的 commit',
  revised_from: '从哪条改派过来',
  unchanged_sweeps: '连续几轮没进展',
  waited_seconds: '等了（秒）',
  workspace_id: '工作区',
  boot_id: '启动 id',
  fingerprint: '指纹',
  claim_request_id: '领取请求',
};

/** 额度档位：free -> reserve（兜底，不自动动用）-> metered。 */
const TIER_LABEL = { free: '免费', reserve: '兜底', metered: '计量' };

const escape = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (ch) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);

const timeOf = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false });
};

/** 只要到分钟：额度曲线上的刻度，精确到秒没有意义。 */
const timeMinute = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleString('zh-CN', {
        hour12: false,
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
};

/** 项目的任务范围：开工回执里的 taskIds ∪ 标题前缀「项目名 · 」。 */
function projectScope() {
  try {
    const raw = localStorage.getItem('agent-office.active-project.v1');
    const projects = JSON.parse(localStorage.getItem('agent-office.projects.v1') || '[]');
    const project = projects.find((item) => item && item.id === raw) || null;
    if (!project) return null;
    const ids = new Set((project.taskIds || []).map(String));
    const name = String(project.name || '').trim();
    return {
      id: project.id,
      name,
      matches: (task) =>
        ids.has(String(task.task_id)) ||
        (Boolean(name) && String(task.title || '').startsWith(`${name} · `)),
    };
  } catch {
    return null;
  }
}

/** 一眼看清"现在怎样"：待领取 / 进行中 / 待审 / 失败 / 已完成。 */
function countBy(tasks) {
  const counts = { waiting: 0, active: 0, review: 0, failed: 0, done: 0 };
  for (const task of tasks) {
    const status = String(task.status || '');
    if (WAITING_STATES.has(status)) counts.waiting += 1;
    else if (['claimed', 'in_progress', 'verifying'].includes(status)) counts.active += 1;
    else if (['awaiting_review', 'changes_requested', 'escalated', 'awaiting_human'].includes(status)) counts.review += 1;
    else if (FAILED_STATES.has(status)) counts.failed += 1;
    else if (status === 'merged') counts.done += 1;
  }
  return counts;
}

export function mountFlatOffice({ root, project: projectOf }) {
  const state = {
    tasks: [],
    selected: null,
    detail: null,
    output: null,
    availability: [],
    pool: null,
    plan: null,
    status: null,
    scope: null,
    filter: 'all',
    error: null,
    messages: [],
    source: 'platform',
    sort: 'updated',
    page: 1,
    pageSize: 20,
    failureOutput: null,
    allEvents: false,
    events: [],
    chatExpanded: false,
  };
  // 本地样例（不连平台）：给没有平台的人看界面用。
  const demoRuntime = createOfficeReadRuntime();
  const demoFixture = createOfficeReadFixture();
  demoRuntime.configure(demoFixture.scope);
  demoRuntime.ingest(demoFixture);

  const api = async (route, body_) => {
    const response = await fetch(`/api/formal${route}`, {
      cache: 'no-store',
      ...(body_
        ? {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Office-Formal': '1' },
            body: JSON.stringify(body_),
          }
        : {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
    return data;
  };

  // ------------------------------------------------- 与管理员对话（@提及）
  //
  // 平台现有的通道是**群消息 + @mention**（R10 §19）：没有第二种私信。
  // 所以"直接和管理员说话"在这里就是：一条 @管理员 的群消息，加上一个把
  // 「我 @ 给管理员的」和「管理员回我的」挑出来看的面板。不假装是私聊。

  const ADMIN_THREAD_REFS = ['to-admin', 'reassign-request', 'dispatch-request'];
  // 定向消息的签名（R10 §19）：只有发送方与收件方看得到。
  const DIRECT_REF = 'direct';

  /** 平台配置的管理员窗口（事实，不是猜的）。 */
  function adminIdentity() {
    return (state.status && state.status.admin && state.status.admin.identity) || null;
  }

  function adminName() {
    const id = adminIdentity();
    if (!id) return null;
    const roster = (state.status && state.status.harnesses) || [];
    const hit = roster.find((item) => item.harness_id === id);
    return (hit && hit.name) || id;
  }

  /**
   * 这轮对话：我 @ 给管理员的，加上从第一条这样的消息开始、
   * 管理员发的那些。
   *
   * 为什么不把管理员在这个群里的所有话都摆进来：群是多项目共用的，
   * 管理员以前的工作回执（不要求我们之间有任何关系的那些）不是
   * “我和管理员的对话”。从我第一条开始，才是这轮对话。
   */
  function adminThread() {
    const adminId = adminIdentity();
    if (!adminId) return { items: [], started: false };
    const ordered = [...state.messages].sort(
      (a, b) => Number(a.created_seq || 0) - Number(b.created_seq || 0),
    );
    const isAdmin = (message) => message.sender_id === adminId;
    const isDirect = (message) =>
      (Array.isArray(message.refs) ? message.refs : []).includes(DIRECT_REF);
    const isMine = (message) =>
      !isAdmin(message) &&
      ((Array.isArray(message.refs) ? message.refs : []).some((ref) => ADMIN_THREAD_REFS.includes(ref)) ||
        isDirect(message));
    const mine = ordered.filter(isMine);
    const started = mine.length > 0;
    const from = started ? Math.min(...mine.map((message) => Number(message.created_seq || 0))) : null;
    const items = ordered.filter(
      (message) => isAdmin(message) ? from === null || Number(message.created_seq || 0) >= from : isMine(message),
    );
    return { items, started };
  }

  /** 对话框挂在 body 上：办公室每次刷新都会重画，挂在里面会被一起收走。 */
  function buildAdminDialog() {
    const dialog = document.createElement('dialog');
    dialog.className = 'flat-dialog flat-admin-chat';
    dialog.id = 'flatAdminChat';
    dialog.setAttribute('aria-label', '与管理员对话');
    dialog.innerHTML = `
      <header class="flat-dialog-head">
        <div>
          <h2 id="flatAdminChatTitle">与管理员对话</h2>
          <p class="flat-note">发出去的是项目群里的 <b>@管理员</b>：管理员会收到这条 mention，他的回话也在群里。不是只有你们俩能看的私聊。</p>
        </div>
        <button type="button" class="flat-dialog-close" data-act="close-admin-chat" aria-label="关闭对话">×</button>
      </header>
      <div class="flat-dialog-list" id="flatAdminChatList"></div>
      <form class="flat-dialog-form" id="flatAdminChatForm">
        <input type="text" id="flatAdminChatInput" placeholder="对管理员说一句话…" aria-label="给管理员的消息" />
        <button type="submit">发送</button>
      </form>`;
    document.body.append(dialog);
    dialog.addEventListener('close', () => {
      const input = dialog.querySelector('#flatAdminChatInput');
      if (input) input.value = '';
    });
    // 这个对话框挂在 body 上（不跟着办公室重画），所以办公室的点击委托听不到它。
    // 关闭按钮必须自己处理，否则对话框关不掉。
    dialog.addEventListener('click', (event) => {
      if (event.target.closest('[data-act="close-admin-chat"]')) {
        dialog.close();
        return;
      }
      if (event.target === dialog) dialog.close();
    });
    dialog.querySelector('#flatAdminChatForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const input = dialog.querySelector('#flatAdminChatInput');
      const body = String(input.value || '').trim();
      const adminId = adminIdentity();
      if (!body || !adminId) return;
      input.value = '';
      try {
        // 定向消息：只有我与管理员看得到（R10 §19）。mention 让管理员**收到**，
        // direct 让其他成员看不到；refs 里的 direct 让这条在本面板里可查。
        await api('/messages', { body, mentions: [adminId], refs: [DIRECT_REF], direct: true });
        await refresh();
      } catch (error) {
        state.error = String(error.message || error);
        render();
      }
    });
    return dialog;
  }

  const adminDialog = buildAdminDialog();

  /** 面板打开时把线程刷新一遍。 */
  function syncAdminDialog() {
    const title = adminDialog.querySelector('#flatAdminChatTitle');
    const list = adminDialog.querySelector('#flatAdminChatList');
    const name = adminName();
    if (title) title.textContent = name ? `与 ${name} 对话` : '与管理员对话';
    if (!list) return;
    const adminId = adminIdentity();
    if (!adminId) {
      list.innerHTML = '<p class="flat-empty">平台还没有配置管理员窗口，管理员 Agent 还没上线。</p>';
      return;
    }
    const { items: thread, started } = adminThread();
    if (!thread.length) {
      list.innerHTML = '<p class="flat-empty">还没有和管理员说过话。发一条，它会出现在项目群里并 @ 到管理员。</p>';
      return;
    }
    const shown = thread.slice(-40);
    const dropped = thread.length - shown.length;
    list.innerHTML = (dropped > 0
        ? `<p class="flat-note">还有 ${dropped} 条早先的，这里只列最后 ${shown.length} 条。</p>`
        : '') +
      shown
        .map((message) => {
        const mine = message.sender_type === 'human';
        const who = mine
          ? '你'
          : message.sender_id === adminId
            ? adminName() || message.sender_id
            : message.sender_id || '平台';
        const at = String(message.created_at || '').replace('T', ' ').slice(0, 16);
        return `<article class="flat-thread-msg${mine ? ' is-mine' : ' is-admin'}">
          <b>${escape(who)}</b><span class="flat-thread-at">${escape(at)}</span>
          <p title="${escape(message.body || '')}">${escape(message.body || '')}</p></article>`;
      })
      .join('');
    // 列表刚改完时高度还没有，这时设 scrollTop 会被截掉；等一帧。
    requestAnimationFrame(() => { list.scrollTop = list.scrollHeight; });
  }

  // ------------------------------------------------ 任务书 / 成员动态

  /** 任务书：方案定稿的正文（人看的那一份）。 */
  function taskBrief() {
    const plan = state.plan && state.plan.draft ? state.plan.draft : null;
    const doc = plan && plan.document ? plan.document : null;
    return {
      planId: plan ? plan.plan_id : null,
      title: (doc && doc.title) || '任务书',
      markdown: doc && typeof doc.markdown === 'string' ? doc.markdown : '',
    };
  }

  /**
   * 成员的动态：全部从**平台的事件日志**算，不是本机积累的观察值。
   *
   * 归属说明：提交、放弃是事件的 actor（就是那个人）；返修与通过是
   * 发生在任务上的，归到该任务**当前持有者**。
   */
  function memberStats() {
    const events = state.events || [];
    const tasks = state.tasks || [];
    const ownerOf = new Map(tasks.map((task) => [task.task_id, task.owner]));
    const titleOf = new Map(tasks.map((task) => [task.task_id, task.title || task.task_id]));
    const rows = new Map();
    const roster = (state.status && state.status.harnesses) || [];
    const adminId = (state.status && state.status.admin && state.status.admin.identity) || null;
    // 只算**在簿的窗口**：平台自己的执行者（sweeper / merge-worker 之类）、
    // 群主、管理员与平台助手都不是“成员”。
    const isMember = (id) => {
      if (!id) return false;
      if (id === adminId) return false;
      if (isAdministratorHarness({ harness_id: id }, adminId)) return false;
      if (isPlatformAssistantHarness({ harness_id: id })) return false;
      return roster.some((item) => item.harness_id === id);
    };
    const ensure = (id) => {
      if (!isMember(id)) return null;
      if (!rows.has(id)) {
        rows.set(id, { id, name: id, submissions: 0, reworks: 0, abandons: 0, passed: 0, durations: [], active: 0, queued: 0 });
      }
      return rows.get(id);
    };
    const startedAt = new Map();
    for (const event of events) {
      const kind = String(event.event_name || '');
      const actor = event.actor_id;
      const taskId = event.aggregate_id;
      const owner = ownerOf.get(taskId) || actor;
      if (kind === 'claim' || kind === 'start' || kind === 'claim_for_revision') {
        startedAt.set(taskId, Date.parse(event.created_at) || 0);
        continue;
      }
      if (kind === 'submit_result' || kind === 'resubmit') {
        const row = ensure(actor);
        if (!row) continue;
        row.submissions += 1;
        const started = startedAt.get(taskId);
        const at = Date.parse(event.created_at) || 0;
        if (started && at > started) row.durations.push(Math.round((at - started) / 1000));
        continue;
      }
      if (kind === 'request_changes' || kind === 'reopen_for_changes') {
        const row = ensure(owner);
        if (row) row.reworks += 1;
        continue;
      }
      if (kind === 'approve' || kind === 'verify_passed' || kind === 'merge_success') {
        const row = ensure(owner);
        if (row) row.passed += 1;
        continue;
      }
      if (kind === 'abandon') {
        const row = ensure(actor);
        if (row) row.abandons += 1;
      }
    }
    // 在手 / 排队：任务列表里的现状（事实）。
    for (const task of tasks) {
      const owner = task.owner;
      if (!owner) continue;
      const row = ensure(owner);
      if (!row) continue;
      if (['claimed', 'in_progress', 'verifying'].includes(task.status)) row.active += 1;
      if (task.status === 'ready') row.queued += 1;
    }
    for (const [id, row] of rows) {
      const hit = roster.find((item) => item.harness_id === id);
      row.name = (hit && hit.name) || id;
    }
    return [...rows.values()]
      .map((row) => {
        const sorted = [...row.durations].sort((a, b) => a - b);
        const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : null;
        const total = row.submissions + row.reworks + row.abandons;
        return { ...row, median, total, score: total ? row.submissions / total : null };
      })
      .sort((a, b) => (b.score ?? -1) - (a.score ?? -1) || (a.median ?? 1e9) - (b.median ?? 1e9) || b.submissions - a.submissions);
  }

  /** 成员的求助：平台事件里的 help_requested，去掉已经有结论的。 */
  function helpRequests() {
    const events = state.events || [];
    const titleOf = new Map((state.tasks || []).map((task) => [task.task_id, task.title || task.task_id]));
    const requested = new Map();
    const settled = new Set();
    for (const event of events) {
      const payload = event.payload && typeof event.payload === 'object' ? event.payload : {};
      const helpId = payload.help_id;
      if (!helpId) continue;
      if (event.event_name === 'help_requested') {
        requested.set(helpId, {
          helpId,
          taskId: event.aggregate_id,
          title: titleOf.get(event.aggregate_id) || event.aggregate_id,
          owner: event.actor_id,
          reason: typeof payload.reason === 'string' ? payload.reason : '',
          at: event.created_at,
        });
      }
      if (['help_accepted', 'help_rejected', 'help_resolved'].includes(event.event_name)) settled.add(helpId);
    }
    return [...requested.values()].filter((item) => !settled.has(item.helpId));
  }

  /**
   * 谁接过这条活：从平台事件里的领取事件看。
   * 当前持有者为空时（放弃、被取消、被改派后的旧任务），
   * 这里能看出谁接过它。
   */
  function holdersOf(taskId) {
    const names = [];
    for (const event of state.events || []) {
      if (event.aggregate_id !== taskId) continue;
      if (!['claim', 'start', 'claim_for_revision', 'take_over'].includes(event.event_name)) continue;
      if (event.actor_id && names[names.length - 1] !== event.actor_id) names.push(event.actor_id);
    }
    return names;
  }

  /** 谁在拿着这条活（拿不了就说谁接过）。 */
  function detailHolds(task) {
    const holds = whoHolds(task);
    const attempt = `第 ${task.attempt_no ?? 0}/${task.max_attempts ?? 0} 次尝试`;
    if (holds.now) return `现在拿着：${holds.now} · ${attempt}`;
    if (holds.before) return `现在没人拿着（之前是 ${holds.before}）· ${attempt}`;
    if (Number(task.attempt_no || 0) > 0) return `现在没人拿着（接过 ${task.attempt_no} 次）· ${attempt}`;
    return `还没人接过 · ${attempt}`;
  }

  function whoHolds(task) {
    if (task.owner) return { now: task.owner, before: null };
    const holders = holdersOf(task.task_id);
    return { now: null, before: holders.length ? holders[holders.length - 1] : null };
  }

  /** 审核结论的说人话（事件名 -> 人话）。 */
  const REVIEW_VERDICT = {
    approve: '通过',
    request_changes: '打回返修',
    reopen_for_changes: '打回重做',
    verify_passed: '机器校验通过',
    merge_success: '已合入',
    merge_conflict: '合入冲突',
    merge_conflict_needs_owner: '合入冲突（需要人决）',
  };

  /**
   * 任务书里的每一条任务：现在谁拿着、接过几次、交了几次、审核结论。
   *
   * 全部取自**平台事件日志**：领取、提交、审核、合入都是事件。
   */
  function briefTasks() {
    const events = state.events || [];
    const byTask = new Map();
    for (const event of events) {
      if (!byTask.has(event.aggregate_id)) byTask.set(event.aggregate_id, []);
      byTask.get(event.aggregate_id).push(event);
    }
    const CLAIM_EVENTS = ['claim', 'start', 'claim_for_revision', 'take_over'];
    const SUBMIT_EVENTS = ['submit_result', 'resubmit'];
    const REVIEW_EVENTS = ['approve', 'request_changes', 'reopen_for_changes', 'verify_passed', 'merge_success', 'merge_conflict', 'merge_conflict_needs_owner'];
    return (state.tasks || []).map((task) => {
      const list = byTask.get(task.task_id) || [];
      const claims = list.filter((e) => CLAIM_EVENTS.includes(e.event_name));
      const submits = list.filter((e) => SUBMIT_EVENTS.includes(e.event_name));
      const reviews = list.filter((e) => REVIEW_EVENTS.includes(e.event_name));
      const durations = [];
      for (const submit of submits) {
        const at = Date.parse(submit.created_at) || 0;
        const claim = [...claims].reverse().find((c) => (Date.parse(c.created_at) || 0) <= at);
        if (claim) durations.push(Math.max(0, Math.round((at - (Date.parse(claim.created_at) || 0)) / 1000)));
      }
      const sorted = [...durations].sort((a, b) => a - b);
      return {
        task,
        claims,
        submits,
        lastReview: reviews.length ? reviews[reviews.length - 1] : null,
        median: sorted.length ? sorted[Math.floor(sorted.length / 2)] : null,
      };
    });
  }

  /** 任务书的内容：先是活账（谁在做、做得怎么样），后面是方案原文。 */
  function briefBody(brief) {
    const rows = briefTasks();
    if (!rows.length) {
      return '<p class="flat-empty">还没有任务。管理员 Agent 细化之后，这里会给出任务书。</p>';
    }
    const done = rows.filter((r) => ['merged', 'approved', 'done'].includes(r.task.status)).length;
    const running = rows.filter((r) => ['claimed', 'in_progress', 'verifying', 'awaiting_review'].includes(r.task.status)).length;
    const cards = rows
      .map((row) => {
        const task = row.task;
        const holds = whoHolds(task);
        const owner = holds.now
          ? `${holds.now}（现在拿着）`
          : holds.before
            ? `现在没人拿着（之前是 ${holds.before}）`
            : Number(task.attempt_no || 0) > 0
              ? `现在没人拿着（接过 ${task.attempt_no} 次，接手的人不在近期这段事件里）`
              : '还没人接过';
        const history = row.claims.map((c) => c.actor_id).filter((v, i, a) => a.indexOf(v) === i);
        const verdict = row.lastReview
          ? `${REVIEW_VERDICT[row.lastReview.event_name] || row.lastReview.event_name}（${row.lastReview.actor_id || '平台'}）`
          : '还没有审核结论';
        const submitter = row.submits.length
          ? row.submits.map((s) => s.actor_id).filter((v, i, a) => a.indexOf(v) === i).join('、')
          : null;
        return `<section class="flat-brief-task">
          <header>
            <span class="flat-chip flat-chip-${escape(task.status)}">${escape(STATE_LABEL[task.status] || task.status)}</span>
            <b>${escape(task.title || task.task_id)}</b>
            <span class="flat-mono">${escape(task.task_id)}</span>
          </header>
          <dl>
            <dt>谁接的</dt>
            <dd>${escape(owner)}${history.length ? `· 接过：${escape(history.join(' → '))}` : ''}· 第 ${escape(task.attempt_no ?? 0)}/${escape(task.max_attempts ?? 0)} 次尝试</dd>
            <dt>做得怎么样</dt>
            <dd>交了 ${row.submits.length} 次${submitter ? `（${escape(submitter)}）` : ''}；${escape(verdict)}${row.median !== null ? `；接手到交付 ${row.median}s` : ''}</dd>
            ${task.last_failure && task.last_failure.reason ? `<dt>没完成的原因</dt><dd>${escape(task.last_failure.reason)}</dd>` : ''}
          </dl>
        </section>`;
      })
      .join('');
    const original = brief.markdown
      ? `<h3 class="flat-brief-sub">方案原文</h3><pre class="flat-brief">${escape(brief.markdown)}</pre>`
      : '';
    const unclaimed = unclaimedTasks();
    const dispatchNote = unclaimed.length
      ? `<p class="flat-brief-action">还有 <b>${unclaimed.length}</b> 条没人接的（待领取）。
         <button type="button" data-act="ask-admin-dispatch">让管理员派出去</button>
         <span class="flat-note">这是一条 @管理员 的群消息；派给谁由管理员自己决定，办公室不代点。</span></p>`
      : '<p class="flat-brief-action flat-note">没有待领取的活：要么已经都在人手上，要么已经收尾了。</p>';
    return `${dispatchNote}<p class="flat-note">共 ${rows.length} 条任务：进行中 ${running} · 已完成 ${done}。下面每一条都写着：现在谁拿着、接过几次、交了几次、审核结论。</p>
      <p class="flat-note">当前持有者与尝试次数是平台自己的字段，一定准；接过的人与交付次数取自**近期这一段事件**，这个群的事件已经超过一页，更早的接手记录不在窗口里。</p>${cards}${original}`;
  }

  /**
   * 请管理员把没人接的活派出去。
   *
   * 平台的派单是**管理员的权**（R10 §22：群主只说目标，管理员细化并派单）。
   * 所以办公室不能自己派（那是替管理员点钮），只能把请求送到群里、
   * @ 到管理员，让管理员自己决定派给谁。
   */
  async function askAdminToDispatch(taskIds) {
    const adminId = adminIdentity();
    if (!adminId) {
      state.error = '平台还没配置管理员窗口，没法把请求送给管理员。';
      render();
      return;
    }
    const ids = (taskIds || []).filter(Boolean);
    const lines = ids.map((id) => {
      const task = (state.tasks || []).find((item) => item.task_id === id);
      return `- ${id}${task && task.title ? `（${task.title}）` : ''}`;
    });
    const body = ids.length
      ? `这些活还没人接，请派出去：\n${lines.join('\n')}`
      : '这个项目里没有待领取的任务。';
    try {
      await api('/messages', { body, mentions: [adminId], refs: ['to-admin', 'dispatch-request'] });
      await refresh();
    } catch (error) {
      state.error = String(error.message || error);
      render();
    }
  }

  /** 这个项目里还没人接的活。 */
  function unclaimedTasks() {
    return (state.tasks || []).filter((task) => !task.owner && task.status === 'ready');
  }

  function buildPanelDialog(id, label) {
    const dialog = document.createElement('dialog');
    dialog.className = 'flat-dialog';
    dialog.id = id;
    dialog.setAttribute('aria-label', label);
    dialog.innerHTML = `
      <header class="flat-dialog-head">
        <div><h2></h2><p class="flat-note"></p></div>
        <button type="button" class="flat-dialog-close" data-flat-close aria-label="关闭">×</button>
      </header>
      <div class="flat-dialog-list"></div>`;
    document.body.append(dialog);
    dialog.addEventListener('click', (event) => {
      if (event.target.closest('[data-flat-close]')) dialog.close();
      else if (event.target === dialog) dialog.close();
    });
    return dialog;
  }

  const briefDialog = buildPanelDialog('flatBrief', '任务书');
  const memberDialog = buildPanelDialog('flatMembers', '成员动态');
  // 任务书对话框同样挂在 body 上，自己处理其中的按钮。
  briefDialog.addEventListener('click', (event) => {
    if (event.target.closest('[data-act="ask-admin-dispatch"]')) {
      void askAdminToDispatch(unclaimedTasks().map((task) => task.task_id));
    }
  });

  function openPanel(dialog, { title, note, body }) {
    dialog.querySelector('.flat-dialog-head h2').textContent = title;
    dialog.querySelector('.flat-dialog-head .flat-note').textContent = note || '';
    const list = dialog.querySelector('.flat-dialog-list');
    const keepScroll = list.scrollTop;
    list.innerHTML = body;
    // 刷新时不把滚动位置拉回去：用户正看着中间那一条。
    if (keepScroll) list.scrollTop = keepScroll;
    if (!dialog.open) dialog.showModal();
  }

  /**
   * 任务书是**活的**：新增任务、新的接手与审核结论都要自己长出来。
   * 办公室每 15 秒刷一次平台，对话框打开时就重算一遍。
   */
  function syncBriefDialog() {
    if (!briefDialog.open) return;
    const brief = taskBrief();
    openPanel(briefDialog, {
      title: brief.title,
      note: brief.planId ? `方案 ${brief.planId}（给人看的那一份）` : '',
      body: briefBody(brief),
    });
  }

  // ---------------------------------------------------------------- 渲染

  /** 排序：人挑的顺序，不是数据自带的顺序。 */
  function sortTasks(tasks) {
    const by = state.sort;
    const copy = tasks.slice();
    copy.sort((a, b) => {
      if (by === 'title') return String(a.title || a.task_id).localeCompare(String(b.title || b.task_id), 'zh-CN');
      if (by === 'status') {
        const diff = (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9);
        if (diff) return diff;
      }
      if (by === 'attempts') {
        const diff = Number(b.attempt_no || 0) - Number(a.attempt_no || 0);
        if (diff) return diff;
      }
      // 默认：最近更新的在前（没有时间戳的退回状态序号）。
      const at = Date.parse(a.updated_at || '') || 0;
      const bt = Date.parse(b.updated_at || '') || 0;
      if (at !== bt) return bt - at;
      return Number(b.state_changed_seq || 0) - Number(a.state_changed_seq || 0);
    });
    return copy;
  }

  function render() {
    const scope = state.scope;
    const counts = countBy(state.tasks);
    const filtered = state.tasks.filter((task) => {
      if (state.filter === 'all') return true;
      if (state.filter === 'failed') return FAILED_STATES.has(task.status);
      if (state.filter === 'waiting') return WAITING_STATES.has(task.status);
      if (state.filter === 'active') return ['claimed', 'in_progress', 'verifying'].includes(task.status);
      if (state.filter === 'review') return ['awaiting_review', 'changes_requested', 'escalated', 'awaiting_human'].includes(task.status);
      return true;
    });

    const sorted = sortTasks(filtered);
    const size = Math.max(1, Number(state.pageSize) || 20);
    const pages = Math.max(1, Math.ceil(sorted.length / size));
    if (state.page > pages) state.page = pages;
    const start = (state.page - 1) * size;
    const pageTasks = sorted.slice(start, start + size);

    root.replaceChildren();
    root.append(
      header(counts),
      body(pageTasks, { total: sorted.length, start, pages, size, all: state.tasks.length }),
      rail(),
      chat(),
    );
    const sourceSelect = root.querySelector('#flatSourceSelect');
    if (sourceSelect) sourceSelect.value = state.source;
    root.dataset.flatState = 'ready';
    if (adminDialog.open) syncAdminDialog();
    syncBriefDialog();
  }

  function header(counts) {
    const wrap = document.createElement('header');
    wrap.className = 'flat-head';
    const status = state.status || {};
    wrap.innerHTML = `
      <div class="flat-head-main">
        <span class="flat-eyebrow">AGENT OFFICE · 数据版</span>
        <h1>${escape(state.scope?.name || '本项目')}</h1>
        <p>${escape(status.group_id || '')} · ${status.connected ? '已连接正式平台' : '未连接正式平台'}</p>
      </div>
      <div class="flat-source">
        <label for="flatSourceSelect">数据源</label>
        <select id="flatSourceSelect">
          <option value="platform">正式平台</option>
          <option value="demo">本地样例</option>
        </select>
      </div>
      <dl class="flat-counts">
        <div><dt>待领取</dt><dd>${counts.waiting}</dd></div>
        <div><dt>进行中</dt><dd>${counts.active}</dd></div>
        <div><dt>待审核</dt><dd>${counts.review}</dd></div>
        <div><dt>失败/停滞</dt><dd class="${counts.failed ? 'is-bad' : ''}">${counts.failed}</dd></div>
        <div><dt>已完成</dt><dd>${counts.done}</dd></div>
      </dl>`;
    return wrap;
  }

  function body(filtered, pager) {
    const wrap = document.createElement('div');
    wrap.className = 'flat-body';

    const list = document.createElement('section');
    list.className = 'flat-tasks';
    const bar = document.createElement('div');
    bar.className = 'flat-taskbar';
    const filters = document.createElement('div');
    filters.className = 'flat-filters';
    for (const [key, label] of [
      ['all', '全部'],
      ['waiting', '待领取'],
      ['active', '进行中'],
      ['review', '待审/返修'],
      ['failed', '失败/停滞'],
    ]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.setAttribute('aria-pressed', String(state.filter === key));
      button.addEventListener('click', () => {
        state.filter = key;
        state.page = 1;
        render();
      });
      filters.append(button);
    }
    bar.append(filters);

    // 排序与每页条数：人自己决定先看哪一片。
    const sortWrap = document.createElement('div');
    sortWrap.className = 'flat-sort';
    const sortLabel = document.createElement('label');
    sortLabel.setAttribute('for', 'flatSort');
    sortLabel.textContent = '排序';
    const sortSelect = document.createElement('select');
    sortSelect.id = 'flatSort';
    for (const [value, label] of [
      ['updated', '最近更新'],
      ['status', '状态紧急度'],
      ['attempts', '尝试次数'],
      ['title', '标题'],
    ]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      option.selected = state.sort === value;
      sortSelect.append(option);
    }
    const sizeLabel = document.createElement('label');
    sizeLabel.setAttribute('for', 'flatPageSize');
    sizeLabel.textContent = '每页';
    const sizeSelect = document.createElement('select');
    sizeSelect.id = 'flatPageSize';
    for (const value of [20, 50, 200]) {
      const option = document.createElement('option');
      option.value = String(value);
      option.textContent = String(value);
      option.selected = Number(state.pageSize) === value;
      sizeSelect.append(option);
    }
    sortWrap.append(sortLabel, sortSelect, sizeLabel, sizeSelect);
    bar.append(sortWrap);
    list.append(bar);

    if (!filtered.length) {
      const empty = document.createElement('p');
      empty.className = 'flat-empty';
      empty.textContent = state.tasks.length
        ? '这个筛选下没有任务。'
        : '本项目还没有任务（去顶栏的「开工配置」发一条目标，管理员 harness 会细化后分派）。'
      list.append(empty);
    }

    for (const task of filtered) {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'flat-task' + (state.selected?.task_id === task.task_id ? ' is-selected' : '');
      const reason = task.last_failure?.reason ? ` · ${task.last_failure.reason}` : '';
      const stamp = task.updated_at ? ` · 更新于 ${escape(timeOf(task.updated_at))}` : '';
      const holds = whoHolds(task);
      const who = holds.now
        ? `${holds.now} 拿着`
        : holds.before
          ? `无人拿着（之前是 ${holds.before}）`
          : Number(task.attempt_no || 0) > 0
            ? `无人拿着（接过 ${task.attempt_no} 次）`
            : '无人拿着';
      row.innerHTML = `
        <span class="flat-chip flat-chip-${escape(task.status)}">${escape(STATE_LABEL[task.status] || task.status)}</span>
        <span class="flat-task-title">${escape(task.title || task.task_id)}</span>
        <span class="flat-task-meta">${escape(who)} · 第 ${escape(task.attempt_no ?? 0)}/${escape(task.max_attempts ?? 0)} 次尝试${escape(reason)}</span>
        <span class="flat-task-foot">${stamp}${reason ? `<span class="flat-task-jump" data-jump="log" data-task="${escape(task.task_id)}">看本次失败的真实日志 ↓</span>` : ''}</span>`;
      row.addEventListener('click', (event) => {
        if (event.target.closest('[data-jump="log"]')) return;
        void select(task.task_id);
      });
      list.append(row);
    }
    const pagerBar = document.createElement('div');
    pagerBar.className = 'flat-pager';
    const from = pager.total ? pager.start + 1 : 0;
    const to = Math.min(pager.start + pager.size, pager.total);
    pagerBar.innerHTML = `
      <span>第 ${from}–${to} 条，共 ${pager.total} 条${pager.all !== pager.total ? `（本筛选内 ${pager.total} / 全部 ${pager.all}）` : ''}</span>
      <span class="flat-pager-btns">
        <button type="button" data-act="page-prev" ${state.page <= 1 ? 'disabled' : ''}>上一页</button>
        <span>${state.page} / ${pager.pages}</span>
        <button type="button" data-act="page-next" ${state.page >= pager.pages ? 'disabled' : ''}>下一页</button>
      </span>`;
    list.append(pagerBar);

    wrap.append(list, detail());
    return wrap;
  }

  function detail() {
    const panel = document.createElement('section');
    panel.className = 'flat-detail';
    if (!state.selected) {
      const hint = document.createElement('p');
      hint.className = 'flat-empty';
      hint.textContent = '选一条任务看它的规格、事件轨迹和 CLI 原话。';
      panel.append(hint);
      return panel;
    }
    const task = state.selected;
    const detailData = state.detail || {};
    const rawEvents = detailData.events;
    const events = Array.isArray(rawEvents)
      ? rawEvents
      : Array.isArray(rawEvents?.events)
        ? rawEvents.events
        : [];
    const diff = detailData.diff || {};
    const files = Array.isArray(diff.files) ? diff.files : [];
    const spec = detailData.task?.spec || {};
    const acceptance = Array.isArray(spec.acceptance)
      ? spec.acceptance.map((item) => item.description || JSON.stringify(item)).join('；')
      : spec.acceptance || '';
    const paths = Array.isArray(spec.exclusive_paths) ? spec.exclusive_paths : [];

    panel.innerHTML = `
      <div class="flat-detail-head">
        <div>
          <span class="flat-chip flat-chip-${escape(task.status)}">${escape(STATE_LABEL[task.status] || task.status)}</span>
          <h2>${escape(task.title || task.task_id)}</h2>
          <p class="flat-mono">${escape(task.task_id)}</p>
          <p class="flat-mono">${escape(detailHolds(task))}</p>
        </div>
        <div class="flat-detail-actions">${humanActions(task)}</div>
      </div>
      ${lineageBlock(task)}
      ${spec.goal ? `<section class="flat-spec"><h3>工单</h3><p>${escape(spec.goal)}</p></section>` : ''}
      ${acceptance ? `<section class="flat-spec"><h3>验收</h3><p>${escape(acceptance)}</p></section>` : ''}
      ${paths.length ? `<section class="flat-spec"><h3>允许改动的路径</h3><ul>${paths.map((p) => `<li><code>${escape(p)}</code></li>`).join('')}</ul></section>` : ''}
      ${files.length ? `<section class="flat-spec"><h3>这次改了什么</h3><ul>${files.map((f) => `<li><code>${escape(typeof f === 'string' ? f : f.path || JSON.stringify(f))}</code></li>`).join('')}</ul></section>` : ''}
      <section class="flat-spec"><h3>事件轨迹</h3>
        <p class="flat-note">共 ${events.length} 条，新的在前。点开每一条，看它到底带了什么。</p>
        <ol class="flat-events">${events
          .slice(-(state.allEvents ? events.length : 14))
          .reverse()
          .map(eventRow)
          .join('')}</ol>
        ${events.length > 14
          ? `<button type="button" data-act="events-all">${state.allEvents ? '只看最近 14 条' : `看全部 ${events.length} 条`}</button>`
          : ''}
      </section>
      ${isFailed(task) ? failureLog(task) : ''}
      ${showLiveOutput()
        ? `<section class="flat-spec"><h3>CLI 原话${state.output?.output?.current ? '（当前这一轮）' : ''}</h3><pre class="flat-output">${escape(outputText())}</pre></section>`
        : ''}`;
    return panel;
  }

  /**
   * 一条事件：标题行是骨架，**payload 才是这件事的细节**。
   * "改派给了谁"、"为什么失败"都在 payload 里，光看事件名等于没看见。
   */
  function eventRow(event) {
    const payload = event.payload && typeof event.payload === 'object' ? event.payload : null;
    const hint = eventHint(event);
    const fields = payload ? payloadFields(payload) : [];
    return `<li class="flat-event">
      <details>
        <summary>
          <span class="flat-event-seq">${escape(event.seq)}</span>
          <b>${escape(event.event_name)}</b>
          <em>${escape(event.actor_id || '')}</em>
          ${hint ? `<span class="flat-event-hint">${escape(hint)}</span>` : ''}
        </summary>
        ${fields.length
          ? `<dl class="flat-event-payload">${fields
              .map(([key, value]) => `<dt>${escape(key)}</dt><dd>${escape(value)}</dd>`)
              .join('')}</dl>`
          : '<p class="flat-empty">这个事件没有附带信息。</p>'}
      </details>
    </li>`;
  }

  /** 事件名旁边那一句话：先说人话，让人知道要不要点开。 */
  function eventHint(event) {
    const payload = event.payload || {};
    if (typeof payload.reason === 'string' && payload.reason.trim()) return clip(payload.reason, 48);
    if (payload.completion && typeof payload.completion.summary === 'string') {
      return clip(payload.completion.summary, 48);
    }
    if (typeof payload.previous_status === 'string') return `上一步是 ${payload.previous_status}`;
    if (typeof payload.failure_reason === 'string' && payload.failure_reason.trim()) {
      return clip(payload.failure_reason, 48);
    }
    return '';
  }

  /** payload 的字段表：键换成中文（认识的），值原样（长的截断并说明）。 */
  function payloadFields(payload) {
    const rows = [];
    for (const [key, value] of Object.entries(payload)) {
      rows.push([FIELD_LABEL[key] || key, clip(formatValue(value), 600)]);
    }
    return rows;
  }

  /** 值怎么变成人话：对象/数组不硬转 JSON 巨兽，能摘要就摘要。 */
  function formatValue(value) {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? '是' : '否';
    if (typeof value === 'number') return String(value);
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      if (!value.length) return '（空）';
      const items = value.map((item) => (item && typeof item === 'object' ? JSON.stringify(item) : String(item)));
      return `${value.length} 项：${items.join('、')}`;
    }
    if (typeof value === 'object') {
      const files = Array.isArray(value.changed_files) ? value.changed_files : null;
      const commit = typeof value.commit === 'string' ? value.commit : '';
      const fileNote = files ? `；改了 ${files.length} 个文件` : '';
      const summary = value.summary ?? value.text ?? value.message;
      if (typeof summary === 'string' && summary.trim()) return summary + fileNote;
      // 成员自述的正文在 harness_output.final_message 里；但 opencode 的"最后一句"
      // 是它自己的事件流（JSON），那不是给人看的话——改说改了哪些文件。
      const final =
        value.harness_output && typeof value.harness_output.final_message === 'string'
          ? value.harness_output.final_message.trim()
          : '';
      if (final && !final.startsWith('{') && !final.startsWith('[')) return final + fileNote;
      const parts = [];
      if (files) parts.push(`改了 ${files.length} 个文件：${files.join('、')}`);
      if (commit) parts.push(`commit ${commit}`);
      if (final) parts.push(`（CLI 的最后一句话是它自己的事件流，${final.length} 字）`);
      if (parts.length) return parts.join('；');
      return JSON.stringify(value);
    }
    return String(value);
  }

  /** 太长的值截断，但要**说清楚被截了多少**——不假装是全文。 */
  function clip(text, limit) {
    const value = String(text ?? '');
    if (value.length <= limit) return value;
    return `${value.slice(0, limit)}…（全文 ${value.length} 字，这里只显示前 ${limit} 字）`;
  }

  /**
   * 「CLI 原话」要不要显示：失败的那一轮归"真实日志"，但**正在重跑的这一轮**
   * 说的是另一回事——它在跑，就该看得见。
   */
  function showLiveOutput() {
    if (!state.output) return false;
    if (!isFailed(state.selected)) return true;
    return state.output?.output?.current === true;
  }

  /**
   * 这条活的**来路**。
   *
   * 两段：跨任务的**改派**（管理员取消旧的、派一条新的——两步各自留痕，
   * 平台没有结构化的"谁接替谁"字段，链接在管理员自己写的那句群里说明里，
   * 所以这里标出来它是"管理员的说法"），和本条活**每一次尝试**
   * （谁领的、什么时候、结果如何、从哪一轮改过来）。
   * 以前这些散在事件流里，要一条条读。
   */
  function lineageBlock(task) {
    const events = detailEvents();
    const attempts = attemptChain(events);
    const chain = reassignChain(task.task_id);
    if (!attempts.length && !chain.before.length && !chain.after.length) return '';

    const nodes = [];
    for (const item of chain.before) nodes.push({ id: item.taskId, edge: item.edge });
    nodes.push({ id: task.task_id, current: true });
    for (const item of chain.after) nodes.push({ id: item.taskId, edge: item.edge });
    const chainHtml = nodes.length > 1
      ? `<p class="flat-chain">${nodes
          .map((node, index) => {
            const label = node.current ? `${node.id}（本条）` : node.id;
            const mark = node.current ? ' is-current' : '';
            return `${index ? '<span class="flat-arrow">▶</span>' : ''}<span class="flat-node${mark}">${escape(label)}</span>`;
          })
          .join('')}</p>
        <p class="flat-note">${escape(chainNote(chain))}</p>`
      : '';

    const attemptHtml = attempts.length
      ? `<h4>这条活的每一次尝试</h4>
        <ol class="flat-attempts">${attempts
          .map((row) => `<li>
            <span class="flat-attempt-no">${row.no != null ? `第 ${row.no} 次` : '第 ? 次'}</span>
            <span class="flat-attempt-id">${escape(row.id)}</span>
            <span class="flat-attempt-owner">${escape(row.owner || '不知道是谁领的')}</span>
            <span class="flat-attempt-outcome">${escape(row.outcome || '（没记录）')}</span>
            <span class="flat-attempt-at">${escape(timeOf(row.at))}</span>
            ${row.revisedFrom ? `<span class="flat-attempt-from">从 ${escape(row.revisedFrom)} 改过来</span>` : ''}
          </li>`)
          .join('')}</ol>`
      : '';

    return `<section class="flat-spec flat-lineage">
      <h3>这条活的来路</h3>
      ${chainHtml}
      ${attemptHtml}
    </section>`;
  }

  /** 详情里那一份事件流。 */
  function detailEvents() {
    const raw = state.detail && state.detail.events;
    if (Array.isArray(raw)) return raw;
    if (Array.isArray(raw?.events)) return raw.events;
    return [];
  }

  /**
   * 每次尝试一行：谁领的、什么时候、结果如何、从哪一轮改过来。
   * 事件流是按 seq 排的，所以"最后一次提到这个 attempt 的事"就是它的结果。
   */
  function attemptChain(events) {
    const rows = new Map();
    let lastAttempt = '';
    for (const event of events) {
      const name = event?.event_name;
      const attemptId = event?.payload?.attempt_id || '';
      if (attemptId) lastAttempt = attemptId;
      const target = attemptId || (name === 'release' || name === 'human.release' ? lastAttempt : '');
      if (!target) continue;
      if (!rows.has(target)) {
        rows.set(target, { id: target, no: null, owner: '', at: '', outcome: '', revisedFrom: '' });
      }
      const row = rows.get(target);
      if (!row.at) row.at = event.created_at || '';
      if (name === 'claim' || name === 'claim_for_revision') {
        if (row.no == null) row.no = event.payload?.attempt_no ?? null;
        row.owner = event.actor_id || row.owner;
        row.revisedFrom = event.payload?.revised_from || row.revisedFrom;
        row.outcome = '领了';
      } else if (name === 'start') {
        row.outcome = '开工了';
      } else if (name === 'submit_result') {
        row.outcome = '交了东西';
      } else if (name === 'verify_passed') {
        row.outcome = '校验通过';
      } else if (name === 'abandon') {
        row.outcome = `没交出来：${event.payload?.reason || '（没写理由）'}`;
      } else if (name === 'release' || name === 'human.release') {
        row.outcome = `交回队列：${event.payload?.reason || '（没写理由）'}`;
      }
    }
    // 被要求返修的那一轮：平台把返修记在评审上，链接在下一轮的 revised_from。
    for (const row of rows.values()) {
      if (!row.revisedFrom) continue;
      const previous = rows.get(row.revisedFrom);
      if (!previous) continue;
      previous.outcome =
        previous.outcome === '交了东西'
          ? '交了，但被要求返修'
          : `${previous.outcome || '跑了'}，随后被要求返修`;
    }
    return [...rows.values()].sort((a, b) => (a.no ?? 99) - (b.no ?? 99));
  }

  /**
   * 跨任务的改派链：读管理员自己写的那句群里说明。
   * 平台没有结构化的"谁接替谁"，所以这是**管理员的说法**，不是平台的字段。
   */
  function reassignChain(taskId) {
    const edges = [];
    // 平台字段优先：新派出去的任务都带 `supersedes`（它写在任务的**快照**里，
    // 也就是任务自己的契约里）。
    for (const task of state.tasks) {
      if (task && task.supersedes) {
        edges.push({ from: task.supersedes, to: task.task_id, source: 'platform' });
      }
    }
    // 旧数据回落到管理员在群里写的那句话（那时平台还没这个字段）。
    for (const message of state.messages) {
      const body = String(message.body || '');
      if (!body.includes('改派')) continue;
      const match = body.match(/(FD-[a-f0-9]{16})\s*→\s*(FD-[a-f0-9]{16})/);
      if (!match) continue;
      if (edges.some((edge) => edge.from === match[1] && edge.to === match[2])) continue;
      edges.push({ from: match[1], to: match[2], body, at: message.created_at, source: 'message' });
    }
    const seen = new Set([taskId]);
    const before = [];
    let current = taskId;
    for (let step = 0; step < 12; step += 1) {
      const edge = edges.find((item) => item.to === current && !seen.has(item.from));
      if (!edge) break;
      seen.add(edge.from);
      before.unshift({ taskId: edge.from, edge });
      current = edge.from;
    }
    const after = [];
    current = taskId;
    for (let step = 0; step < 12; step += 1) {
      const edge = edges.find((item) => item.from === current && !seen.has(item.to));
      if (!edge) break;
      seen.add(edge.to);
      after.push({ taskId: edge.to, edge });
      current = edge.to;
    }
    return { before, after };
  }

  /** 链上的那条边是平台记的，还是管理员在群里说的。 */
  function chainNote(chain) {
    const edge = chain.before[chain.before.length - 1]?.edge || chain.after[0]?.edge;
    if (!edge) return '';
    if (edge.source === 'platform') {
      return '改派是平台记的：任务自己的快照里写着它接替了哪一条。';
    }
    return '改派是管理员在群里说明的：' + String(edge.body || '').slice(0, 160);
  }

  /** 这条活是不是"这次没干成"：平台记了失败理由，或者状态本身就是失败态。 */
  function isFailed(task) {
    return Boolean(task?.last_failure?.reason) || FAILED_STATES.has(String(task?.status || ''));
  }

  /** 失败那一轮的 attempt id：从事件里拿（abandon/release 的 payload 带 attempt_id）。 */
  function failedAttemptId(detailData) {
    const raw = detailData?.events;
    const events = Array.isArray(raw) ? raw : Array.isArray(raw?.events) ? raw.events : [];
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (event?.event_name !== 'abandon' && event?.event_name !== 'release') continue;
      const attemptId = event?.payload?.attempt_id;
      if (attemptId) return String(attemptId);
    }
    return null;
  }

  /**
   * 本次失败的真实日志：失败那一轮 attempt 的 stdout/stderr **原样**。
   * 平台给的失败理由是一句话，理由背后是 harness 真的说过的话——这里把它原样端上来。
   */
  function failureLog(task) {
    const source = state.failureOutput?.output || state.output?.output || null;
    const stderr = String(source?.stderr || '');
    const stdout = String(source?.stdout || '');
    const facts = [
      source?.attempt_id ? `attempt ${source.attempt_id}` : '',
      source?.harness_id ? `harness ${source.harness_id}` : '',
      source?.attempt_status ? `那一轮的状态 ${source.attempt_status}` : '',
      source?.updated_at ? `时间 ${timeOf(source.updated_at)}` : '',
    ].filter(Boolean).join(' · ');
    return `<section class="flat-spec flat-fail-log" id="flatFailLog">
      <h3>本次失败的真实日志</h3>
      ${task.last_failure?.reason ? `<p class="flat-note">平台记的失败理由：${escape(task.last_failure.reason)}</p>` : ''}
      ${task.last_failure?.at ? `<p class="flat-note">失败于 ${escape(timeOf(task.last_failure.at))}</p>` : ''}
      ${facts ? `<p class="flat-note">${escape(facts)}</p>` : ''}
      ${stderr ? `<h4>stderr</h4><pre class="flat-output">${escape(stderr.slice(-4000))}</pre>` : ''}
      ${stdout ? `<h4>stdout</h4><pre class="flat-output">${escape(stdout.slice(-4000))}</pre>` : ''}
      ${!stderr && !stdout ? '<p class="flat-empty">这一轮没有留下 CLI 输出（进程被杀，或它什么也没写）。</p>' : ''}
    </section>`;
  }

  /** CLI 原话：平台的 /output 形状是 {output: {stdout, stderr}}，两边都容一下。 */
  function outputText() {
    const raw = state.output && typeof state.output === 'object'
      ? state.output.output || state.output
      : state.output;
    const text = typeof raw === 'string' ? raw : String(raw?.stdout || raw?.text || '');
    return text.slice(-4000) || '（还没有 CLI 原话）';
  }

  /** 人自己的动作。平台与管理员 harness 都不代劳这些。 */
  function humanActions(task) {
    const spent = Number(task.attempt_no || 0) >= Number(task.max_attempts || 0);
    const buttons = [];
    // 还没人接的：请管理员派出去。派给谁是管理员的决定，
    // 办公室只负责把这个请求送到管理员面前（群里的 @mention）。
    if (!task.owner && task.status === 'ready') {
      buttons.push(
        `<button type="button" data-act="ask-dispatch" title="这是一条 @管理员 的群消息；派给谁由管理员决定">请管理员派出去</button>`,
      );
    }
    if (spent) {
      buttons.push(
        `<button type="button" data-act="override">请求再给一次机会（预算豁免）</button>`,
      );
    }
    buttons.push(`<button type="button" data-act="reassign">请求管理员改派</button>`);
    return buttons.join('');
  }

  /**
   * 额度池：名字只是轮廓；**明细**（有哪些模型、什么档位、谁花了多少）点开才是真的。
   * 额度池是"谁的钱"的问题，所以明细里必须有消费者，而不只是源名。
   */
  function poolCard() {
    const pool = state.pool || {};
    const upstreams = pool.upstreams || [];
    const models = pool.models || [];
    const usage = pool.usage || {};
    const usedUpstreams = usage.upstreams || {};
    const consumers = usage.consumers || {};
    const modelsByUpstream = new Map();
    for (const model of models) {
      const key = model.upstream || model.owned_by || '?';
      if (!modelsByUpstream.has(key)) modelsByUpstream.set(key, []);
      modelsByUpstream.get(key).push(model);
    }
    if (!upstreams.length) {
      return '<section class="flat-card"><h3>额度池</h3><p class="flat-empty">池子现在是空的。</p></section>';
    }
    const items = upstreams.map((source) => {
      const name = source.name || '?';
      const own = modelsByUpstream.get(name) || [];
      const used = usedUpstreams[name] || null;
      const tiers = [...new Set(own.map((model) => model.tier).filter(Boolean))];
      const consumersHere = used?.consumers || {};
      return `<details class="flat-pool-item">
        <summary>
          <b>${escape(name)}</b>
          <span>${escape(source.protocol || '')}${own.length ? ` · ${own.length} 个模型` : ''}</span>
          <em>${used ? `已用 ${used.requests} 次` : '还没人用过'}</em>
        </summary>
        <dl class="flat-facts">
          <dt>上游地址</dt><dd><code>${escape(source.base_url || '—')}</code></dd>
          <dt>来源</dt><dd>${escape(source.origin || '—')}</dd>
          ${source.note ? `<dt>备注</dt><dd>${escape(source.note)}</dd>` : ''}
        </dl>
        ${own.length
          ? `<h4>模型${tiers.length ? `（${tiers.map((tier) => escape(TIER_LABEL[tier] || tier)).join(' / ')}）` : ''}</h4>
             <ul class="flat-pool-models">${own
               .map((model) => `<li><code>${escape(model.id)}</code><span class="flat-tier flat-tier-${escape(model.tier || 'free')}">${escape(TIER_LABEL[model.tier] || model.tier || '')}</span></li>`)
               .join('')}</ul>`
          : '<p class="flat-empty">这个额度源还没探测到模型。</p>'}
        ${used
          ? `<h4>已用额度</h4>
             <p class="flat-note">${used.requests} 次请求 · prompt ${used.prompt_tokens || 0} / completion ${used.completion_tokens || 0} tokens</p>
             ${Object.keys(consumersHere).length
               ? `<p class="flat-note">谁用过：${Object.entries(consumersHere).map(([who, count]) => `${escape(who)} × ${count}`).join('，')}</p>`
               : ''}`
          : ''}
        ${upstreamTimeline(name)}
      </details>`;
    });
    const consumerItems = Object.entries(consumers).map(([who, value]) => {
      const usedHere = Object.entries(value.upstreams || {});
      return `<li><b>${escape(who)}</b>：${value.requests || 0} 次 · ${usedHere.map(([name, count]) => `${escape(name)} × ${count}`).join('，') || '—'}</li>`;
    });
    return `<section class="flat-card">
      <h3>额度池</h3>
      <p class="flat-note">${upstreams.length} 个额度源 · ${models.length} 个模型 · 累计 ${usage.requests || 0} 次请求</p>
      ${inventoryBlock()}
      ${timelineBlock()}
      ${items.join('')}
      ${consumerItems.length
        ? `<details class="flat-pool-item"><summary><b>谁用了谁的额度</b><span>${consumerItems.length} 个消费者</span></summary><ul class="flat-pool-consumers">${consumerItems.join('')}</ul></details>`
        : '<p class="flat-note">还没有人从池子里借过额度。</p>'}
    </section>`;
  }

  /**
   * 额度用掉的**时间分布**：一根柱子一小时。
   * 只给总数是不行的——"是不是刚刚把某个窗口的额度打满了"是关于时间的问题。
   */
  function sparkline(buckets, pick) {
    const values = buckets.map((bucket) => Math.max(0, Number(pick(bucket) || 0)));
    const peak = Math.max(1, ...values);
    const bars = buckets
      .map((bucket, index) => {
        const value = values[index];
        const height = value ? Math.max(14, Math.round((value / peak) * 100)) : 2;
        const when = bucket.at ? timeMinute(bucket.at) : '—';
        return `<i class="${value ? 'has' : ''}" style="height:${height}%" title="${escape(when)} · ${value} 次"></i>`;
      })
      .join('');
    return `<div class="flat-spark">${bars}</div>`;
  }

  /** 什么时候用得最多。 */
  function peakNote(buckets, pick) {
    let best = null;
    for (const bucket of buckets) {
      const value = Number(pick(bucket) || 0);
      if (!best || value > best.value) best = { value, at: bucket.at };
    }
    if (!best || !best.value) return '这段时间没有用量';
    return `高峰 ${timeMinute(best.at)}（${best.value} 次）`;
  }

  /**
   * 额度档位库存：每档有多少模型、平台花了多少、**还剩多少**。
   * 问得到余额的档位才给数字；问不到的（免费档、按量计费）老实说"没有余额"。
   */
  function inventoryBlock() {
    const inventory = state.pool && state.pool.inventory;
    if (!inventory || !Array.isArray(inventory.tiers)) return '';
    const rows = inventory.tiers.map((tier) => {
      const balance = tier.balance;
      const spend = tier.spend || {};
      let gauge = '';
      let detail = '';
      if (balance && balance.known) {
        const interval = balance.interval || {};
        const weekly = balance.weekly || {};
        const percent = weekly.remaining_percent ?? interval.remaining_percent;
        if (percent != null) {
          gauge = `<div class="flat-gauge"><i style="width:${Math.max(0, Math.min(100, Number(percent)))}%"></i><span>${escape(percent)}%</span></div>`;
        }
        const parts = [];
        if (interval.remaining_percent != null) parts.push(`本时段还剩 ${interval.remaining_percent}%`);
        if (weekly.remaining_percent != null) parts.push(`本周还剩 ${weekly.remaining_percent}%`);
        const resets = [];
        if (interval.resets_at) resets.push(`本时段 ${timeMinute(interval.resets_at)} 重置`);
        if (weekly.resets_at) resets.push(`本周 ${timeMinute(weekly.resets_at)} 重置`);
        detail = `<p class="flat-note">${escape(parts.join(' · '))}${resets.length ? `；${escape(resets.join('，'))}` : ''}</p>
          <p class="flat-note">来源 ${escape(balance.source || '')} · ${escape(timeMinute(balance.checked_at))} 查的</p>`;
      } else if (balance && balance.known === false) {
        detail = `<p class="flat-note">问不到余额：${escape(balance.reason || '')}</p>`;
      }
      // 计量档的**钱**：每条 provider 自己的余额（provider 自己报的数字）。
      const balances = Array.isArray(tier.balances) ? tier.balances : [];
      if (balances.length) {
        const known = balances.filter((item) => item.known);
        const unknown = balances.filter((item) => !item.known);
        detail += `<p class="flat-note">余额：${known.length
          ? known
              .map((item) => {
                if (item.remaining != null) {
                  return `${item.upstream} ${item.unit || ''} ${item.remaining}`.trim();
                }
                const amounts = (item.balances || [])
                  .map((line) => `${line.currency} ${line.total}`)
                  .join('，');
                return `${item.upstream} ${amounts}`;
              })
              .join('；')
          : '这一档的 provider 都没有可查的余额接口'}</p>`;
        // 账号层面的用量：provider 自己报的（平台只是它的一个调用方）。
        const daily = known
          .flatMap((item) => (item.daily || []).map((row) => ({ ...row, unit: item.unit })))
          .slice(-3);
        if (daily.length) {
          detail += `<p class="flat-note">账号最近几天（provider 自己报的）：${escape(
            daily
              .map(
                (row) =>
                  `${row.date} ${row.requests} 次 · ${
                    row.actual_cost != null ? row.actual_cost : row.cost
                  } ${row.unit || ''}`,
              )
              .join('；'),
          )}</p>`;
        }
        if (unknown.length) {
          detail += `<p class="flat-note">${escape(
            unknown.map((item) => `${item.upstream}：${item.reason || '问不到'}`).join('；'),
          )}</p>`;
        }
      }
      return `<div class="flat-tier-row">
        <div class="flat-tier-head">
          <b>${escape(tier.label || tier.tier)}</b>
          <span>${tier.models} 个模型 · 平台已用 ${spend.requests || 0} 次${
            spend.prompt_tokens || spend.completion_tokens
              ? ` · prompt ${spend.prompt_tokens || 0} / completion ${spend.completion_tokens || 0} tokens`
              : ''
          }${spend.tokens_unknown ? ` · 有 ${spend.tokens_unknown} 次的 token 没记上` : ''}${
            spend.credits ? ` · ${spend.credits} credits（provider 记的是 credits，不是 token）` : ''
          }</span>
        </div>
        ${gauge}
        ${detail}
        <p class="flat-note">${escape(tier.note || '')}</p>
      </div>`;
    });
    return `<div class="flat-inventory">
      <h4>额度档位库存</h4>
      ${rows.join('')}
    </div>`;
  }

  /** 池子整体：最近 N 小时的用量。 */
  function timelineBlock() {
    const timeline = state.pool && state.pool.timeline;
    if (!timeline || !Array.isArray(timeline.buckets) || !timeline.buckets.length) return '';
    const buckets = timeline.buckets;
    const total = buckets.reduce((sum, bucket) => sum + Number(bucket.requests || 0), 0);
    const first = buckets[0].at;
    const last = buckets[buckets.length - 1].at;
    return `<div class="flat-timeline">
      <div class="flat-timeline-head">
        <span>最近 ${timeline.hours || buckets.length} 小时的用量</span>
        <em>${escape(peakNote(buckets, (bucket) => bucket.requests))}</em>
      </div>
      ${sparkline(buckets, (bucket) => bucket.requests)}
      <div class="flat-timeline-axis"><span>${escape(timeMinute(first))}</span><span>${escape(timeMinute(last))}</span></div>
      <p class="flat-note">这段时间共 ${total} 次请求${
        timeline.outside_window ? `；更早的还有 ${timeline.outside_window} 次，不在这个窗口里` : ''
      }。</p>
    </div>`;
  }

  /** 某个额度源自己的曲线：它的额度是什么时候没的。 */
  function upstreamTimeline(name) {
    const timeline = state.pool && state.pool.timeline;
    if (!timeline || !Array.isArray(timeline.buckets)) return '';
    const pick = (bucket) => (bucket.upstreams || {})[name] || 0;
    const total = timeline.buckets.reduce((sum, bucket) => sum + Number(pick(bucket) || 0), 0);
    if (!total) return '';
    return `<h4>什么时候用掉的</h4>
      ${sparkline(timeline.buckets, pick)}
      <p class="flat-note">${escape(peakNote(timeline.buckets, pick))}；这段时间共 ${total} 次。</p>`;
  }

  function rail() {
    const wrap = document.createElement('aside');
    wrap.className = 'flat-rail';
    // 管理员窗口与平台助手不是"能领活的成员"：它们审稿与协调，不干活。
    const adminId = (state.status && state.status.admin && state.status.admin.identity) || null;
    const usable = state.availability.filter(
      (item) =>
        item.usable &&
        !isAdministratorHarness(item, adminId) &&
        !isPlatformAssistantHarness(item),
    );
    const plan = state.plan && state.plan.draft ? state.plan.draft : null;
    const admin = adminName();
    const { items: thread, started } = adminThread();
    const brief = taskBrief();
    const helps = helpRequests();
    const unclaimed = unclaimedTasks();

    wrap.innerHTML = `
      <section class="flat-card flat-card-brief">
        <h3>任务书</h3>
        ${brief.planId
          ? `<p><b>${escape(brief.title)}</b></p>
             <p class="flat-note">${escape(brief.planId)}</p>
             <p class="flat-note">${(state.tasks || []).length} 条任务${unclaimed.length ? `（${unclaimed.length} 条没人接）` : ''}</p>
             <button type="button" data-act="open-brief">查看任务书</button>`
          : `<p class="flat-note">${(state.tasks || []).length} 条任务</p>
             <p class="flat-empty">还没有方案正文。</p>`}
      </section>
      <section class="flat-card flat-card-members">
        <h3>成员</h3>
        ${helps.length
          ? `<p class="flat-note">${helps.length} 条求助待处理</p>
             <ul class="flat-list">${helps.slice(0, 4).map((item) => `<li><b>${escape(item.owner || '未知')}</b><span>${escape(clip(item.reason || '（没写原因）', 40))}</span><span>${escape(clip(item.title, 28))}</span></li>`).join('')}</ul>`
          : '<p class="flat-empty">没有待处理的求助。</p>'}
        <button type="button" data-act="open-members">成员动态表</button>
      </section>
      <section class="flat-card flat-card-admin">
        <h3>与管理员对话</h3>
        ${admin
          ? `<p><b>${escape(admin)}</b>${adminIdentity() && admin !== adminIdentity() ? ` · ${escape(adminIdentity())}` : ''}</p>
             <p class="flat-note">${!started
               ? '你还没 @ 过管理员。发一条，他会收到 mention。'
               : thread.length
                 ? `这轮对话 ${thread.length} 条。`
                 : '还没说过话。发一条，管理员会收到 mention。'}</p>
             <button type="button" data-act="open-admin-chat">打开对话</button>
             <button type="button" data-act="open-direct-window">开私聊窗口</button>`
          : '<p class="flat-empty">平台还没配置管理员窗口。</p>'}
      </section>
      ${poolCard()}
      <section class="flat-card">
        <h3>现在能干活</h3>
        <ul class="flat-list">${usable
          .map((item) => `<li><b>${escape(item.harness_id)}</b><span>${escape(item.model || '未知模型')}</span></li>`)
          .join('') || '<li class="flat-empty">没有可用窗口。</li>'}</ul>
      </section>
      <section class="flat-card">
        <h3>等我审核的方案</h3>
        ${plan
          ? `<p>${escape(plan.plan_id)} · ${escape((plan.document || {}).tasks || 0)} 个任务</p>
             <p class="flat-note">${escape((plan.document || {}).markdown_path || '')}</p>
             <button type="button" data-act="approve-plan">批准并派单</button>`
          : '<p class="flat-empty">没有待审的方案。</p>'}
      </section>`;
    return wrap;
  }

  /** 群聊：办公室的对话就在平台的群里（不是本地的假消息）。 */
  /**
   * 项目群聊：收起来只占一条。
   *
   * 之前是把消息横着摆在一条 190px 的带子里，消息被切一半、还有一条
   * 横向滚动条——那不算“放在这里”。现在收成一条带子，需要看完整
   * 历史时才展开（纵向滚动，每条三行）。
   */
  function chat() {
    const wrap = document.createElement('section');
    wrap.className = 'flat-chat' + (state.chatExpanded ? ' is-expanded' : '');
    const head = document.createElement('div');
    head.className = 'flat-chat-head';
    const title = document.createElement('h3');
    title.textContent = '项目群聊';
    const count = document.createElement('span');
    count.className = 'flat-chat-count';
    count.textContent = state.messages.length ? `${state.messages.length} 条` : '还没消息';
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'flat-chat-toggle';
    toggle.dataset.act = 'chat-toggle';
    toggle.textContent = state.chatExpanded ? '收起 ▾' : '展开 ▴';
    toggle.setAttribute('aria-expanded', String(Boolean(state.chatExpanded)));
    head.append(title, count, toggle);
    wrap.append(head);
    const list = document.createElement('div');
    list.className = 'flat-chat-list';
    if (!state.messages.length) {
      const empty = document.createElement('p');
      empty.className = 'flat-empty';
      empty.textContent = '群里还没有消息。';
      list.append(empty);
    }
    // 定向消息不属于群聊（只有发送方与收件方看得到），不能混进来。
    const groupMessages = state.messages.filter(
      (message) => !(Array.isArray(message.refs) ? message.refs : []).includes(DIRECT_REF),
    );
    for (const message of groupMessages.slice(-40)) {
      const item = document.createElement('article');
      const isHuman = message.sender_type === 'human';
      item.className = 'flat-msg' + (isHuman ? ' is-human' : '');
      const who = isHuman ? '你' : message.sender_id || '平台';
      const at = String(message.created_at || '').replace('T', ' ').slice(5, 16);
      item.innerHTML = `<b>${escape(who)}</b><span class="flat-msg-at">${escape(at)}</span><p>${escape(message.body || '')}</p>`;
      list.append(item);
    }
    wrap.append(list);
    const form = document.createElement('form');
    form.className = 'flat-chat-form';
    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = '在项目群里说一句话（写 @管理员 会真的向他提醒）';
    input.setAttribute('aria-label', '群聊消息');
    const send = document.createElement('button');
    send.type = 'submit';
    send.textContent = '发送';
    form.append(input, send);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const body = String(input.value || '').trim();
      if (!body) return;
      input.value = '';
      try {
        // 在群里手动 @ 了管理员，就真的建一条 mention（不只是文字）：
        // 平台靠 mention 建投递记录，管理员才收得到。
        const adminId = adminIdentity();
        const mentions =
          adminId && (body.includes(`@${adminId}`) || body.includes('@管理员')) ? [adminId] : [];
        await api('/messages', { body, mentions, refs: mentions.length ? ['to-admin'] : [] });
        await refresh();
      } catch (error) {
        state.error = String(error.message || error);
        render();
      }
    });
    wrap.append(form);
    return wrap;
  }

  // ---------------------------------------------------------------- 交互

  async function select(taskId) {
    state.selected = state.tasks.find((task) => task.task_id === taskId) || null;
    state.detail = null;
    state.output = null;
    state.failureOutput = null;
    render();
    try {
      const [detailData, outputData] = await Promise.all([
        api(`/tasks/${encodeURIComponent(taskId)}`),
        api(`/tasks/${encodeURIComponent(taskId)}/output`).catch(() => null),
      ]);
      state.detail = detailData;
      state.output = outputData;
      // 「本次失败的真实日志」要看的是**失败那一轮**，不是正在重跑的那一轮。
      const failedAttempt = failedAttemptId(detailData);
      if (failedAttempt && outputData?.output?.attempt_id !== failedAttempt) {
        const failed = await api(
          `/tasks/${encodeURIComponent(taskId)}/output?attempt_id=${encodeURIComponent(failedAttempt)}`,
        ).catch(() => null);
        if (failed) state.failureOutput = failed;
      }
    } catch (error) {
      state.error = String(error.message || error);
    }
    render();
  }

  async function doAction(action, payload) {
    try {
      await api(payload.route, payload.body);
      await refresh();
    } catch (error) {
      state.error = String(error.message || error);
      render();
    }
  }

  root.addEventListener('change', (event) => {
    const source = event.target.closest('#flatSourceSelect');
    if (source) {
      state.source = source.value === 'demo' ? 'demo' : 'platform';
      void refresh();
      return;
    }
    const sort = event.target.closest('#flatSort');
    if (sort) {
      state.sort = sort.value || 'updated';
      state.page = 1;
      render();
      return;
    }
    const size = event.target.closest('#flatPageSize');
    if (size) {
      state.pageSize = Number(size.value) || 20;
      state.page = 1;
      render();
    }
  });

  root.addEventListener('click', (event) => {
    // 任务行里的「看本次失败的真实日志」：选那条活，再把日志滚到眼前。
    const jump = event.target.closest('[data-jump="log"]');
    if (jump) {
      const taskId = jump.dataset.task;
      if (taskId) {
        void select(taskId).then(() => {
          document.getElementById('flatFailLog')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
      }
      return;
    }
    const button = event.target.closest('button[data-act]');
    if (!button) return;
    if (button.dataset.act === 'page-prev') {
      state.page = Math.max(1, state.page - 1);
      render();
      return;
    }
    if (button.dataset.act === 'events-all') {
      state.allEvents = !state.allEvents;
      render();
      return;
    }
    if (button.dataset.act === 'chat-toggle') {
      state.chatExpanded = !state.chatExpanded;
      render();
      if (state.chatExpanded) {
        const list = root.querySelector('.flat-chat-list');
        if (list) list.scrollTop = list.scrollHeight;
      }
      return;
    }
    if (button.dataset.act === 'open-brief') {
      const brief = taskBrief();
      openPanel(briefDialog, {
        title: brief.title,
        note: brief.planId ? `方案 ${brief.planId}（给人看的那一份）` : '',
        body: briefBody(brief),
      });
      return;
    }
    if (button.dataset.act === 'open-members') {
      const rows = memberStats();
      const body = rows.length
        ? `<table class="flat-table">
             <thead><tr><th>成员</th><th>在手</th><th>排队</th><th>提交</th><th>返修</th><th>放弃</th><th>通过率</th><th>中位耗时</th></tr></thead>
             <tbody>${rows.map((row) => `<tr>
                <td>${escape(row.name)}</td>
                <td>${row.active}</td>
                <td>${row.queued}</td>
                <td>${row.submissions}</td>
                <td>${row.reworks}</td>
                <td>${row.abandons}</td>
                <td>${row.score === null ? '未知' : Math.round(row.score * 100) + '%'}</td>
                <td>${row.median === null ? '未知' : row.median + 's'}</td>
              </tr>`).join('')}</tbody>
           </table>`
        : '<p class="flat-empty">还没有可核验的跳转。</p>';
      openPanel(memberDialog, {
        title: '成员动态',
        note: '全部从平台事件日志算：提交与放弃归事件的 actor，返修与通过归任务当前持有者。这是观测指标，不是能力认证。',
        body,
      });
      return;
    }
    if (button.dataset.act === 'open-direct-window') {
      // 独立窗口（另开一个标签页）：与管理员的定向消息。
      window.open('/direct.html', 'agent-office-direct', 'width=820,height=760');
      return;
    }
    if (button.dataset.act === 'open-admin-chat') {
      if (!adminDialog.open) adminDialog.showModal();
      syncAdminDialog();
      return;
    }
    if (button.dataset.act === 'close-admin-chat') {
      adminDialog.close();
      return;
    }
    if (button.dataset.act === 'page-next') {
      state.page += 1;
      render();
      return;
    }
    const task = state.selected;
    if (button.dataset.act === 'override' && task) {
      void doAction('/task/budget_override', {
        route: '/task/budget_override',
        body: { task_id: task.task_id, reason: '人给一次机会：尝试预算已用完' },
      });
      return;
    }
    if (button.dataset.act === 'ask-dispatch' && task) {
      void askAdminToDispatch([task.task_id]);
      return;
    }
    if (button.dataset.act === 'reassign' && task) {
      const adminId = adminIdentity();
      void doAction('/messages', {
        route: '/messages',
        body: {
          body: [
            `请把这条活改派给还有额度的窗口：${task.title || task.task_id}（${task.task_id}）`,
            `上次持有者：${task.owner || '无人持有'}`,
            task.last_failure?.reason ? `失败原因：${task.last_failure.reason}` : '',
          ]
            .filter(Boolean)
            .join('\n'),
          // 这条是**说给管理员听的**：mention 让它进他的收件箱，
          // refs 让它出现在"与管理员对话"面板里。
          mentions: adminId ? [adminId] : [],
          task_id: task.task_id,
          refs: ['to-admin', 'reassign-request'],
        },
      });
      return;
    }
    if (button.dataset.act === 'approve-plan') {
      const draft = state.plan && state.plan.draft;
      if (!draft) return;
      void doAction('/plan/approve', { route: '/plan/approve', body: { plan_id: draft.plan_id } });
    }
  });

  // ---------------------------------------------------------------- 数据

  async function refresh() {
    try {
      const scope = projectScope() || (projectOf && projectOf());
      state.scope = scope;
      if (state.source === 'demo') {
        const snapshot = demoRuntime.snapshot();
        state.tasks = snapshot.tasks || [];
        state.messages = snapshot.messages || [];
        state.status = { connected: false, group_id: '（本地样例）' };
        state.availability = [];
        state.pool = null;
        state.plan = null;
        state.error = null;
        render();
        return;
      }
      // 先把**任务表**画出来，再去补右栏与群聊：额度池要问 provider 的余额
      // （几秒），等它回来才出第一屏，看着就像卡住了。
      const [status, tasks] = await Promise.all([
        api('/status').catch(() => null),
        api('/tasks').catch(() => ({ tasks: [] })),
      ]);
      state.status = status;
      const all = tasks.tasks || [];
      state.tasks = scope ? all.filter(scope.matches) : all;
      state.error = null;
      if (!state.selected && state.tasks.length) {
        const first = state.tasks.find((task) => FAILED_STATES.has(task.status)) || state.tasks[0];
        void select(first.task_id);
      }
      render();
      const [availability, pool, plan, messages, events] = await Promise.all([
        api('/availability').catch(() => ({ availability: [] })),
        api('/pool').catch(() => null),
        api('/plan').catch(() => null),
        api('/messages?since_seq=0&limit=500').catch(() => ({ messages: [] })),
        api('/events?limit=400').catch(() => ({ events: [] })),
      ]);
      state.availability = availability.availability || [];
      state.pool = pool;
      state.plan = plan;
      state.messages = messages.messages || [];
      state.events = events.events || [];
    } catch (error) {
      state.error = String(error.message || error);
    }
    render();
  }

  const timer = setInterval(() => void refresh(), 15000);
  void refresh();

  return {
    refresh,
    stop: () => clearInterval(timer),
  };
}
