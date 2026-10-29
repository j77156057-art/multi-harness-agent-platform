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
import { baseHarnessOf, isAdministratorHarness, isPlatformAssistantHarness } from './harness-roles.js';
import { createOfficeReadRuntime } from './office-read-runtime.js';
import { createOfficeReadFixture } from './office-read-fixture.js';
import { ACTION_HINT, ACTION_LABEL, CANCEL_STATES, humanDecisionActions, releaseNeedsOverride } from './human-actions.js';
import { STATE_LABEL, DONE_STATES, eventLabel as eventLabelLocal } from './state-labels.js';
// 人话从**平台**来（平台才是这些文本的出处）：失败原因是平台写的、事件名是平台的
// 事件、窗口登记名在平台的注册表里。平台够不着时用本地兜底。
import { humanize, summarize, translated } from './wording.js';
import { headlineText, parseTranscript, readableText, shortenPath } from './cli-transcript.js';
import { escapeHtml } from './html-escape.js';
import {
  groupByStatus,
  hostLine,
  leftoverHint,
  rowsView,
  statusOf,
  summaryLine,
} from './harness-install.js';

const escape = escapeHtml;

// 状态词（STATE_LABEL）从 `state-labels.js` 来 —— 与「各窗口说了什么」那个
// 独立页面共用一份，两个页面的说法不会各说各话。

const FAILED_STATES = new Set(['abandoned', 'stale', 'cancelled']);
const WAITING_STATES = new Set(['draft', 'ready', 'queued']);

// 平台助手（后台那个**有模型、有只读工具**的助手主体）。
//
// 它不是一个要被勾选或注册的窗口：它在工作区域里**常驻**，随手能问 ——
// 新建项目时不用另外选，平台注册表里有没有某个窗口也跟它无关。
//
// 以前左栏那一行「平台助手」指向的是 `office-admin`（管理员窗口，做巡检与上报），
// 平台没注册它时整行就消失 —— 名字与职责都串了：巡检面板是"巡检与上报"，
// 能读日志、调记录、回答问题的是这个助手主体。
const ASSISTANT_ID = 'office-assistant';

/** 状态的"紧急度"：越靠前越该先被人看见。 */
const STATUS_RANK = {
  abandoned: 0, stale: 0, blocked: 0, needs_help: 0, escalated: 0, awaiting_human: 0,
  changes_requested: 1, plan_drift: 1, impl_drift: 1, awaiting_review: 1,
  claimed: 2, in_progress: 2, verifying: 2, reclaiming: 2,
  ready: 3, queued: 3, draft: 3,
  merging: 4, approved: 4, merged: 4, cancelled: 4,
};

// 事件 payload 里**只给机器看**的字段：哈希、commit、内部编号。
// 真数据里 `approve` 事件的 payload 就是它们（findings_hash / reviewed_commit /
// review_id）—— 摆出来像玄学，人读不出发生了什么。机器字段藏进 title 里
// （鼠标悬停可见），给人看的字段才摆出来。
const MACHINE_ONLY_FIELDS = new Set([
  'findings_hash',
  'reviewed_commit',
  'revert_commit',
  'reverted_commit',
  'commit',
  'branch',
  'receipt_id',
  'decision_id',
  'review_id',
  'claim_request_id',
  'registration_version',
  'fencing_epoch',
  'workspace_id',
  'boot_id',
  'fingerprint',
  'attempt_id',
  'event_id',
  'plan_id',
  'snapshot_id',
]);

