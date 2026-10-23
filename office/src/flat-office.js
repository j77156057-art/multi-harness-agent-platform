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
import { ACTION_HINT, ACTION_LABEL, humanDecisionActions, releaseNeedsOverride } from './human-actions.js';
import { STATE_LABEL, DONE_STATES, eventLabel as eventLabelLocal } from './state-labels.js';
// 人话从**平台**来（平台才是这些文本的出处）：失败原因是平台写的、事件名是平台的
// 事件、窗口登记名在平台的注册表里。平台够不着时用本地兜底。
import { humanize, summarize, translated } from './wording.js';
import { headlineText, parseTranscript } from './cli-transcript.js';
import { escapeHtml } from './html-escape.js';

const escape = escapeHtml;

// 状态词（STATE_LABEL）从 `state-labels.js` 来 —— 与「各窗口说了什么」那个
// 独立页面共用一份，两个页面的说法不会各说各话。

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
  attempt_id: '第几次尝试',
  attempt_no: '第几次尝试',
  attempt_budget_override: '多给了一次机会',
  branch: '分支',
  budget_override_by: '谁多给了一次机会',
  claim_request_id: '接活请求',
  commit: 'commit',
  completion: '成员自述',
  decided_by: '谁决定的',
  decision_id: '裁决',
  failure_reason: '失败原因',
  fencing_epoch: '安全代号',
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
const TIER_LABEL = { free: '免费', reserve: '兜底', metered: '计量', user: '你自己的' };


// 转义统一走 `html-escape.js`（那里是照这份抄的原始实现，别再手抄第二份）。
const timeOf = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false });
};

/**
 * 事件行里的**短时间**：`10/18 20:20`。
 *
 * 事件列表那一列很窄，完整的 `2026/10/18 20:20:08` 放不下（窄屏会横向溢出）。
 * 完整时间留在 title 里。
 */
const timeShort = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const month = String(date.getMonth() + 1);
  const day = String(date.getDate());
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${month}/${day} ${hours}:${minutes}`;
};

/** 只要到分钟：额度曲线上的刻度，精确到秒没有意义。 */
const timeMinute = (value) => {  if (!value) return '—';
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
    userKeys: [],
    userKeyPending: null,
    // 方案原文那句人话（平台翻的）；跟着方案原文走，方案换了就重翻。
    briefSummary: '',
    briefSummaryFor: '',
    // 哪些折叠卡片是开着的。
    //
    // 右栏每 15 秒重画一次（为了看到新数据）。不记住这件事，用户刚点开
    // 「额度池」，下一轮刷新就"啪"地合上了 —— 正在看的东西会从手里跑掉。
    openFolds: new Set(),
    source: 'platform',
    sort: 'updated',
    page: 1,
    pageSize: 20,
    failureOutput: null,
    allEvents: false,
    events: [],
    chatExpanded: false,
  };

  // 用户**正在输入**的东西。
  //
  // 办公室每 15 秒整块重画一次（为了看到新数据）。重画会把输入框一起换掉，
  // 所以人家正在填的 key 会在半路上消失 —— 这不是"小毛病"，是这东西没法用。
  // 重画之前存下来（值、光标、焦点），重画之后放回去。
  //
  // 输入框只要带 `data-draft="名字"`，就会被记住；名字自己起，别重复。
  const drafts = { fields: {}, focus: '', start: 0, end: 0 };

  /** 重画之前：把用户填过的东西存下来（值 + 光标位置）。 */
  function captureDrafts() {
    root.querySelectorAll('[data-draft]').forEach((field) => {
      drafts.fields[field.dataset.draft] = field.value;
    });
    const active = document.activeElement;
    if (active && active.dataset && active.dataset.draft && root.contains(active)) {
      drafts.focus = active.dataset.draft;
      drafts.start = active.selectionStart ?? null;
      drafts.end = active.selectionEnd ?? null;
    }
  }

  /** 重画之后：把值、光标和焦点放回去。 */
  function restoreDrafts() {
    root.querySelectorAll('[data-draft]').forEach((field) => {
      const saved = drafts.fields[field.dataset.draft];
      if (saved !== undefined && field.value !== saved) field.value = saved;
    });
    if (!drafts.focus) return;
    const field = root.querySelector(`[data-draft="${drafts.focus}"]`);
    if (!field || typeof field.focus !== 'function') return;
    field.focus();
    if (drafts.start !== null && typeof field.setSelectionRange === 'function') {
      try {
        field.setSelectionRange(drafts.start, drafts.end);
      } catch {
        // 不是文本框（select 之类）就放着。
      }
    }
  }

  // 各栏的滚动位置。
  //
  // 重画会把这些栏整个换掉，滚动位置也就没了：用户滚到额度池看额度，下一次
  // 刷新就"啪"地回到顶上 —— 跟刚才那个"正在填的表单被抹掉"是同一类毛病。
  const scrollState = new Map();
  const SCROLLING_PANES = ['.flat-rail', '.flat-tasks', '.flat-detail', '.flat-chat-list'];

  function captureScroll() {
    for (const selector of SCROLLING_PANES) {
      const pane = root.querySelector(selector);
      if (pane) scrollState.set(selector, { top: pane.scrollTop, left: pane.scrollLeft });
    }
  }

  function restoreScroll() {
    for (const [selector, position] of scrollState) {
      const pane = root.querySelector(selector);
      if (!pane) continue;
      pane.scrollTop = position.top;
      pane.scrollLeft = position.left;
    }
  }
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

  /** 删掉一个东西（DELETE 也要带同源写的那个头）。 */
  const apiDelete = async (route) => {
    const response = await fetch(`/api/formal${route}`, {
      method: 'DELETE',
      cache: 'no-store',
      headers: { 'X-Office-Formal': '1' },
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
   * 窗口的名字。
   *
   * 平台上登记的名字是给人看的（"Codex 窗口"），id 是给机器看的
   * （"codex-window"）。界面上要**名字**：一串 id 对第一次用的人没有意义。
   * 名册里没有就退回 id —— 总比空着强。
   */
  function harnessName(id) {
    if (!id) return '';
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
          <p class="flat-note">发出去的是项目群里的 <b>@管理员</b>：管理员会收到这条消息，他的回话也在群里。不是只有你们俩能看的私聊。</p>
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

  /**
   * 这条活现在是谁在做。
   *
   * 真数据实测踩到的坑：合入之后平台把 owner 清空了，于是"已完成"的活在列表里
   * 写着"没人做（之前是 OpenCode）"—— 一件做完的事说成没人做，读者会以为它
   * 还在等人。所以先看这条活**处在什么状态**，再说人。
   */
  function whoWord(task, holds) {
    const status = String(task.status || '');
    if (DONE_STATES.has(status)) {
      return holds.before ? `做完了（之前是 ${holds.before} 做的）` : '做完了';
    }
    if (['abandoned', 'stale', 'cancelled'].includes(status)) {
      return holds.before ? `没做成（之前是 ${holds.before} 做的）` : '没做成';
    }
    if (holds.now) return `${holds.now} 在做`;
    if (holds.before) return `现在没人做（之前是 ${holds.before}）`;
    if (Number(task.attempt_no || 0) > 0) return `现在没人做（试过 ${task.attempt_no} 次）`;
    return '还没人接';
  }

  /** 谁在拿着这条活（拿不了就说谁接过）。 */
  function detailHolds(task) {
    const holds = whoHolds(task);
    const attempt = attemptWord(task);
    return `${whoWord(task, holds)} · ${attempt}`;
  }

  /**
   * 尝试次数的说法。
   *
   * "第 1/3 次尝试"是平台的说法；"第 1 次尝试（最多 3 次）"是人的话 ——
   * 后者直接回答了"还能再做几次"。
   */
  function attemptWord(task) {
    const used = Number(task.attempt_no || 0);
    const max = Number(task.max_attempts || 0);
    if (!max) return `已经试了 ${used} 次`;
    return `第 ${used} 次尝试（最多 ${max} 次）`;
  }

  function whoHolds(task) {
    if (task.owner) return { now: harnessName(task.owner), before: null };
    const holders = holdersOf(task.task_id);
    return {
      now: null,
      before: holders.length ? harnessName(holders[holders.length - 1]) : null,
    };
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
          ? `${holds.now}（在做）`
          : holds.before
            ? `现在没人做（之前是 ${holds.before}）`
            : Number(task.attempt_no || 0) > 0
              ? `现在没人做（接过 ${task.attempt_no} 次，接手的人不在近期这段记录里）`
              : '还没人接过';
        const history = row.claims.map((c) => c.actor_id).filter((v, i, a) => a.indexOf(v) === i);
        const who = (id) => (id ? harnessName(id) : '');
        const verdict = row.lastReview
          ? `${REVIEW_VERDICT[row.lastReview.event_name] || row.lastReview.event_name}（${who(row.lastReview.actor_id) || '平台'}）`
          : '还没有审核结论';
        const submitter = row.submits.length
          ? row.submits.map((s) => s.actor_id).filter((v, i, a) => a.indexOf(v) === i).map(who).join('、')
          : null;
        return `<section class="flat-brief-task">
          <header>
            <span class="flat-chip flat-chip-${escape(task.status)}">${escape(STATE_LABEL[task.status] || task.status)}</span>
            <b>${escape(task.title || task.task_id)}</b>
            <span class="flat-mono" title="任务编号 ${escape(task.task_id)}">${escape(translated(task.task_id) || task.task_id)}</span>
          </header>
          <dl>
            <dt>谁接的</dt>
            <dd>${escape(owner)}${history.length ? `· 接过：${escape(history.map(who).join(' → '))}` : ''}· 第 ${escape(task.attempt_no ?? 0)}/${escape(task.max_attempts ?? 0)} 次尝试</dd>
            <dt>做得怎么样</dt>
            <dd>交了 ${row.submits.length} 次${submitter ? `（${escape(submitter)}）` : ''}；${escape(verdict)}${row.median !== null ? `；接手到交付 ${row.median}s` : ''}</dd>
            ${task.last_failure && task.last_failure.reason ? `<dt>没完成的原因</dt><dd>${escape(reasonText(task.last_failure.reason))}</dd>` : ''}
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
      <p class="flat-note">当前持有者与尝试次数是平台自己的字段，一定准；接过的人与交付次数取自**近期这一段事件**，这个群的事件已经超过一页，更早的接手记录不在窗口里。</p>
      <p class="flat-brief-summary" data-brief-summary${state.briefSummary ? '' : ' hidden'}>${escape(state.briefSummary || '')}</p>${cards}${original}`;
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

  /**
   * 处置对话框：一次把该问的问清楚。
   *
   * 为什么要有它：这几条动作的入参不是同一套 —— 回答/重开要的是**答复**
   * （写进决策行，R10 §18），取消/放行要的是**原因**，放行在预算用完时还要
   * 显式带上**预算豁免**（R10 §8）。让人自己填一个空白 textarea，猜不出
   * 哪个字段是哪个，于是每个动作问一次、一次只问它要的东西。
   */
  const decisionDialog = document.createElement('dialog');
  decisionDialog.className = 'flat-dialog is-compact';
  decisionDialog.id = 'flatDecision';
  decisionDialog.setAttribute('aria-label', '处置这条活');
  decisionDialog.innerHTML = `
    <header class="flat-dialog-head">
      <div><h2></h2><p class="flat-note"></p></div>
      <button type="button" class="flat-dialog-close" data-decision-close aria-label="关闭">×</button>
    </header>
    <div class="flat-dialog-list">
      <label class="flat-field"><span data-decision-label></span>
        <textarea data-decision-text rows="3"></textarea>
      </label>
      <label class="flat-check" data-decision-override-wrap hidden>
        <input type="checkbox" data-decision-override /> 顺便再给一次机会
        <span class="flat-note">这条活的尝试次数已经用完了。不勾这个，平台不放行它。</span>
      </label>
      <div class="flat-dialog-actions">
        <button type="button" data-decision-cancel>取消</button>
        <button type="button" data-decision-confirm>确认</button>
      </div>
    </div>`;
  document.body.append(decisionDialog);

  /** 打开处置对话框；确认时 resolve {text, override}，取消时 resolve null。 */
  function askDecision({ title, note, label, placeholder, override }) {
    return new Promise((resolve) => {
      decisionDialog.querySelector('h2').textContent = title;
      decisionDialog.querySelector('.flat-note').textContent = note || '';
      decisionDialog.querySelector('[data-decision-label]').textContent = label;
      const text = decisionDialog.querySelector('[data-decision-text]');
      text.value = '';
      text.placeholder = placeholder || '';
      const wrap = decisionDialog.querySelector('[data-decision-override-wrap]');
      const box = decisionDialog.querySelector('[data-decision-override]');
      wrap.hidden = !override;
      box.checked = false;
      const finish = value => {
        decisionDialog.close();
        decisionDialog.removeEventListener('close', onClose);
        resolve(value);
      };
      const onClose = () => {
        decisionDialog.removeEventListener('close', onClose);
        resolve(null);
      };
      decisionDialog.addEventListener('close', onClose);
      decisionDialog.querySelector('[data-decision-confirm]').onclick = () =>
        finish({ text: text.value.trim(), override: box.checked });
      decisionDialog.querySelector('[data-decision-cancel]').onclick = () => decisionDialog.close();
      decisionDialog.querySelector('[data-decision-close]').onclick = () => decisionDialog.close();
      if (!decisionDialog.open) decisionDialog.showModal();
      text.focus();
    });
  }

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
    // 重画之前先把**用户正在输入的东西**存下来：办公室每 15 秒重画一次，
    // 不存的话，正在填的表单（"加一个 key"）会被抹掉半截。
    captureDrafts();
    captureScroll();
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
    restoreDrafts();
    restoreScroll();
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
    // 顶部第一句要回答两个问题：**这是哪儿**，**我现在该看什么**。
    // 没有它，一进来就是一屏表格 —— 知道在看，但不知道该干什么。
    const waitingOnMe = (state.tasks || []).filter((task) =>
      ['awaiting_human', 'escalated'].includes(String(task.status)),
    ).length;
    const guide = waitingOnMe
      ? `有 <b>${waitingOnMe}</b> 件事在等你处理。左边是所有正在做的事，点一条看详情。`
      : '这是你这个项目的办公室。左边是所有正在做的事，点一条看详情；右边是需要你过目的东西。';
    wrap.innerHTML = `
      <div class="flat-head-main">
        <span class="flat-eyebrow">这个项目的办公室</span>
        <h1>${escape(state.scope?.name || '本项目')}</h1>
        <p class="flat-guide">${guide}</p>
      </div>
      <div class="flat-source">
        <label for="flatSourceSelect">数据源</label>
        <select id="flatSourceSelect">
          <option value="platform">正式平台</option>
          <option value="demo">本地样例</option>
        </select>
      </div>
      <ul class="flat-counts">
        <li class="${counts.waiting ? 'has-value' : ''}"><span>等人接</span><b>${counts.waiting}</b></li>
        <li class="${counts.active ? 'has-value' : ''}"><span>正在做</span><b>${counts.active}</b></li>
        <li class="is-review ${counts.review ? 'has-value' : ''}"><span>等人处理</span><b>${counts.review}</b></li>
        <li class="is-failed ${counts.failed ? 'has-value' : ''}"><span>没做成</span><b>${counts.failed}</b></li>
        <li class="${counts.done ? 'has-value' : ''}"><span>完成了</span><b>${counts.done}</b></li>
      </ul>
      ${
        state.error
          ? `<div class="flat-error">
              <b>刚才那一下没成：</b>${escape(state.error)}
              <button type="button" data-act="dismiss-error">知道了</button>
            </div>`
          : ''
      }`;
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
      ['waiting', '等人接'],
      ['active', '正在做'],
      ['review', '等人处理'],
      ['failed', '没做成'],
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
        : '本项目还没有任务。去顶栏的「开工配置」写一句你要做的事，管理员会把它拆成任务分派下去。'
      list.append(empty);
    }

    for (const task of filtered) {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'flat-task' + (state.selected?.task_id === task.task_id ? ' is-selected' : '');
      const reason = task.last_failure?.reason ? ` · ${reasonText(task.last_failure.reason)}` : '';
      const stamp = task.updated_at ? ` · ${escape(timeAgo(task.updated_at))}更新` : '';
      const holds = whoHolds(task);
      const who = whoWord(task, holds);
      row.innerHTML = `
        <span class="flat-chip flat-chip-${escape(task.status)}">${escape(STATE_LABEL[task.status] || task.status)}</span>
        <span class="flat-task-title">${escape(listTitle(task))}</span>
        <span class="flat-task-meta">${escape(who)} · ${escape(attemptWord(task))}${escape(reason)}</span>
        <span class="flat-task-foot">${stamp}${reason ? `<span class="flat-task-jump" data-jump="log" data-task="${escape(task.task_id)}">看它最后说了什么 ↓</span>` : ''}</span>`;
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
          <p class="flat-summary">${escape(statusSentence(task))}</p>
          <p class="flat-mono">${escape(detailHolds(task))}</p>
        </div>
        <div class="flat-detail-actions">${humanActions(task)}</div>
      </div>
      ${lineageBlock(task)}
      ${spec.goal ? `<section class="flat-spec"><h3>这件事要做什么</h3><p>${escape(spec.goal)}</p></section>` : ''}
      ${acceptance ? `<section class="flat-spec"><h3>做到什么程度算完成</h3><p>${escape(acceptance)}</p></section>` : ''}
      ${paths.length ? `<section class="flat-spec"><h3>只允许改这些地方</h3><ul>${paths.map((p) => `<li><code>${escape(p)}</code></li>`).join('')}</ul></section>` : ''}
      ${files.length ? `<section class="flat-spec"><h3>这次改了什么</h3><ul>${files.map((f) => `<li><code>${escape(typeof f === 'string' ? f : f.path || JSON.stringify(f))}</code></li>`).join('')}</ul></section>` : ''}
      <section class="flat-spec"><h3>这条活的经过</h3>
        <p class="flat-note">${events.length ? `一共 ${events.length} 步，新的在最上面。点开每一步看细节。` : '还没有记录。'}</p>
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
      ${showLiveOutput() ? liveOutputBlock() : ''}
      <details class="flat-tech">
        <summary>技术信息（任务编号等）</summary>
        <p>任务编号 ${escape(task.task_id)}</p>
      </details>
      <p class="flat-detail-foot">以上就是这条活的全部记录。想看整体，去右栏的「要做的事」和「看成员表现」。</p>`;
    return panel;
  }

  /**
   * 路径只留文件名。
   *
   * 界面上印一整条 `D:\study\tools\agent\new agent\.worktrees\mavis\data\...`
   * 是给写代码的人看的，不是给看进度的人看的 —— 而且它把整张卡片撑变形了。
   * 完整路径留在 title 里（鼠标悬上去能看到）。
   */
  function fileNameOf(path) {
    const text = String(path || '').trim();
    if (!text) return '';
    const parts = text.split(/[\\/]/);
    return parts[parts.length - 1] || text;
  }

  /**
   * 相对时间：「5 天前」比「2026/10/19 04:59:31」好读。
   * 超过一周才给绝对时间 —— 那时候"几周前"也没多大意义了。
   */
  function timeAgo(value) {
    const when = new Date(value);
    if (Number.isNaN(when.getTime())) return '';
    const seconds = Math.max(0, (Date.now() - when.getTime()) / 1000);
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    if (seconds < 86400 * 30) return `${Math.floor(seconds / 86400)} 天前`;
    return timeOf(value);
  }

  /**
   * 列表里的标题：**去掉项目名前缀**。
   *
   * 平台给的任务标题是「项目名 · 任务名」，那是给跨项目的地方看的。在这个
   * 页面里每条都带一遍同样的前缀，只会让真正的任务名淹没在重复里 ——
   * 页面顶上已经写了这是哪个项目。
   */
  function listTitle(task) {
    const title = String(task.title || task.task_id);
    const name = String((state.scope && state.scope.name) || '').trim();
    const prefix = name ? `${name} · ` : '';
    const trimmed = prefix && title.startsWith(prefix) ? title.slice(prefix.length) : title;
    return stripHarnessSuffix(trimmed);
  }

  /**
   * 标题末尾的「（qoder）」这种窗口名：详情里已经说清是谁做的，
   * 列表里再挂一遍只是噪音 —— 而且它是机器名，不是标题的一部分。
   */
  function stripHarnessSuffix(title) {
    const roster = (state.status && state.status.harnesses) || [];
    const known = new Set();
    for (const item of roster) {
      if (item && item.harness_id) known.add(String(item.harness_id));
      if (item && item.name) known.add(String(item.name));
    }
    const match = /[（(]([^（()）]{1,40})[）)]\s*$/.exec(title);
    if (!match) return title;
    const inner = match[1].trim();
    if (!known.has(inner)) return title;
    return title.slice(0, match.index).trim();
  }

  /**
   * 失败原因说人话。
   *
   * 平台给的答案优先（`POST /wording/translate`）—— 那是这些文本的出处；
   * 平台够不着时用本地兜底。兜底认不出的**原样留着**（宁可原样，也不要编一个
   * 错的说法）。
   */
  function reasonText(reason) {
    const text = String(reason || '').trim();
    if (!text) return '';
    const fromPlatform = translated(text);
    if (fromPlatform) return fromPlatform;
    const exit = /^harness CLI exited (\d+)$/.exec(text);
    if (exit) return `窗口的命令退出了（退出码 ${exit[1]}）`;
    if (/^harness timeout:/.test(text)) return '窗口跑超时了，没在时间内做完';
    if (/quota|rate.?limit|insufficient/i.test(text)) return '额度用完了';
    if (/^(provider|upstream)/i.test(text)) return '模型那边出了问题';
    return text;
  }

  /** 事件名说人话：平台翻过就用平台的，没翻过用本地那张表。 */
  function eventName(event) {
    const name = String(event?.event_name || '');
    if (!name) return '';
    return translated(name) || eventLabelLocal(name);
  }

  /**
   * 一句话：**这条活现在怎么样了**。
   *
   * 状态词是给筛选和计数用的（"等你处理"），但选中一条活之后，人想知道的是
   * "所以现在该怎么办"。这句话就是那个答案。
   */
  function statusSentence(task) {
    const who = whoHolds(task);
    const failure = reasonText(task.last_failure?.reason);
    switch (String(task.status)) {
      case 'awaiting_human':
        return '这条活在等你回答。回答之后，它会回到干活的人手里。';
      case 'escalated':
        return '这条活卡住了，平台把它交给了你：放回去重做，或者干脆取消。';
      case 'ready':
        return '这条活还没人接。可以让管理员安排个人。';
      case 'queued':
        return '这条活在排队，等资源空出来。';
      case 'claimed':
        return '已经有人接了，正在准备开工。';
      case 'in_progress':
        return `${who.now || '有人'}正在做这条活。`;
      case 'verifying':
        return '做完了，机器正在检查结果。';
      case 'awaiting_review':
        return '做完了，正在等人审。';
      case 'changes_requested':
        return '审的人觉得要返工，正在打回去。';
      case 'approved':
        return '审过了，正在合进主分支。';
      case 'merging':
        return '正在合进主分支。';
      case 'merged':
        return '已经合进主分支，这件事做完了。';
      case 'abandoned':
        return failure ? `这件事被放弃了：${failure}` : '这件事被放弃了。';
      case 'stale':
        return '干活的那个窗口失联了，这条活卡在这里。';
      case 'cancelled':
        return '这件事被取消了，不会再有人做。';
      case 'plan_drift':
        return '需求变了，这条活需要重新确认一下。';
      case 'impl_drift':
        return '做的东西和需求对不上了，需要人看一眼。';
      case 'blocked':
        return '这条活遇到问题过不去了。';
      case 'needs_help':
        return '干活的人正在求助，等人搭把手。';
      case 'reclaiming':
        return '正在重新找人做这条活。';
      default:
        return '这条活还在进行中。';
    }
  }

  /**
   * 一条事件：标题行是骨架，**payload 才是这件事的细节**。
   * "改派给了谁"、"为什么失败"都在 payload 里，光看事件名等于没看见。
   */
  function eventRow(event) {
    const payload = event.payload && typeof event.payload === 'object' ? event.payload : null;
    const hint = eventHint(event);
    const fields = payload ? payloadFields(payload) : [];
    // 事件列表是给人看"这条活经历了什么"的：时间、人话的动作、谁做的。
    // 事件编号（seq）留�� title 里 —— 它是给查问题的人看的，不是给人读的。
    const who = actorName(event);
    return `<li class="flat-event">
      <details>
        <summary title="事件编号 ${escape(event.seq ?? '')} · ${escape(timeOf(event.created_at))}${event.event_name && eventName(event) !== event.event_name ? ` · 原文 ${escape(event.event_name)}` : ''}">
          <span class="flat-event-seq">${escape(timeShort(event.created_at))}</span>
          <b>${escape(eventName(event))}</b>
          <em>${escape(who)}</em>
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

  /** 事件是谁做的：人写"你"，窗口写它的登记名。 */
  function actorName(event) {
    const id = String(event.actor_id || '');
    if (!id) return '';
    if (String(event.actor_type || '') === 'human') return '你';
    if (id === 'office-operator') return '你';
    return harnessName(id);
  }

  /** 事件名旁边那一句话：先说人话，让人知道要不要点开。 */
  function eventHint(event) {
    const payload = event.payload || {};
    if (typeof payload.reason === 'string' && payload.reason.trim()) return clip(payload.reason, 48);
    if (payload.completion && typeof payload.completion.summary === 'string') {
      return clip(payload.completion.summary, 48);
    }
    if (typeof payload.previous_status === 'string') return `上一步是 ${STATE_LABEL[payload.previous_status] || payload.previous_status}`;
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
            // 链上写**这条活叫什么**，不写它的编号 —— 编号是给查问题的人看的。
            const task = (state.tasks || []).find((item) => item.task_id === node.id);
            const label = task ? listTitle(task) : node.id;
            const mark = node.current ? ' is-current' : '';
            const tip = node.current ? '（本条）' : '';
            return `${index ? '<span class="flat-arrow">▶</span>' : ''}<span class="flat-node${mark}" title="${escape(node.id)}">${escape(label)}${tip}</span>`;
          })
          .join('')}</p>
        <p class="flat-note">${escape(chainNote(chain))}</p>`
      : '';

    const attemptHtml = attempts.length
      ? `<h4>这条活的每一次尝试</h4>
        <ol class="flat-attempts">${attempts
          .map((row) => `<li>
            <span class="flat-attempt-no" title="${escape(row.id || '')}">${row.no != null ? `第 ${row.no} 次` : '第 ? 次'}</span>
            <span class="flat-attempt-owner">${escape(row.owner || '不知道是谁领的')}</span>
            <span class="flat-attempt-outcome">${escape(row.outcome || '（没记录）')}</span>
            <span class="flat-attempt-at">${escape(timeOf(row.at))}</span>
            ${row.revisedFrom ? `<span class="flat-attempt-from">从上一轮改过来</span>` : ''}
          </li>`)
          .join('')}</ol>`
      : '';

    // 什么也没有就别画一个空框（"这条活的来路"里只留一个标题，看着像坏了）。
    if (!chainHtml && !attemptHtml) return '';
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
        row.outcome = `没交出来：${reasonText(event.payload?.reason) || '（没写理由）'}`;
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
      source?.attempt_id ? `第 ${source.attempt_id} 次尝试` : '',
      source?.harness_id ? `窗口 ${harnessName(source.harness_id)}` : '',
      source?.attempt_status ? `当时的状态 ${source.attempt_status}` : '',
      source?.updated_at ? `时间 ${timeOf(source.updated_at)}` : '',
    ].filter(Boolean).join(' · ');
    return `<section class="flat-spec flat-fail-log" id="flatFailLog">
      <h3>它最后说了什么</h3>
      ${task.last_failure?.reason ? `<p class="flat-note">平台记下的原因：${escape(task.last_failure.reason)}</p>` : ''}
      ${task.last_failure?.at ? `<p class="flat-note">失败于 ${escape(timeOf(task.last_failure.at))}</p>` : ''}
      ${facts ? `<p class="flat-note">${escape(facts)}</p>` : ''}
      ${stderr ? `<h4>报错内容</h4><pre class="flat-output">${escape(stderr.slice(-4000))}</pre>` : ''}
      ${stdout ? `<h4>输出内容</h4><pre class="flat-output">${escape(stdout.slice(-4000))}</pre>` : ''}
      ${!stderr && !stdout ? '<p class="flat-empty">这一轮它什么也没留下（进程被杀，或者它没说话）。</p>' : ''}
    </section>`;
  }

  /** CLI 原话：平台的 /output 形状是 {output: {stdout, stderr}}，两边都容一下。 */
  function outputText() {
    const raw = state.output && typeof state.output === 'object'
      ? state.output.output || state.output
      : state.output;
    const text = typeof raw === 'string' ? raw : String(raw?.stdout || raw?.text || '');
    return text.slice(-4000) || '（它还没输出过东西）';
  }

  /**
   * 「它自己输出的内容」：机器日志先给人话（结论 + 做了什么），原文收在后面。
   * 和「各个窗口都说了什么」那个页面用的是同一个解析器（`cli-transcript.js`）。
   */
  function liveOutputBlock() {
    const raw = state.output && typeof state.output === 'object'
      ? state.output.output || state.output
      : state.output;
    const stdout = typeof raw === 'string' ? raw : String(raw?.stdout || '');
    const transcript = parseTranscript(stdout);
    const title = `它自己输出的内容${state.output?.output?.current ? '（当前这一轮）' : ''}`;
    if (transcript.isJson && transcript.texts.length) {
      const tools = transcript.tools.slice(-6).reverse();
      return `<section class="flat-spec"><h3>${title}</h3>
        <p class="flat-note">它最后说了</p>
        <pre class="flat-output">${escape(headlineText(transcript) || '（这段里没有可读的文字）')}</pre>
        ${tools.length ? `<p class="flat-note">它做了什么（最近 ${tools.length} 步）</p><ul class="flat-list outputs-tools">${tools.map((tool) => `<li><span>${escape(tool.label)}</span></li>`).join('')}</ul>` : ''}
        <details class="outputs-more-block"><summary>看原始记录</summary><pre class="outputs-raw">${escape(stdout.slice(-4000))}</pre></details>
      </section>`;
    }
    return `<section class="flat-spec"><h3>${title}</h3><pre class="flat-output">${escape(outputText())}</pre></section>`;
  }

  /** 人自己的动作。平台与管理员 harness 都不代劳这些。 */
  function humanActions(task) {
    const spent = Number(task.attempt_no || 0) >= Number(task.max_attempts || 0);
    const buttons = [];
    // 还没人接的：请管理员派出去。派给谁是管理员的决定，
    // 办公室只负责把这个请求送到管理员面前（群里的 @mention）。
    if (!task.owner && task.status === 'ready') {
      buttons.push(
        `<button type="button" data-act="ask-dispatch" title="这是一条 @管理员 的群消息；派给谁由管理员决定">交给管理员安排</button>`,
      );
    }
    if (spent) {
      buttons.push(
        `<button type="button" data-act="override">再给一次机会</button>`,
      );
    }
    buttons.push(`<button type="button" data-act="reassign">请管理员换个人做</button>`);
    // 处置动词：回答提问、放行、重开、取消。
    //
    // 这些是**人自己的动作**（R10 §18），旧的人操作面里有过（已随 Vue 控制台
    // 一起删掉），迁到 Agent Office 时整批丢了：于是"等人回答"的任务在界面上
    // 是个死状态 —— 人只能发条群消息，平台不会把它当成决策。
    // 状态集合见 `human-actions.js`，与后端守卫一一对应；后端仍是唯一裁判。
    //
    // 按钮上写的是**人话**：平台那边叫"放行回队列"，人只知道"放回去重新找人做"。
    for (const action of humanDecisionActions(task)) {
      const label = ACTION_LABEL[action] || action;
      const hint = ACTION_HINT[action] || '';
      buttons.push(
        `<button type="button" data-act="decide-${action}" title="${escape(hint)}">${escape(label)}</button>`,
      );
    }
    return buttons.join('');
  }

  /**
   * 额度来源的名字。
   *
   * 用户自己加的 key 在平台那边叫 `user:k6d9cea` —— 那是机器名，界面上没人
   * 看得懂。这里换成「你的 key「我的测试 key」」：池子里的每一份额度都要说清
   * 是谁的钱。
   */
  function poolSourceName(name) {
    const text = String(name || '');
    if (!text.startsWith('user:')) return text;
    const keyId = text.slice('user:'.length);
    const key = (state.userKeys || []).find((item) => item.id === keyId);
    return key ? `你的 key「${key.label || '未命名'}」` : '你自己的 key';
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
      // 次数一律兜底：平台没给这个数的时候显示 0，而不是 "undefined 次"。
      const usedCount = Number(used?.requests || 0);
      return `<details class="flat-pool-item">
        <summary>
          <b>${escape(poolSourceName(name))}</b>
          <span>${own.length ? `${own.length} 个模型` : '还没探到模型'}</span>
          <em>${used ? `已用 ${usedCount} 次` : '还没人用过'}</em>
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
             <p class="flat-note">${usedCount} 次请求 · 输入 ${Number(used.prompt_tokens || 0)} / 输出 ${Number(used.completion_tokens || 0)} 个字（token）</p>
             ${Object.keys(consumersHere).length
               ? `<p class="flat-note">谁用过：${Object.entries(consumersHere).map(([who, count]) => `${escape(harnessName(who))} × ${count}`).join('，')}</p>`
               : ''}`
          : ''}
        ${upstreamTimeline(name)}
      </details>`;
    });
    const consumerItems = Object.entries(consumers).map(([who, value]) => {
      const usedHere = Object.entries(value.upstreams || {});
      return `<li><b>${escape(harnessName(who))}</b>：${Number(value.requests || 0)} 次 · ${usedHere.map(([name, count]) => `${escape(name)} × ${count}`).join('，') || '—'}</li>`;
    });
    return `<section class="flat-card flat-card-fold">
      <details data-fold="pool"${state.openFolds.has('pool') ? ' open' : ''}>
        <summary>额度池 · ${upstreams.length} 个额度来源，已用 ${Number(usage.requests || 0)} 次</summary>
        <p class="flat-note">谁还有多少额度、谁花了多少 —— 要看才展开。</p>
        ${inventoryBlock()}
        ${timelineBlock()}
        ${items.join('')}
        ${consumerItems.length
          ? `<details class="flat-pool-item"><summary><b>谁用了额度</b><span>${consumerItems.length} 个成员</span></summary><ul class="flat-pool-consumers">${consumerItems.join('')}</ul></details>`
          : '<p class="flat-note">还没有人从池子里借过额度。</p>'}
        ${userKeysBlock()}
      </details>
    </section>`;
  }

  /**
   * 平台**来问**你一件事时，摆在右栏最上面的一张卡。
   *
   * 为什么单独一张卡、放在最上面：平台在等一个决定的时候，那件事是**现在**
   * 唯一重要的事。埋在额度池那张长卡片的底下（要展开、要滚到底），
   * 等于没有这一问 —— 人看不到，平台就一直等着。
   */
  function askCard() {
    const pending = state.userKeyPending || null;
    if (!pending) return '';
    return `<section class="flat-card flat-card-ask">
      <h3>平台在问你一件事</h3>
      <div class="flat-userkey-ask">
        <p><b>免费额度用完了。</b>要不要用它自己加的 key 继续做？</p>
        <p class="flat-note">用的是你的 key「${escape(pending.label || '未命名')}」${
          pending.model ? `（模型 ${escape(pending.model)}）` : ''
        }。你点了头它才会花；不点头它不会自己动。</p>
        <div class="flat-dialog-actions">
          <button type="button" data-act="deny-user-key" data-key="${escape(pending.key_id)}">先不用</button>
          <button type="button" data-act="approve-user-key" data-key="${escape(pending.key_id)}" data-mode="once">同意用一次</button>
          <button type="button" data-act="approve-user-key" data-key="${escape(pending.key_id)}" data-mode="always">以后都用它</button>
        </div>
      </div>
    </section>`;
  }

  /**
   * 用户自己的 API key。
   *
   * 这一块是**人自己的钱**：加进来之后平台替它保管，但平台**不会自己花** ——
   * 免费额度用完时，平台会**来问**你，点了头才用。
   *
   * 「平台来问你了」那张卡片**不在这里**：它挂在右栏最上面（`askCard`）——
   * 埋在这张长卡片的底下，谁都找不到。
   */
  function userKeysBlock() {
    const keys = state.userKeys || [];
    // 协议名是人给 key 起的**机器名**（「openai_chat」），界面上要让人看懂。
    const protocolName = (protocol) =>
      ({
        openai_chat: 'OpenAI 兼容',
        openai_responses: 'OpenAI Responses',
        anthropic: 'Anthropic',
      })[protocol] || '其他';
    const rows = keys.length
      ? keys
          .map(
            (key) => `<li class="flat-userkey">
              <b>${escape(key.label || '未命名')}</b>
              <span>${escape(protocolName(key.protocol))} · ${escape(key.base_url || '')}</span>
              <span class="flat-note">${
                key.approval === 'always'
                  ? '已同意一直使用'
                  : key.approval === 'once'
                    ? '已同意用一次'
                    : '还没同意使用'
              }${key.requests ? ` · 已用 ${key.requests} 次` : ''}</span>
              <button type="button" data-act="drop-user-key" data-key="${escape(key.id)}">删掉</button>
            </li>`,
          )
          .join('')
      : '<p class="flat-empty">还没加过自己的 key。</p>';
    return `<section class="flat-userkeys">
      <h3>自己的 API key</h3>
      <p class="flat-note">平台替你保管，但<b>不会自己花</b> —— 免费额度用完时它会来问你，点了头才用。</p>
      <ul class="flat-list flat-userkeys-list">${rows}</ul>
      <details class="flat-userkeys-add" data-fold="userkey-add"${
        state.openFolds.has('userkey-add') ? ' open' : ''
      }>
        <summary>加一个 key</summary>
        <div class="flat-userkeys-form">
          <label>名字<input data-draft="userkey-label" data-userkey-label placeholder="例如：我的 OpenAI" maxlength="60" /></label>
          <label>协议<select data-draft="userkey-protocol" data-userkey-protocol>
            <option value="openai_chat">OpenAI 兼容（/v1/chat/completions）</option>
            <option value="openai_responses">OpenAI Responses</option>
            <option value="anthropic">Anthropic（/v1/messages）</option>
          </select></label>
          <label>地址<input data-draft="userkey-base" data-userkey-base placeholder="https://api.openai.com/v1" /></label>
          <label>模型（可以留空，让平台去问）<input data-draft="userkey-models" data-userkey-models placeholder="gpt-4.1, o3" /></label>
          <label>key<input data-draft="userkey-key" data-userkey-key type="password" placeholder="sk-..." autocomplete="off" /></label>
          <button type="button" data-act="add-user-key">加进来</button>
          <p class="flat-note" data-userkey-error></p>
        </div>
      </details>
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
      ${askCard()}
      <section class="flat-card flat-card-brief">
        <h3>要做的事</h3>
        ${brief.planId
          ? `<p><b>${escape(brief.title)}</b></p>
             <p class="flat-note">${(state.tasks || []).length} 件事${unclaimed.length ? `（${unclaimed.length} 件还没人接）` : ''}</p>
             <button type="button" data-act="open-brief">看全部要做的事</button>`
          : `<p class="flat-note">${(state.tasks || []).length} 件事</p>
             <p class="flat-empty">还没有方案正文。</p>`}
      </section>
      <section class="flat-card flat-card-members">
        <h3>成员</h3>
        ${helps.length
          ? `<p class="flat-note">${helps.length} 条求助待处理</p>
             <ul class="flat-list">${helps.slice(0, 4).map((item) => `<li><b>${escape(item.owner || '未知')}</b><span>${escape(clip(item.reason || '（没写原因）', 40))}</span><span>${escape(clip(item.title, 28))}</span></li>`).join('')}</ul>`
          : '<p class="flat-empty">没有待处理的求助。</p>'}
        <button type="button" data-act="open-members">看成员表现</button>
      </section>
      <section class="flat-card flat-card-admin">
        <h3>与管理员对话</h3>
        ${admin
          ? `<p><b>${escape(admin)}</b></p>
             <p class="flat-note">${!started
               ? '你还没找过管理员。发一条，他会看到。'
               : thread.length
                 ? `这轮对话 ${thread.length} 条。`
                 : '还没说过话。发一条，管理员就看到了。'}</p>
             <button type="button" data-act="open-admin-chat">打开对话</button>
             <button type="button" data-act="open-direct-window">开私聊窗口</button>`
          : '<p class="flat-empty">平台还没配置管理员窗口。</p>'}
      </section>
      ${poolCard()}
      <section class="flat-card flat-card-fold">
        <details data-fold="workers"${state.openFolds.has('workers') ? ' open' : ''}>
          <summary>现在能干活的人 · ${usable.length} 个</summary>
          <ul class="flat-list">${usable
            .map((item) => `<li><b>${escape(harnessName(item.harness_id))}</b><span>${escape(item.model || '模型未知')}</span></li>`)
            .join('') || '<li class="flat-empty">现在没有能干活的人。</li>'}</ul>
        </details>
        <button type="button" data-act="open-outputs" title="另开一个窗口，看每个窗口做完活之后都说了什么">看他们说了什么</button>
      </section>
      <section class="flat-card">
        <h3>等我批准的方案</h3>
        ${plan
          ? `<p>方案 ${escape(plan.plan_id)} · ${escape((plan.document || {}).tasks || 0)} 件事</p>
             <p class="flat-note" title="${escape((plan.document || {}).markdown_path || '')}">${escape(
               translated((plan.document || {}).markdown_path) ||
                 fileNameOf((plan.document || {}).markdown_path),
             )}</p>
             <button type="button" data-act="approve-plan">批准，开始做</button>
             <button type="button" data-act="reject-plan" title="驳回这份方案：退回给管理员，改一版再来找你">驳回方案</button>`
          : '<p class="flat-empty">没有等你批准的方案。</p>'}
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
    for (const message of state.messages.slice(-40)) {
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
    input.dataset.draft = 'chat';
    const send = document.createElement('button');
    send.type = 'submit';
    send.textContent = '发送';
    form.append(input, send);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const body = String(input.value || '').trim();
      if (!body) return;
      input.value = '';
      delete drafts.fields.chat;
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

  // ---------------------------------------------------- 自己的 API key

  /** 把用户自己加的 key 读回来（只读列表与遮罩，key 本身不在这条路上）。 */
  async function loadUserKeys() {
    try {
      const result = await api('/user-keys');
      state.userKeys = result.keys || [];
      state.userKeyPending = result.pending || null;
    } catch (error) {
      // 没连平台的时候这一段没有意义，不该在顶上报错。
      state.userKeys = [];
      state.userKeyPending = null;
    }
  }

  async function addUserKey() {
    const form = root.querySelector('.flat-userkeys-form');
    if (!form) return;
    const error = form.querySelector('[data-userkey-error]');
    const value = (selector) => String(form.querySelector(selector)?.value || '').trim();
    const label = value('[data-userkey-label]');
    const key = value('[data-userkey-key]');
    if (!label || !key) {
      if (error) error.textContent = '名字和 key 都要填。';
      return;
    }
    const models = value('[data-userkey-models]')
      .split(/[,，\s]+/)
      .filter(Boolean);
    try {
      await api('/user-keys', {
        label,
        protocol: value('[data-userkey-protocol]') || 'openai_chat',
        base_url: value('[data-userkey-base]'),
        key,
        models,
      });
      if (error) error.textContent = '';
      form.querySelector('[data-userkey-key]').value = '';
      form.querySelector('[data-userkey-models]').value = '';
      // 加过了：草稿清掉（否则重画会把刚加过的 key 又填回表单里）。
      delete drafts.fields['userkey-key'];
      delete drafts.fields['userkey-models'];
      await refresh();
    } catch (problem) {
      if (error) error.textContent = String(problem.message || problem);
    }
  }

  async function dropUserKey(keyId) {
    if (!keyId) return;
    try {
      await apiDelete(`/user-keys/${encodeURIComponent(keyId)}`);
      await refresh();
    } catch (error) {
      state.error = String(error.message || error);
      render();
    }
  }

  async function answerUserKey(keyId, mode) {
    if (!keyId) return;
    try {
      await api(`/user-keys/${encodeURIComponent(keyId)}/${mode === 'none' ? 'deny' : 'approve'}`, {
        mode: mode === 'none' ? 'none' : mode,
      });
      await refresh();
    } catch (error) {
      state.error = String(error.message || error);
      render();
    }
  }

  // 记住哪些折叠卡片是开着的（右栏每 15 秒重画一次，不记住就会"啪"地合上）。
  root.addEventListener('toggle', (event) => {
    const fold = event.target.closest?.('details[data-fold]');
    if (!fold) return;
    if (fold.open) state.openFolds.add(fold.dataset.fold);
    else state.openFolds.delete(fold.dataset.fold);
  }, true);

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

  root.addEventListener('click', (event) => {    // 任务行里的「看本次失败的真实日志」：选那条活，再把日志滚到眼前。
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
    // 一次新的动作开始：把上一条"没成"的提示收起来（这次要是再失败，会写新的）。
    if (state.error) {
      state.error = null;
      render();
    }
    if (button.dataset.act === 'dismiss-error') {
      render();
      return;
    }
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
      // 方案原文是"源码一样"的段落：让**平台**翻成一句人话（翻不出来就还是原文）。
      // 摘要存在 state 里 —— 只写进 DOM 的话，下一次刷新（15 秒）就把它抹掉了。
      if (brief.markdown) {
        if (state.briefSummaryFor !== brief.markdown) {
          state.briefSummaryFor = brief.markdown;
          state.briefSummary = '';
          void summarize(brief.markdown).then((summary) => {
            if (summary && state.briefSummaryFor === brief.markdown) {
              state.briefSummary = summary;
              render();
            }
          });
        }
      }
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
    if (button.dataset.act === 'add-user-key') {
      void addUserKey();
      return;
    }
    if (button.dataset.act === 'drop-user-key') {
      void dropUserKey(button.dataset.key);
      return;
    }
    if (button.dataset.act === 'approve-user-key') {
      void answerUserKey(button.dataset.key, button.dataset.mode || 'once');
      return;
    }
    if (button.dataset.act === 'deny-user-key') {
      void answerUserKey(button.dataset.key, 'none');
      return;
    }
    if (button.dataset.act === 'open-outputs') {
      // 独立窗口（另开一个标签页）：每个窗口做完活之后都说了什么。
      window.open('/outputs.html', 'agent-office-outputs', 'width=980,height=860');
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
    if (button.dataset.act === 'reject-plan') {
      const draft = state.plan && state.plan.draft;
      if (!draft) return;
      void (async () => {
        const answer = await askDecision({
          title: '驳回这份方案',
          note: `方案 ${draft.plan_id}：驳回之后它会退回给管理员，改一版再来找你。`,
          label: '为什么驳回（平台会记下来）',
          placeholder: '例如：范围太大，先做核心链路',
        });
        if (!answer) return;
        if (!answer.text) {
          state.error = '驳回方案要写为什么 —— 平台会记下这句话，下次改的时候用得上。';
          render();
          return;
        }
        await doAction('/plan/reject', {
          route: '/plan/reject',
          body: { plan_id: draft.plan_id, reason: answer.text },
        });
      })();
      return;
    }
    const decision = button.dataset.act && button.dataset.act.startsWith('decide-')
      ? button.dataset.act.slice('decide-'.length)
      : '';
    if (decision && task) {
      void (async () => {
        const spec = {
          resume: {
            title: '我来回答',
            note: '你的回答会记在平台上，干活的人能看到；这条活会回到他手里继续做。',
            label: '你的回答（可以留空，但写清楚点更好）',
            route: '/human/resume',
          },
          reopen: {
            title: '重新打开这条活',
            note: '清掉现在的负责人，这条活回到「等人接」，谁都能再接。',
            label: '为什么重新打开（可以留空）',
            route: '/human/reopen',
          },
          release: {
            title: '放回去重新找人做',
            note: '这条活会回到「等人接」的列表，下一轮再找人。',
            label: '为什么放回去（可以留空）',
            route: '/human/release',
          },
          cancel: {
            title: '不做了',
            note: '取消就是结束：这条活不会再有人做。',
            label: '为什么不做了（必填）',
            route: '/human/cancel',
          },
        }[decision];
        if (!spec) return;
        const needsOverride = decision === 'release' && releaseNeedsOverride(task);
        const answer = await askDecision({
          title: spec.title,
          note: spec.note,
          label: spec.label,
          placeholder: decision === 'cancel' ? '例如：这条活的方向不对，先停' : '',
          override: needsOverride,
        });
        if (!answer) return;
        if (decision === 'cancel' && !answer.text) {
          state.error = '不做了的话要写为什么 —— 平台会把这句话记下来。';
          render();
          return;
        }
        if (needsOverride && !answer.override) {
          // 平台会回 409（R10 §8），但让人自己读那个码不如直接说清楚。
          state.error = '这条活的尝试次数已经用完了。放回去之前要勾上「顺便再给一次机会」。';
          render();
          return;
        }
        const body = { task_id: task.task_id };
        if (decision === 'resume' || decision === 'reopen') {
          if (answer.text) body.answer = answer.text;
        } else if (decision === 'release') {
          body.reason = answer.text;
          if (needsOverride && answer.override) body.budget_override = true;
        } else {
          body.reason = answer.text;
        }
        await doAction(spec.route, { route: spec.route, body });
      })();
    }
  });

  // ---------------------------------------------------------------- 数据

  /**
   * 把这一屏要显示的机器文本一次问平台要回来。
   *
   * 只问**没问过**的（`wording.js` 里有缓存），所以每 15 秒的刷新不会变成
   * 每 15 秒一次请求。
   */
  async function loadWording() {
    const texts = [];
    for (const task of state.tasks || []) {
      if (task.last_failure?.reason) texts.push(task.last_failure.reason);
      if (task.task_id) texts.push(task.task_id);
    }
    for (const event of state.events || []) {
      if (event?.event_name) texts.push(event.event_name);
    }
    const plan = state.plan && state.plan.draft ? state.plan.draft : null;
    const path = plan && plan.document ? plan.document.markdown_path : '';
    if (path) texts.push(path);
    if (!texts.length) return;
    await humanize(texts);
  }

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
      // 这里**不**清 state.error：一次失败的提示要留到人下一次动手为止。
      // 每 15 秒把它抹掉，等于"点了没反应" —— 提示闪过就没了，人只记得没反应。
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
        loadUserKeys(),
      ]);
      state.availability = availability.availability || [];
      state.pool = pool;
      state.plan = plan;
      state.messages = messages.messages || [];
      state.events = events.events || [];
      // 人话从平台来（问过的会缓存，不会每 15 秒问一次）。
      await loadWording();
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