/** payload 里的字段名（认识的给中文，不认识的照实写英文键）。 */
const FIELD_LABEL = {
  attempt_id: '第几次尝试',
  attempt_no: '第几次尝试',
  attempt_budget_override: '多给了一次机会',
  branch: '分支',
  budget_override_by: '谁多给了一次机会',
  claim_request_id: '领取请求',
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

export function mountFlatOffice({ root, project: projectOf, onStart, onStatus, onAddMember }) {
  const state = {
    tasks: [],
    selected: null,
    detail: null,
    output: null,
    availability: [],
    // 当前看的是哪个会话：tasks=项目任务、group=项目群聊、assistant=平台助手、
    // window=某个窗口（id 是它的 harness_id）。
    view: { kind: 'tasks', id: null },
    // 第二批数据（窗口名单、群消息、额度池）到了没有：没到的时候左栏要说
    // 「正在读取…」，不能让它看起来像"这个项目里没有窗口"。
    loaded: false,
    // 当前筛选下的活（任务表算好的）：详情里的紧凑列表要用同一份，
    // 免得详情里摆着一条被筛选掉的活。
    filtered: [],
    // 读过的任务书（spec）：窗口页的活清单点开才去读，读过一次记一分钟。
    windowSpecs: {},
    // **还没保存的模型挑选**（按窗口）：办公室每 15 秒重画一次，只存在 DOM 里的
    // 选择会被重画抹掉 —— 人挑好模型，过一会儿就没了。
    modelPicks: {},
    pool: null,
    // 这台机器上装了哪些 Harness CLI、官方从哪装（平台只读探测的结果）。
    // 初次进页面不主动探（六个版本命令要几秒），人点「探测」才去问。
    installs: null,
    installsChecking: false,
    installsError: null,
    // 池子状态是什么时候读到的：定时刷新早发起、晚落地，**旧**的不能盖掉新的
    // （否则刚定档的清单会被下一轮刷新抹掉）。
    poolAt: 0,
    plan: null,
    status: null,
    scope: null,
    filter: 'all',
    // 列表的看法：'person' = 按窗口归堆（谁忙谁闲），'time' = 平铺按时间排。
    groupBy: 'person',
    error: null,
    // 上一件「成功了但还没说完」的事（比如刚给某个窗口开好新成员）。
    // 与 error 分开：成功和失败摆同一个框里，人会以为是失败。
    memberNote: '',
    // 哪一行的「改档」按钮是展开的（按模型 id）；重画之后要还在。
    tierEdit: null,
    // 定档/改档/撤档之后的那句话说在这里（与 error 分开）。
    modelNote: '',
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
    // 助手面板要不要滚到最新（刚进面板、刚发完一句时）。
    assistantScrollBottom: false,
    // 提问送不出去时的实话（空 = 没问题）。
    assistantError: '',
    // 助手这一轮做了什么（引擎 trace 的摘要，只读）。
    assistantRuns: [],
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
  const SCROLLING_PANES = ['.flat-convs', '.flat-rail', '.flat-tasks', '.flat-detail', '.flat-chat-list'];

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
    if (!response.ok) {
      const detail = data && typeof data.error === 'object' ? data.error : null;
      const text = detail
        ? [detail.code, detail.message].filter(Boolean).join('：')
        : String(data.error || `请求失败（${response.status}）`);
      throw new Error(text);
    }
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
  // 「这条要改目标」的签名（R10 §19）：成员窗口提，管理员听见并带给群主。
  const GOAL_CHANGE_REF = 'goal-change';
// 还在跑的状态：这些不是「上一轮」，是「现在正在做」。
const RUNNING_STATUSES = new Set(['claimed', 'in_progress', 'verifying']);

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
        <input type="text" id="flatAdminChatInput" placeholder="向管理员发送消息…" aria-label="给管理员的消息" />
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
      list.innerHTML = '<p class="flat-empty">平台尚未配置管理员窗口。</p>';
      return;
    }
    const { items: thread, started } = adminThread();
    if (!thread.length) {
      list.innerHTML = '<p class="flat-empty">尚未与管理员沟通。发送的消息将出现在项目群聊中，并通知管理员。</p>';
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
   * 还在等人。所以先看这条任务**处在什么状态**，再说人。
   */
  /**
   * 标题拆成**主句**与**副句**。
   *
   * 管理员写的标题常是"修开工链路：lucide 解析、开工自测、目标截断、向导文案" ——
   * 冒号前是"这件事是什么"，冒号后是"怎么做"（一串技术枚举）。一屏里三条
   * 这么长的标题叠着，看的人只会觉得是日志。主句当标题，副句降成一行小字。
   */
  function titleParts(task) {
    const title = listTitle(task);
    const cut = title.indexOf('：');
    if (cut <= 0 || cut > 24) return { main: title, sub: '' };
    const main = title.slice(0, cut).trim();
    const sub = title.slice(cut + 1).trim();
    if (!main || !sub || sub.length > 80) return { main: title, sub: '' };
    return { main, sub };
  }

  function whoWord(task, holds) {
    const status = String(task.status || '');
    if (DONE_STATES.has(status)) {
      return holds.before ? `已完成（此前由 ${holds.before} 执行）` : '已完成';
    }
    if (['abandoned', 'stale', 'cancelled'].includes(status)) {
      return holds.before ? `未完成（此前由 ${holds.before} 执行）` : '未完成';
    }
    if (holds.now) return `${holds.now} 执行中`;
    if (holds.before) return `当前无人执行（此前由 ${holds.before} 执行）`;
    if (Number(task.attempt_no || 0) > 0) return `当前无人执行（已尝试 ${task.attempt_no} 次）`;
    return '尚无人领取';
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
    // 「第 0 次尝试」是句废话 —— 一次都没试过就别数了。
    if (!used) return '尚未开始尝试';
    if (!max) return `已尝试 ${used} 次`;
    return `第 ${used} 次尝试（最多 ${max} 次）`;
  }

  /**
   * 谁手上：现在谁在做（**id**）、之前是谁做的（**id**）。
   *
   * 分组要按 id 归堆（两个人可能同名，名字不能当钥匙），所以这里给的是 id；
   * 要人看的名字由 `whoHolds` 那一层换。
   */
  function holderIds(task) {
    if (task.owner) return { now: task.owner, before: null };
    const holders = holdersOf(task.task_id);
    return { now: null, before: holders.length ? holders[holders.length - 1] : null };
  }

  function whoHolds(task) {
    const ids = holderIds(task);
    return {
      now: ids.now ? harnessName(ids.now) : null,
      before: ids.before ? harnessName(ids.before) : null,
    };
  }

  /**
   * 按「谁」把活归堆。
   *
   * 现在谁在做就归谁；已完成、未完成的归**最后做的那个人** —— 一条活做完没人
   * 接手，它还是那个人的活。没人碰过的归「尚无人领取」。
   *
   * 每堆带三个数：手上（在做的）、已完成、未完成。**忙闲是从"手上"推出来的**，
   * 不是猜的：手上有人在做的活就是忙碌，没有就是空闲。
   */
  function groupByPerson(tasks) {
    const groups = new Map();
    for (const task of tasks || []) {
      const ids = holderIds(task);
      const key = ids.now || ids.before || '';
      let group = groups.get(key);
      if (!group) {
        group = {
          key,
          name: key ? harnessName(key) : '尚无人领取',
          tasks: [],
          active: 0,
          done: 0,
          failed: 0,
          waiting: 0,
        };
        groups.set(key, group);
      }
      group.tasks.push(task);
      const status = String(task.status || '');
      if (['claimed', 'in_progress', 'verifying'].includes(status)) group.active += 1;
      else if (DONE_STATES.has(status)) group.done += 1;
      else if (FAILED_STATES.has(status)) group.failed += 1;
      else group.waiting += 1;
    }
    const list = [...groups.values()];
    // 顺序是一句话：**谁在干活 → 活还缺人 → 谁有空**。
    const rank = (group) => (group.active > 0 ? 0 : group.key ? 2 : 1);
    list.sort((a, b) => {
      if (rank(a) !== rank(b)) return rank(a) - rank(b);
      if (a.active !== b.active) return b.active - a.active;
      return b.tasks.length - a.tasks.length;
    });
    return list;
  }

  /** 这一堆是忙是闲（依据：手上有几条在做的活）。 */
  function groupMood(group) {
    if (group.active > 0) return { text: '忙碌', kind: 'is-busy' };
    if (!group.key) return { text: '待领取', kind: 'is-waiting' };
    return { text: '空闲', kind: 'is-idle' };
  }

  /** 这一堆的数：手上几条、已完成几条、未完成几条。 */
  function groupFacts(group) {
    const bits = [];
    if (group.active) bits.push(`进行中 ${group.active} 条`);
    if (group.waiting) bits.push(`待领取 ${group.waiting} 条`);
    if (group.done) bits.push(`已完成 ${group.done} 条`);
    if (group.failed) bits.push(`未完成 ${group.failed} 条`);
    return bits.join(' · ');
  }

  /** 审核结论的说人话（事件名 -> 人话）。 */
  const REVIEW_VERDICT = {
    approve: '通过',
    request_changes: '退回返修',
    reopen_for_changes: '退回重做',
    verify_passed: '自动校验通过',
    merge_success: '已合入',
    merge_conflict: '合并冲突',
    merge_conflict_needs_owner: '合并冲突（需人工决定）',
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

  /** 任务书的内容：先是任务清单（谁在做、执行情况），后面是方案原文。 */
  function briefBody(brief) {
    const rows = briefTasks();
    if (!rows.length) {
      return '<p class="flat-empty">暂无任务。管理员细化后，这里将显示任务详情。</p>';
    }
    const done = rows.filter((r) => ['merged', 'approved', 'done'].includes(r.task.status)).length;
    const running = rows.filter((r) => ['claimed', 'in_progress', 'verifying', 'awaiting_review'].includes(r.task.status)).length;
    const cards = rows
      .map((row) => {
        const task = row.task;
        const holds = whoHolds(task);
        const owner = holds.now
          ? `${holds.now}（执行中）`
          : holds.before
            ? `当前无人执行（此前由 ${holds.before} 执行）`
            : Number(task.attempt_no || 0) > 0
              ? `当前无人执行（已交接 ${task.attempt_no} 次，接收方不在近期记录中）`
              : '尚无人领取';
        const history = row.claims.map((c) => c.actor_id).filter((v, i, a) => a.indexOf(v) === i);
        const who = (id) => (id ? harnessName(id) : '');
        const verdict = row.lastReview
          ? `${REVIEW_VERDICT[row.lastReview.event_name] || row.lastReview.event_name}（${who(row.lastReview.actor_id) || '平台'}）`
          : '尚无审核结论';
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
            <dt>执行方</dt>
            <dd>${escape(owner)}${history.length ? `· 接手：${escape(history.map(who).join(' → '))}` : ''}· 第 ${escape(task.attempt_no ?? 0)}/${escape(task.max_attempts ?? 0)} 次尝试</dd>
            <dt>执行情况</dt>
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
      ? `<p class="flat-brief-action">另有 <b>${unclaimed.length}</b> 条任务尚未领取。
         <button type="button" data-act="ask-admin-dispatch">让管理员派出去</button>
         <span class="flat-note">这是一条 @管理员 的群消息；派给谁由管理员自己决定，办公室不代点。</span></p>`
      : '<p class="flat-brief-action flat-note">暂无待领取的任务：或已全部领取，或已收尾。</p>';
    return `${dispatchNote}<p class="flat-note">共 ${rows.length} 条任务：进行中 ${running} · 已完成 ${done}。每条均标明当前执行者、接手次数、提交次数与审核结论。</p>
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
      state.error = '平台尚未配置管理员窗口，请求无法送达。';
      render();
      return;
    }
    const ids = (taskIds || []).filter(Boolean);
    const lines = ids.map((id) => {
      const task = (state.tasks || []).find((item) => item.task_id === id);
      return `- ${id}${task && task.title ? `（${task.title}）` : ''}`;
    });
    const body = ids.length
      ? `以下任务尚无人领取，请分派：\n${lines.join('\n')}`
      : '本项目暂无待领取的任务。';
    try {
      await api('/messages', { body, mentions: [adminId], refs: ['to-admin', 'dispatch-request'] });
      await refresh();
    } catch (error) {
      state.error = String(error.message || error);
      render();
    }
  }

  /** 这个项目里尚无人领取的活。 */
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
  decisionDialog.setAttribute('aria-label', '任务处置');
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
        <input type="checkbox" data-decision-override /> 顺便追加一次尝试
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

    state.filtered = filtered;
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
    );
    // 底下**不再**挂群聊条：群聊已经是左栏的一个会话，中间那栏铺开它。
    // 同一份消息摆两处只是重复，左栏那一行还带着「最后一条」的摘要。
    restoreDrafts();
    restoreScroll();
    // 助手面板：进了面板或刚发完一句时，滚到最新那条（对话是往下长的）。
    if (state.assistantScrollBottom) {
      state.assistantScrollBottom = false;
      const pane = root.querySelector('.flat-detail');
      if (pane) pane.scrollTop = pane.scrollHeight;
    }
    const sourceSelect = root.querySelector('#flatSourceSelect');
    if (sourceSelect) sourceSelect.value = state.source;
    root.dataset.flatState = 'ready';
    if (adminDialog.open) syncAdminDialog();
    syncBriefDialog();
    // 需要人处理的事**不再弹**：左栏那一行带红点，点进去是一页。浮层以前压在
    // 内容上（正好盖住模型清单），而且"关掉"不等于有结论。
  }

  /**
   * 项目名（给人看的那一份）。
   *
   * 平台上的项目名有时带内部批注（"重开-112（平台项目 112）"）—— 那是写给
   * 运维看的括号说明，界面上留着就像日志。去掉括号里的话，名字本身留下。
   */
  function projectName() {
    const name = String((state.scope && state.scope.name) || '').trim() || '本项目';
    return name.replace(/[（(][^（()）]{0,30}[)）]\s*$/, '').trim() || name;
  }

  /**
   * 「今天」这一段人话。
   *
   * 顶上原来给的是**计数徽章** —— 那是**总共**几条，不是今天，而且得自己加。
   * 这里补一段话：这个项目**今天**发生了什么。
   *
   * 每句都是平台上的事实算出来的：
   *   · 今天新来的 —— 任务 created_at 落在今天；
   *   · 今天做完的 —— 状态是已完成、updated_at 落在今天；
   *   · 今天未完成的 —— 状态是未完成、updated_at 落在今天；
   *   · 现在在干的 —— 状态是 claimed/in_progress/verifying；
   *   · 额度 —— 平台给的 24 小时用量时间线。额度是**全平台**的，不是这个项目的；
   *     时间线盖不满今天的时候就说"最近一天"，不假装它是"今天"。
   *
   * "今天"按**本机时区**算：办公室跑在人的浏览器里，他说的"今天"就是他那边
   * 的今天（平台的时间戳是 UTC，直接比会差八小时）。
   */
  function todayStory() {
    const now = new Date();
    const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const isToday = (stamp) => {
      const at = Date.parse(stamp || '');
      return Number.isFinite(at) && at >= dayStart;
    };
    const tasks = state.tasks || [];
    const fresh = tasks.filter((task) => isToday(task.created_at));
    const done = tasks.filter((task) => DONE_STATES.has(String(task.status)) && isToday(task.updated_at));
    const failed = tasks.filter((task) => FAILED_STATES.has(String(task.status)) && isToday(task.updated_at));
    const working = tasks.filter((task) => ['claimed', 'in_progress', 'verifying'].includes(String(task.status)));
    const windows = new Set(working.map((task) => task.owner).filter(Boolean));

    const bits = [];
    if (fresh.length) bits.push(`新接手 ${fresh.length} 条任务`);
    if (done.length) bits.push(`完成 ${done.length} 条`);
    if (failed.length) bits.push(`未完成 ${failed.length} 条`);

    const sentences = [];
    if (bits.length) {
      sentences.push(`今天${bits.join('、')}。`);
    } else {
      // 今天没动静 —— 也要说清楚"多久没动静了"，否则人不知道是刚清空还是躺了三天。
      const latest = tasks.reduce((best, task) => {
        const at = Date.parse(task.updated_at || '') || 0;
        return at > best.at ? { at, task } : best;
      }, { at: 0, task: null });
      sentences.push(latest.task
        ? `本项目今天暂无进展 —— 最新一条更新于 ${timeAgo(latest.task.updated_at)}。`
        : '本项目今天暂无进展。');
    }
    if (working.length) {
      sentences.push(`${windows.size || working.length} 个窗口正在执行任务。`);
    }
    // 额度是全平台的用量，单独一句，别混进"这个项目"的账里。
    const timeline = state.pool && state.pool.timeline;
    if (timeline && Array.isArray(timeline.buckets) && timeline.buckets.length) {
      const from = Date.parse(timeline.buckets[0].at);
      const covered = Number.isFinite(from) && from <= dayStart;
      const used = timeline.buckets
        .filter((bucket) => Date.parse(bucket.at) >= dayStart)
        .reduce((sum, bucket) => sum + Number(bucket.requests || 0), 0);
      sentences.push(covered
        ? `额度池今天用了 ${used} 次。`
        : `额度池最近一天用了 ${used} 次。`);
    }
    return sentences.join('');
  }

  function header(counts) {
    const wrap = document.createElement('header');
    wrap.className = 'flat-head';
    const status = state.status || {};
    // 顶部第一句要回答两个问题：**这是哪儿**，**我现在该看什么**。
    // 没有它，一进来就是一屏表格 —— 知道在看，但不知道该干什么。
    const waiting = (state.tasks || []).filter((task) =>
      ['awaiting_human', 'escalated'].includes(String(task.status)),
    );
    const waitingOnMe = waiting.length;
    const working = (state.tasks || []).filter((task) =>
      ['claimed', 'in_progress', 'verifying'].includes(String(task.status)),
    ).length;
    // 主角是"现在该干什么"，不是"一共有多少条"。所以先说人话，再给数字。
    // 一个任务都没有的项目：顶部这句话就是**唯一的路标** —— 说清现在是什么，
    // 再说清第一步点哪里。空项目下还说「左边是所有正在做的事」是骗人的：那里一条都没有。
    const emptyProject = !(state.tasks || []).length;
    const guide = waitingOnMe
      ? `有 <b>${waitingOnMe}</b> 条任务需要你处理${working ? `，另有 ${working} 条任务正在执行` : ''}。可在任务表中查看。`
      : working
        ? `有 <b>${working}</b> 条任务正在执行${counts.done ? `，其中 ${counts.done} 条已完成` : ''}。暂无需要你处理的任务。`
        : emptyProject
          ? '当前项目尚无任务。可在顶部「开工配置」中描述目标，管理员将据此细化并分派任务。'
          : '本项目暂无需要你处理的任务。可选择一条任务查看详情，或在左侧选择窗口查看其当前状态。';
    wrap.innerHTML = `
      <div class="flat-head-main">
        <span class="flat-eyebrow">这个项目的办公室</span>
        <h1>${escape(projectName())}</h1>
        <p class="flat-guide">${guide}</p>
        <p class="flat-today">${escape(todayStory())}</p>
      </div>
      <ul class="flat-counts">
        <li class="${counts.waiting ? 'has-value' : ''}"><span>待领取</span><b>${counts.waiting}</b></li>
        <li class="${counts.active ? 'has-value' : ''}"><span>执行中</span><b>${counts.active}</b></li>
        <li class="is-review ${counts.review ? 'has-value' : ''}"><span>待处理</span><b>${counts.review}</b></li>
        <li class="is-failed ${counts.failed ? 'has-value' : ''}"><span>未完成</span><b>${counts.failed}</b></li>
        <li class="${counts.done ? 'has-value' : ''}"><span>完成了</span><b>${counts.done}</b></li>
      </ul>
      <div class="flat-source">
        <label for="flatSourceSelect" title="现在看的是哪一份数据：正式平台上的，还是本机的样例">看的数据</label>
        <select id="flatSourceSelect" title="正式平台上的项目 / 本机的样例数据">
          <option value="platform">正式平台</option>
          <option value="demo">本地样例</option>
        </select>
      </div>
      ${
        state.error
          ? `<div class="flat-error">
              <b>刚才那一下没成：</b>${escape(state.error)}
              <button type="button" data-act="dismiss-error">知道了</button>
            </div>`
          : ''
      }
      ${
        state.memberNote
          ? `<div class="flat-note-row">
              ${escape(state.memberNote)}
              <button type="button" data-act="dismiss-member-note">知道了</button>
            </div>`
          : ''
      }`;
    return wrap;
  }

  /**
   * 列表里的一条活。
   *
   * `hideWho` 用于按人归堆的视图：块头已经写了是谁，这里就不用每行再写一遍。
   * 「尚无人领取」那一堆例外 —— 那里没有名字，行里得说清「当前无人执行」。
   */
  function taskRow(task, { hideWho = false } = {}) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className =
      'flat-task flat-task-' + escape(task.status || 'ready') +
      (state.selected?.task_id === task.task_id ? ' is-selected' : '');
    const reason = task.last_failure?.reason ? ` · ${reasonText(task.last_failure.reason)}` : '';
    const stamp = task.updated_at ? ` · ${escape(timeAgo(task.updated_at))}更新` : '';
    const holds = whoHolds(task);
    const who = whoWord(task, holds);
    const parts = titleParts(task);
    const whoPart = hideWho ? '' : escape(who);
    row.innerHTML = `
      <span class="flat-chip flat-chip-${escape(task.status)}">${escape(STATE_LABEL[task.status] || task.status)}</span>
      <span class="flat-task-title">${escape(parts.main)}</span>
      ${parts.sub ? `<span class="flat-task-sub">${escape(parts.sub)}</span>` : ''}
      <span class="flat-task-meta">${isRunning(task) ? '<i class="flat-pulse" title="正在执行"></i>' : ''}${whoPart}${whoPart ? ' · ' : ''}${escape(attemptWord(task))}${escape(reason)}</span>
      <span class="flat-task-foot">${stamp}${reason ? `<span class="flat-task-jump" data-jump="log" data-task="${escape(task.task_id)}">看最后一次输出 ↓</span>` : ''}</span>`;
    row.addEventListener('click', (event) => {
      if (event.target.closest('[data-jump="log"]')) return;
      void select(task.task_id);
    });
    return row;
  }

  /**
   * 左栏：会话列表（电脑版微信那一列）。
   *
   * 人在这个项目里打交道的是**几个对象**：项目本身、项目群聊、平台助手，还有每一个
   * 窗口（管理员与成员）。列成一栏，点谁看谁 —— 先看到"我跟谁在来往"，再点进去看
   * "它现在在做什么"。
   *
   * 每行的状态摘要都是**平台上的事实**算出来的（手上的活、在跑的活、上一次在干什么、
   * 能不能接活），不猜。
   */
  function conversations() {
    const list = document.createElement('section');
    list.className = 'flat-convs';

    const head = document.createElement('div');
    head.className = 'flat-convs-head';
    head.textContent = '会话';
    list.append(head);

    const addRow = (kind, id, name, status, onClick, dot = false) => {
      const button = document.createElement('button');
      button.type = 'button';
      const on = state.view.kind === kind && (state.view.id || null) === (id || null);
      button.className = 'flat-conv' + (on ? ' is-on' : '');
      // 窗口行带上自己的身份：名单的顺序会变（谁在线、谁先被读到），
      // 按位置找窗口就会找错人；按身份找才是那一个窗口。
      if (id) button.dataset.harness = id;
      // 红点 = **还有事没结论**：处理掉或说了「先不管」才消失（不是"看过了"就消失）。
      button.innerHTML = `<b>${escape(name)}${
        dot ? '<i class="flat-conv-dot" title="有待你处理的事"></i>' : ''
      }</b><span>${escape(status)}</span>`;
      // 右键这一行 = 「给这一家再开一个窗口」。只在窗口行上有（项目任务/群聊
      // 那两行不是 Harness，右键没意义）。
      if (kind === 'window' && id) {
        button.dataset.addMemberFrom = id;
        button.title = '右键可以给这一家再开一个窗口';
      }
      button.addEventListener('click', onClick || (() => {
        state.view = { kind, id: id || null };
        render();
      }));
      list.append(button);
    };

    const counts = countBy(state.tasks || []);
    // 需要你处理的事排在最上面：它是唯一一类"卡住等你一句话"的东西，
    // 埋在一串窗口中间就没人看。红点只在这一行、只在这类事真有结论时才灭。
    const attention = attentionCount();
    addRow(
      'alerts',
      null,
      '需要你处理',
      attention ? `${attention} 件事在等你` : '没有待你处理的事',
      () => {
        state.view = { kind: 'alerts', id: null };
        render();
      },
      attention > 0,
    );
    addRow('tasks', null, '项目任务', `${(state.tasks || []).length} 条任务 · ${counts.waiting} 条待领取`);

    const groupMessages = (state.messages || []).filter(
      (message) => !(Array.isArray(message.refs) ? message.refs : []).includes(DIRECT_REF),
    );
    const last = groupMessages.slice(-1)[0];
    addRow(
      'group',
      null,
      '项目群聊',
      last
        ? oneLine(readableMessageBody(last) || last.body || '')
        : state.loaded
          ? '暂无消息'
          : '正在读取…',
    );

    const adminId = adminIdentity();
    const roster = state.availability || [];
    // 窗口名单是第二批才到的（要探每个 CLI 的配置，慢）。这段时间说清楚在读，
    // 别让左栏空着像"这个项目里没有窗口"。
    if (!state.loaded && !roster.length) {
      addRow('window', null, '窗口名单', '正在读取…');
    }
    // 平台助手**常驻**：工作区域里一直有它，不用在新建项目时勾选，也不依赖平台
    // 注册表里有没有某个窗口（以前这里要找得到 `office-admin` 才显示这一行）。
    addRow('assistant', ASSISTANT_ID, '平台助手', assistantStatusText(), () => {
      state.view = { kind: 'assistant', id: ASSISTANT_ID };
      state.assistantScrollBottom = true;
      render();
    });
    if (adminId) {
      addRow('window', adminId, `管理员 · ${harnessName(adminId)}`, windowStatusText(adminId));
    }
    for (const item of roster) {
      const id = String(item.harness_id || '');
      if (!id || id === adminId) continue;
      // 平台助手已经有自己那一行（上面那个常驻的「平台助手」）。名单里再列一遍，
      // 左栏就会出现两个平台助手 —— 同一个东西被当成两个对象。
      if (id === ASSISTANT_ID) continue;
      // `office-admin` 是平台助手的**模型身份**，不是窗口：没有任何 CLI 响应这个
      // id，列出来只会让人以为项目里还有第三个管理员（见 harness-roles.js）。
      if (isPlatformAssistantHarness(item)) continue;
      // 别的管理员的窗口（以前某轮配置留下的）确实在平台上，但它**不是本项目的**
      // 管理员。不说明这一点，左栏就看起来像有三个管理员。
      const otherAdmin = /-admin$/i.test(id);
      const status = windowStatusText(id);
      addRow(
        'window',
        id,
        harnessName(id),
        otherAdmin ? `不是本项目的管理员 · ${status}` : status,
      );
    }
    return list;
  }

  /** 大数字加千分位：token 到百万级，不分组根本读不出来。 */
  function groupDigits(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return '0';
    return String(Math.trunc(number)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  /** 一行摘要：只截断，不解释截断（解释留给展开正文的地方）。 */
  function oneLine(text) {
    const value = String(text || '').replace(/\s+/g, ' ').trim();
    return value.length > 26 ? `${value.slice(0, 26)}…` : value;
  }

  /**
   * 这个窗口的 token 度量。
   *
   * 平台的用量账里 **consumer 就是 `harness/<id>`** —— 所以「这个窗口烧了多少」不用
   * 另算，读那一格就行。小时桶里也有每个 consumer 的 token，用来回答「每小时多少」。
   */
  function windowMetrics(harnessId) {
    const pool = state.pool || {};
    const usage = pool.usage || {};
    const consumers = usage.consumers || {};
    const entry = consumers[`harness/${harnessId}`] || null;
    // **两笔账**：额度池记的（entry）与成员自报的（entry.reported）。
    // 后者是成员用自带模型跑的活 —— 平台不在那条链路上，用量由成员自己的 CLI 报上来。
    const reported = (entry && entry.reported) || null;
    const buckets = (pool.timeline && pool.timeline.buckets) || [];
    let lastHour = null;
    let reportedHour = null;
    for (const bucket of buckets) {
      const spent = (bucket.consumer_tokens || {})[`harness/${harnessId}`];
      if (spent && (!lastHour || String(bucket.at) > String(lastHour.at))) {
        lastHour = { at: bucket.at, ...spent };
      }
      const reportedSpent = (bucket.reported_consumer_tokens || {})[`harness/${harnessId}`];
      if (reportedSpent && (!reportedHour || String(bucket.at) > String(reportedHour.at))) {
        reportedHour = { at: bucket.at, ...reportedSpent };
      }
    }
    return { entry, reported, lastHour, reportedHour };
  }

  /** 这个窗口现在怎么样（一行话）。全部是平台上的事实。 */
  function windowStatusText(harnessId) {
    const info = (state.availability || []).find((item) => item.harness_id === harnessId);
    // 它做过的活：现在是它的，**或者**最后领过它的是它（合入后平台会清空 owner，
    // 只看 now 会让窗口页永远是空的 —— 与任务表的归堆口径必须一致）。
    const mine = (state.tasks || []).filter((task) => {
      const holders = holderIds(task);
      return holders.now === harnessId || holders.before === harnessId;
    });
    const running = mine.filter((task) => isRunning(task));
    if (running.length) {
      return `执行中：${clip(titleParts(running[0]).main || running[0].title || '', 18)}`;
    }
    if (info && info.last_attempt_status && info.last_task_id) {
      const label = STATE_LABEL[info.last_attempt_status] || info.last_attempt_status;
      // 还在跑的那一轮不是「上一轮」—— 它就是现在。
      return RUNNING_STATUSES.has(String(info.last_attempt_status))
        ? '执行中'
        : `上一轮：${label}`;
    }
    if (mine.length) return `手上有 ${mine.length} 条任务`;
    if (info && info.status === 'offline') return '离线';
    if (info && info.usable === false) return '暂时无法领取任务';
    return '空闲';
  }

  /**
   * 平台助手：工作区域里**常驻**的那一块。
   *
   * 两部分，缺一不可：
   *   · **项目速览** —— 任务有没有下发、成员领了没有、现在到哪一步。这些是平台上
   *     的事实，前端直接算：模型不在、助手没在跑，这一块照样给。
   *   · **问它一句** —— 定向私聊给后台助手主体（`office-assistant`）。它有只读
   *     工具：能读平台日志、调取任务记录，回答你关于这个项目的问题。
   *
   * 面板里没有分派、处置、审核这类动作：那些是管理员窗口与人的事。
   */
  function assistantPane() {
    const panel = document.createElement('section');
    panel.className = 'flat-detail flat-assistant';

    const tasks = state.tasks || [];
    const counts = countBy(tasks);
    const waiting = tasks.filter((task) => WAITING_STATES.has(String(task.status)));
    // 没人领 = 状态还在排队，但**没有持有者** —— 这就是「任务下发了没人接」。
    const unclaimed = waiting.filter((task) => !holderIds(task).now);
    const working = tasks.filter((task) => isRunning(task));
    const byWindow = new Map();
    for (const task of working) {
      const who = holderIds(task).now || '未指定执行者';
      byWindow.set(who, (byWindow.get(who) || 0) + 1);
    }

    const head = document.createElement('div');
    head.className = 'flat-detail-head';
    head.innerHTML = `
      <div>
        <h2>平台助手</h2>
        <p class="flat-summary">它读平台日志、调取记录，回答你关于这个项目的问题。</p>
      </div>
      <div class="flat-detail-actions">
        <button type="button" data-act="assistant-detail">巡检与上报详情</button>
      </div>`;
    panel.append(head);

    // ---- 项目速览（平台事实） ----
    const facts = document.createElement('dl');
    facts.className = 'flat-facts';
    const rows = [
      ['任务', `${tasks.length} 条：待领取 ${counts.waiting} · 执行中 ${counts.active} · 待处理 ${counts.review} · 未完成 ${counts.failed} · 已完成 ${counts.done}`],
      ['还没人领', unclaimed.length
        ? `${unclaimed.length} 条：${unclaimed.slice(0, 3).map((task) => task.title || task.task_id).join('、')}${unclaimed.length > 3 ? ' 等' : ''}`
        : '没有（排队的活都有人领了）'],
      ['正在做', byWindow.size
        ? [...byWindow.entries()].map(([who, count]) => `${harnessName(who) || who}（${count} 条）`).join('、')
        : '当前没有窗口在执行'],
    ];
    if (!tasks.length) rows.push(['本项目', '还没有任务 —— 助手只能回答平台上的事，没有活可查。']);
    for (const [key, value] of rows) {
      const dt = document.createElement('dt');
      dt.textContent = key;
      const dd = document.createElement('dd');
      dd.textContent = value;
      facts.append(dt, dd);
    }
    const overview = document.createElement('section');
    overview.className = 'flat-spec';
    overview.innerHTML = '<h3>项目速览</h3>';
    overview.append(facts);
    panel.append(overview);

    // ---- 它这一轮做了什么（引擎 trace 的摘要） ----
    // 折起来：它默认不是要看的东西，但「它自己动了几步、看到了什么」该随时能翻。
    const trace = document.createElement('details');
    trace.className = 'flat-spec flat-assistant-trace';
    const runs = state.assistantRuns || [];
    // 优先给**最近一次完整跑完**的那轮：中途停掉的那次信息不全（没跑完、
    // 模型没记上），看完它不如看完上一次。
    const latest = runs.find((run) => run.status === 'done') || runs[0];
    const summary = document.createElement('summary');
    summary.textContent = latest
      ? `它最近一次做完的一轮（${(latest.steps || []).length} 步 · ${latest.model || '模型未记上'}）`
      : '它这一轮做了什么';
    trace.append(summary);
    if (!latest) {
      const empty = document.createElement('p');
      empty.className = 'flat-empty';
      empty.textContent = '还没有可看的记录（助手还没在这一轮里动过手）。';
      trace.append(empty);
    } else {
      const head = document.createElement('p');
      head.className = 'flat-note';
      head.textContent = `状态 ${latest.status || '未知'} · 引用 ${latest.self_check || '未记上'}`;
      trace.append(head);
      const list = document.createElement('ol');
      list.className = 'flat-assistant-steps';
      for (const step of latest.steps || []) {
        const row = document.createElement('li');
        const what = document.createElement('b');
        what.textContent = step.tool || step.kind || '一步';
        row.append(what);
        if (step.thought) {
          const thought = document.createElement('p');
          thought.className = 'flat-assistant-thought';
          thought.textContent = step.thought;
          row.append(thought);
        }
        if (step.observation) {
          const observation = document.createElement('p');
          observation.className = 'flat-note';
          observation.textContent = `看到：${step.observation}`;
          row.append(observation);
        }
        list.append(row);
      }
      trace.append(list);
    }
    panel.append(trace);

    // ---- 对话（我问的 + 它答的，按时间交替） ----
    const ask = document.createElement('section');
    ask.className = 'flat-spec flat-assistant-ask';
    ask.innerHTML = '<h3>对话</h3>';
    // 口径要说清楚：助手读的是**本群**的任务表（同一个群里还并着别的项目），
    // 上面那份速览才是**本项目**的口径。两个数字不一样不是谁算错了。
    const scope = document.createElement('p');
    scope.className = 'flat-note';
    scope.textContent = '助手读的是本群的任务表；同一个群里还并着别的项目，所以它的计数可能比上面的本项目多。本项目的口径看「项目速览」。';
    ask.append(scope);

    const thread = document.createElement('div');
    thread.className = 'flat-assistant-thread';
    const turns = assistantThread();
    if (!turns.length) {
      const empty = document.createElement('p');
      empty.className = 'flat-empty';
      empty.textContent = '还没有对话。问一句，它会记住这次谈话；若一直没有回答，多半是后台助手没有在运行。';
      thread.append(empty);
    }
    for (const message of turns) {
      const mine = String(message.sender_type || '') === 'human';
      const item = document.createElement('article');
      item.className = 'flat-assistant-turn ' + (mine ? 'is-mine' : 'is-assistant');
      const who = document.createElement('p');
      who.className = 'flat-assistant-who';
      const label = document.createElement('b');
      label.textContent = mine ? '我' : '平台助手';
      const when = document.createElement('span');
      when.className = 'flat-note';
      when.textContent = timeAgo(message.created_at || message.created);
      who.append(label, when);
      const body = document.createElement('p');
      body.className = 'flat-assistant-text';
      body.textContent = readableMessageBody(message) || String(message.body || '');
      item.append(who, body);
      thread.append(item);
    }
    ask.append(thread);

    const presets = document.createElement('div');
    presets.className = 'flat-assistant-presets';
    for (const question of ['现在进度怎么样？', '有哪些任务还没人领？', '最近失败的活卡在哪一步？']) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = question;
      button.dataset.act = 'assistant-preset';
      button.dataset.question = question;
      presets.append(button);
    }
    ask.append(presets);
    const form = document.createElement('div');
    form.className = 'flat-assistant-form';
    const input = document.createElement('input');
    input.type = 'text';
    input.id = 'flatAssistantQuestion';
    input.className = 'flat-assistant-input';
    input.setAttribute('data-draft', 'assistant-question');
    input.placeholder = '问一句…（它记得这次谈话）';
    input.setAttribute('aria-label', '问平台助手');
    const send = document.createElement('button');
    send.type = 'button';
    send.className = 'primary';
    send.dataset.act = 'assistant-send';
    send.textContent = '发送';
    form.append(input, send);
    ask.append(form);
    if (state.assistantError) {
      const failure = document.createElement('p');
      failure.className = 'flat-assistant-error';
      failure.textContent = state.assistantError;
      ask.append(failure);
    }
    const note = document.createElement('p');
    note.className = 'flat-note';
    note.textContent = '提问以定向私聊发给后台助手；它只读不改，回答里会写明依据。';
    ask.append(note);
    panel.append(ask);

    return panel;
  }

  /** 这条私聊里的对话（我问的 + 它答的），按时间排。 */
  function assistantThread() {
    return (state.messages || [])
      .filter((message) =>
        (Array.isArray(message.refs) ? message.refs : []).includes(DIRECT_REF),
      )
      .sort((a, b) => Number(a.created_seq || 0) - Number(b.created_seq || 0));
  }

  /** 助手发来的定向消息（它回的是私聊，所以只有这一条线程）。 */
  function assistantAnswers() {
    return (state.messages || []).filter(
      (message) => String(message.sender_id || message.author_id || '') === ASSISTANT_ID,
    );
  }

  /** 左栏那一行的状态：它最近一次回答是什么时候。 */
  function assistantStatusText() {
    const answers = assistantAnswers();
    if (!answers.length) return '随时能问';
    return `最近回答 · ${timeAgo(answers[answers.length - 1].created_at || answers[answers.length - 1].created)}`;
  }

  /** 问一句：定向私聊发给后台助手主体。 */
  async function askAssistant(question) {
    const text = String(question || '').trim();
    if (!text) return;
    state.assistantScrollBottom = true;
    state.assistantError = '';
    try {
      await api('/messages', { body: text, mentions: [ASSISTANT_ID], direct: true });
    } catch (error) {
      // 问不出去就说出来：静默失败会让人以为助手不理人。
      state.assistantError = `这句话没能送出去：${String(error.message || error)}`;
      render();
      return;
    }
    await refresh();
  }

  /** 中间那一栏：按选中的会话给内容。 */
  function conversationPane(filtered, pager) {
    const pane = document.createElement('section');
    pane.className = 'flat-main';
    if (state.view.kind === 'group') {
      pane.append(chat());
      return pane;
    }
    if (state.view.kind === 'assistant') {
      pane.append(assistantPane());
      return pane;
    }
    if (state.view.kind === 'window') {
      pane.append(windowPane(state.view.id));
      return pane;
    }
    if (state.view.kind === 'alerts') {
      pane.append(alertsPane());
      return pane;
    }
    // 项目任务：选中一条活就看详情，没选就看任务表。
    if (state.selected) {
      pane.append(detail());
      return pane;
    }
    pane.append(taskTable(filtered, pager));
    return pane;
  }

    // 档位的中文说法（界面上到处都要用，别处写一遍就散了一地）。
  const TIER_WORDS = {
    free: '免费',
    metered: '付费',
    reserve: '兜底',
    user: '自有 Key',
  };

  const tierWord = (tier) => TIER_WORDS[String(tier || '')] || String(tier || '未知');

  // 上次探到的清单放在浏览器里：刷新页面、关掉再开，都还记得上一次是什么样。
  const POOL_SNAPSHOT_KEY = 'agent-office.pool-models.v1';

  function poolSnapshot() {
    try {
      const raw = window.localStorage.getItem(POOL_SNAPSHOT_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  function savePoolSnapshot(pool) {
    try {
      window.localStorage.setItem(
        POOL_SNAPSHOT_KEY,
        JSON.stringify({
          at: Date.now(),
          models: ((pool && pool.models) || []).map((item) => ({
            id: String(item.id),
            tier: String(item.tier || ''),
            label: poolModelLabel(item).label,
          })),
          rosters: (pool && pool.rosters) || {},
        }),
      );
    } catch {
      // 存不下就算了：探针照样能报，只是没法跟「上次」比。
    }
  }

  /**
   * 这次的清单与上次的比：**多了什么**、**谁换了档**、**CLI 还能用但没人定档的**。
   *
   * 档位会变 —— 某个免费额度改成收费，只改一行配置。所以「免费」不是永久的，
   * 得有个探针能重新读一遍，再把变化说出来。
   */
  function comparePool(pool) {
    const list = (pool && pool.models) || [];
    const snapshot = poolSnapshot();
    const before = (snapshot && snapshot.models) || [];
    const added = list
      .filter((item) => !before.some((old) => old.id === String(item.id)))
      .map((item) => ({
        id: String(item.id),
        label: poolModelLabel(item).label,
        tier: String(item.tier || ''),
      }));
    const retyped = list
      .map((item) => {
        const old = before.find((entry) => entry.id === String(item.id));
        if (!old || old.tier === String(item.tier || '')) return null;
        return {
          id: String(item.id),
          label: poolModelLabel(item).label,
          before: tierWord(old.tier),
          after: tierWord(item.tier),
        };
      })
      .filter(Boolean);
    // CLI 自己的名册里有、池子里没有的：CLI 还能跑它，但**没人说它免费还是收费**
    // —— 平台不猜，所以不给用；但人要看得见它存在。
    const undeclared = [];
    const rosters = (pool && pool.rosters) || {};
    for (const [cli, names] of Object.entries(rosters)) {
      for (const name of names || []) {
        const id = `${cli}/${String(name).split('/').pop()}`;
        if (!list.some((item) => String(item.id) === id)) {
          undeclared.push({ cli, name: String(name), id });
        }
      }
    }
    // 名册本身也变了（CLI 新增了模型）：上一次探到时没有它。
    const previousRosters = (snapshot && snapshot.rosters) || {};
    const newRosterEntries = [];
    for (const [cli, names] of Object.entries(rosters)) {
      const known = previousRosters[cli] || [];
      for (const name of names || []) {
        if (!known.includes(name)) newRosterEntries.push({ cli, name: String(name) });
      }
    }
    return {
      added,
      retyped,
      undeclared,
      newRosterEntries,
      first: before.length === 0,
      total: list.length,
      at: Date.now(),
    };
  }

  /** 探针的结果怎么说给人听：多了什么、谁换了档、正用着的那个变了没有。 */
  function poolProbeNote(harnessId) {
    const probe = state.poolProbe;
    if (!probe) return '';
    // 长名单压短：八十个模型名摆出来没有人看得下去 —— 说清有几个、举几个例子。
    const cap = (names, limit = 5) =>
      names.length > limit ? `${names.slice(0, limit).join('、')}…还有 ${names.length - limit} 个` : names.join('、');
    const bits = [];
    if (probe.first) {
      bits.push(`首次探测到 ${probe.total} 个模型`);
    } else if (!probe.added.length && !probe.retyped.length) {
      bits.push('与上次探测结果一致');
    } else {
      if (probe.added.length) {
        bits.push(`新增 ${probe.added.length} 个：${cap(probe.added.map((item) => item.label))}`);
      }
      if (probe.retyped.length) {
        bits.push(
          `${probe.retyped.length} 个档位变更：${cap(
            probe.retyped.map((item) => `${item.label}（${item.before} → ${item.after}）`),
          )}`,
        );
      }
    }
    if (probe.newRosterEntries && probe.newRosterEntries.length && !probe.first) {
      bits.push(
        `各家 CLI 新增：${cap(probe.newRosterEntries.map((item) => `${item.cli} 的 ${item.name}`))}`,
      );
    }
    if (probe.undeclared && probe.undeclared.length) {
      bits.push(
        `另有 ${probe.undeclared.length} 个模型 CLI 声明可用但平台未标注档位（免费或收费尚无人声明）：${cap(
          probe.undeclared.map((item) => `${item.cli} 的 ${item.name}`),
        )}`,
      );
    }
    const info = (state.availability || []).find((item) => item.harness_id === harnessId);
    const chosen = info && info.model_id ? String(info.model_id) : '';
    const changed = probe.retyped.find((item) => item.id === chosen);
    if (changed) {
      bits.push(
        `该窗口正在使用「${changed.label}」${
          changed.after === '付费' ? '现按量计费' : '档位已变更'
        } —— 平台将停止使用，需重新确认授权。`,
      );
    }
    return bits.join('；');
  }

  /**
   * 重新探一遍池子：平台清掉发现缓存重读一遍，回来再与上次的清单比。
   *
   * 探一次要问二十几个上游，所以**只有人点了才探**；平时 15 秒的自动刷新
   * 读的是缓存。
   */
  async function probePool() {
    const fresh = await api('/pool/probe', {}).catch(() => null);
    if (!fresh) {
      state.error = '重新探测失败：平台未返回结果。';
      render();
      return;
    }
    state.pool = fresh;
    state.poolAt = Date.now();
    state.poolProbe = comparePool(fresh);
    savePoolSnapshot(fresh);
    state.error = null;
    render();
  }

  /** 收下平台回的额度池清单（定档、改档、撤档、重探都会回一份新的）。 */
  function applyPool(pool) {
    if (!pool) return;
    state.pool = pool;
    state.poolAt = Date.now();
    state.poolProbe = comparePool(pool);
    savePoolSnapshot(pool);
  }

  /** 这个窗口此刻选的是哪个额度池模型（不是池子模型就返回空串）。 */
  function currentPoolModelOf(harnessId) {
    const info = (state.availability || []).find((item) => item.harness_id === harnessId);
    if (!info || String(info.model_mode || '') !== 'pool') return '';
    return String(info.model_id || '');
  }

  /** 展开/收起某一行的改档按钮。 */
  function editTier(button) {
    state.tierEdit = String(button.dataset.modelId || '') || null;
    render();
  }

  /**
   * 改档：同一条声明会被覆盖，所以再声明一次就是改档。
   *
   * 改的若是**这个窗口正在用的模型**，平台对"能不能花"的判断也跟着变：把一条
   * 正在用的模型改成收费，却留着窗口继续用，就等于让它按量计费而没人点过头。
   * 所以那一档切换时先退回窗口自带模型，并说明为什么。
   */
  async function retierModel(button) {
    const harnessId = String(button.dataset.harness || '');
    const cli = String(button.dataset.cli || '');
    const model = String(button.dataset.model || '');
    const tier = String(button.dataset.tier || '');
    const modelId = `${cli}/${String(model).split('/').pop()}`;
    button.disabled = true;
    try {
      const result = await api('/pool/declaration', {cli, model, tier});
      if (!result || !result.pool) throw new Error('平台没有回新的额度池清单');
      applyPool(result.pool);
      state.tierEdit = null;
      if (currentPoolModelOf(harnessId) === modelId) {
        if (tier === 'metered') {
          await applyModelChoice({harnessId, mode: 'cli', modelId: '', paidConsent: false});
          state.modelNote = `${modelId} 已按收费定档。这个窗口原本在用它，先切回窗口自带模型 —— 按量计费要你点头才花。`;
        } else {
          state.modelNote = `${modelId} 已按免费定档，这个窗口继续用它。`;
        }
      } else {
        state.modelNote = `${modelId} 已按${tier === 'metered' ? '收费' : '免费'}定档。`;
      }
      render();
    } catch (error) {
      button.disabled = false;
      state.error = `改档失败：${String((error && error.message) || error)}`;
      render();
    }
  }

  /** 撤档：撤掉**人写的那条**声明，模型回到「没定档」。 */
  async function withdrawModel(button) {
    const harnessId = String(button.dataset.harness || '');
    const cli = String(button.dataset.cli || '');
    const model = String(button.dataset.model || '');
    const modelId = `${cli}/${String(model).split('/').pop()}`;
    button.disabled = true;
    try {
      const result = await api('/pool/declaration/withdraw', {cli, model});
      if (!result || !result.pool) throw new Error('平台没有回新的额度池清单');
      applyPool(result.pool);
      state.tierEdit = null;
      if (!result.withdrawn) {
        state.modelNote = `${modelId} 没有可撤的声明 —— 它用的是内置档位。`;
      } else if (currentPoolModelOf(harnessId) === modelId) {
        await applyModelChoice({harnessId, mode: 'cli', modelId: '', paidConsent: false});
        state.modelNote = `已撤掉 ${modelId} 的档位。这个窗口原本在用它，已切回窗口自带模型。`;
      } else {
        state.modelNote = `已撤掉 ${modelId} 的档位，它回到「没定档」。`;
      }
      render();
    } catch (error) {
      button.disabled = false;
      state.error = `撤档失败：${String((error && error.message) || error)}`;
      render();
    }
  }

  /**
   * 给「CLI 自己说它能用、但没人定档」的模型定档。
   *
   * 免费还是收费是**人的事实**（账户里的事），平台猜不出来；所以这里只做一件事：
   * 把人的判断写进池子的声明（沿用同一家的调用约定），然后重探一遍。
   */
  async function declareModel(button) {
    const cli = String(button.dataset.cli || '');
    const model = String(button.dataset.model || '');
    const tier = String(button.dataset.tier || '');
    button.disabled = true;
    try {
      const result = await api('/pool/declaration', { cli, model, tier });
      applyPool(result && result.pool);
      state.error = null;
      render();
    } catch (error) {
      button.disabled = false;
      state.error = `档位设置失败：${String((error && error.message) || error)}`;
      render();
    }
  }

  /**
   * 中途把一个窗口加进这个项目，并**通知管理员**。
   *
   * 加人要让人知道：管理员据此知道，并把**还没人领的活**改派给它（已经在跑的活
   * 不换人 —— 换人是人按条决定的）。
   */
  async function addMemberToProject(harnessId) {
    const adminId = adminIdentity();
    const name = harnessName(harnessId) || harnessId;
    try {
      await api('/messages', {
        body: `${name} 中途加入了这个项目。`,
        mentions: adminId ? [adminId] : [],
        refs: [
          'add-member',
          `add-member-json:${harnessId}`,
          ...(adminId ? ['to-admin'] : []),
        ],
      });
      onAddMember?.(harnessId);
      state.memberAdded = harnessId;
      state.error = null;
      render();
    } catch (error) {
      state.error = `加入失败：${String((error && error.message) || error)}`;
      render();
    }
  }

  /** 收费模型要人点头：问一次，答了才算。 */
  const paidModelDialog = buildPanelDialog('flatPaidModel', '确认使用收费模型');

  function askPaidConsent(label) {
    return new Promise((resolve) => {
      paidModelDialog.querySelector('h2').textContent = '该模型按量计费';
      paidModelDialog.querySelector('.flat-note').textContent =
        `「${label}」按调用次数计费。使用需你确认 —— 否则继续使用该窗口自带的模型。`;
      const list = paidModelDialog.querySelector('.flat-dialog-list');
      list.innerHTML = `<p class="flat-brief-action">
          <button type="button" data-paid="yes">确认使用（知晓计费）</button>
          <button type="button" data-paid="no">不用</button>
        </p>`;
      const onClick = (event) => {
        const yes = event.target.closest('[data-paid="yes"]');
        const no = event.target.closest('[data-paid="no"]');
        if (!yes && !no) return;
        paidModelDialog.removeEventListener('click', onClick);
        paidModelDialog.close();
        resolve(Boolean(yes));
      };
      paidModelDialog.addEventListener('click', onClick);
      paidModelDialog.showModal();
    });
  }

/**
   * 额度池里一个模型怎么念给人听。
   *
   * 池子里的模型 id 是给机器看的（`opencode-cli/opencode/beam-free`），人要的是
   * 「**哪家的什么模型**」—— 所以从额度名（`upstream`，形如 `qoder:Qwen3.8-Flash`）
   * 里切出那家 harness，剩下的就是它内部模型的名字。
   */
  function poolModelLabel(entry) {
    const id = String((entry && entry.id) || '');
    const owner = String((entry && (entry.upstream || entry.owned_by)) || '');
    const head = owner.split(':')[0] || owner;
    const prefixed = head && id.toLowerCase().startsWith(`${head.toLowerCase()}/`);
    const model = prefixed ? id.slice(head.length + 1) : id;
    // 「谁家的 · 哪个模型」：属主与模型之间用间隔号，不写成"X 的 Y"——
    // 中文里"claude-provider 的 xxx"读起来像半句没说完的话。
    if (String((entry && entry.tier) || '') === 'user') {
      return {harness: '自有 Key', model, label: `自有 Key · ${model}`};
    }
    return {harness: head, model, label: head ? `${head} · ${model}` : model};
  }

  // 额度池的档位，按人看的顺序：先不花钱的，再花钱的，最后是兜底与自己掏钱的。
  const POOL_TIER_GROUPS = [
    {
      tier: 'free',
      title: '免费额度',
      mark: '免费',
      hint: '不产生额外费用：使用各家 CLI 自带的免费额度。',
    },
    {
      tier: 'metered',
      title: '付费额度',
      mark: '付费',
      hint: '按量计费：按调用次数计费 —— 需你确认后方可使用。',
    },
    {
      tier: 'reserve',
      title: '兜底额度',
      mark: '兜底',
      hint: '订阅额度：平台不会自行使用，需你点名后才会启用。',
    },
    {
      tier: 'user',
      title: '自有 Key',
      mark: '你的 key',
      hint: '自有 API Key：平台不会自行使用，需你先确认授权。',
    },
  ];

  /**
   * 这个窗口用哪个模型 —— **能选，选了平台照办**。
   *
   * 两个来源：**自带**（这个窗口自己的 CLI 配置里那个）与**额度池**（池子里的某个
   * 模型，写清楚是哪家的什么模型、属于哪一档额度）。选完存进平台的登记，下一轮
   * 这个窗口干活时平台就按它安排 —— 不是列出来给人看而已。
   */
  function modelChoiceBlock(harnessId) {
    const info = (state.availability || []).find((item) => item.harness_id === harnessId) || null;
    // 人刚刚挑的（还没保存）优先；没有就显示平台上登记着的那个。
    const pick = state.modelPicks[harnessId] || null;
    const mode = pick ? pick.mode : info ? String(info.model_mode || 'cli') : 'cli';
    const modelId = pick ? pick.modelId : info ? String(info.model_id || '') : '';
    const models = (state.pool && state.pool.models) || [];
    const rosters = (state.pool && state.pool.rosters) || {};
    const section = document.createElement('section');
    section.className = 'flat-spec flat-model';
    section.dataset.harness = harnessId;
    const cliRow = `<label class="flat-model-row"><input type="radio" name="flatModelMode" value="cli" ${
      mode === 'pool' ? '' : 'checked'
    }>
        <span>窗口自带模型</span><span class="flat-model-hint">这个窗口自己的配置${
          info && info.model ? `（${escape(info.model)}）` : ''
        }</span></label>`;
    // 按**额度属主**分组：谁家的额度、谁家的模型 —— 摊平成一长串谁都看不清。
    const groups = new Map();
    const groupOf = (key) => {
      if (!groups.has(key)) groups.set(key, { key, models: [], undeclared: [] });
      return groups.get(key);
    };
    for (const item of models) {
      const owner = String(item.upstream || item.owned_by || '');
      const key = owner.split(':')[0] || owner;
      groupOf(key).models.push(item);
    }
    for (const [cli, names] of Object.entries(rosters)) {
      const group = groupOf(cli);
      for (const name of names || []) {
        const id = `${cli}/${String(name).split('/').pop()}`;
        if (models.some((item) => String(item.id) === id)) continue;
        group.undeclared.push({ id, name: String(name) });
      }
    }
    const declaredKeys = new Set(
      ((state.pool && state.pool.declarations) || []).map(
        (entry) => `${entry.cli}/${String(entry.model).split('/').pop()}`,
      ),
    );
    const groupHtml = [...groups.values()]
      .map((group) => {
        const rows = [];
        // 只有 CLI 那几家的模型能在这里改档：别家的额度写在各自的配置里，
        // 平台没有替它们改档的口子。
        const cliGroup = Object.prototype.hasOwnProperty.call(rosters, group.key);
        for (const item of group.models) {
          const label = poolModelLabel(item);
          const tier = String(item.tier || '');
          const mark = POOL_TIER_GROUPS.find((entry) => entry.tier === tier);
          const id = String(item.id);
          // 声明的写法是 CLI 自己那种（`opencode/beam-free`），不是短名。
          const cliModel = String(item.upstream || '').includes(':')
            ? String(item.upstream).split(':').slice(1).join(':')
            : String(item.model || label.model);
          const editing = cliGroup && state.tierEdit === id;
          const editor = editing
            ? `<span class="flat-model-edit">
                 <button type="button" data-act="retier-model" data-harness="${escape(harnessId)}" data-cli="${escape(group.key)}" data-model="${escape(cliModel)}" data-tier="free">按免费</button>
                 <button type="button" data-act="retier-model" data-harness="${escape(harnessId)}" data-cli="${escape(group.key)}" data-model="${escape(cliModel)}" data-tier="metered">按收费</button>
                 ${
                   declaredKeys.has(id)
                     ? `<button type="button" data-act="withdraw-model" data-harness="${escape(harnessId)}" data-cli="${escape(group.key)}" data-model="${escape(cliModel)}">撤档</button>`
                     : '<span class="flat-model-hint">内置档位，撤不了</span>'
                 }
                 <button type="button" data-act="edit-tier" data-model-id="">收起</button>
               </span>`
            : cliGroup
              ? `<span class="flat-model-edit"><button type="button" data-act="edit-tier" data-model-id="${escape(id)}">改档</button></span>`
              : '';
          rows.push(
            `<label class="flat-model-row"><input type="radio" name="flatModelMode" value="pool" data-model-id="${escape(id)}" ${
              modelId === id ? 'checked' : ''
            }>
              <span>${escape(label.model)}</span><em class="flat-model-mark is-${escape(tier)}">${escape(
                mark ? mark.mark : '未知',
              )}</em>${editor}</label>`,
          );
        }
        for (const entry of group.undeclared) {
          // CLI 自己说它能用，但**没人说它免费还是收费** —— 平台不猜。
          //
          // 以前这里只有「标成免费 / 标成收费」两个按钮：定完档，这个模型仍然
          // 不在本窗口的选项里，得再回来点一次单选、再按一次保存。于是五十多个
          // 模型"看得见、选不了"。现在两个按钮就是人的那句话本身：按免费/按收费
          // **用它** —— 定档、选中、保存一次做完。
          rows.push(
            `<p class="flat-model-row is-undeclared"><span>${escape(entry.name)}</span>
              <em class="flat-model-mark is-unknown">没定档</em>
              <button type="button" data-act="use-undeclared" data-harness="${escape(harnessId)}" data-cli="${escape(group.key)}" data-model="${escape(entry.name)}" data-tier="free">按免费用它</button>
              <button type="button" data-act="use-undeclared" data-harness="${escape(harnessId)}" data-cli="${escape(group.key)}" data-model="${escape(entry.name)}" data-tier="metered">按收费用它</button>
            </p>`,
          );
        }
        const label = group.key === 'user' ? '自有 Key' : group.key;
        const summary = `${label}（${group.models.length} 个可用${
          group.undeclared.length ? ` · ${group.undeclared.length} 个没定档，定一下就能用` : ''
        }）`;
        return `<details class="flat-model-owner" data-fold="model-${escape(group.key)}"${
          state.openFolds.has(`model-${group.key}`) ? ' open' : ''
        }>
          <summary>${escape(summary)}</summary>
          ${rows.join('')}
        </details>`;
      })
      .join('');
    const undeclaredTotal = [...groups.values()].reduce((sum, group) => sum + group.undeclared.length, 0);
    // 池子还没读到的时候别报「0 家」—— 那不是「池子是空的」，是「还在读」。
    const poolPending = !state.pool;
    const picked = models.find((item) => String(item.id) === modelId);
    const current =
      mode === 'pool'
        ? `额度池：${picked ? poolModelLabel(picked).label : modelId || '（所选模型已不在额度池中）'}`
        : `窗口自带模型${info && info.model ? `（${info.model}）` : ''}`;
    const probe = poolProbeNote(harnessId);
    section.innerHTML = `<h3>该窗口使用的模型</h3>
      <p class="flat-note">当前使用：${escape(current)}</p>
      ${cliRow}
      <details class="flat-model-list" data-fold="model-list"${
        state.openFolds.has('model-list') ? ' open' : ''
      }>
        <summary>${
          poolPending
            ? '额度池中的模型（正在读取…）'
            : `额度池里的模型（${groups.size} 家 · ${models.length} 个可用${
                undeclaredTotal ? ` · ${undeclaredTotal} 个还没定档` : ''
              }）`
        }</summary>
        <p class="flat-model-hint">按额度归属分组：各家的额度与模型。每档档位由额度方自行声明 —— 免费额度也可能改为收费；点「重新探一遍」重新读取，有变化会提示。</p>
        ${groupHtml}
      </details>
      <p class="flat-model-actions">
        <button type="button" data-act="save-model-choice" data-harness="${escape(harnessId)}">保存</button>
        <button type="button" data-act="probe-pool">重新探一遍</button>
        ${probe ? `<span class="flat-model-probe">${escape(probe)}</span>` : ''}
      </p>
      ${
        state.modelNote
          ? `<div class="flat-note-row">${escape(state.modelNote)}
              <button type="button" data-act="dismiss-model-note">知道了</button>
            </div>`
          : ''
      }`;
    return section;
  }

  /**
   * 把「这个窗口用哪个模型」存到平台，然后读回来重画。
   *
   * 保存失败要说清楚是哪一步没成 —— 人以为已经换了模型，下一轮却在用旧的，
   * 那比报错更糟。
   */
  /** 把「这个窗口用哪个模型」发到平台，并把可用性读回来。 */
  async function applyModelChoice({harnessId, mode, modelId, paidConsent}) {
    await api('/model-selection', {
      harness_id: harnessId,
      model_mode: mode,
      model_id: modelId,
      paid_consent: Boolean(paidConsent),
    });
    const availability = await api('/availability').catch(() => null);
    if (availability && Array.isArray(availability.availability)) {
      state.availability = availability.availability;
    }
    // 存上了：接下来显示的是平台登记里的那个，不再是「刚刚挑的」。
    delete state.modelPicks[harnessId];
    state.error = null;
    render();
  }

  async function saveModelChoice(button) {
    const harnessId = String(button.dataset.harness || '');
    const pane = button.closest('.flat-model');
    const picked = pane ? pane.querySelector('input[name="flatModelMode"]:checked') : null;
    const mode = picked && picked.value === 'pool' ? 'pool' : 'cli';
    const modelId = mode === 'pool' ? String((picked && picked.dataset.modelId) || '') : '';
    const entry = ((state.pool && state.pool.models) || []).find(
      (item) => String(item.id) === modelId,
    );
    const tier = entry ? String(entry.tier || '') : '';
    // 按量计费的先问一句：人没点头就不发（服务端还会再拒一次，两道闸门）。
    if (tier === 'metered') {
      const yes = await askPaidConsent(poolModelLabel(entry).label);
      if (!yes) return;
    }
    button.disabled = true;
    try {
      await applyModelChoice({harnessId, mode, modelId, paidConsent: tier === 'metered'});
    } catch (error) {
      button.disabled = false;
      state.error = `模型选择未能保存：${String((error && error.message) || error)}`;
      render();
    }
  }

  /**
   * 用一个**还没定档**的模型：先按人点的那个按钮定档，再当成本窗口的选择存下去。
   *
   * 为什么合成一步：模型不在池子的可路由清单里时，选了也不会真走池子 —— 平台会
   * 直接退回窗口自带的模型，人却以为换成了。以前"定档"和"选用"是两个不相干的
   * 按钮，五十多个模型因此看得见、选不了。现在点「按免费用它 / 按收费用它」就是
   * 人对档位的那一句话：定档、选中、保存一起完成。
   */
  async function useUndeclaredModel(button) {
    const harnessId = String(button.dataset.harness || '');
    const cli = String(button.dataset.cli || '');
    const model = String(button.dataset.model || '');
    const tier = String(button.dataset.tier || '');
    if (!harnessId || !cli || !model || !tier) return;
    const modelId = `${cli}/${String(model).split('/').pop()}`;
    const label = tier === 'metered' ? '按收费用它' : '按免费用它';
    button.disabled = true;
    button.textContent = '正在设置…';
    try {
      const declared = await api('/pool/declaration', {cli, model, tier});
      if (!declared || !declared.pool) throw new Error('平台没有回新的额度池清单');
      state.pool = declared.pool;
      state.poolAt = Date.now();
      state.poolProbe = comparePool(declared.pool);
      savePoolSnapshot(declared.pool);
      const entry = (state.pool.models || []).find((item) => String(item.id) === modelId);
      if (!entry) throw new Error(`定档之后额度池里仍然没有 ${modelId}`);
      // 人已经按了"按收费用它"——那一句就是收费的点头；免费的不必再问。
      await applyModelChoice({
        harnessId,
        mode: 'pool',
        modelId,
        paidConsent: String(entry.tier || '') === 'metered',
      });
    } catch (error) {
      button.disabled = false;
      button.textContent = label;
      state.error = `这个模型没能用上：${String((error && error.message) || error)}`;
      render();
    }
  }

/**
   * 「模型用不了」的迹象。
   *
   * 限流、额度用尽、通道连不上、认证被拒、服务端 5xx、超时 —— 这几类换个模型
   * 就能继续。产物不合规、输出不是 JSON 那类**不在此列**：换模型也不解决，
   * 混进来只会让人以后无视这个弹窗。
   */
  const MODEL_TROUBLE = /rate[\s_-]?limit|too many requests|\b429\b|quota|insufficient|额度用尽|额度用完|余额不足|connection[\s_-]?refused|connection[\s_-]?reset|econnrefused|stream disconnected|failed to connect|no route to host|error sending request|provider\.transport|unauthorized|invalid[\s_-]?api[\s_-]?key|\b401\b|\b50[0234]\b|service unavailable|timed?[\s_-]?out|模型用不了|ChannelUnavailable/i;

  /** 记下"这个窗口这次的这件事我处理过了"，免得弹窗一直弹。 */
  const TROUBLE_DISMISSED_KEY = 'agent-office.model-trouble-dismissed.v1';

  function dismissedTroubles() {
    try {
      const raw = localStorage.getItem(TROUBLE_DISMISSED_KEY);
      const list = raw ? JSON.parse(raw) : [];
      return new Set(Array.isArray(list) ? list.map(String) : []);
    } catch {
      return new Set();
    }
  }

  function rememberTroubleDismissed(signature) {
    if (!signature) return;
    const set = dismissedTroubles();
    set.add(signature);
    try {
      localStorage.setItem(TROUBLE_DISMISSED_KEY, JSON.stringify([...set].slice(-80)));
    } catch {
      // 存不下就只是会再弹一次，不影响正事。
    }
  }

  /**
   * 哪些窗口的模型现在用不了了。
   *
   * 两个来源，都是平台上**已经记着**的事实：
   *   · 成员：它那条活被放回队列时写下的失败原因（平台自己补了一句"模型用不了：
   *     …"，带的是 CLI 说的那一句）；
   *   · 管理员与平台助手：它们在群里报的失败（原文里就有限流/连接的说法）。
   *
   * 成员的传阅意见之类的正文**不参与**判断：那里也常出现 429 这类字眼，那是
   * 在讨论，不是它自己的模型坏了。
   */
  function modelTroubleWindows() {
    const found = new Map();
    const adminId = adminIdentity();
    const note = (harnessId, why, when) => {
      const id = String(harnessId || '');
      if (!id) return;
      const existing = found.get(id);
      if (existing) {
        existing.count += 1;
        return;
      }
      found.set(id, { harnessId: id, why: String(why || ''), when: String(when || ''), count: 1 });
    };
    for (const task of state.tasks || []) {
      const raw = String(task.last_failure?.reason || '');
      if (!MODEL_TROUBLE.test(raw)) continue;
      // 模型坏掉的时候，这条活记在**最后持有它**的窗口上（合入后平台会清 owner）。
      const holders = holderIds(task);
      note(holders.now || holders.before, troubleReason(reasonText(raw)), task.last_failure?.at || '');
    }
    // 管理员与助手在群里报的失败：只看最近一小段，且只认它们自己发的。
    // 成员的消息不参与：传阅意见里讨论到「429」是在讨论额度，不是它自己的
    // 模型坏了 —— 成员那条由它自己的任务失败来说。
    const recent = (state.messages || []).slice(-40);
    for (const message of recent) {
      if (String(message.sender_type || '') !== 'harness') continue;
      const sender = String(message.sender_id || '');
      if (!sender) continue;
      // 只认管理员与平台助手：它们报的失败会带原始原因。
      const isAdminSender = Boolean(adminId) && sender === adminId;
      const isAssistantSender = sender === ASSISTANT_ID || /assistant/i.test(sender);
      if (!isAdminSender && !isAssistantSender) continue;
      // 判据读**原文**：`readableMessageBody` 默认只取第一行，而失败原因往往
      // 写在第二行之后（"这一条没有答上来。\n原因：… stream disconnected …"）——
      // 拿那句摘要去判，永远判不出模型坏了。展示才用人话。
      const body = String(message.body || '');
      if (!MODEL_TROUBLE.test(body)) continue;
      // 展示用尾巴那一截（见 troubleReason）：摘要只截 26 字，放在这一页上看不到成因。
      note(sender, troubleReason(body), message.created_at);
    }
    return [...found.values()];
  }

  /** 这次这件事的指纹：换一次失败就是新的一件事，处理过的不再打扰。 */
  function troubleSignature(entry) {
    return `${entry.harnessId}|${String(entry.why || '').slice(0, 60)}|${entry.when || ''}`;
  }

  /**
   * 一条消息/一句失败里"为什么用不了"的那一句。
   *
   * 取**尾巴**：成因往往写在后半段（"这一条没有答上来。原因：… connection refused"），
   * 从头截断只会剩下 "AdminHarnessE…"，等于没写。所以在"需要你处理"这一页上
   * 最多留 160 字、优先留后面那半。
   */
  function troubleReason(text) {
    const flat = String(text || '').replace(/\s+/g, ' ').trim();
    if (!flat) return '';
    // 原始失败常常是**半截 JSON**（`…ailed","error":{"message":"stream disconnected…"}`）：
    // 里面那句 message 才是"为什么用不了"，把机器结构整段摆给人看等于没说。
    const quoted = /"message"\s*:\s*"([^"]{4,300})"/.exec(flat);
    const base = quoted ? quoted[1] : flat;
    const marker = base.lastIndexOf('原因');
    const tail = marker >= 0 ? base.slice(marker) : base;
    return tail.length > 160 ? `…${tail.slice(-160)}` : tail;
  }

  /** 一个窗口的"换成哪个模型"：按额度属主分组，每条标出档位。 */
  function troublePicker(harnessId) {
    const models = (state.pool && state.pool.models) || [];
    const groups = new Map();
    for (const item of models) {
      const owner = String(item.upstream || item.owned_by || '');
      const key = owner.split(':')[0] || owner;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    const options = [...groups.entries()]
      .map(([owner, items]) => {
        const rows = items
          .map((item) => {
            const label = poolModelLabel(item).label;
            const tier = String(item.tier || '');
            const mark = POOL_TIER_GROUPS.find((entry) => entry.tier === tier);
            return `<option value="${escape(item.id)}">${escape(label)} · ${escape(
              mark ? mark.mark : '未知档位',
            )}</option>`;
          })
          .join('');
        return `<optgroup label="${escape(owner === 'user' ? '你自己的 Key' : owner)}">${rows}</optgroup>`;
      })
      .join('');
    if (!options) {
      return '<p class="flat-empty">额度池里现在没有可选的模型（点「重新探一遍」看看）。</p>';
    }
    return `<select data-trouble-select="${escape(harnessId)}" aria-label="换成哪个模型">
        <option value="">选一个额度池里的模型…</option>${options}
      </select>`;
  }

  /**
   * 「需要你处理」这一页里的事。
   *
   * 现在只有一类：某个窗口的模型用不了了。以后要加别的（等不到人拍板的方案、
   * 成员一直在等的求助），在这里加一条就行 —— 左栏那个红点看的是这份清单，
   * 不是某一类，这样"还有事没处理"只有一个出处。
   */
  function attentionItems() {
    return openModelTroubles().map((entry) => ({
      kind: 'model-trouble',
      signature: troubleSignature(entry),
      harnessId: entry.harnessId,
      title: harnessName(entry.harnessId) || entry.harnessId,
      why: entry.why,
      count: entry.count,
      when: entry.when,
    }));
  }

  /**
   * 需要人处理的事有几件 —— 左栏那个红点只看这一句。
   *
   * 它随「处理掉」或「先不管」一起消失：两条路都记在
   * `agent-office.model-trouble-dismissed.v1` 里，所以红点不是"看过了就没了"，
   * 而是"这件事有结论了才没了"。
   */
  function attentionCount() {
    return attentionItems().length;
  }

  /** 这一页：需要你处理的事，一件一块。处理完就没了。 */
  function alertsPane() {
    const pane = document.createElement('section');
    pane.className = 'flat-main flat-alerts';
    const items = attentionItems();
    const head = document.createElement('header');
    head.className = 'flat-detail-head';
    head.innerHTML = `<div>
        <h2>需要你处理</h2>
        <p class="flat-summary">${
          items.length ? `${items.length} 件事在等你。处理完或选择「先不管」之后，左栏的红点会消失。` : '现在没有需要你处理的事。'
        }</p>
      </div>`;
    pane.append(head);
    if (!items.length) return pane;
    for (const item of items) {
      const block = document.createElement('section');
      block.className = 'flat-spec flat-alert';
      block.dataset.signature = item.signature;
      block.dataset.harness = item.harnessId;
      block.innerHTML = `
        <h3>${escape(item.title)}</h3>
        <p class="flat-note">它的模型用不了了${item.count > 1 ? `（${item.count} 条活都卡在这）` : ''}。${escape(item.why)}</p>
        <p class="flat-note">换成额度池里的模型就能继续 —— 你选完，下一轮这个窗口就按它跑，活不用重做。</p>
        <div class="flat-alert-pick">
          <label>换成 ${troublePicker(item.harnessId)}</label>
          <button type="button" data-act="apply-trouble-model" data-harness="${escape(item.harnessId)}">就用这个模型</button>
          <button type="button" data-act="dismiss-trouble" data-harness="${escape(
            item.harnessId,
          )}" data-signature="${escape(item.signature)}">先不管</button>
        </div>
        <p class="flat-trouble-error" data-trouble-error="${escape(item.harnessId)}"></p>`;
      pane.append(block);
    }
    return pane;
  }

  /** 人给某个窗口挑了模型：存进平台登记，这件事就算处理过了。 */
  async function applyTroubleModel(button) {
    const harnessId = String(button.dataset.harness || '');
    const block = button.closest('.flat-alert') || button.closest('.flat-trouble');
    const select = block ? block.querySelector('[data-trouble-select]') : null;
    const modelId = select ? String(select.value || '') : '';
    const note = block ? block.querySelector(`[data-trouble-error="${harnessId}"]`) : null;
    if (!modelId) {
      if (note) note.textContent = '先选一个模型。';
      return;
    }
    const entry = ((state.pool && state.pool.models) || []).find((item) => String(item.id) === modelId);
    const tier = entry ? String(entry.tier || '') : '';
    if (tier === 'metered') {
      const yes = await askPaidConsent(poolModelLabel(entry).label);
      if (!yes) return;
    }
    button.disabled = true;
    try {
      await api('/model-selection', {
        harness_id: harnessId,
        model_mode: 'pool',
        model_id: modelId,
        paid_consent: tier === 'metered',
      });
      // 这件事处理过了：同一个失败不再弹（下次是新的失败就还会弹）。
      // 一个窗口可能同时有几件事记着（一条活 + 它自己在群里报的失败），
      // 全都得记上 —— 只记一条，回来还会弹。
      for (const item of modelTroubleWindows()) {
        if (item.harnessId === harnessId) rememberTroubleDismissed(troubleSignature(item));
      }
      if (block) block.remove();
      if (note) note.textContent = '';
      const availability = await api('/availability').catch(() => null);
      if (availability && Array.isArray(availability.availability)) {
        state.availability = availability.availability;
      }
      render();
    } catch (error) {
      button.disabled = false;
      if (note) note.textContent = `没能存上：${String((error && error.message) || error)}`;
    }
  }

  /** 「先不管」：这件事有结论了（不处理），左栏的红点跟着消失。 */
  function dismissTrouble(button) {
    const signature = String(button.dataset.signature || '');
    if (!signature) return;
    rememberTroubleDismissed(signature);
    render();
  }

  /** 还没处理过的那些：处理过的不再占着这一页，也不再让红点亮着。 */
  function openModelTroubles() {
    const dismissed = dismissedTroubles();
    return modelTroubleWindows().filter((entry) => !dismissed.has(troubleSignature(entry)));
  }

  /**
   * 一条活的**任务书**：目标原文、验收标准、只允许改的路径。
   *
   * 目标原文不在任务列表里（`/tasks` 的行只有 title/status/owner/…），
   * 它在 `/tasks/{id}` 的 `spec.goal` 里 —— 所以**点开才去读**，读过一次
   * 记一分钟。读不到就说读不到，不能给一个空框让人以为这条活没有目标。
   */
  async function loadTaskSpec(taskId) {
    const cached = state.windowSpecs[taskId];
    if (cached && Date.now() - cached.at < 60000) return cached.spec;
    const data = await api(`/tasks/${encodeURIComponent(taskId)}`).catch(() => null);
    const spec = (data && data.task && data.task.spec) || null;
    state.windowSpecs[taskId] = { at: Date.now(), spec };
    return spec;
  }

  /**
   * 窗口页里的一条活：收起时是「状态 + 标题 + 做到第几次 + 现在什么状态」，
   * 点开就是**这条活完整的目标原文**（不截断）+ 验收标准 + 只允许改的路径 +
   * 动作记录。
   */
  function windowTaskCard(task) {
    const card = document.createElement('details');
    card.className = 'flat-wtask';
    // 展开状态要活过 15 秒一次的重画：办公室靠 data-fold 记住哪些折叠是开着的。
    const foldKey = `wtask:${String(task.task_id || '')}`;
    card.dataset.fold = foldKey;
    const summary = document.createElement('summary');
    summary.innerHTML = `<span class="flat-chip flat-chip-${escape(task.status)}">${escape(STATE_LABEL[task.status] || task.status)}</span>
      <b>${escape(titleParts(task).main || task.title || task.task_id)}</b>
      <span class="flat-wtask-meta">${escape(attemptWord(task))} · ${escape(statusSentence(task))}</span>`;
    card.append(summary);
    const body = document.createElement('div');
    body.className = 'flat-wtask-body';
    body.textContent = '正在读取任务详情…';
    card.append(body);
    const load = async () => {
      if (card.dataset.loaded || card.dataset.loading) return;
      card.dataset.loading = '1';
      const spec = await loadTaskSpec(task.task_id);
      card.dataset.loading = '';
      if (!card.open) return;
      card.dataset.loaded = '1';
      const goal = String((spec && spec.goal) || '').trim();
      const acceptance = spec && Array.isArray(spec.acceptance)
        ? spec.acceptance.map((item) => (item && item.description) || JSON.stringify(item)).filter(Boolean)
        : [];
      const paths = spec && Array.isArray(spec.exclusive_paths) ? spec.exclusive_paths : [];
      const events = (state.events || []).filter((event) => event.aggregate_id === task.task_id);
      body.innerHTML = `
        <p class="flat-note">任务目标</p>
        <p class="flat-wtask-goal">${goal ? escape(goal) : '（平台未提供该任务的目标）'}</p>
        ${acceptance.length ? `<p class="flat-note">验收标准</p><ul class="flat-list">${acceptance.map((item) => `<li>${escape(item)}</li>`).join('')}</ul>` : ''}
        ${paths.length ? `<p class="flat-note">允许修改的路径</p><ul class="flat-list">${paths.map((path) => `<li><code>${escape(path)}</code></li>`).join('')}</ul>` : ''}
        <p class="flat-note">动作记录${events.length ? `（${events.length} 步）` : ''}</p>
        ${events.length ? `<ol class="flat-events">${events.map((event) => eventRow(event, { subject: taskTitleOf(event.aggregate_id) })).join('')}</ol>` : '<p class="flat-empty">暂无该任务的记录。</p>'}`;
    };
    card.addEventListener('toggle', () => {
      if (card.open) void load();
    });
    // 重画之前它是展开的：重画之后仍然是展开的，而且照样把任务书读出来。
    if (state.openFolds.has(foldKey)) {
      card.open = true;
      void load();
    }
    return card;
  }

  /**
   * 这个窗口自己的 CLI 会话。
   *
   * 干活时的输出已经能看到（任务详情里的「成员输出」）。这里补的是**对话**
   * 那一半：它审阅方案、细化需求、回答问题时也真的跑着自己的 CLI，那些会话
   * 现在同样留下了 transcript，按「它说了什么 / 它做了什么」呈现 —— 与它在
   * 终端里自己显示的那份是同一段输出。
   *
   * 读不到就说读不到，不拿平台自己的记录冒充它的会话。
   */
  function cliSessionsPanel(harnessId) {
    const box = document.createElement('section');
    box.className = 'flat-spec flat-cli-sessions';
    box.innerHTML = '<h3>它自己的 CLI 会话</h3><p class="flat-note">正在读它跑过的 CLI…</p>';
    api(`/harnesses/${encodeURIComponent(harnessId)}/runs`).then((data) => {
      const runs = (data && data.runs) || [];
      box.innerHTML = '<h3>它自己的 CLI 会话</h3>';
      if (!runs.length) {
        const empty = document.createElement('p');
        empty.className = 'flat-empty';
        empty.textContent = (data && data.note) || '这个窗口还没有留下 CLI 运行记录。';
        box.append(empty);
        return;
      }
      const note = document.createElement('p');
      note.className = 'flat-note';
      note.textContent = `共 ${runs.length} 次运行（干活与对话都在内）—— 这是它自己的 CLI 打印的。`;
      box.append(note);
      const list = document.createElement('div');
      list.className = 'flat-wtasks';
      for (const run of runs) list.append(cliSessionCard(run, harnessId));
      box.append(list);
    }).catch(() => {
      box.innerHTML = '<h3>它自己的 CLI 会话</h3>';
      const empty = document.createElement('p');
      empty.className = 'flat-empty';
      empty.textContent = '（暂时读不到它的 CLI 运行记录）';
      box.append(empty);
    });
    return box;
  }

  /**
   * 它的平台凭据：这个窗口手上有几条、哪条在用、哪些可以收回。
   *
   * 为什么要有这一块：平台只存凭据的哈希（`auth_tokens.token_hash`），所以凭据
   * 一旦签发出去，**唯一还能指认它的东西就是指纹**。而一个窗口每被重新签发一次
   * 就多一条仍然有效的凭据 —— 以前没有任何界面能说出哪条是哪条，也就无从收回。
   *
   * 三条边界：① 显示的只有指纹，凭据原文只在本机那个 key 文件里；② 哪条在用是
   * 按窗口自己的 key 文件认出来的，不是猜的；③ **正在用的那条不给按钮** ——
   * 吊销它会让这个窗口的下一次请求被平台拒绝，而重签是"开窗口"那一步的事，
   * 办公室不在这里替它换。
   */
  function windowCredentialsPanel(harnessId) {
    const box = document.createElement('section');
    box.className = 'flat-spec flat-credentials';
    let lastNote = '';

    const stamp = (value) => {
      // 平台存的是 UTC；这里换成这台机器的时间，否则"我刚签发的凭据"会显示成八小时前。
      const date = new Date(String(value || ''));
      if (Number.isNaN(date.getTime())) return String(value || '').slice(5, 16).replace('T', ' ');
      const pad = (number) => String(number).padStart(2, '0');
      return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
        date.getMinutes(),
      )}`;
    };

    const rowOf = (row) => {
      const status =
        row.status === 'in_use' ? '在用' : row.status === 'revoked' ? '已吊销' : '仍有效';
      const when = stamp(row.created_at);
      // 加粗行只说"现在是什么状态、什么时候"，理由另起一行淡化：
      // 三个时间加一句话挤在一行里，会让已经作废的那条比在用的那条更响。
      const headline =
        row.status === 'revoked'
          ? `${status} · ${stamp(row.revoked_at)} 吊销 · ${when} 签发`
          : `${status} · ${when} 签发`;
      const element = document.createElement('div');
      element.className =
        'flat-cred' +
        (row.status === 'in_use' ? ' flat-cred-live' : '') +
        (row.status === 'revoked' ? ' flat-cred-dead' : '');
      element.dataset.fingerprint = String(row.fingerprint || '');
      element.innerHTML = `
        <div class="flat-cred-main">
          <b>${escape(headline)}</b>
          <span>指纹 ${escape(String(row.fingerprint || ''))}${
            row.label ? ` · 标签：${escape(String(row.label))}` : ''
          }${row.key_file ? ` · 文件 ${escape(String(row.key_file))}` : ' · 本机没有它的 key 文件'}</span>
          ${
            row.status === 'revoked' && row.revoked_reason
              ? `<span>原因：${escape(String(row.revoked_reason))}</span>`
              : ''
          }
        </div>
        ${row.status === 'valid' ? '<button type="button" data-revoke="1">吊销</button>' : ''}`;
      return element;
    };

    const paint = (data) => {
      const rows = (data && data.credentials) || [];
      box.innerHTML = '<h3>它的平台凭据</h3>';

      const explain = document.createElement('p');
      explain.className = 'flat-note';
      explain.textContent =
        '平台只保存凭据的哈希，这里显示的是指纹；凭据原文只在本机 key 文件里。重签一次就多一条，旧的仍能开工。';
      box.append(explain);

      if (!rows.length) {
        const empty = document.createElement('p');
        empty.className = 'flat-empty';
        empty.textContent = (data && data.note) || '这个窗口还没有平台凭据。';
        box.append(empty);
      } else {
        // 人打开这一块，先想知道"它现在用哪条"，所以按状态排：在用 → 仍有效 →
        // 已吊销；同一档里新的在前。收回去的那些留在最下面，看得见但不抢眼。
        const rank = (row) => (row.status === 'in_use' ? 0 : row.status === 'valid' ? 1 : 2);
        const ordered = [...rows].sort((left, right) => {
          const byRank = rank(left) - rank(right);
          if (byRank !== 0) return byRank;
          return String(right.created_at || '').localeCompare(String(left.created_at || ''));
        });
        const list = document.createElement('div');
        list.className = 'flat-creds';
        for (const row of ordered) list.append(rowOf(row));
        box.append(list);

        const live = rows.filter((row) => row.status === 'in_use').length;
        const keeper = document.createElement('p');
        keeper.className = 'flat-note';
        keeper.textContent = live
          ? '正在使用的那一条没有按钮：吊销它，它的下一次请求就会被平台拒绝。要换一份凭据，得重新开这个窗口。'
          : '没有一条在用：这台机器上找不到这个窗口能用的凭据文件，它现在开不了工。';
        box.append(keeper);
      }

      if (lastNote) {
        const row = document.createElement('p');
        row.className = 'flat-note-row';
        row.textContent = lastNote;
        const clear = document.createElement('button');
        clear.type = 'button';
        clear.textContent = '知道了';
        clear.addEventListener('click', () => {
          lastNote = '';
          row.remove();
        });
        row.append(clear);
        box.append(row);
      }
    };

    const load = () =>
      api(`/harnesses/${encodeURIComponent(harnessId)}/credentials`)
        .then(paint)
        .catch((error) => {
          box.innerHTML = '<h3>它的平台凭据</h3>';
          const empty = document.createElement('p');
          empty.className = 'flat-empty';
          empty.textContent = `暂时读不到它的凭据（${String((error && error.message) || '').trim() || '平台没有回应'}）。`;
          box.append(empty);
        });

    // 吊销是这一块唯一的写动作：只提交**指纹**，不提交凭据，也不自称是谁在操作
    // （平台按人自己的 token 认身份）。
    box.addEventListener('click', (event) => {
      const button = event.target.closest('[data-revoke]');
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      const item = button.closest('.flat-cred');
      const fingerprint = item && item.dataset.fingerprint;
      if (!fingerprint) return;
      button.disabled = true;
      button.textContent = '正在吊销…';
      api(`/harnesses/${encodeURIComponent(harnessId)}/credentials/revoke`, {
        fingerprint,
        // 不编理由：平台自己会记下"谁在什么时候收的"。界面再写一句就是自说自话。
      })
        .then((result) => {
          lastNote = (result && result.note) || '已吊销。';
        })
        .catch((error) => {
          lastNote = `没能吊销：${String((error && error.message) || '').trim() || '平台拒绝了这次请求'}`;
        })
        .then(() => load());
    });

    box.innerHTML = '<h3>它的平台凭据</h3><p class="flat-note">正在读它的凭据…</p>';
    void load();
    return box;
  }

  /** 一次 CLI 运行：它说了什么、它做了什么。 */
  function cliSessionCard(run, harnessId) {
    const card = document.createElement('details');
    card.className = 'flat-wtask flat-cli-session';
    // 折叠状态要记住：办公室每 15 秒重画一次，用户刚展开想读这段会话，
    // 下一轮就被"啪"地合上 —— 正在看的东西从手里跑掉。
    const foldKey = `cli-session:${harnessId}:${(run && run.kind) || ''}:${(run && run.key) || ''}`;
    card.dataset.fold = foldKey;
    const stdout = String((run && run.stdout) || '');
    const transcript = parseTranscript(stdout);
    const tools = foldRepeats(transcript.tools.slice(-6).reverse(), (tool) => tool.label);
    const headline = transcript.isJson ? readableText(headlineText(transcript)) : '';
    const steps = transcript.isJson ? [] : recentSteps(stdout, 6);
    const at = String((run && run.at) || '').replace('T', ' ').slice(5, 16);
    const kb = run && run.stdout_bytes ? ` · ${Math.max(1, Math.round(Number(run.stdout_bytes) / 1024))} KB` : '';
    const rawKey = `${foldKey}:raw`;
    card.innerHTML = `
      <summary>
        <b>${escape(String((run && run.label) || '一次运行'))}</b>
        <span class="flat-note">${escape(at)}${escape(kb)}</span>
      </summary>
      <div class="flat-wtask-body">
        ${
          headline
            ? `<p class="flat-note">它最后说了</p><pre class="flat-output">${escape(headline)}</pre>`
            : steps.length
              ? `<p class="flat-note">它刚做的（最近 ${steps.length} 步）</p><ul class="flat-list flat-live-steps">${[
                  ...steps,
                ]
                .reverse()
                .map(
                  (step) =>
                    `<li><span>${escape(clip(step.text, 140))}${
                      step.count > 1 ? ` <em>×${step.count}</em>` : ''
                    }</span></li>`,
                )
                .join('')}</ul>`
              : '<p class="flat-empty">（这一段没有可读的文字）</p>'
        }
        ${
          tools.length
            ? `<p class="flat-note">它做了什么（最近 ${tools.length} 步）</p><ul class="flat-list outputs-tools">${tools
                .map(
                  (tool) =>
                    `<li><span>${escape(tool.text)}${tool.count > 1 ? ` <em>×${tool.count}</em>` : ''}</span></li>`,
                )
                .join('')}</ul>`
            : ''
        }
        ${
          run && run.redacted_lines
            ? `<p class="flat-note">其中 ${Number(run.redacted_lines)} 行含疑似凭据，平台已遮挡。</p>`
            : ''
        }
        ${
          stdout
            ? `<details class="outputs-more-block" data-fold="${escape(rawKey)}"${
                state.openFolds.has(rawKey) ? ' open' : ''
              }><summary>查看原始记录</summary><pre class="outputs-raw">${escape(
                shortenPath(stdout.slice(-4000)),
              )}</pre></details>`
            : ''
        }
      </div>`;
    if (state.openFolds.has(foldKey)) card.open = true;
    return card;
  }

  function windowPane(harnessId) {
    const panel = document.createElement('section');
    panel.className = 'flat-detail flat-window';
    const info = (state.availability || []).find((item) => item.harness_id === harnessId) || null;
    // 这个项目里的成员（开工时定的 + 中途加进来的）。
    const projectMembers = new Set(
      [((projectOf && projectOf()) || {}).members || []].map((id) => String(id)),
    );
    const name = harnessName(harnessId) || harnessId;
    // 它做过的活：现在是它的，**或者**最后领过它的是它（合入后平台会清空 owner，
    // 只看 now 会让窗口页永远是空的 —— 与任务表的归堆口径必须一致）。
    const mine = (state.tasks || []).filter((task) => {
      const holders = holderIds(task);
      return holders.now === harnessId || holders.before === harnessId;
    });
    const running = mine.filter((task) => isRunning(task));
    const done = mine.filter((task) => DONE_STATES.has(String(task.status)));
    const failed = mine.filter((task) => FAILED_STATES.has(String(task.status)));
    const isAssistant = info ? isPlatformAssistantHarness(info) : false;

    const facts = [];
    facts.push(['状态', info ? (info.status === 'online' ? '在线' : '离线') : '未知']);
    // 还没在这个项目里的窗口：给一个「加入这个项目」——中途来的人也得能干活。
    const isMember = projectMembers.has(harnessId);
    // 管理员窗口不算「要加进来的成员」：它是管理员（开工时就定下了）。
    const isAdminWindow = harnessId === adminIdentity();
    const joinable =
      Boolean(info) && !isMember && !isAdminWindow && info.status === 'online';
    if (info && info.model) facts.push(['使用的模型', info.model]);
    if (info && info.degraded) facts.push(['降级', '该窗口当前处于降级状态']);
    if (info && info.usable === false) {
      const why = (info.reasons || []).map((r) => r.detail || r.code).filter(Boolean).join('；');
      facts.push(['无法领取任务的原因', why || '平台未登记凭据']);
    }
    facts.push(['本项目里的任务', `${mine.length} 条（执行中 ${running.length}、已完成 ${done.length}、未完成 ${failed.length}）`]);
    if (info && info.last_task_id) {
      facts.push([
        '最近一次尝试',
        `${info.last_task_id}（${STATE_LABEL[info.last_attempt_status] || info.last_attempt_status || '未知'}）`,
      ]);
    }

    panel.innerHTML = `
      <div class="flat-detail-head">
        <div>
          <h2>${escape(name)}</h2>
          <p class="flat-summary">${escape(windowStatusText(harnessId))}</p>
        </div>
        <div class="flat-detail-actions">
          ${state.memberAdded === harnessId ? '<span class="flat-note">已通知管理员</span>' : ''}
          ${isAssistant ? '<button type="button" data-act="assistant-detail">巡检与上报详情</button>' : ''}
          ${
            joinable
              ? `<button type="button" data-act="add-member" data-harness="${escape(harnessId)}">加入这个项目</button>`
              : isMember
                ? '<span class="flat-note">已在这个项目里</span>'
                : ''
          }
        </div>
      </div>
      <dl class="flat-facts">${facts.map(([k, v]) => `<dt>${escape(k)}</dt><dd>${escape(v)}</dd>`).join('')}</dl>`;

    // 现在正在做什么：读它正在跑的那条活的实时日志（与任务详情同一个端点）。
    if (running.length) {
      const task = running[0];
      const box = document.createElement('section');
      box.className = 'flat-spec flat-live';
      box.innerHTML = `<h3>当前正在执行</h3>
        <p class="flat-note">${escape(task.title || task.task_id)}</p>
        <pre class="flat-output">正在读它写的日志…</pre>`;
      panel.append(box);
      api(`/tasks/${encodeURIComponent(task.task_id)}/output`).then((data) => {
        const raw = data && data.output ? data.output : data;
        const stdout = typeof raw === 'string' ? raw : String(raw?.stdout || raw?.text || '');
        const pre = box.querySelector('.flat-output');
        if (!pre) return;
        if (!stdout) {
          pre.textContent = '（尚无输出）';
          return;
        }
        const steps = recentSteps(stdout);
        pre.textContent = steps.length
          ? steps.map((step) => step.text).join('\n')
          : readableText(headlineText(parseTranscript(stdout))) || stdout.slice(-1500);
      }).catch(() => {
        const pre = box.querySelector('.flat-output');
        if (pre) pre.textContent = '（暂时无法读取其日志）';
      });
    } else {
      const empty = document.createElement('p');
      empty.className = 'flat-empty';
      empty.textContent = '该窗口当前没有进行中的任务。';
      panel.append(empty);
    }

    // 这个窗口的活：**详细清单**。每条一张可展开的卡片 —— 收起时是
    // 「状态 + 标题 + 做到第几次」，点开就是这条活完整的目标原文。
    if (mine.length) {
      const section = document.createElement('section');
      section.className = 'flat-spec';
      const list = document.createElement('div');
      list.className = 'flat-wtasks';
      for (const task of [...mine].reverse()) list.append(windowTaskCard(task));
      section.innerHTML = '<h3>它做过的任务（展开可查看任务详情）</h3>';
      section.append(list);
      panel.append(section);
    }

    // 它自己的 CLI 会话：干活有（上面的「当前正在执行」），**对话也要有**。
    //
    // 这个窗口就是一台真的 CLI（Claude Code / Codex / OpenCode / Pi / Qoder
    // / CodeBuddy）。它审阅方案时同样在跑自己的 CLI，同样说了话、调了工具 ——
    // 但那些输出以前不留，于是这个面板只剩平台的记录，看不到它自己的会话。
    panel.append(cliSessionsPanel(harnessId));

    // token 度量：**两笔账**，各管各的。
    //   · 额度池记的 —— 走额度池的调用，平台自己记的（consumer = `harness/<id>`）。
    //   · 成员自报的 —— 成员用自带模型跑的活，平台不在那条链路上，用量由成员自己的
    //     CLI 报上来，平台从它的原始输出里解析。
    const metrics = windowMetrics(harnessId);
    const poolEntry = metrics.entry;
    const reportedEntry = metrics.reported;
    if (poolEntry || reportedEntry) {
      const rows = [];
      if (poolEntry && Number(poolEntry.requests || 0) > 0) {
        const total = Number(poolEntry.prompt_tokens || 0) + Number(poolEntry.completion_tokens || 0);
        rows.push(
          [
            '额度池：消耗总 token',
            `${groupDigits(total)}（输入 ${groupDigits(poolEntry.prompt_tokens)} · 输出 ${groupDigits(poolEntry.completion_tokens)} · 命中缓存 ${groupDigits(poolEntry.cached_tokens)}）`,
          ],
          [
            '额度池：缓存命中率',
            poolEntry.cache_hit_rate == null
              ? '（无数据）'
              : `${Math.round(Number(poolEntry.cache_hit_rate) * 100)}%`,
          ],
          [
            '额度池：每秒生成 token',
            poolEntry.generation_rate == null
              ? '（无数据）'
              : `${poolEntry.generation_rate} token/秒`,
          ],
          [
            '额度池：最近一小时 token',
            metrics.lastHour
              ? `${groupDigits(Number(metrics.lastHour.prompt_tokens || 0) + Number(metrics.lastHour.completion_tokens || 0))}（${String(metrics.lastHour.at).slice(0, 13).replace('T', ' ')} 那个小时）`
              : '（这一小时没有记录）',
          ],
        );
      }
      if (reportedEntry && Number(reportedEntry.requests || 0) > 0) {
        const total =
          Number(reportedEntry.prompt_tokens || 0) + Number(reportedEntry.completion_tokens || 0);
        rows.push(
          [
            '成员自报：消耗总 token',
            `${groupDigits(total)}（输入 ${groupDigits(reportedEntry.prompt_tokens)} · 输出 ${groupDigits(reportedEntry.completion_tokens)} · 命中缓存 ${groupDigits(reportedEntry.cached_tokens)}）`,
          ],
          [
            '成员自报：缓存命中率',
            reportedEntry.cache_hit_rate == null
              ? '（无数据）'
              : `${Math.round(Number(reportedEntry.cache_hit_rate) * 100)}%`,
          ],
          [
            '成员自报：最近一小时 token',
            metrics.reportedHour
              ? `${groupDigits(Number(metrics.reportedHour.prompt_tokens || 0) + Number(metrics.reportedHour.completion_tokens || 0))}（${String(metrics.reportedHour.at).slice(0, 13).replace('T', ' ')} 那个小时）`
              : '（这一小时没有记录）',
          ],
        );
      }
      if (rows.length) {
        const box = document.createElement('section');
        box.className = 'flat-spec';
        box.innerHTML = `<h3>用量</h3>
          <dl class="flat-facts">${rows
            .map(([key, value]) => `<dt>${escape(key)}</dt><dd>${escape(value)}</dd>`)
            .join('')}</dl>
          <p class="flat-note">两笔账各管各的：走额度池的调用由平台记账；成员用自带模型跑的那部分，用量由成员自己的 CLI 报上来，平台从它的原始输出里解析。命中率 = 命中缓存的 token ÷ 总输入（总输入含缓存）。认不出的写「无数据」，不填 0。</p>`;
        panel.append(box);
      }
    }

    // 该窗口使用的模型：自带，还是额度池里的某个模型。选了就照办。
    panel.append(modelChoiceBlock(harnessId));

    // 它的平台凭据：几条、哪条在用、哪些能收回。放在模型之后、动作记录之前 ——
    // 它回答的是"这个窗口凭什么能开工"，与"它做过什么"是前后两步。
    panel.append(windowCredentialsPanel(harnessId));

    // 它的轨迹：平台事件里 actor 是它的那几条。
    const trace = (state.events || [])
      .filter((event) => event.actor_id === harnessId)
      .slice(-8)
      .reverse();
    const traceBox = document.createElement('section');
    traceBox.className = 'flat-spec';
    traceBox.innerHTML = `<h3>它做过的动作</h3>
      <p class="flat-note">平台记录的本窗口动作：领取过哪些任务、做过哪些修改、何时出现问题。最新在前。</p>
      ${
        trace.length
          ? `<ol class="flat-events">${trace
              .map((event) =>
                eventRow(event, {
                  subject: taskTitleOf(event.aggregate_id),
                  jumpTaskId: knownTaskId(event.aggregate_id),
                }),
              )
              .join('')}</ol>`
          : '<p class="flat-empty">暂无动作记录。</p>'
      }`;
    panel.append(traceBox);

    return panel;
  }

  /**
   * 左栏窗口行右键：给这一家 Harness 再开一个窗口。
   *
   * 为什么在这里做：开屏那个清单只报"几家 Harness、各几个窗口"—— 那一步是
   * 决定"这个项目用谁"；具体再开几个窗口，是进了项目、看着这个项目的情况才
   * 决定的事。所以新增入口放在这里，右键那一家就行。
   *
   * 名字按人说的来：`<Harness 名> · 新增1`、`新增2`。
   */
  function addMemberMenu() {
    const menu = document.createElement('div');
    menu.className = 'flat-ctxmenu';
    menu.hidden = true;
    menu.setAttribute('role', 'menu');
    document.body.append(menu);
    let target = '';
    const close = () => {
      menu.hidden = true;
      target = '';
    };
    // 用 pointerdown 关，不用 click：右键之后浏览器还会补一个事件，
    // 用 click 会把**刚打开**的菜单立刻关掉（菜单弹不出来就是这么来的）。
    // pointerdown 时菜单还没建出来，所以这一下不会误关。
    document.addEventListener('pointerdown', (event) => {
      if (menu.hidden) return;
      if (menu.contains(event.target)) return;
      close();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') close();
    });
    window.addEventListener('resize', close);
    root.addEventListener('scroll', close, true);
    menu.addEventListener('click', (event) => {
      const button = event.target.closest('[data-ctx-add]');
      if (!button) return;
      const harnessId = target;
      close();
      if (harnessId) void addMemberWindow(harnessId);
    });
    return {
      open(harnessId, x, y) {
        target = harnessId;
        const brand = baseHarnessOf(harnessId);
        menu.innerHTML = `<button type="button" data-ctx-add>给「${escape(
          brandLabelOf(brand, harnessId),
        )}」再开一个窗口</button>`;
        menu.hidden = false;
        // 贴边时往里收一点，别让菜单跑到屏幕外。
        const rect = menu.getBoundingClientRect();
        const left = Math.min(x, window.innerWidth - rect.width - 8);
        const top = Math.min(y, window.innerHeight - rect.height - 8);
        menu.style.left = `${Math.max(8, left)}px`;
        menu.style.top = `${Math.max(8, top)}px`;
        const first = menu.querySelector('button');
        if (first) first.focus();
      },
      close,
    };
  }

  const memberMenu = addMemberMenu();

  /** 这一家 Harness 叫什么（名册里的登记名优先，否则退回来实例名去掉后缀）。 */
  function brandLabelOf(brand, sampleId) {
    // 品牌自己那条登记（`qoder` → 「Qoder CLI CN」）在平台的注册表里；
    // 窗口名单（availability）里只有实例，没有品牌这一条。
    const registered = (state.status && state.status.harnesses) || [];
    const own = registered.find((item) => String(item.harness_id || '') === brand);
    if (own && own.name) return own.name;
    const roster = (state.availability || []);
    const sample = roster.find((item) => String(item.harness_id || '') === sampleId);
    const name = String((sample && sample.name) || '');
    if (name) return name.split('·')[0].trim() || brand;
    return brand;
  }

  /**
   * 给这一家再开一个窗口：写进平台注册表，然后加进这个项目。
   *
   * 说清楚两件做不到的事（不假装）：新窗口**还没有凭据**、也**还没有 worker**
   * 在跑 —— 平台从浏览器里签不了凭据，也不会替人起进程。所以它注册完会显示
   * 「暂时无法领取任务」，要人按现有流程给它配凭据、起 worker。
   */
  async function addMemberWindow(harnessId) {
    const brand = baseHarnessOf(harnessId);
    const label = brandLabelOf(brand, harnessId);
    const roster = state.availability || [];
    // 同品牌已有的窗口：取一个没被占的号，再数一下已经叫「新增」的有几个。
    const used = new Set(
      roster
        .map((item) => String(item.harness_id || ''))
        .filter((id) => baseHarnessOf(id) === brand),
    );
    let slot = 1;
    while (used.has(`${brand}-member-${slot}`)) slot += 1;
    const instanceId = `${brand}-member-${slot}`;
    const created = roster.filter((item) =>
      String((item && item.name) || '').includes('新增'),
    ).length;
    const name = `${label} · 新增${created + 1}`;
    try {
      await api('/harnesses', {
        source_harness_id: brand,
        instance_id: instanceId,
        name,
      });
    } catch (error) {
      state.error = `没能给「${label}」开新窗口：${String((error && error.message) || error)}`;
      render();
      return;
    }
    // 注册成功：加进这个项目（与「加入这个项目」同一个动作）。
    addMemberToProject(instanceId);
    // 再让它**真能干活**：没凭据就签发、没 worker 就起一个它自己的。
    // 这一步失败不能说成成功——人以为新窗口能用了，实际领不到活。
    try {
      const started = await api('/harnesses/provision', { harness_id: instanceId });
      state.memberNote = started && started.started
        ? `${name} 已经能接活了：${String(started.note || '')}`
        : `${name} 已加进项目；${String((started && started.reason) || '它本来就在跑')}。`;
    } catch (error) {
      state.memberNote = `${name} 已加进项目，但还没能开始干活：${String(
        (error && error.message) || error,
      )}（凭据或 worker 没起来，它现在领不到活）`;
    }
    state.error = null;
    state.memberAdded = instanceId;
    await refresh();
  }

  function body(filtered, pager) {
    const wrap = document.createElement('div');
    wrap.className = 'flat-body';
    wrap.append(conversations(), conversationPane(filtered, pager));
    return wrap;
  }

  /** 任务表：筛选、按人归堆、分页。（现在住在中间那一栏） */
  function taskTable(filtered, pager) {
    const list = document.createElement('section');
    list.className = 'flat-tasks';
    // 一条任务都没有时，「全部 / 待领取 / 执行中 / 按人 / 排序 / 每页」全是**没有对象的
    // 开关**：第一次用的人一进来先面对八个按钮，却不知道该按哪个。没有活可筛就收起来。
    const hasAnyTask = (state.tasks || []).length > 0;
    const bar = document.createElement('div');
    bar.className = 'flat-taskbar';
    const filters = document.createElement('div');
    filters.className = 'flat-filters';
    for (const [key, label] of [
      ['all', '全部'],
      ['waiting', '待领取'],
      ['active', '执行中'],
      ['review', '待处理'],
      ['failed', '未完成'],
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

    // 两种看法：**按人**归堆（谁忙谁闲）or 平铺按时间排。
    // 默认按人 —— 办公室的日常问题是"今天该找谁""这个窗口是不是压了一堆活"，
    // 这两个问题按状态排答不上来。
    const viewWrap = document.createElement('div');
    viewWrap.className = 'flat-view';
    for (const [value, label] of [['person', '按人'], ['time', '按时间']]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.setAttribute('aria-pressed', String(state.groupBy === value));
      button.addEventListener('click', () => {
        state.groupBy = value;
        state.page = 1;
        render();
      });
      viewWrap.append(button);
    }

    // 工具（看法、排序、每页）排成一行靠右，别各占一行散在下面。
    const tools = document.createElement('div');
    tools.className = 'flat-tools';
    tools.append(viewWrap, sortWrap);
    bar.append(tools);

    if (hasAnyTask) list.append(bar);

    if (!filtered.length) {
      const empty = document.createElement('p');
      empty.className = 'flat-empty';
      empty.textContent = hasAnyTask
        ? '当前筛选条件下没有任务。'
        : '当前项目暂无任务。'
      list.append(empty);
      if (!hasAnyTask) {
        // 一个任务都没有时，这一栏是**最大的一块空白**，也是第一次用的人最先看的
        // 地方。原先这句话长在详情栏的空状态里，可详情栏只有选中一条活才出现 ——
        // 空项目反而看不到它。放到任务表这一侧才对。
        const hint = document.createElement('p');
        hint.className = 'flat-empty';
        hint.textContent = '任务将在此处展示：当前执行者、执行进度与失败原因。';
        list.append(hint);
        const start = document.createElement('button');
        start.type = 'button';
        start.className = 'flat-start';
        start.dataset.act = 'start-project';
        start.textContent = '配置目标';
        list.append(start);
        const note = document.createElement('p');
        note.className = 'flat-note';
        // 不重复顶部那句话：这里只解释**这个按钮**会带来什么。
        note.textContent = '将打开顶部「开工配置」。只需描述目标，无需事先拆解步骤。';
        list.append(note);
      }
    }

    // 列表：按「人」归堆（默认），或者平铺按时间排。
    if (state.groupBy === 'person') {
      for (const group of groupByPerson(filtered)) {
        const box = document.createElement('section');
        box.className = 'flat-group';
        box.dataset.group = group.key || 'none';
        const mood = groupMood(group);
        const facts = groupFacts(group);
        const head = document.createElement('header');
        head.className = 'flat-group-head';
        head.innerHTML = `
          <span class="flat-group-name">${escape(group.name)}</span>
          <span class="flat-group-mood ${mood.kind}">${escape(mood.text)}</span>
          ${facts ? `<span class="flat-group-facts">${escape(facts)}</span>` : ''}`;
        box.append(head);
        const groupBody = document.createElement('div');
        groupBody.className = 'flat-group-body';
        for (const task of group.tasks) groupBody.append(taskRow(task, { hideWho: Boolean(group.key) }));
        box.append(groupBody);
        list.append(box);
      }
    } else {
      for (const task of filtered) list.append(taskRow(task));
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
    // 没有东西可翻的时候，这一行只剩「第 0–0 条，共 0 条」和两个点不动的按钮。
    if (hasAnyTask) list.append(pagerBar);

    return list;
  }

  /**
   * 详情上方的紧凑活列表：换看另一条活不用先回任务表。
   *
   * 用的是**任务表同一份筛选结果**（state.filtered）—— 详情里不该摆着
   * 一条被筛选掉的活。超过 12 条只摆前 12 条，剩下的说一句。
   */
  function taskSwitcherHtml() {
    const all = state.filtered || [];
    const current = state.selected ? state.selected.task_id : '';
    let list = all.slice(0, 12);
    // 当前正在看的那条**必须在列表里**：它排在 12 条之外的话，既不会被标出来，
    // 也点不回来。把它换到末位。
    if (current && !list.some((task) => task.task_id === current)) {
      const hit = all.find((task) => task.task_id === current);
      if (hit) list = [...list.slice(0, 11), hit];
    }
    if (list.length < 2) return '';
    const more = all.length - list.length;
    return `<section class="flat-switcher">
      <p class="flat-note">本项目的任务（点选即可切换）</p>
      <div class="flat-switcher-row">${list
        .map(
          (task) => `<button type="button" class="flat-switch${
            task.task_id === current ? ' is-on' : ''
          }" data-open-task="${escape(task.task_id)}">
            <b>${escape(titleParts(task).main || task.title || task.task_id)}</b>
            <span>${escape(STATE_LABEL[task.status] || task.status)}</span>
          </button>`,
        )
        .join('')}</div>
      ${more > 0 ? `<p class="flat-note">还有 ${more} 条，用「← 返回任务表」看全部。</p>` : ''}
    </section>`;
  }

  function detail() {
    const panel = document.createElement('section');
    panel.className = 'flat-detail';
    if (!state.selected) {
      const hint = document.createElement('p');
      hint.className = 'flat-empty';
      hint.textContent = '选择一条任务以查看其规格、动作记录与 CLI 原话。';
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
      <button type="button" class="flat-back" data-act="back-tasks">← 返回任务表</button>
      ${taskSwitcherHtml()}
      <div class="flat-detail-head">
        <div>
          <span class="flat-chip flat-chip-${escape(task.status)}">${escape(STATE_LABEL[task.status] || task.status)}</span>
          <h2>${escape(titleParts(task).main)}</h2>
          ${titleParts(task).sub ? `<p class="flat-detail-sub">${escape(titleParts(task).sub)}</p>` : ''}
          <p class="flat-summary">${escape(statusSentence(task))}</p>
          <p class="flat-mono">${escape(detailHolds(task))}</p>
        </div>
        <div class="flat-detail-actions">${humanActions(task)}</div>
      </div>
      ${lineageBlock(task)}
      ${spec.goal ? `<section class="flat-spec"><h3>任务目标</h3><p>${escape(spec.goal)}</p></section>` : ''}
      ${handoffBlock(spec)}
      ${acceptance ? `<section class="flat-spec"><h3>验收标准</h3><p>${escape(acceptance)}</p></section>` : ''}
      ${paths.length ? `<section class="flat-spec"><h3>允许修改的路径</h3><ul>${paths.map((p) => `<li><code>${escape(p)}</code></li>`).join('')}</ul></section>` : ''}
      ${files.length ? `<section class="flat-spec"><h3>这次改了什么</h3><ul>${files.map((f) => `<li><code>${escape(typeof f === 'string' ? f : f.path || JSON.stringify(f))}</code></li>`).join('')}</ul></section>` : ''}
      <section class="flat-spec"><h3>动作记录</h3>
        <p class="flat-note">${events.length ? `一共 ${events.length} 步，新的在最上面。点开每一步看细节。` : '暂无记录。'}</p>
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
      <p class="flat-detail-foot">以上就是该任务的全部记录。想看整体，去右栏的「待办事项」和「查看成员执行情况」。</p>`;
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
    // 平台的任务标题是「项目名 · 任务名」。scope 手里只有项目 id，对不上名字时
    // 就按**第一段**剥 —— 否则一屏里三条任务顶着同一个长标题，像复制粘贴的。
    const withoutProject =
      trimmed === title && title.includes(' · ') ? title.slice(title.indexOf(' · ') + 3).trim() : trimmed;
    return stripHarnessSuffix(withoutProject);
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
    const worker = /^formal worker error:\s*(\w+)/i.exec(text);
    if (worker) return `平台内部出错了（${worker[1]}）`;
    const exit = /^harness CLI exited (\d+)$/.exec(text);
    if (exit) return `窗口命令已退出（退出码 ${exit[1]}）`;
    if (/^harness timeout:/.test(text)) return '窗口执行超时，未在时限内完成';
    if (/quota|rate.?limit|insufficient/i.test(text)) return '额度已用尽';
    if (/^(provider|upstream)/i.test(text)) return '模型服务异常';
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
        return '该任务正在等待你的回答。回答后将交回执行成员继续处理。';
      case 'escalated':
        return '该任务已阻塞，平台已转交由你处理：可重新分派，或直接取消。';
      case 'ready':
        return '该任务尚无人领取。可请管理员分派。';
      case 'queued':
        return '该任务正在排队，等待可用资源。';
      case 'claimed':
        return '已有人领取，正在准备执行。';
      case 'in_progress':
        return `${who.now || '有人'}正在执行该任务。`;
      case 'verifying':
        return '已完成提交，正在自动校验结果。';
      case 'awaiting_review':
        return '已完成提交，正在等待审核。';
      case 'changes_requested':
        return '审核方要求返工，正在退回。';
      case 'approved':
        return '审核通过，正在合入主分支。';
      case 'merging':
        return '正在合入主分支。';
      case 'merged':
        return '已合入主分支，任务完成。';
      case 'abandoned':
        return failure ? `任务已放弃：${failure}` : '任务已放弃。';
      case 'stale':
        return '执行窗口失联，任务已阻塞。';
      case 'cancelled':
        return '任务已取消，不会再继续执行。';
      case 'plan_drift':
        return '需求已变更，该任务需重新确认。';
      case 'impl_drift':
        return '执行结果与需求不符，需人工确认。';
      case 'blocked':
        return '该任务因异常无法继续。';
      case 'needs_help':
        return '执行成员正在求助，等待协助。';
      case 'reclaiming':
        return '正在重新分派该任务。';
      default:
        return '该任务仍在进行中。';
    }
  }

    /** 这条事件真的落在办公室已知的一条活上吗（方案级事件的聚合号不是活）。 */
  function knownTaskId(taskId) {
    const id = String(taskId || '');
    if (!id) return '';
    return (state.tasks || []).some((task) => String(task.task_id) === id) ? id : '';
  }

  /**
   * 活的名字（人起的）。
   *
   * 取自办公室已经有的任务表；取不到就退回编号 —— **不编**。
   */
  function taskTitleOf(taskId) {
    const id = String(taskId || '');
    if (!id) return '';
    const task = (state.tasks || []).find((item) => String(item.task_id) === id);
    if (!task) return id;
    const title = String(listTitle(task) || '').trim();
    return title && title !== id ? title : id;
  }

/**
   * 事件的折叠键：同一条事件在重画之后还得是同一个键。
   *
   * 用聚合号 + 序号 + 事件名 + 时间拼：序号没有时（本地样例就没有），
   * 事件名加时间也能把两条不同的动作分开。
   */
  function eventFoldKey(event) {
    const id = String(event.aggregate_id || '');
    const seq = event.seq == null ? '' : String(event.seq);
    const name = String(event.event_name || '');
    const at = String(event.created_at || '');
    return `event:${id}:${seq}:${name}:${at}`;
  }

/**
   * 一条事件：标题行是骨架，**payload 才是这件事的细节**。
   * "改派给了谁"、"为什么失败"都在 payload 里，光看事件名等于没看见。
   */
  function eventRow(event, options = {}) {
    const payload = event.payload && typeof event.payload === 'object' ? event.payload : null;
    const hint = eventHint(event);
    const fields = payload ? payloadFields(payload) : [];
    // 动作 + 对象才是一句话：「批改了「收一条真实任务」」—— 只写「批改了」没人读得懂。
    const subject = options.subject ? `<span class="flat-event-subject">${escape(options.subject)}</span>` : '';
    // 动作与活之间要**有路**：点这一行就能去看那条活的详情。
    const jump = options.jumpTaskId
      ? `<button type="button" class="flat-event-jump" data-open-task="${escape(options.jumpTaskId)}">查看任务详情</button>`
      : '';
    // 事件列表是给人看"这条活经历了什么"的：时间、人话的动作、谁做的。
    // 事件编号（seq）留在 title 里 —— 它是给查问题的人看的，不是给人读的。
    const who = actorName(event);
    // 同一条事件在重画之后还得是同一个键，否则用户展开的那一行会被合上。
    const foldKey = eventFoldKey(event);
    return `<li class="flat-event">
      <details data-fold="${escape(foldKey)}"${state.openFolds.has(foldKey) ? ' open' : ''}>
        <summary title="事件编号 ${escape(event.seq ?? '')} · ${escape(timeOf(event.created_at))}${event.event_name && eventName(event) !== event.event_name ? ` · 原文 ${escape(event.event_name)}` : ''}">
          <span class="flat-event-seq">${escape(timeShort(event.created_at))}</span>
          <b>${escape(eventName(event))}</b>
          ${subject}
          <em>${escape(who)}</em>
          ${hint ? `<span class="flat-event-hint">${escape(hint)}</span>` : ''}
          ${jump}
        </summary>
        ${fields.length
          ? `<dl class="flat-event-payload">${fields
              .map(([key, value]) => `<dt>${escape(key)}</dt><dd>${escape(value)}</dd>`)
              .join('')}</dl>`
          : '<p class="flat-empty">该事件没有可展示的细节。</p>'}
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
      // 机器字段不摆（哈希、commit、内部编号）—— 它们对人没有意义。
      if (MACHINE_ONLY_FIELDS.has(key)) continue;
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
  /**
   * 「上一次未完成，窗口回传的现场信息」。
   *
   * 上一轮失败时，平台把现场报告（错误、CLI 自己的话、动过哪些文件）**追加到工单
   * 后面** —— 那是给**干活的人**看的：接手的人要靠它接着做。人看它没用：真数据里
   * 那条工单有 2957 字，后面 2900 字是一整份 CLI 报错转储。
   *
   * 所以把它单独摆出来，并且**用人话说一句**：谁做的、哪儿错了、有没有产出。
   * 这些都是现场报告里的**结构化字段**——不用问模型（问模型反而会编）。
   * 原文收在后面，想查的人自己展开。
   */
  function handoffBlock(spec) {
    const report = spec && spec.handoff;
    if (!report || typeof report !== 'object') return '';
    const bits = [];
    const who = report.failed_harness ? harnessName(report.failed_harness) : '';
    if (who) bits.push(`上一次由 ${who} 执行`);
    // 网关已经翻过一遍（平台的规则表）；翻不出来就用本地兜底。
    const error = report.error_human || reasonText(report.error || '');
    if (error) bits.push(error);
    const touched = Array.isArray(report.files_touched) ? report.files_touched : [];
    bits.push(touched.length ? `改动 ${touched.length} 个文件` : '未产出文件');
    // 它自己最后说的话：CLI 的原话是一段机器日志（JSON 行），用办公室现成的
    // 解析器挑出那句**人话**（"API Error: 503 No available accounts"），
    // 原话收在后面 —— 跟输出页用的是同一个解析器。
    const said = String(report.cli_stdout_tail || report.cli_stderr_tail || '').trim();
    const saidHuman = said ? readableText(headlineText(parseTranscript(said))) : '';
    return `<section class="flat-spec flat-handoff">
      <h3>上一次未完成，窗口回传的现场信息</h3>
      <p>${escape(bits.join('，'))}。</p>
      ${saidHuman ? `<p class="flat-handoff-said">其最后输出为：${escape(saidHuman)}</p>` : ''}
      ${said ? `<details class="flat-tech"><summary>看它自己的原话</summary><pre>${escape(said.slice(-1200))}</pre></details>` : ''}
      ${spec.handoff_raw ? `<details class="flat-tech"><summary>看原始现场（机器记的）</summary><pre>${escape(spec.handoff_raw)}</pre></details>` : ''}
    </section>`;
  }

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
      ? `<h4>该任务的每一次尝试</h4>
        <ol class="flat-attempts">${attempts
          .map((row) => `<li>
            <span class="flat-attempt-no" title="${escape(row.id || '')}">${row.no != null ? `第 ${row.no} 次` : '第 ? 次'}</span>
            <span class="flat-attempt-owner">${escape(row.owner || '领取方未知')}</span>
            <span class="flat-attempt-outcome">${escape(row.outcome || '（无记录）')}</span>
            <span class="flat-attempt-at">${escape(timeOf(row.at))}</span>
            ${row.revisedFrom ? `<span class="flat-attempt-from">由上一轮转入</span>` : ''}
          </li>`)
          .join('')}</ol>`
      : '';

    // 什么也没有就别画一个空框（"任务来源与交接"里只留一个标题，看着像坏了）。
    if (!chainHtml && !attemptHtml) return '';
    return `<section class="flat-spec flat-lineage">
      <h3>任务来源与交接</h3>
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
        row.outcome = '已提交';
      } else if (name === 'verify_passed') {
        row.outcome = '校验通过';
      } else if (name === 'abandon') {
        row.outcome = `未提交：${reasonText(event.payload?.reason) || '（没写理由）'}`;
      } else if (name === 'release' || name === 'human.release') {
        row.outcome = `退回队列：${event.payload?.reason || '（没写理由）'}`;
      }
    }
    // 被要求返修的那一轮：平台把返修记在评审上，链接在下一轮的 revised_from。
    for (const row of rows.values()) {
      if (!row.revisedFrom) continue;
      const previous = rows.get(row.revisedFrom);
      if (!previous) continue;
      previous.outcome =
        previous.outcome === '已提交'
          ? '已提交，但被要求返修'
          : `${previous.outcome || '已执行'}，随后被要求返修`;
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
      <h3>最后一次输出</h3>
      ${task.last_failure?.reason ? `<p class="flat-note">平台记下的原因：${escape(task.last_failure.reason)}</p>` : ''}
      ${task.last_failure?.at ? `<p class="flat-note">失败于 ${escape(timeOf(task.last_failure.at))}</p>` : ''}
      ${facts ? `<p class="flat-note">${escape(facts)}</p>` : ''}
      ${stderr ? `<h4>报错内容</h4><pre class="flat-output">${escape(stderr.slice(-4000))}</pre>` : ''}
      ${stdout ? `<h4>输出内容</h4><pre class="flat-output">${escape(stdout.slice(-4000))}</pre>` : ''}
      ${!stderr && !stdout ? '<p class="flat-empty">本轮没有留下任何输出（进程被终止，或未产生输出）。</p>' : ''}
    </section>`;
  }

  /** CLI 原话：平台的 /output 形状是 {output: {stdout, stderr}}，两边都容一下。 */
  function outputText() {
    const raw = state.output && typeof state.output === 'object'
      ? state.output.output || state.output
      : state.output;
    const text = typeof raw === 'string' ? raw : String(raw?.stdout || raw?.text || '');
    return text.slice(-4000) || '（尚无输出）';
  }

  /**
   * 「成员输出」：机器日志先给人话（结论 + 做了什么），原文收在后面。
   * 和「各个窗口都说了什么」那个页面用的是同一个解析器（`cli-transcript.js`）。
   */
  /**
   * 这条活**正在跑**吗？
   *
   * 正在跑的活是"活"的：有人在做，输出文件在长。页面上要能看出它在自己动
   * （心跳点 + 持续刷新的输出），而不是一条静止的记录。
   */
  function isRunning(task) {
    if (!task) return false;
    if (['claimed', 'in_progress', 'verifying'].includes(String(task.status))) return true;
    if (DONE_STATES.has(String(task.status))) return false;
    return ['abandoned', 'stale', 'cancelled', 'escalated', 'awaiting_human'].includes(String(task.status))
      ? false
      : Boolean(task.owner);
  }

  /** 正在跑的那条活，输出文件最后一次被写是什么时候。 */
  function liveStamp() {
    const raw = state.output && typeof state.output === 'object'
      ? state.output.output || state.output
      : null;
    const at = raw && raw.updated_at ? String(raw.updated_at) : '';
    if (!at) return '尚无输出';
    const ago = timeAgo(at);
    return ago === '刚刚' ? '刚刚还在动' : `${ago}还有动静`;
  }

  function liveOutputBlock() {
    const raw = state.output && typeof state.output === 'object'
      ? state.output.output || state.output
      : state.output;
    const stdout = typeof raw === 'string' ? raw : String(raw?.stdout || '');
    const transcript = parseTranscript(stdout);
    const running = isRunning(state.selected);
    const title = running ? '当前正在执行' : '成员输出';
    const stamp = running
      ? `<p class="flat-live-stamp"><i class="flat-pulse"></i>${escape(liveStamp())} · 每 4 秒自动刷新</p>`
      : '';
    if (transcript.isJson && transcript.texts.length) {
      const tools = foldRepeats(transcript.tools.slice(-6).reverse(), (tool) => tool.label);
      return `<section class="flat-spec flat-live" id="flatLive">${stamp}
        <h3>${title}${state.output?.output?.current ? '（当前这一轮）' : ''}</h3>
        <p class="flat-note">它最后说了</p>
        <pre class="flat-output">${escape(readableText(headlineText(transcript)) || '（这段里没有可读的文字）')}</pre>
        ${tools.length ? `<p class="flat-note">它做了什么（最近 ${tools.length} 步）</p><ul class="flat-list outputs-tools">${tools.map((tool) => `<li><span>${escape(tool.text)}${tool.count > 1 ? ` <em>×${tool.count}</em>` : ''}</span></li>`).join('')}</ul>` : ''}
        <details class="outputs-more-block"><summary>查看原始记录</summary><pre class="outputs-raw">${escape(stdout.slice(-4000))}</pre></details>
      </section>`;
    }
    // 不是结构化日志（就是一段纯文本）：**别把整段倒出来**。
    //
    // 活的输出是每几秒长一行的，一屏几十行几乎一样 —— 看着像卡带，人根本
    // 读不出"它做到哪了"。这里只给**最近几步**，连着重复的折成一条（×N）。
    const steps = recentSteps(stdout);
    return `<section class="flat-spec flat-live" id="flatLive">${stamp}
      <h3>${title}</h3>
      ${
        steps.length
          ? `<p class="flat-note">它刚做的（最近 ${steps.length} 步，新的在上面）</p>
             <ul class="flat-list flat-live-steps">${[...steps]
               .reverse()
               .map(
                 (step) =>
                   `<li><span>${escape(clip(step.text, 140))}${
                     step.count > 1 ? ` <em>×${step.count}</em>` : ''
                   }</span></li>`,
               )
               .join('')}</ul>`
          : '<p class="flat-empty">尚无输出。</p>'
      }
      ${stdout ? '<details class="outputs-more-block"><summary>查看原始记录</summary><pre class="outputs-raw">' + escape(shortenPath(stdout.slice(-4000))) + '</pre></details>' : ''}
    </section>`;
  }

  /**
   * 最近的若干步：**连着重复的折成一条**。
   *
   * worker 干活时日志是一行行长出来的，其中常常连着好几行一样（进度、重试、
   * 同一句话重复打印）。整段倒出来，人只看到一片重复 —— 所以这里按"连续
   * 相同"折叠（×N），再只留最后几步。
   */
  function recentSteps(stdout, limit = 8) {
    const lines = String(stdout || '')
      .split(/\r?\n/)
      // 绝对路径压成短形式：人要的是「在哪儿做了什么」，不是那条路径。
      .map((line) => shortenPath(line).trim())
      .filter(Boolean);
    return foldRepeats(lines, (line) => line).slice(-limit);
  }

  /** 连续相同的折成一条（×N）。 */
  function foldRepeats(items, keyOf) {
    const folded = [];
    for (const item of items) {
      const text = String(keyOf(item) ?? '');
      const last = folded[folded.length - 1];
      if (last && last.key === stepKey(text)) {
        last.count += 1;
        last.text = text;
        continue;
      }
      folded.push({ text, key: stepKey(text), count: 1, item });
    }
    return folded;
  }

  /**
   * 一行日志的"内容"（**把计数拿掉**）。
   *
   * worker 写出来的东西常常是"第 12 步：改代码"这样带计数的行 —— 逐行看每一行
   * 都不同，但**说的是同一件事**。只按原文比，看不出重复；所以比的是去掉计数
   * 之后的内容。
   */
  function stepKey(text) {
    return String(text || '')
      .replace(/^\s*(第\s*)?\d+\s*(步|行|次|个|项)?\s*[：:、,，.。\-–]?\s*/, '')
      .replace(/[\s#]*\d+\s*$/, '')
      .trim();
  }

  /** 人自己的动作。平台与管理员 harness 都不代劳这些。 */
  function humanActions(task) {
    const spent = Number(task.attempt_no || 0) >= Number(task.max_attempts || 0);
    const buttons = [];
    // 尚无人领取的：请管理员派出去。派给谁是管理员的决定，
    // 办公室只负责把这个请求送到管理员面前（群里的 @mention）。
    if (!task.owner && task.status === 'ready') {
      buttons.push(
        `<button type="button" data-act="ask-dispatch" title="这是一条 @管理员 的群消息；派给谁由管理员决定">交给管理员分派</button>`,
      );
    }
    if (spent) {
      buttons.push(
        `<button type="button" data-act="override">追加一次尝试</button>`,
      );
    }
    buttons.push(`<button type="button" data-act="reassign">请管理员改派</button>`);
    // 处置动词：回答提问、放行、重开、取消。
    //
    // 这些是**人自己的动作**（R10 §18），旧的人操作面里有过（已随 Vue 控制台
    // 一起删掉），迁到 Agent Office 时整批丢了：于是"等人回答"的任务在界面上
    // 是个死状态 —— 人只能发条群消息，平台不会把它当成决策。
    // 状态集合见 `human-actions.js`，与后端守卫一一对应；后端仍是唯一裁判。
    //
    // 按钮上写的是**人话**：平台那边叫"放行回队列"，人只知道"重新分派"。
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
    return key ? `你的 Key「${key.label || '未命名'}」` : '自有 Key';
  }

  /**
   * 这台机器上装了哪些 Harness CLI。
   *
   * 平台那一侧只做两件事：给出官方安装方式（纯数据，很快），以及在人对它说
   * 「探一下」之后跑每条 CLI 的 `--version`（六个子进程，几秒）。所以：
   *
   *   · 进页面时只取**安装方式**（`probe_local=false`），界面立刻有内容；
   *   · 状态默认是「还没探测」—— 不探测就说「缺」，等于替这台机器撒谎；
   *   · 只有人点了按钮才真的去跑版本命令。
   */
  async function loadInstalls({ probe = false } = {}) {
    if (probe) {
      state.installsChecking = true;
      state.installsError = null;
      render();
    }
    try {
      const data = await api(`/installers${probe ? '' : '?probe_local=false'}`);
      if (data && Array.isArray(data.installers)) {
        state.installs = data;
        state.installsError = null;
      }
    } catch (error) {
      // 读不到就说读不到（可能是旧后端没有这条路），不假装成"没装"。
      state.installsError = String(error.message || error);
    } finally {
      state.installsChecking = false;
    }
    render();
  }

  /**
   * 「装 CLI」这张卡：**谁缺、官方从哪装**。
   *
   * 放在右栏靠前的位置，因为它是新电脑上的第一步 —— 没有 CLI，
   * 后面的窗口、任务、传阅都无从谈起。
   */
  function installCard() {
    const payload = state.installs;
    if (!payload && !state.installsError) {
      return `<section class="flat-card"><h3>Harness CLI</h3><p class="flat-empty">正在读取各 CLI 的官方安装方式…</p></section>`;
    }
    if (state.installsError) {
      return `<section class="flat-card"><h3>Harness CLI</h3>
        <p class="flat-empty">读不到安装清单：${escape(state.installsError)}</p>
        <p class="flat-note">这条路要平台后端一起更新；本地样例模式没有安装清单。</p></section>`;
    }
    const rows = rowsView(payload);
    const groups = groupByStatus(rows);
    const open = state.openFolds.has('installs');
    const missing = Number((payload.summary && payload.summary.missing) || 0);
    return `<section class="flat-card flat-card-fold">
      <details data-fold="installs"${open ? ' open' : ''}>
        <summary>Harness CLI · ${escape(summaryLine(payload))}</summary>
        <p class="flat-note">${escape(hostLine(payload))}${
          payload.guidanceOnly ? '' : ' 安装后需要重开终端，PATH 才会生效。'
        }</p>
        <p class="flat-note">安装命令给人在自己的终端里执行；平台不会替你下载或运行安装脚本。</p>
        ${groups
          .map(
            (group) => `<details class="flat-pool-item">
              <summary><b>${escape(statusOf({ status: group.status }).label)}</b><span>${group.rows.length} 个</span></summary>
              <ul class="flat-list">${group.rows.map(installRow).join('')}</ul>
            </details>`,
          )
          .join('')}
        <p class="flat-inline-actions">
          <button type="button" data-act="installs-probe" ${state.installsChecking ? 'disabled' : ''}>${
            state.installsChecking ? '正在探测…' : '探测这台机器'
          }</button>
        </p>
        ${
          missing
            ? `<p class="flat-note">还有 ${missing} 个没装：装完再点一次「探测这台机器」，状态会跟着变。</p>`
            : ''
        }
      </details>
    </section>`;
  }

  /** 一条 CLI 的展开内容：官方地址、安装命令、验证命令、装完还差什么。 */
  function installRow(view) {
    const bits = [];
    if (view.version) bits.push(`版本 ${view.version}`);
    if (view.foundCommand) bits.push(`命令 ${view.foundCommand}`);
    if (view.path) bits.push(shortenPath(view.path));
    const hint = leftoverHint(view);
    return `<li>
      <b>${escape(view.name)}</b>
      <span>${escape(bits.join(' · ') || view.provider || '')}</span>
      ${view.detail ? `<span class="flat-note">${escape(view.detail)}</span>` : ''}
      ${
        view.officialUrl
          ? `<a href="${escape(view.officialUrl)}" target="_blank" rel="noreferrer noopener">官方文档</a>`
          : '<span class="flat-note">官方地址未核实，平台不猜。</span>'
      }
      ${
        view.installCommand
          ? `<code class="flat-cmd">${escape(view.installCommand)}</code>`
          : '<span class="flat-note">这个系统没有官方安装命令。</span>'
      }
      ${view.verifyCommand ? `<span class="flat-note">装完自检：${escape(view.verifyCommand)}</span>` : ''}
      ${hint ? `<span class="flat-note">${escape(hint)}</span>` : ''}
      ${view.notes.length ? `<span class="flat-note">${escape(view.notes.join(' '))}</span>` : ''}
    </li>`;
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
      return '<section class="flat-card"><h3>额度池</h3><p class="flat-empty">暂无额度来源记录。</p></section>';
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
          <span>${own.length ? `${own.length} 个模型` : '尚未探测到模型'}</span>
          <em>${used ? `已用 ${usedCount} 次` : '尚无人使用'}</em>
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
          : '<p class="flat-empty">该额度来源尚未探测到模型。</p>'}
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
          : '<p class="flat-note">尚无人从额度池借用额度。</p>'}
        ${userKeysBlock()}
      </details>
    </section>`;
  }

  /**
   * 「等你处理的事」—— 右栏的第一张卡。
   *
   * 右栏原来第一张是"要做的事"（全部 9 件事的摘要），真正**要人动手**的那几件
   * 藏在列表里。把它顶到最上面，点一下就跳到那条活 —— 这一屏的第一张卡就
   * 是"我现在该干什么"。
   */
  function waitingCard() {
    const waiting = (state.tasks || []).filter((task) =>
      ['awaiting_human', 'escalated'].includes(String(task.status)),
    );
    if (!waiting.length) return '';
    const rows = waiting
      .slice(0, 5)
      .map((task) => {
        const parts = titleParts(task);
        const ago = task.updated_at ? timeAgo(task.updated_at) : '';
        const waited = ago && ago !== '刚刚' ? ` · 已经等了 ${ago.replace(/前$/, '')}` : '';
        return `<li><button type="button" data-act="open-task" data-task="${escape(task.task_id)}">
            <b>${escape(parts.main)}</b>
            <span>${escape(STATE_LABEL[task.status] || task.status)} · ${escape(
              task.last_failure?.reason ? reasonText(task.last_failure.reason) : '等你给个说法',
            )}${escape(waited)}</span>
          </button></li>`;
      })
      .join('');
    return `<section class="flat-card flat-card-waiting">
        <h3>${cardIcon('bell')}等你处理</h3>
        <ul class="flat-list flat-waiting-list">${rows}</ul>
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
      <h3>平台需要你确认</h3>
      <div class="flat-userkey-ask">
        <p><b>免费额度已用尽。</b>要不要用它自己添加的 Key 继续执行？</p>
        <p class="flat-note">用的是你的 key「${escape(pending.label || '未命名')}」${
          pending.model ? `（模型 ${escape(pending.model)}）` : ''
        }。经你确认后才会使用；未确认前不会启用。</p>
        <div class="flat-dialog-actions">
          <button type="button" data-act="deny-user-key" data-key="${escape(pending.key_id)}">先不用</button>
          <button type="button" data-act="approve-user-key" data-key="${escape(pending.key_id)}" data-mode="once">同意用一次</button>
          <button type="button" data-act="approve-user-key" data-key="${escape(pending.key_id)}" data-mode="always">以后都用它</button>
        </div>
      </div>
    </section>`;
  }

  /**
   * 用户自有 API Key。
   *
   * 这一块是**人自己的钱**：加进来之后平台替它保管，但平台**不会自己花** ——
   * 免费额度用完时，平台会**来问**你，点了头才用。
   *
   * 「平台来问你了」那张卡片**不在这里**：它挂在右栏最上面（`askCard`）——
   * 埋在这张长卡片的底下，谁都找不到。
   */
  /**
   * 小图标（内联 SVG）。
   *
   * 右栏六张卡长得一模一样，是"看不出这是哪个产品"的大头 —— 一张卡一个身份，
   * 眼睛才有地方落。图标都是线稿，跟整体的细线风格一致。
   */
  const CARD_ICONS = {
    brief: '<path d="M8 6h11M8 12h11M8 18h7"/><circle cx="4" cy="6" r="1.3"/><circle cx="4" cy="12" r="1.3"/><circle cx="4" cy="18" r="1.3"/>',
    people: '<circle cx="9" cy="8" r="3"/><path d="M3.5 19c.6-3 2.7-4.5 5.5-4.5s4.9 1.5 5.5 4.5"/><path d="M16 5.4a3 3 0 0 1 0 5.7M18 19c-.2-2.2-1.1-3.6-2.8-4.3"/>',
    chat: '<path d="M4 5.5h16v10H9.5L4.5 19z"/>',
    gauge: '<path d="M4 17.5a8 8 0 1 1 16 0"/><path d="M12 17.5l4.2-5.2"/>',
    workers: '<circle cx="8" cy="8" r="3"/><path d="M2.5 19c.6-3 2.7-4.5 5.5-4.5"/><circle cx="17" cy="9" r="2.4"/><path d="M15.5 19c.3-2.4 1.6-3.6 3.5-3.9"/>',
    stamp: '<path d="M5 19.5h14M6.5 16.5h11v-3.2l-2.8-.9-1-4.9a2 2 0 0 0-4 0l-1 4.9-2.2.7z"/>',
    bell: '<path d="M6.5 9.5a5.5 5.5 0 1 1 11 0c0 3.6 1.4 5 1.4 5H5.1s1.4-1.4 1.4-5z"/><path d="M10 18a2.1 2.1 0 0 0 4 0"/>',
    key: '<circle cx="8" cy="12" r="3.6"/><path d="M11.6 12H21l-2 2.2M17.5 12v2.6"/>',
  };

  const cardIcon = (name) =>
    `<svg class="flat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${CARD_ICONS[name] || ''}</svg>`;

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
      : '<p class="flat-empty">尚未添加自有 Key。</p>';
    return `<section class="flat-userkeys">
      <h3>自有 API Key</h3>
      <p class="flat-note">平台代为保管，但<b>不会自行使用</b> —— 免费额度用尽时会征求你的确认。</p>
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
    if (!best || !best.value) return '该时段无用量';
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
        detail = `<p class="flat-note">无法查询余额：${escape(balance.reason || '')}</p>`;
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
          : '该档位的 Provider 均无可查询余额的接口'}</p>`;
        // 账号层面的用量：provider 自己报的（平台只是它的一个调用方）。
        const daily = known
          .flatMap((item) => (item.daily || []).map((row) => ({ ...row, unit: item.unit })))
          .slice(-3);
        if (daily.length) {
          detail += `<p class="flat-note">账号近几日用量（由 Provider 自报）：${escape(
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
    return `<h4>用量分布</h4>
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
    // 这个项目里已经勾好的成员（新建项目时在表单里选的）。成员要等「开工配置」
    // 确认、目标提交之后才在平台上生效 —— 所以确认之前这里确实是 0 个。
    // 说成「请去指定成员」是冤枉人：人已经指定过了。
    const pickedMembers = (() => {
      const project = projectOf && projectOf();
      return project && Array.isArray(project.members) ? project.members.length : 0;
    })();
    const plan = state.plan && state.plan.draft ? state.plan.draft : null;
    const admin = adminName();
    const { items: thread, started } = adminThread();
    const brief = taskBrief();
    const helps = helpRequests();
    const unclaimed = unclaimedTasks();

    wrap.innerHTML = `
      ${askCard()}
      ${waitingCard()}
      <section class="flat-card flat-card-brief">
        <h3>${cardIcon('brief')}待办事项</h3>
        ${brief.planId
          ? `<p><b>${escape(brief.title)}</b></p>
             <p class="flat-note">${(state.tasks || []).length} 条任务${unclaimed.length ? `（${unclaimed.length} 条尚未领取）` : ''}</p>
             <button type="button" data-act="open-brief">查看全部待办</button>`
          : `<p class="flat-note">${(state.tasks || []).length} 条任务</p>
             <p class="flat-empty">暂无方案正文。</p>`}
      </section>
      <section class="flat-card flat-card-members">
        <h3>${cardIcon('people')}成员求助</h3>
        ${helps.length
          ? `<p class="flat-note">${helps.length} 条求助待处理</p>
             <ul class="flat-list">${helps.slice(0, 4).map((item) => `<li><b>${escape(item.owner || '未知')}</b><span>${escape(clip(item.reason || '（没写原因）', 40))}</span><span>${escape(clip(item.title, 28))}</span></li>`).join('')}</ul>`
          : '<p class="flat-empty">暂无待处理求助。</p>'}
        <button type="button" data-act="open-members">查看成员执行情况</button>
      </section>
      <section class="flat-card flat-card-admin">
        <h3>${cardIcon('chat')}与管理员对话</h3>
        ${admin
          ? `<p><b>${escape(admin)}</b></p>
             <p class="flat-note">${!started
               ? '尚未与管理员沟通。发送一条消息，管理员即可收到。'
               : thread.length
                 ? `本轮对话 ${thread.length} 条。`
                 : '尚未发送消息。发送后管理员即可收到。'}</p>
             <button type="button" data-act="open-admin-chat">打开对话</button>
             <button type="button" data-act="open-direct-window">开私聊窗口</button>`
          : '<p class="flat-empty">平台还没配置管理员窗口。</p>'}
      </section>
      ${installCard()}
      ${poolCard()}
      <section class="flat-card flat-card-fold">
        <details data-fold="workers"${state.openFolds.has('workers') ? ' open' : ''}>
          <summary>可执行成员 · ${usable.length} 个</summary>
          <ul class="flat-list">${usable
            .map((item) => `<li><b>${escape(harnessName(item.harness_id))}</b><span>${escape(item.model || '模型未登记')}</span></li>`)
            .join('') || `<li class="flat-empty">${
              pickedMembers
                ? `已选 ${pickedMembers} 个成员窗口，在「开工配置」确认后生效。`
                : '暂无可执行成员窗口：请在顶部「开工配置」中指定管理员与成员。'
            }</li>`}</ul>
        </details>
        <button type="button" data-act="open-outputs" title="另开一个窗口，查看各成员的执行输出">查看成员输出</button>
      </section>
      <section class="flat-card">
        <h3>${cardIcon('stamp')}待批准的方案</h3>
        ${plan
          ? `<p>方案 ${escape(plan.plan_id)} · ${escape((plan.document || {}).tasks || 0)} 条任务</p>
             <p class="flat-note" title="${escape((plan.document || {}).markdown_path || '')}">${escape(
               translated((plan.document || {}).markdown_path) ||
                 fileNameOf((plan.document || {}).markdown_path),
             )}</p>
             <button type="button" data-act="approve-plan">批准，开始做</button>
             <button type="button" data-act="reject-plan" title="驳回方案：退回管理员修订后重新提交">驳回方案</button>`
          : '<p class="flat-empty">暂无待批准的方案。</p>'}
      </section>`;
    return wrap;
  }

  /**
   * 群聊摘要：最近群里在说什么。
   *
   * 群聊里 180 条消息，大多是平台的回执（管理员窗口 121 条、你 55 条、成员 4 条）。
   * 人真正要知道的只有三件事：**一共多少、谁说的、最近一条是什么**。
   *
   * 每句都是算出来的，不猜：
   *   · 条数与分工 —— 按 sender_type 和平台名册算；
   *   · 最近一条 —— 时间 + 那句话（洗过机器标记；任务号换成任务名、窗口 id 换成登记名）；
   *   · 今天有没有新动静 —— 按本机时区的今天算（跟顶上那段「今天」一个口径）。
   */
  function chatDigest() {
    const messages = state.messages || [];
    if (!messages.length) return '';
    const adminId = state.status && state.status.admin && state.status.admin.identity;
    let you = 0, admin = 0, others = 0;
    for (const message of messages) {
      if (message.sender_type === 'human') you += 1;
      else if (adminId && message.sender_id === adminId) admin += 1;
      else others += 1;
    }
    const adminName = adminId ? harnessName(adminId) : '管理员窗口';
    const who = [];
    if (admin) who.push(`${adminName} ${admin} 条`);
    if (you) who.push(`你 ${you} 条`);
    if (others) who.push(`其他成员 ${others} 条`);

    const last = messages[messages.length - 1];
    const today = new Date();
    const dayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
    const fresh = messages.filter((message) => {
      const at = Date.parse(message.created_at || '');
      return Number.isFinite(at) && at >= dayStart;
    });

    const bits = [`群里 ${messages.length} 条消息：${who.join('、')}`];
    if (fresh.length) {
      bits.push(`今天新增 ${fresh.length} 条 —— 最后一条为 ${timeAgo(last.created_at)}`);
    } else {
      bits.push(`今天暂无新消息 —— 最后一条为 ${timeAgo(last.created_at)}`);
    }
    const said = readableMessageBody(last, { firstLineOnly: true });
    if (said) bits.push(said);
    return `${bits.join('。')}。`;
  }

  /**
   * 消息正文（人话版）。
   *
   * 正文是机器写的（"管理员 Harness 改派：FD-… → FD-…（… → opencode）"），所以：
   * 先让平台把机器标记洗掉，再把**任务号**换成任务名、**窗口 id**换成登记名 ——
   * 人读到的是"哪条活、哪个窗口"，不是编号。
   *
   * 改派那条消息里前后两个编号常常是**同一条活的两代**（接替关系），换出来的
   * 名字也就一样 —— 那就只留一个，别摆成"甲 → 甲"。
   */
  function readableMessageBody(message, { firstLineOnly = false } = {}) {
    const raw = String((message && message.body) || '').trim();
    if (!raw) return '';
    const washed = translated(raw) || raw;
    let text = firstLineOnly ? washed.split('\n')[0].trim() : washed;
    // 任务号 → 任务名（人起的名字）
    const names = new Map();
    for (const task of state.tasks || []) {
      const id = String(task.task_id || '');
      if (!id) continue;
      names.set(id, listTitle(task));
      names.set(id.slice(3, 10), listTitle(task));
    }
    // 同一条活的两代（接替关系）：「甲 → 乙」其实是同一条活，只留一个名字 ——
    // 不然会摆成「甲 → 甲」，那句话等于没说。
    for (const [idA, titleA] of names) {
      if (!text.includes(idA)) continue;
      for (const [idB, titleB] of names) {
        if (idA === idB || titleA !== titleB || !text.includes(idB)) continue;
        text = text.split(`${idA} → ${idB}`).join(`「${titleA}」`);
        text = text.split(`${idA} -> ${idB}`).join(`「${titleA}」`);
      }
    }
    for (const [id, title] of names) {
      if (!id || !text.includes(id)) continue;
      text = text.split(id).join(`「${title}」`);
    }
    // 窗口 id → 登记名。**长的先换**：codex-window-admin 与 codex 都登记着，
    // 先换短的会把长的截成"Codex CLI-window-admin"。
    // 前后都不能再接字母/数字/横线 —— 免得把 codex-window-admin 截断，
    // 也免得把 "pi" 换成 "Pi CLI" 时顺手改掉 "pipe" 里的 pi。
    const roster = [...((state.status && state.status.harnesses) || [])]
      .filter((harness) => harness.harness_id && String(harness.harness_id).length >= 2)
      .sort((a, b) => String(b.harness_id).length - String(a.harness_id).length);
    for (const harness of roster) {
      const id = String(harness.harness_id);
      const name = harness.name || id;
      // 前后都不能再接字母/数字/横线 —— 免得把 codex-window-admin 截断。
      text = text.replace(
        new RegExp(`(^|[^A-Za-z0-9_-])${id}(?![A-Za-z0-9_-])`, 'g'),
        (match, before) => `${before}${name}`,
      );
    }
    return text.length > 140 ? `${text.slice(0, 140)}…` : text;
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
    count.textContent = state.messages.length ? `${state.messages.length} 条` : '暂无消息';
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'flat-chat-toggle';
    toggle.dataset.act = 'chat-toggle';
    toggle.textContent = state.chatExpanded ? '收起 ▾' : '展开 ▴';
    toggle.setAttribute('aria-expanded', String(Boolean(state.chatExpanded)));
    head.append(title, count, toggle);
    wrap.append(head);
    const digest = document.createElement('p');
    digest.className = 'flat-chat-digest';
    digest.textContent = chatDigest();
    if (digest.textContent) wrap.append(digest);
    const list = document.createElement('div');
    list.className = 'flat-chat-list';
    if (!state.messages.length) {
      const empty = document.createElement('p');
      empty.className = 'flat-empty';
      empty.textContent = '群内暂无消息。';
      list.append(empty);
    }
    // 定向消息不属于群聊（只有发送方与收件方看得到），不能混进来。
    const groupMessages = state.messages.filter(
      (message) => !(Array.isArray(message.refs) ? message.refs : []).includes(DIRECT_REF),
    );
    for (const message of groupMessages.slice(-40)) {
      const item = document.createElement('article');
      const isHuman = message.sender_type === 'human';
      // 成员窗口提的「要改目标」：群里一眼要看见（这是等群主拍板的事）。
      const isGoalChange = (Array.isArray(message.refs) ? message.refs : []).includes(
        GOAL_CHANGE_REF,
      );
      item.className =
        'flat-msg' + (isHuman ? ' is-human' : '') + (isGoalChange ? ' is-goal-change' : '');
      const who = isHuman ? '你' : harnessName(message.sender_id) || '平台';
      const at = String(message.created_at || '').replace('T', ' ').slice(5, 16);
      const body = readableMessageBody(message) || message.body || '';
      item.innerHTML = `<b>${escape(who)}</b><span class="flat-msg-at">${escape(at)}</span>${
        isGoalChange ? '<em class="flat-msg-flag">要改目标</em>' : ''
      }<p>${escape(body)}</p>`;
      list.append(item);
    }
    wrap.append(list);
    const form = document.createElement('form');
    form.className = 'flat-chat-form';
    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = '在项目群聊中发言（@ 管理员即可直接通知管理员）';
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
      return true;
    } catch (error) {
      state.error = String(error.message || error);
      render();
      // 失败要说出来 —— 调用方（取消那条活）要靠这个知道**没成**，
      // 别让它以为动作已经落地了。
      return false;
    }
  }

  // ---------------------------------------------------- 自有 API Key

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
      if (error) error.textContent = '名称与 Key 均为必填。';
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

  // 左栏窗口行右键：给这一家 Harness 再开一个窗口。
  // 只认带 `data-add-member-from` 的行（窗口行）；别的地方右键照旧。
  //
  // 两个事件都接：`contextmenu` 是标准那一个；有的内嵌浏览器只发
  // `mousedown(button=2)` 不发 `contextmenu`，只接一个就会出现"右键没反应"。
  const openMemberMenuAt = (event) => {
    const row = event.target.closest?.('[data-add-member-from]');
    if (!row) return;
    // 行本身是个 <button>：不拦掉的话右键可能顺带触发它的点击（切换视图）。
    event.preventDefault();
    event.stopPropagation();
    memberMenu.open(String(row.dataset.addMemberFrom || ''), event.clientX, event.clientY);
  };
  root.addEventListener('contextmenu', openMemberMenuAt);
  root.addEventListener('mousedown', (event) => {
    if (event.button !== 2) return;
    openMemberMenuAt(event);
  });

  // 记住哪些折叠卡片是开着的（右栏每 15 秒重画一次，不记住就会"啪"地合上）。
  root.addEventListener('toggle', (event) => {
    const fold = event.target.closest?.('details[data-fold]');
    if (!fold) return;
    if (fold.open) state.openFolds.add(fold.dataset.fold);
    else state.openFolds.delete(fold.dataset.fold);
  }, true);

  root.addEventListener('change', (event) => {
    // 模型清单里的挑选：记进 state（重画之后还在）。
    const modelRadio = event.target.closest('input[name="flatModelMode"]');
    if (modelRadio) {
      const block = modelRadio.closest('.flat-model');
      const harnessId = block ? String(block.dataset.harness || '') : '';
      if (harnessId) {
        state.modelPicks[harnessId] = {
          mode: modelRadio.value === 'pool' ? 'pool' : 'cli',
          modelId: String(modelRadio.dataset.modelId || ''),
        };
      }
      return;
    }
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

  // 助手的提问框：回车就发（Shift+回车留着换行）。
  root.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    const input = event.target;
    if (!input || input.id !== 'flatAssistantQuestion') return;
    event.preventDefault();
    void askAssistant(input.value);
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
    // 「换看某条活」的按钮没有 data-act，只有 data-open-task —— 必须在
    // data-act 过滤**之前**处理，否则点了没反应。
    const openTask = event.target.closest('button[data-open-task]');
    if (openTask) {
      // 这个按钮在 <summary> 里：不阻止默认行为的话，点它会顺手把这一行展开。
      event.preventDefault();
      state.view = { kind: 'tasks', id: null };
      void select(openTask.dataset.openTask);
      return;
    }
    const button = event.target.closest('button[data-act]');
    if (!button) return;
    // 一次新的动作开始：把上一条"没成"的提示收起来（这次要是再失败，会写新的）。
    if (state.error) {
      state.error = null;
      render();
    }
    if (button.dataset.act === 'save-model-choice') {
      void saveModelChoice(button);
      return;
    }
    if (button.dataset.act === 'probe-pool') {
      void probePool();
      return;
    }
    if (button.dataset.act === 'declare-model') {
      void declareModel(button);
      return;
    }
    if (button.dataset.act === 'use-undeclared') {
      void useUndeclaredModel(button);
      return;
    }
    if (button.dataset.act === 'edit-tier') {
      // 这些按钮挂在 <label> 里（那一行同时是单选项）：不拦住的话，点「改档」
      // 会顺带把这一行选上。
      event.preventDefault();
      editTier(button);
      return;
    }
    if (button.dataset.act === 'retier-model') {
      event.preventDefault();
      void retierModel(button);
      return;
    }
    if (button.dataset.act === 'withdraw-model') {
      event.preventDefault();
      void withdrawModel(button);
      return;
    }
    if (button.dataset.act === 'dismiss-model-note') {
      state.modelNote = '';
      render();
      return;
    }
    if (button.dataset.act === 'add-member') {
      void addMemberToProject(String(button.dataset.harness || ''));
      return;
    }
    if (button.dataset.act === 'dismiss-error') {
      // 按钮写着「知道了」，按了就得真的消失。以前只 render() 不清 state，
      // 于是提示一直挂着 —— 人点了没反应，只能以为没点上。
      state.error = null;
      render();
      return;
    }
    if (button.dataset.act === 'dismiss-member-note') {
      state.memberNote = '';
      render();
      return;
    }
    if (button.dataset.act === 'back-tasks') {
      // 从详情回到任务表（会话式的主从切换）。
      state.selected = null;
      state.detail = null;
      state.output = null;
      state.failureOutput = null;
      render();
      return;
    }
    if (button.dataset.act === 'open-assistant' || button.dataset.act === 'assistant-detail') {
      // 巡检与上报的详细面板是另一块（顶栏那个按钮），这里把它叫起来。
      document.getElementById('adminAgentBtn')?.click();
      return;
    }
    if (button.dataset.act === 'assistant-preset') {
      // 常用问题：点了就是问（不让人再抄一遍）。
      void askAssistant(button.dataset.question || button.textContent);
      return;
    }
    if (button.dataset.act === 'assistant-send') {
      const input = root.querySelector('#flatAssistantQuestion');
      const question = input ? input.value : '';
      if (!String(question || '').trim()) {
        input?.focus();
        return;
      }
      void askAssistant(question);
      return;
    }
    if (button.dataset.act === 'start-project') {
      // 空项目里的那一步：把第一次用的人直接送到「开工配置」（写一句要做的事）。
      onStart?.();
      return;
    }
    if (button.dataset.act === 'page-prev') {
      state.page = Math.max(1, state.page - 1);
      render();
      return;
    }
    if (button.dataset.act === 'installs-probe') {
      // 人的动作：现在去问这台机器上有没有这些命令。
      void loadInstalls({ probe: true });
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
    if (button.dataset.act === 'open-task' && button.dataset.task) {
      // 「等你处理」那一张：点一下就跳到那条活。
      void select(button.dataset.task);
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
    if (button.dataset.act === 'apply-trouble-model') {
      void applyTroubleModel(button);
      return;
    }
    if (button.dataset.act === 'dismiss-trouble') {
      dismissTrouble(button);
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
        body: { task_id: task.task_id, reason: '人工追加尝试：尝试预算已用完' },
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
            `请将该任务改派给仍有额度的窗口：${task.title || task.task_id}（${task.task_id}）`,
            `上次执行方：${task.owner || '无人持有'}`,
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
          title: '驳回方案',
          note: `方案 ${draft.plan_id}：驳回之后它会退回给管理员，改一版再来找你。`,
          label: '驳回原因（平台将记录）',
          placeholder: '例如：范围太大，先做核心链路',
        });
        if (!answer) return;
        if (!answer.text) {
          state.error = '驳回方案需说明原因 —— 平台会记录该说明，供后续修订参考。';
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
            title: '回答问题',
            note: '你的回答会记录在平台，执行成员可见；该任务将交回其继续处理。',
            label: '你的回答（可留空，建议写明）',
            route: '/human/resume',
          },
          reopen: {
            title: '重新打开任务',
            note: '清除当前执行者，该任务回到「待领取」，任何成员均可领取。',
            label: '重新打开的原因（可留空）',
            route: '/human/reopen',
          },
          release: {
            title: '重新分派',
            note: '该任务将回到「待领取」列表，等待下一轮分派。',
            label: '重新分派的原因（可留空）',
            route: '/human/release',
          },
          cancel: {
            title: '取消任务',
            note: '取消即终止：该任务不会再被执行。',
            label: '取消原因（必填）',
            route: '/human/cancel',
          },
        }[decision];
        if (!spec) return;
        const needsOverride = decision === 'release' && releaseNeedsOverride(task);
        const answer = await askDecision({
          title: spec.title,
          note: spec.note,
          label: spec.label,
          placeholder: decision === 'cancel' ? '例如：任务方向有误，先暂停' : '',
          override: needsOverride,
        });
        if (!answer) return;
        if (decision === 'cancel' && !answer.text) {
          state.error = '取消任务需说明原因 —— 平台会记录该说明。';
          render();
          return;
        }
        if (needsOverride && !answer.override) {
          // 平台会回 409（R10 §8），但让人自己读那个码不如直接说清楚。
          state.error = '该任务的尝试次数已用尽。重新分派前需勾选「顺便追加一次尝试」。';
          render();
          return;
        }
        if (decision === 'cancel' && !CANCEL_STATES.has(String(task.status))) {
          // 列表里的状态可能已经过期了（这条活刚被别人收口）。先问一句，别把一个
          // 早就结束的活再取消一遍，也别因此去惊动管理员。
          state.error = '该任务状态已变更（可能已被处理），无需取消。请刷新后查看。';
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
        const ok = await doAction(spec.route, { route: spec.route, body });
        // 只有**真的取消了**才去通知管理员 —— 取消失败还去发消息，等于谎报。
        if (decision === 'cancel' && ok) await notifyAdminOfCancel(task, answer.text);
      })();
    }
  });

  /**
   * 取消一条活之后，**必须**给管理员发一条群消息。
   *
   * 为什么：管理员 worker 只认三种群消息（`project-goal` / `project-goal-answer` /
   * `reassign-request`）。取消不在其中 —— 不通知他，他就在一份已经不成立的计划
   * 上继续干活（方案快照里那条还在、分配轮照旧收口）。
   *
   * 这条消息带 `task-cancelled` 标记，管理员那边会就**这一条原本要做的部分**重新
   * 细化一版（或者确认不用再做了），其余按原计划继续。
   */
  async function notifyAdminOfCancel(task, reason) {
    const adminId = adminIdentity();
    const title = task.title || task.task_id;
    // 机器读的那份（任务号 + 原因 + 标题）放 refs 里；正文是人话。
    const envelope = {
      task_id: task.task_id,
      title,
      reason: String(reason || '').trim(),
    };
    try {
      await doAction('/messages', {
        route: '/messages',
        body: {
          body: [
            `取消任务：${envelope.reason}`,
            `（这一条：${title}）`,
            '管理员将针对原计划内容重新细化，或确认不再执行；其余部分按原计划继续。',
          ].join('\n'),
          // mention 让它进管理员的收件箱，refs 让它出现在「与管理员对话」面板里。
          mentions: adminId ? [adminId] : [],
          task_id: task.task_id,
          refs: [
            'to-admin',
            'task-cancelled',
            `task-cancelled:${task.task_id}`,
            `task-cancelled-json:${JSON.stringify(envelope)}`,
          ],
        },
      });
    } catch (error) {
      // 活已经取消了（那一步成功了），通知失败不能把取消本身说成没成 —— 但得让人
      // 知道管理员**没**被通知到。
      state.error = `任务已取消，但未能通知管理员：${error.message}`;
      render();
    }
  }

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
    if (texts.length) await humanize(texts);
    // 群聊的消息正文也是机器写的（"管理员 Harness 改派：FD-… → FD-…"）。
    // 只问**最近这几十条**（下面就摆这些），问过的走缓存，不重复问。
    const recent = (state.messages || []).slice(-40).map((message) => String(message.body || '')).filter(Boolean);
    if (recent.length) await humanize(recent, 'text');
  }

  async function refresh(options) {
    // 人点了刷新 → 顺手**重探一遍**池子（新增的免费额度、变成收费的额度都要知道）。
    // 平时 15 秒的自动刷新不探：探一次要问二十几个上游。
    const wantProbe = Boolean(options && options.probe);
    let probed = null;
    if (wantProbe) {
      probed = await api('/pool/probe', {}).catch(() => null);
    }
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
      //
      // 但**事件**必须跟任务表一起到：按人归堆要知道每条活是谁接的（从领取事件
      // 看），分两批到的话第一屏会先摆成"一堆没人接"，等事件到了再重排 —— 一闪。
      const [status, tasks, events] = await Promise.all([
        api('/status').catch(() => null),
        api('/tasks').catch(() => ({ tasks: [] })),
        api('/events?limit=400').catch(() => ({ events: [] })),
      ]);
      state.status = status;
      state.events = events.events || [];
      const all = tasks.tasks || [];
      state.tasks = scope ? all.filter(scope.matches) : all;
      // 这里**不**清 state.error：一次失败的提示要留到人下一次动手为止。
      // 每 15 秒把它抹掉，等于"点了没反应" —— 提示闪过就没了，人只记得没反应。
      // 不自动选中第一条活：默认给人看**任务表**，点开哪条再看哪条
      // （会话式的用法：先看列表，再点进去）。
      render();
      const poolReadAt = Date.now();
      // 窗口名单与状态栏**先**落地：池子那一次读可能要十几秒（冷启动要问各家
      // CLI 的名册），把它和名单放在同一个 Promise.all 里，慢的那个会把快的
      // 一起拖住 —— 名单空着、状态栏没数。
      const availability = await api('/availability').catch(() => ({ availability: [] }));
      state.availability = availability.availability || [];
      // 安装清单**自己一段、自己重绘**：它不该等额度池那几秒（那边一问就是二十几个
      // 上游）才跟着出现。没读到就让它自己说没读到，别一直挂着「正在读取」。
      // 读到的安装方式不会每 15 秒变，所以只取一次；失败时等人点「探测」再重试。
      if (!state.installs && !state.installsError) void loadInstalls();
      state.loaded = true;
      // 顶栏的状态：平台连不连得上、几个窗口在线（平台上的事实，不猜）。
      const online = state.availability.filter((item) => item.status === 'online').length;
      onStatus?.({
        connected: true,
        online,
        offline: state.availability.length - online,
      });
      const [pool, plan, messages, runs] = await Promise.all([
        probed ? Promise.resolve(probed) : api('/pool').catch(() => null),
        api('/plan').catch(() => null),
        api('/messages?since_seq=0&limit=500').catch(() => ({ messages: [] })),
        // 助手这一轮做了什么（引擎写在服务器本地的 trace，这里读成摘要）。
        api('/assistant/runs').catch(() => ({ runs: [] })),
        loadUserKeys(),
      ]);
      // 这一轮读到的池子是**发起时**的状态：要是这期间有人定过档（探针/定档
      // 写进去的更新鲜），就别用这份旧的盖上去。
      if (poolReadAt >= state.poolAt) {
        state.pool = pool;
        state.poolAt = poolReadAt;
      }
      if (probed) {
        // 探过了：把与上次的差别记下来（多了什么、谁换了档、谁还没定档）。
        state.poolProbe = comparePool(probed);
        savePoolSnapshot(probed);
      }
      state.plan = plan;
      state.messages = messages.messages || [];
      state.assistantRuns = runs.runs || [];
      state.events = events.events || [];
      // 人话从平台来（问过的会缓存，不会每 15 秒问一次）。
      await loadWording();
    } catch (error) {
      state.error = String(error.message || error);
    }
    render();
  }

  /**
   * 盯着**正在跑**的那条活。
   *
   * 平台的 `/output` 读的是 worker 正在写的那个日志文件的尾巴，所以它本来就是
   * 活的 —— 办公室以前只是**点开时取一次**，之后就冻住了。现在每 4 秒取一次，
   * 而且**只换那一块**（不整页重画，免得把人正在看的东西刷掉）。
   */
  async function refreshLive() {
    const task = state.selected;
    if (!task || !isRunning(task)) return;
    const data = await api(`/tasks/${encodeURIComponent(task.task_id)}/output`).catch(() => null);
    if (!data) return;
    const before = String((state.output && state.output.output && state.output.output.stdout) || '');
    state.output = data;
    const after = String((data && data.output && data.output.stdout) || '');
    const box = root.querySelector('#flatLive');
    if (!box) return;
    if (after === before) {
      // 没有新字，但"还有动静"的时间变了 —— 只改那一行。
      const stamp = box.querySelector('.flat-live-stamp');
      if (stamp) stamp.innerHTML = `<i class="flat-pulse"></i>${escape(liveStamp())} · 每 4 秒自动刷新`;
      return;
    }
    box.outerHTML = liveOutputBlock();
  }

  const timer = setInterval(() => void refresh(), 15000);
  // 正在跑的活 4 秒一轮（只有选中它的时候才有请求）。
  const liveTimer = setInterval(() => void refreshLive(), 4000);
  void refresh();

  return {
    refresh,
    stop: () => {
      clearInterval(timer);
      clearInterval(liveTimer);
    },
  };
}
