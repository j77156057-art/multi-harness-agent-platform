// 平台助手：最基础的事务层（前端运行时，只消费正式平台真实回执）。
//
// 职责边界（对齐 PROACTIVE-HELP-REQUIREMENT.md §3.1 / TASK-ALLOCATION-ARBITRATION.md）：
//   · 做：定期巡检、追加式进度记录、停滞判定、负担观测、中转播报，以及把异常（超时 /
//     卡在同一任务过久 / 待审查积压 / 无人可派）上报给本项目的管理员窗口。
//   · 不做：拆解任务表、裁决竞争、重新分工、续期/重开/取消/去重、领取与提交审核。
//     这些是管理员窗口的职责；split / request_help / accept_help / reject_help /
//     release / abandon 始终是窗口侧权限，助手不代任何成员发起。
//   · 不伪造 Claim / Lease / 进度，不改变任务 owner，不自动执行外部检索结果。
//   · 自报与核验分开：所有数字都来自平台回执；没有可信数据时写“未知”，不虚构。
// 文件名沿用 admin-agent.js：改名会牵动构建入口与既有验证脚本，界面文案已统一为「平台助手」。
import './admin-agent.css';
import { createRoutedMessage } from './routed-message.js';
import { isAdministratorHarness, isPlatformAssistantHarness } from './harness-roles.js';
import { readableText } from './cli-transcript.js';

const POLL_MS = 20000; // 面板刷新周期：只影响显示新鲜度，不参与停滞判定
// 进度窗口按 PROACTIVE-HELP-REQUIREMENT.md §3 的产品默认值：每 5 分钟核对一次，
// 连续两个窗口无实质进展提醒说明卡点，连续四个窗口且无有效回应交管理员。
// 这是可调默认值（?agentWindowMs= 覆盖），不覆盖平台的租约/升级阈值。
const WINDOW_MS = Number(new URLSearchParams(location.search).get('agentWindowMs')) || 5 * 60 * 1000;
const STALL_WINDOWS = 2, ESCALATE_WINDOWS = 4;
const WINDOW_LABEL = WINDOW_MS >= 60000 ? `${Math.round(WINDOW_MS / 60000)} 分钟` : `${Math.round(WINDOW_MS / 1000)} 秒`;
const windowsOf = age => Math.floor(age / WINDOW_MS);
const SAMPLE_CAP = 40;
const STORE_KEY = 'officeAdminAgent/v1';
// 积压与催促（对齐 PROACTIVE-HELP-REQUIREMENT.md 的协调事项）：积压项登记首次出现时间，
// 超过 URGE_WINDOWS 个进度窗口后才考虑催促；催促前必须看目标 Harness 当前负担，
// 负担重则暂缓（不影响其任务完成）。催促记入记忆清单并冷却，避免重复打扰。
const URGE_WINDOWS = (() => {
  const raw = new URLSearchParams(location.search).get('agentUrgeWindows');
  return raw !== null && /^\d+$/.test(raw) ? Number(raw) : 4;
})();
const REMIND_COOLDOWN_MS = 4 * WINDOW_MS;
const LOAD_HOLD = 2; // 目标「在办 + 排队」达到此值即暂缓催促
const BACKLOG_KINDS = {
  'queue:ready': { label: '排队待领取', hint: '队列积压：FIFO，无优先级' },
  'dead:stale': { label: '租约失效', hint: '平台不自动回收；管理员人面只能取消，或另派协助子任务' },
  'dead:abandoned': { label: '已放弃', hint: '失败即终态；管理员可重新打开回到 ready，或取消' },
  'review:pending': { label: '待审查', hint: '占住 worktree 名额' },
};

// 人设只用于“播报语气”，不参与调度排序；调度只用观测指标。
const PERSONAS = {
  'codex': '安静专注，喜欢把想法变成能直接跑起来的东西。',
  'claude-code': '善于解释和整理，提交前习惯自己先过一遍。',
  'opencode': '习惯先找反例，交叉检查边界与证据。',
  'qoder': '做事有节奏，偏爱把计划拆成可核对的小步。',
  'codebuddy': '动手快，喜欢先把流程跑通再补细节。',
};
// 管理员的真实前置条件见 backend/app/human/service.py：状态不匹配时平台返回 request.conflict。
// 这些状态只用于**判定某条线索是否值得上报给管理员**（例如 stale 没有自动回收、abandoned 是终态），
// 平台助手不据此放出任何处置按钮 —— 续期 / 重开 / 释放 / 取消都是管理员窗口的动作。
const ADMIN_ACTIONABLE = new Set(['awaiting_review', 'awaiting_human', 'plan_drift', 'impl_drift', 'abandoned', 'escalated', 'stale']);
const ACTIVE = new Set(['claimed', 'in_progress', 'verifying']);
const TERMINAL = new Set(['merged', 'cancelled']);
const STATE_LABEL = { ready: '待领取', claimed: '已领取', in_progress: '正在执行', verifying: '已提交·验证中',
  awaiting_review: '验证通过·待审查', changes_requested: '需要返修', approved: '审查通过', merged: '已合入',
  abandoning: '正在放弃', abandoned: '已放弃', cancelled: '已取消', stale: '租约失效',
  reclaiming: '重新领取中', blocked: '已阻塞', escalated: '已升级·需人工', merging: '正在合入',
  awaiting_human: '等人工决定', plan_drift: '计划漂移' };
const baseOf = id => String(id || '').replace(/-(member|window)-[A-Za-z0-9_-]+$/, '');
// 「谁现在能用」的原因码来自平台（稳定 code + 原始细节）：这里只做展示层本地化，
// 细节原样保留——平台说的事实不该被翻译改写。
const REASON_LABEL = {
  not_registered: '未登记',
  offline: '离线',
  no_credential: '无平台凭据（领不了活）',
  self_reported_degraded: '自报降级',
  last_attempt_failed: '上次尝试失败',
};
const reasonText = reason => {
  if (!reason) return '';
  if (typeof reason === 'string') return reason;
  const label = REASON_LABEL[reason.code] || reason.code || '未知原因';
  return reason.detail ? `${label}：${reason.detail}` : label;
};
// 面板里拼进 innerHTML 的 harness 名称来自注册表（可被外部写入），必须转义。
const escapeAttr = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));const personaFor = id => PERSONAS[baseOf(id)] || PERSONAS[id] || '按自己的节奏推进。';

function loadStore({ fresh = false } = {}) {
  if (fresh) return { samples: {}, observed: {}, events: [], backlog: {}, reminders: {} };
  try {
    const parsed = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (parsed && typeof parsed === 'object') return { samples: parsed.samples || {}, observed: parsed.observed || {},
      events: parsed.events || [], backlog: parsed.backlog || {}, reminders: parsed.reminders || {} };
  } catch { /* 记录损坏时从空开始，不中断巡检 */ }
  return { samples: {}, observed: {}, events: [], backlog: {}, reminders: {} };
}
function saveStore(db) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(db)); } catch { /* 存储不可用时只保留内存记录 */ }
}

export function mountAdminAgent({ launchArea, phoneArea, members = [], bootstrap = true, adminHarness = null, project = null }) {
  const button = document.createElement('button');
  button.id = 'adminAgentBtn'; button.type = 'button'; button.className = 'primary admin-agent-launch';
  button.textContent = '平台助手';
  launchArea.append(button);
  const phoneButton = phoneArea ? button.cloneNode(true) : null;
  if (phoneButton) { phoneButton.id = 'adminAgentPhoneBtn'; phoneButton.className = 'phone-agent-launch admin-agent-launch'; phoneArea.append(phoneButton); }
  // 上报的收件人：本项目的管理员窗口（用户在开工向导里选的那个外部窗口）。
  // 它不是平台助手本身，也不推断：没有配置就没有收件人，界面上直接说明。
  let adminRecipient = typeof adminHarness === 'string' && adminHarness ? adminHarness : null;

  const dialog = document.createElement('dialog');
  dialog.id = 'adminAgentDialog'; dialog.className = 'agent-dialog';
  dialog.innerHTML = `<header class="agent-head"><div><h2>平台助手 · 盯着项目</h2>
    <p>只看不改：它把平台上的事实列出来，不做规划、不分派、不改谁负责。超时或长时间没进展时，报给本项目的管理员。</p>
    <details class="agent-rules"><summary>职责边界（点开查看）</summary>
      <p>平台助手负责：定时巡检、进度记录、停滞判定、催促与上报。收件人是本项目的管理员窗口。</p>
      <p>不属于平台助手：拆解任务表、裁决竞争、重新分工、续期重开取消、领取与提交审核 —— 这些是管理员窗口与正式分派面板的事。求助/接单（split / request_help / accept_help / reject_help / release / abandon）始终是窗口侧权限，助手不代任何成员发起。</p>
    </details></div>
    <button type="button" data-close aria-label="关闭平台助手">关闭</button></header>
    <p data-connection role="status">正在检查连接…</p>
    <p data-action class="agent-action-result" role="status" hidden></p>
    <div class="agent-body" data-body hidden></div>`;
  document.body.append(dialog);

  const $ = s => dialog.querySelector(s);
  const body = $('[data-body]');
  const feedback = text => { $('[data-connection]').textContent = text; };
  // 动作结果要留得住：面板每 20 秒刷新，写进连接状态行会被下一轮覆盖，人就看不到平台是否受理。
  const showAction = (text, ok = true) => {
    const el = $('[data-action]');
    el.textContent = text; el.hidden = false;
    el.classList.toggle('is-error', !ok);
  };

  const db = loadStore({ fresh: bootstrap === false });
  let connected = false, timer = null, tasks = [], harnesses = [], lastPatrolAt = null, snapshot = null;
  // 窗口 id → 登记名。面板上别只印 id（"codex-window-admin" 谁认得）。
  // 统一走这里：别在别的函数里直接写 `names` —— 浏览器里 `names` 是 window 的
  // 内建属性（frame 名列表），写错地方会静默拿到它、`.get` 就炸。
  const nameOf = id => (snapshot && snapshot.names && snapshot.names.get(id)) || id;
  // 「谁现在能用」：平台侧的事实（凭据、在线、最近一次失败的原因、当前模型）。
  // 这是只读观察面——这里不派单、不改派、不决定谁接手。
  let availability = [];
  // 可用性读不到时要说"读不到"，不能装作"没有窗口"——两者含义完全不同。
  let availabilityError = null;
  // 平台自己的额度池：池子里有哪些额度、谁的额度被谁花了。
  let pool = null;
  let poolError = null;
  // 方案定稿：管理员细化 → 传阅 → 完善后的那一版，等人最终审核。
  let planDoc = null;
  let planError = null;
  // 后台的 harness 注册表是全局的：每个项目（group）共用同一批登记窗口，不会因为
  // 新开一个项目就另起一份。所以 /status 返回的是全部在线 harness，巡检摘要若直接
  // 用它，就会把别的项目的负担也算进来。真正的范围归属来自：
  //   1. 本项目的任务（/tasks 已经按 group 过滤）；
  //   2. 本项目「新建项目」时勾选的成员。
  // 未提及的登记窗口只能显示为「未加入本项目」，绝不进入数据汇总。
  let memberScope = null;   // null = 尚未得知（此时只统计有任务证据的 harness）
  const setMemberScope = ids => {
    memberScope = Array.isArray(ids) ? new Set(ids) : null;
  };
  const inScope = id => !id ? false : !memberScope ? false : memberScope.has(id);
  // 平台的「项目」后端其实是一个 group：同一个 group 下并列多个前端项目
  // （每个项目一条 project-goal）。`/tasks` 返回整群任务，不能直接当成「本项目」。
  // 项目范围只能用前端自己的证据：开工回执的 taskIds ∪ 标题前缀「项目名 · 」，
  // 与正式任务观察面板同一判定。
  const projectInfo = () => {
    const value = typeof project === 'function' ? project() : project;
    return value && value.id ? value : null;
  };
  const projectTaskMatch = task => {
    const scope = projectInfo();
    if (!scope) return false;
    const ids = new Set((scope.taskIds || []).map(String));
    const name = String(scope.name || '').trim();
    return (ids.size > 0 && ids.has(String(task.task_id))) ||
      (Boolean(name) && String(task.title || '').startsWith(`${name} · `));
  };
  // 摘要里同时给出「本项目 / 本群」两个数，避免再次把整群负担说成本项目的。
  let groupTaskCount = 0;
  // 面板每 20 秒整体重渲染；草稿必须留在渲染之外，否则用户填一半就被清空。
  const backlogDraft = { text: '', target: '' };

  const api = async (route, body_) => {
    const response = await fetch('/api/formal' + route, { cache: 'no-store', ...(body_ ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Office-Formal': '1' }, body: JSON.stringify(body_) } : {}) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '请求失败');
    return data;
  };

  // ---- 巡检：确定性规则，先记录再参与调度 ----
  // 巡检本身不调模型：只是把回执上的状态跃迁追加进本地记录，并在超过窗口时标出停滞。
  // 是否把停滞报给管理员由人点按钮，助手不自动替管理员做决定。
  function patrol(now) {
    const events = [];
    const bump = owner => (db.observed[owner] || (db.observed[owner] = { submissions: 0, reworks: 0, abandons: 0, durations: [] }));
    for (const task of tasks) {
      const key = `${task.task_id}|${task.attempt_id || '-'}|${task.epoch || 0}`;
      const sig = `${task.status}|${task.owner || '-'}|${task.receipt_id || '-'}`;
      const history = db.samples[key] || (db.samples[key] = []);
      const prev = history[history.length - 1];
      const owner = task.owner || (prev && prev.owner) || null;
      if (prev && prev.sig === sig) continue;
      history.push({ at: now, sig, status: task.status, owner, title: task.title || task.task_id });
      if (history.length > SAMPLE_CAP) history.splice(0, history.length - SAMPLE_CAP);
      if (!prev) continue;
      events.push({ at: now, from: prev.status, to: task.status, harness: owner, task_id: task.task_id, title: task.title || task.task_id });
      // 只在可核验的状态跃迁上记观测：自报进度不进入能力指标。
      if (ACTIVE.has(prev.status) && task.status === 'awaiting_review' && owner) {
        const obs = bump(owner); obs.submissions += 1;
        const started = history.find(item => ACTIVE.has(item.status));
        if (started) { obs.durations.push(Math.round((now - started.at) / 1000)); if (obs.durations.length > 20) obs.durations.shift(); }
      }
      if (task.status === 'changes_requested' && owner) bump(owner).reworks += 1;
      if (['abandoned', 'stale'].includes(task.status) && owner) bump(owner).abandons += 1;
    }
    db.events = events.concat(db.events).slice(0, 40);
    saveStore(db);
    lastPatrolAt = now;

    const names = new Map(harnesses.map(h => [h.harness_id, h.name || h.harness_id]));
    // 管理员窗口与平台助手的模型身份不是可领取任务的成员：即便旧任务回执里
    // 提到过它们，也不进负担表、不算「本项目成员」。
    const support = id => Boolean(id) && (
      isAdministratorHarness({ harness_id: id }, adminRecipient) ||
      isPlatformAssistantHarness({ harness_id: id })
    );
    const load = new Map();
    const ensure = id => { if (!load.has(id)) load.set(id, { queued: 0, active: 0, running: 0 }); return load.get(id); };
    // 只有属于本项目的 harness 才进负担表。判定证据：
    //   1. 本项目「新建项目」时勾选的成员（memberScope）；
    //   2. 本项目任务上出现过：指定执行者、持有者、提交者、审查者。
    // 任务已按项目过滤，所以第 2 条不会再引入同群其他项目的窗口。
    const evidenced = new Set();
    for (const task of tasks) {
      for (const id of task.assigned_harness_ids || []) if (id && !support(id)) evidenced.add(id);
      if (task.owner && !support(task.owner)) evidenced.add(task.owner);
      if (task.attempt_owner && !support(task.attempt_owner)) evidenced.add(task.attempt_owner);
      if (task.reviewer_harness_id && !support(task.reviewer_harness_id)) evidenced.add(task.reviewer_harness_id);
    }
    const scoped = id => Boolean(id) && !support(id) && (inScope(id) || evidenced.has(id));
    for (const h of harnesses) if (scoped(h.harness_id)) ensure(h.harness_id).running = Number(h.running_tasks) || 0;
    for (const task of tasks) {
      if (task.status === 'ready') for (const id of task.assigned_harness_ids || []) if (id && !support(id)) ensure(id).queued += 1;
      if (ACTIVE.has(task.status) && task.owner && !support(task.owner)) ensure(task.owner).active += 1;
    }
    // 明确不属于本项目、但确实登记在册的窗口：单独列出，不参与任何统计。
    const outsiders = harnesses.filter(h => !support(h.harness_id) && !scoped(h.harness_id))
      .map(h => ({ id: h.harness_id, name: names.get(h.harness_id) }));
    // 「谁现在能用」同样只看本项目：登记表是全局的，整表贴出来等于把别的项目的
    // 窗口算成本项目的人手。
    const scopedAvailability = availability
      .filter(item => scoped(item.harness_id))
      .map(item => ({ ...item, name: names.get(item.harness_id) || item.harness_id }));
    const outOfScopeAvailability = availability.length - scopedAvailability.length;

    const ranking = [...load.keys()].map(id => {
      const o = db.observed[id] || { submissions: 0, reworks: 0, abandons: 0, durations: [] };
      const total = o.submissions + o.reworks + o.abandons;
      const sorted = [...o.durations].sort((a, b) => a - b);
      const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : null;
      return { id, name: names.get(id) || id, submissions: o.submissions, reworks: o.reworks, abandons: o.abandons,
        total, median, score: total ? o.submissions / total : null, ...load.get(id) };
    }).sort((a, b) => (b.score ?? -1) - (a.score ?? -1) || (a.median ?? 1e9) - (b.median ?? 1e9) || b.submissions - a.submissions);

    const inflight = tasks.filter(t => ACTIVE.has(t.status)).map(task => {
      const history = db.samples[`${task.task_id}|${task.attempt_id || '-'}|${task.epoch || 0}`] || [];
      const last = history[history.length - 1];
      const age = last ? now - last.at : 0;
      return { task, age, owner: task.owner || (last && last.owner) || null,
        stalled: age >= STALL_WINDOWS * WINDOW_MS, escalated: age >= ESCALATE_WINDOWS * WINDOW_MS };
    }).sort((a, b) => b.age - a.age);

    const counts = {};
    for (const task of tasks) counts[task.status] = (counts[task.status] || 0) + 1;

    // ---- 积压登记：机器推导的积压项，记首次出现时间（附时间） ----
    const BACKLOG_SOURCE = { 'queue:ready': 'ready', 'dead:stale': 'stale', 'dead:abandoned': 'abandoned', 'review:pending': 'awaiting_review' };
    const derived = [];
    for (const [key, meta] of Object.entries(BACKLOG_KINDS)) {
      const status = BACKLOG_SOURCE[key];
      const matching = tasks.filter(t => t.status === status);
      const entry = db.backlog[key] || (db.backlog[key] = { first: now, last: now, kind: 'derived' });
      entry.last = now; entry.count = matching.length; entry.kind = 'derived';
      derived.push({ key, label: meta.label, hint: meta.hint, count: matching.length,
        first: entry.first, age: now - entry.first,
        sample: matching.length ? (matching[0].title || matching[0].task_id) : null });
    }
    const manual = Object.entries(db.backlog).filter(([, v]) => v.kind === 'coordination')
      .map(([key, v]) => ({ key, ...v, age: now - v.first, reminders: db.reminders[key] || [] }))
      .sort((a, b) => b.age - a.age);
    saveStore(db);

    const idle = ranking.filter(r => r.active === 0 && r.running === 0 && r.queued === 0);
    // 参照主 agent / subagent：派发前先确认有可用的执行者。有排队任务却没有在跑的 worker，
    // 队列不会推进（任务会被永远晾在 ready）。
    const deadQueues = ranking.filter(r => r.queued > 0 && r.running === 0).sort((a, b) => b.queued - a.queued);
    // 参照主 agent / subagent：不要把同一件事重复派给多个 subagent。同名任务会一起占队列与 worktree。
    // 只统计还能被处置的副本：abandoned 平台不在任何 cancel 边上，混进来只会放大数字。
    const byTitle = new Map();
    for (const t of tasks) {
      if (TERMINAL.has(t.status) || t.status === 'abandoned') continue;
      const key = String(t.title || t.task_id).toLowerCase().replace(/\s+/g, ' ').trim();
      if (!byTitle.has(key)) byTitle.set(key, []);
      byTitle.get(key).push(t);
    }
    const dupes = [...byTitle.values()].filter(list => list.length > 1)
      .map(list => ({ title: list[0].title || list[0].task_id, count: list.length, tasks: list }))
      .sort((a, b) => b.count - a.count);
    const suggestions = [];
    for (const r of deadQueues) {
      suggestions.push({ kind: 'no-worker', from: r,
        text: `${r.name} 有 ${r.queued} 条排队任务，但当前没有在跑的 worker（平台运行中 0），这些任务不会有人领取。要么启动该 Harness 的 worker，要么把任务改派给有 worker 的 Harness。` });
    }
    for (const d of dupes.slice(0, 3)) {
      suggestions.push({ kind: 'duplicate', dupe: d,
        text: `「${d.title}」有 ${d.count} 条同名任务（${d.tasks.map(t => t.task_id).join(' / ')}），会重复占用队列与 worktree；确认是否只需保留一条。` });
    }
    for (const r of ranking.filter(r => r.queued >= 2).sort((a, b) => b.queued - a.queued)) {
      const target = idle.find(x => baseOf(x.id) !== baseOf(r.id));
      if (!target) break;
      suggestions.push({ kind: 'reassign', from: r, target,
        text: `${r.name} 有 ${r.queued} 条排队任务；${target.name} 当前空闲（能力观测 ${target.score === null ? '未知' : Math.round(target.score * 100) + '%'}）。可把队首任务改派给它。` });
    }
    for (const item of inflight.filter(x => x.stalled)) {
      suggestions.push({ kind: item.escalated ? 'escalate' : 'help', owner: item.owner, task: item.task,
        text: item.escalated
          ? `${item.task.title || item.task.task_id} 已连续 ${windowsOf(item.age)} 个进度窗口无实质进展，需要管理员重新规划（是否改派或收口由管理员决定）。`
          : `${item.task.title || item.task.task_id} 连续 ${windowsOf(item.age)} 个窗口无实质进展，建议管理员先看卡点，必要时向负责人要最小复现。` });
    }
    snapshot = { now, names, counts, load, ranking, inflight, suggestions, idle, derived, manual, deadQueues, dupes, outsiders, scopedAvailability, outOfScopeAvailability };
    return snapshot;
  }

  // ---- 中转播报：把真实事件按人设语气写出来，由人确认后发布 ----
  // 发布者是平台助手（平台只接受管理员身份写消息），正文里写清这是代为转述，不是成员本人。
  function draftNarration() {
    const recent = db.events.slice(0, 4);
    if (!recent.length) return { lines: [], prompt: null };
    const lines = recent.map(event => {
      const who = event.harness || '未指派';
      const state = STATE_LABEL[event.to] || event.to;
      const verb = event.to === 'awaiting_review' ? '提交了产物，等待人工审查'
        : event.to === 'in_progress' ? '领取并开始执行'
        : event.to === 'changes_requested' ? '收到返修意见，准备重新提交'
        : `状态更新为「${state}」`;
      return { who, text: `${who}：${verb}——《${event.title}》。${personaFor(who)}` };
    });
    const recipients = [...new Set(recent.map(e => e.harness).filter(Boolean))];
    const message = createRoutedMessage({
      sender: '平台助手',
      recipients: recipients.length ? recipients.map(id => ({ instance_id: id, display_name: id, role: 'member' })) : [{ instance_id: 'group', display_name: '项目群', role: 'member' }],
      kind: 'progress_narration',
      taskId: recent[0].task_id,
      subject: '巡检进度播报',
      body: lines.map(l => l.text).join('\n'),
      action: 'inform',
    });
    return { lines, message };
  }

  function render() {
    if (!snapshot) return;
    try {
      renderBody();
    } catch (error) {
      // 渲染失败不能只留半截面板：明确报出来，否则管理员会以为「面板就只有这些」。
      const box = document.createElement('section'); box.className = 'agent-section';
      const h = document.createElement('h3'); h.textContent = '面板渲染失败';
      const p = document.createElement('p'); p.className = 'agent-note';
      p.textContent = `${error && error.message ? error.message : String(error)}（已渲染 ${body.children.length} 节，后面的小节被跳过）`;
      box.append(h, p); body.append(box); body.hidden = false;
    }
  }

  // 本项目的成员范围由「新建项目」时勾选的成员决定；这是平台助手唯一的范围来源。
  setMemberScope(
    members.map(member => member && (member.id || member.harness_id)).filter(Boolean),
  );

  function renderBody() {
    const { counts, ranking, inflight, suggestions, idle, derived, manual, now, load, deadQueues, dupes, outsiders, scopedAvailability, outOfScopeAvailability } = snapshot;
    body.replaceChildren();

    const section = (title, note) => {
      const wrap = document.createElement('section'); wrap.className = 'agent-section';
      const h = document.createElement('h3'); h.textContent = title; wrap.append(h);
      if (note) { const p = document.createElement('p'); p.className = 'agent-note'; p.textContent = note; wrap.append(p); }
      return wrap;
    };

    // 1. 巡检摘要
    const patrol = section('进度总览', `面板每 ${POLL_MS / 1000} 秒刷新；进度窗口 ${WINDOW_LABEL}，连续 ${STALL_WINDOWS} 个窗口无实质进展提醒，连续 ${ESCALATE_WINDOWS} 个窗口交管理员。`);
    const summary = document.createElement('p'); summary.className = 'agent-kv';
    const scopeNote = memberScope
      ? `${memberScope.size} 个成员`
      : `按任务证据判定 ${load.size} 个窗口`;
    // The group id is already printed in the connection line right above; the
    // scope is what this line has to say.
    summary.textContent = `本项目 ${tasks.length} 条任务（本群共 ${groupTaskCount} 条）· 范围：${scopeNote} · `
      + (Object.entries(counts).map(([k, v]) => `${STATE_LABEL[k] || k} ${v}`).join(' · ') || '当前没有任务');
    const when = document.createElement('p'); when.className = 'agent-kv';
    when.textContent = `上次巡检：${new Date(lastPatrolAt).toLocaleTimeString()} · 在办 ${inflight.length} 条 · 记了 ${Object.keys(db.samples).length} 条进度`;
    // 范围和负担都要能追溯到来源，否则「为什么这个窗口没算进来」只能靠猜。
    const origin = document.createElement('p'); origin.className = 'agent-kv agent-origin';
    origin.textContent = '这里只算这个项目自己的活（开工时领到的那些，加上标题以「项目名 · 」开头的）。'
      + `同一个群里的别的项目不算进来；本机登记的 ${harnesses.length} 个窗口是所有项目共用的。`;
    const loadNote = document.createElement('p'); loadNote.className = 'agent-kv agent-origin';
    loadNote.textContent = '「平台运行中」只算开发中的那一种（复核的不算）；每条活、每一次尝试都各有自己的目录和分支，互不覆盖。';
    patrol.append(summary, when, origin, loadNote);
    if (adminRecipient) {
      const who = document.createElement('p'); who.className = 'agent-kv agent-origin';
      // 印登记名，不印 id（"codex-window-admin" 谁认得）。
      who.textContent = `上报收件人：管理员窗口 ${nameOf(adminRecipient)}。`;
      patrol.append(who);
    } else {
      const who = document.createElement('p'); who.className = 'agent-kv agent-origin is-missing';
      who.textContent = '本项目还没有指定管理员窗口：下面的上报按钮不可用。请先在开工向导的「管理员」里选一个。';
      patrol.append(who);
    }
    // 只列真的有排队 / 在办 / 平台运行中的窗口。全零的成员折成一行说明，
    // 否则「本项目」看起来仍然像一长串窗口在排队。
    const busyRows = [...ranking]
      .filter(r => r.queued + r.active + r.running > 0)
      .sort((a, b) => (b.queued + b.active) - (a.queued + a.active));
    if (!busyRows.length) {
      const p = document.createElement('p'); p.className = 'agent-note';
      p.textContent = '本项目现在没有窗口在排队，也没有窗口在干活。';
      patrol.append(p);
    } else {
      const loadTable = document.createElement('table'); loadTable.className = 'agent-table';
      loadTable.innerHTML = '<thead><tr><th>窗口</th><th>排队</th><th>在办</th><th>平台运行中</th><th>负担</th></tr></thead>';
      const tbody = document.createElement('tbody');
      for (const r of busyRows) {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${escapeAttr(r.name)}</td><td>${r.queued}</td><td>${r.active}</td><td>${r.running}</td><td>${r.queued + r.active + r.running}</td>`;
        tbody.append(tr);
      }
      loadTable.append(tbody); patrol.append(loadTable);
    }
    const idleRows = ranking.filter(r => r.queued + r.active + r.running === 0);
    if (idleRows.length) {
      const p = document.createElement('p'); p.className = 'agent-note';
      p.textContent = `本项目其余 ${idleRows.length} 个成员现在闲着（上面没列它们）。`;
      patrol.append(p);
    }
    if (outsiders.length) {
      const note = document.createElement('p'); note.className = 'agent-scope-note';
      note.textContent = `另有 ${outsiders.length} 个登记窗口未加入本项目，不参与上表统计：${outsiders.map(o => o.name).join('、')}。`;
      patrol.append(note);
    }
    body.append(patrol);

    // 1b. 谁现在能用 —— 只读观察面：可用/降级/无凭据/最近失败原因 + 当前模型。
    // 这里不派单、不改派、不代谁决定要不要换模型；换不换是管理员和人的事。
    const who = section('谁能干活',
      '谁现在能接活、用的哪个模型、最近一次为什么失败。'
      + '模型是各 CLI 自己的配置里读到的，读不到就写「未知」。');
    if (availabilityError) {
      const p = document.createElement('p'); p.className = 'agent-note is-missing';
      p.textContent = `平台读不到可用性（${availabilityError}）：现在说不出"谁能用"，不是"没人能用"。`;
      who.append(p);
    } else if (!scopedAvailability.length) {
      const p = document.createElement('p'); p.className = 'agent-note';
      p.textContent = '本项目范围内没有登记在册的窗口。';
      who.append(p);
    } else {
      const table = document.createElement('table'); table.className = 'agent-table';
      table.innerHTML = '<thead><tr><th>窗口</th><th>状态</th><th>模型</th><th>原因</th></tr></thead>';
      const tbody = document.createElement('tbody');
      for (const item of scopedAvailability) {
        const tr = document.createElement('tr');
        const state = item.usable ? '可用' : '不可用';
        const reason = (item.reasons || []).map(reasonText).join('；') || '—';
        const model = item.model ? (item.model_alias ? `${item.model}（别名 ${item.model_alias}）` : item.model) : '未知';
        tr.innerHTML = `<td>${escapeAttr(item.name)}</td><td>${state}</td><td>${escapeAttr(model)}</td><td>${escapeAttr(reason)}</td>`;
        tbody.append(tr);
      }
      table.append(tbody); who.append(table);
      const usable = scopedAvailability.filter(item => item.usable).length;
      const note = document.createElement('p'); note.className = 'agent-kv agent-origin';
      note.textContent = `本项目 ${scopedAvailability.length} 个窗口：${usable} 个可用，${scopedAvailability.length - usable} 个不可用。`
        + (outOfScopeAvailability > 0 ? `另有 ${outOfScopeAvailability} 个登记窗口不在本项目，不在此列出。` : '');
      who.append(note);
    }
    body.append(who);

    // 1c. 额度池 —— 平台自己的池子：谁的额度在里面、谁的额度被谁花了。
    const poolSection = section('额度',
      '谁家的额度都放进一个池子里。'
      + '某个窗口的额度用完了，就换池子里还活着的那份额度接着做。');
    if (poolError) {
      const p = document.createElement('p'); p.className = 'agent-note is-missing';
      p.textContent = `读不到额度池（${poolError}）。`;
      poolSection.append(p);
    } else if (pool) {
      const upstreams = pool.upstreams || [];
      const models = pool.models || [];
      if (!upstreams.length) {
        const p = document.createElement('p'); p.className = 'agent-note';
        p.textContent = '池子里现在没有额度源。';
        poolSection.append(p);
      } else {
        const table = document.createElement('table'); table.className = 'agent-table';
        table.innerHTML = '<thead><tr><th>额度源</th><th>档位</th><th>协议</th><th>模型数</th><th>说明</th></tr></thead>';
        const tbody = document.createElement('tbody');
        const TIER_LABEL = { free: '免费额度', reserve: '兜底额度（不自动动用）', metered: '按量计费' };
        for (const source of upstreams) {
          const count = models.filter(item => item.upstream === source.name).length;
          const tier = source.tier || (models.find(item => item.upstream === source.name) || {}).tier || 'metered';
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${escapeAttr(source.name)}</td><td>${escapeAttr(TIER_LABEL[tier] || tier)}</td>`
            + `<td>${escapeAttr(source.protocol)}</td><td>${count}</td><td>${escapeAttr(source.note || '—')}</td>`;
          tbody.append(tr);
        }
        table.append(tbody); poolSection.append(table);
      }
      const consumers = (pool.usage && pool.usage.consumers) || {};
      const borrowed = Object.entries(consumers).filter(([, value]) => value && value.requests);
      if (borrowed.length) {
        const list = document.createElement('p'); list.className = 'agent-kv';
        list.textContent = borrowed.map(([who_, value]) => {
          const upstreams_ = Object.entries(value.upstreams || {}).map(([name, count]) => `${name} ×${count}`).join('、');
          return `${who_} 用了 ${upstreams_}`;
        }).join('；');
        poolSection.append(list);
      } else {
        const p = document.createElement('p'); p.className = 'agent-note';
        p.textContent = '还没有人从池子里借过额度。';
        poolSection.append(p);
      }
    }
    body.append(poolSection);

    // 2. 停滞提醒 —— 助手只判定与上报，不在这里处置（续期/重开/取消/改派都是管理员的事）。
    const stalls = section('卡住的事',
      `窗口还在线不代表它在干活 —— 只有平台记录上的状态变了才算真进展。连续 ${STALL_WINDOWS} 次没动静就标成卡住，再久就报给管理员。这里不提供处置按钮。`);
    if (!inflight.length) { const p = document.createElement('p'); p.className = 'agent-note'; p.textContent = '当前没有在办任务。'; stalls.append(p); }
    for (const item of inflight) {
      const row = document.createElement('article'); row.className = 'agent-row' + (item.escalated ? ' is-escalate' : item.stalled ? ' is-stall' : '');
      row.innerHTML = `<strong>${item.task.title || item.task.task_id}</strong>
        <span>${STATE_LABEL[item.task.status] || item.task.status} · ${item.owner || '无持有者'} · 距上次实质进展 ${Math.round(item.age / 1000)}s</span>
        <p>${item.escalated ? `连续 ${ESCALATE_WINDOWS} 个窗口无进展：需要管理员重新规划（改派、拆分或收口由管理员决定）。`
          : item.stalled ? `连续 ${STALL_WINDOWS} 个窗口无进展：建议管理员先看卡点，必要时向负责人要最小复现。` : '进度正常。'}</p>`;
      if (item.escalated || item.stalled) row.append(reportAction(item, 'staleness'));
      stalls.append(row);
    }
    body.append(stalls);

    // 2b. 失败与接手 —— 谁倒了、谁能接、怎么把"改派请求"递到管理员那里。
    // 办公室只列事实并**转达请求**：改派由管理员窗口决定并执行，UI 不派单。
    const failedTasks = tasks.filter(t => t.status === 'abandoned' || t.status === 'stale');
    const usableWindows = availability.filter(item => item.usable);
    const takeover = section('没做成的活',
      '哪条活没做成、哪些窗口现在还能接活（连它们用的哪个模型）。'
      + '要换人请按「请管理员换个人做」——那只是请求，换不换由管理员决定；'
      + '办公室自己不派单。');
    if (!failedTasks.length) {
      const p = document.createElement('p'); p.className = 'agent-note';
      p.textContent = '本项目没有失败或停滞的任务。';
      takeover.append(p);
    }
    for (const task of failedTasks) {
      const row = document.createElement('article'); row.className = 'agent-row';
      const owner = task.owner || '（无持有者）';
      const state = STATE_LABEL[task.status] || task.status;
      const candidates = usableWindows.map(item => ({
        id: item.harness_id,
        name: nameOf(item.harness_id),
        model: item.model || '未知模型',
      }));
      const head = document.createElement('p');
      head.textContent = `「${task.title || task.task_id}」${state} · 上次持有者 ${owner}`
        + (candidates.length ? ` · 现在能接的有 ${candidates.length} 个窗口` : ' · 现在没有能接活的窗口');
      row.append(head);
      // 为什么倒的：平台记下的最后一次 abandon/release 理由（不是猜的）。
      const reason = task.last_failure && task.last_failure.reason;
      if (reason) {
        const why = document.createElement('p'); why.className = 'agent-note';
        why.textContent = `失败原因：${reason}`;
        row.append(why);
      }
      if (!candidates.length) {
        const note = document.createElement('p'); note.className = 'agent-note is-missing';
        note.textContent = '所有登记窗口当前都不可用（凭据/失败/离线）：这时改派只会换个地方再倒。';
        row.append(note);
      }
      const actions = document.createElement('div'); actions.className = 'agent-actions';
      // 预算用完的活：只有人能给它"再给一次机会"（R10 §8 拒绝自动回环）。
      const spent = Number(task.attempt_no || 0) >= Number(task.max_attempts || 0);
      if (spent) {
        const again = actionButton('请求再给一次机会（预算豁免）', () => grantBudgetOverride(task),
          '这条活的尝试预算已经用完（R10 §8）。只有人能给它一次机会——平台和管理员窗口都不给自己豁免。');
        actions.append(again);
      }
      let select = null;
      if (candidates.length) {
        select = document.createElement('select');
        select.className = 'agent-select';
        const auto = document.createElement('option');
        auto.value = ''; auto.textContent = '由管理员按负载挑';
        select.append(auto);
        for (const candidate of candidates) {
          const option = document.createElement('option');
          option.value = candidate.id;
          option.textContent = `${candidate.name}（${candidate.model}）`;
          select.append(option);
        }
        actions.append(select);
      }
      const btn = actionButton(
        adminRecipient ? '请管理员换个人做' : '请管理员换个人做（本项目未指定管理员）',
        () => requestReassign(task, select ? select.value : ''),
        adminRecipient
          ? `把改派请求递到管理员窗口 ${nameOf(adminRecipient)}（由它决定并执行）`
          : '本项目还没有指定管理员窗口：请先在开工向导的「管理员」里选一个',
      );
      btn.disabled = !adminRecipient;
      actions.append(btn);
      row.append(actions);
      takeover.append(row);
    }
    body.append(takeover);

    // 2c. 方案定稿 —— 管理员细化 → 传阅 → 完善后的那一版，等**人**最终审核。
    const planSection = section('等你批准的方案',
      '管理员把要求拆成一版方案，给成员传阅收意见，再并进这一版。'
      + '下面是给人看的最终稿。'
      + '只有人点了「批准，开始做」，任务才会被创建、才能有人接。');
    if (planError) {
      const p = document.createElement('p'); p.className = 'agent-note is-missing';
      p.textContent = `读不到方案：${planError}`;
      planSection.append(p);
    } else if (planDoc && planDoc.draft && planDoc.draft.document) {
      const doc = planDoc.draft.document;
      const meta = document.createElement('p'); meta.className = 'agent-kv agent-origin';
      meta.textContent = `方案 ${doc.plan_id} · 第 ${doc.revision} 轮传阅后 · ${doc.tasks} 个任务`
        + ` · 文件：${doc.markdown_path}`;
      planSection.append(meta);
      // 方案正文是**Markdown 源码**（带 ##、表格竖线、反引号）。人读的是"这件事
      // 怎么做"，不是源码；再长也不该让人被一堵字墙挡住 —— 上面给结论，其余折叠。
      const docText = readableText(doc.markdown || '');
      const docLines = docText.split('\n');
      const shown = docLines.slice(0, 12);
      const rest = docLines.slice(12);
      const body = document.createElement('pre');
      body.className = 'agent-doc';
      body.textContent = shown.join('\n') || '（空）';
      planSection.append(body);
      if (rest.length) {
        const more = document.createElement('details'); more.className = 'agent-more';
        const summary = document.createElement('summary');
        summary.textContent = `方案还有 ${rest.length} 行`;
        const full = document.createElement('pre');
        full.className = 'agent-doc';
        full.textContent = rest.join('\n');
        more.append(summary, full);
        planSection.append(more);
      }
      const actions = document.createElement('div'); actions.className = 'agent-actions';
      const btn = actionButton('批准，开始做', () => approvePlan(planDoc.draft.plan_id),
        '由人最终审核：批准后这份方案才会变成可领取的任务');
      actions.append(btn);
      planSection.append(actions);
    } else {
      const p = document.createElement('p'); p.className = 'agent-note';
      p.textContent = '当前没有待审的方案稿（管理员细化后会在��里出现）。';
      planSection.append(p);
    }
    body.append(planSection);

    // 3. 能力观测
    const rank = section('谁干得好', '只统计平台回执上的可核验跃迁（提交/返修/放弃）。这是观测指标，不是能力认证，也不是许可。');
    // 全零的成员没有可看的指标，列出来只会把「本项目」撑成一长串名字。
    const measured = ranking.filter(r => r.total > 0);
    if (!measured.length) { const p = document.createElement('p'); p.className = 'agent-note'; p.textContent = '本项目还没有可核验的跃迁可统计。'; rank.append(p); }
    const rankTable = document.createElement('table'); rankTable.className = 'agent-table';
    rankTable.innerHTML = '<thead><tr><th>#</th><th>窗口</th><th>提交</th><th>返修</th><th>放弃</th><th>通过率</th><th>中位耗时</th></tr></thead>';
    const rbody = document.createElement('tbody');
    measured.forEach((r, index) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${index + 1}</td><td>${escapeAttr(r.name)}</td><td>${r.submissions}</td><td>${r.reworks}</td><td>${r.abandons}</td>
        <td>${r.score === null ? '未知' : Math.round(r.score * 100) + '%'}</td><td>${r.median === null ? '未知' : r.median + 's'}</td>`;
      rbody.append(tr);
    });
    rankTable.append(rbody); rank.append(rankTable); body.append(rank);

    // 4. 需要管理员处理的线索 —— 这里只给判断与上报，不做分工动作。
    const plan = section('要管理员处理的事',
      '助手只出线索：卡点、无人可派、重复派发。改派、拆分、重开、取消由管理员窗口决定；求助/接单始终走窗口侧流程，助手不代任何成员发起。');
    if (!suggestions.length) { const p = document.createElement('p'); p.className = 'agent-note'; p.textContent = '当前没有需要管理员重新规划的事项。'; plan.append(p); }
    for (const item of suggestions) {
      const row = document.createElement('article'); row.className = 'agent-row';
      const p = document.createElement('p'); p.textContent = item.text; row.append(p);
      row.append(reportAction(item, item.kind === 'reassign' || item.kind === 'no-worker' ? 'routing' : 'staleness'));
      plan.append(row);
    }
    if (dupes.length) {
      const head = document.createElement('p'); head.className = 'agent-note';
      head.textContent = `同名任务 ${dupes.length} 组：同一目标被重复派发会一起占队列与 worktree。保留哪一条、取消哪些由管理员决定，助手这里只报告。`;
      plan.append(head);
      for (const d of dupes.slice(0, 8)) {
        const item = dedupPlan(d, load);
        const row = document.createElement('article'); row.className = 'agent-row';
        row.innerHTML = `<strong>${d.title}</strong>
          <span>${d.count} 条 · ${d.tasks.map(t => `${t.task_id}（${STATE_LABEL[t.status] || t.status}）`).join(' / ')}</span>`;
        const note = document.createElement('p'); note.className = 'agent-note'; note.textContent = item.note;
        row.append(note, reportAction({ task: { task_id: d.tasks[0].task_id, title: d.title, status: d.tasks[0].status }, note: `${item.note}；本组共 ${d.count} 条：${d.tasks.map(t => `${t.task_id}（${STATE_LABEL[t.status] || t.status}）`).join(' / ')}` }, 'duplicate'));
        plan.append(row);
      }
      if (dupes.length > 8) {
        const more = document.createElement('p'); more.className = 'agent-note';
        more.textContent = `这里只列出前 8 组；其余 ${dupes.length - 8} 组同样报告给管理员。`;
        plan.append(more);
      }
    }
    body.append(plan);

    // 5. 积压登记（附首次时间）+ 催促记忆
    const backlogSec = section('攒着的事',
      `这里记着每件事从什么时候开始积压。超过 ${URGE_WINDOWS} 次没进展才提醒，而且那个窗口要是手上活太多就先不打扰；同一个窗口 ${Math.round(REMIND_COOLDOWN_MS / 60000)} 分钟内只提醒一次。`);
    // 只登记真的有数量的积压项；全是 0 时整张表都是噪音。
    const backlogged = derived.filter(d => d.count > 0);
    if (!backlogged.length) {
      const p = document.createElement('p'); p.className = 'agent-note';
      p.textContent = '本项目当前没有积压项。';
      backlogSec.append(p);
    } else {
      const bTable = document.createElement('table'); bTable.className = 'agent-table';
      bTable.innerHTML = '<thead><tr><th>积压项</th><th>数量</th><th>首次出现</th><th>已积压</th><th>说明</th></tr></thead>';
      const bbody = document.createElement('tbody');
      for (const d of backlogged) {
        const tr = document.createElement('tr'); tr.className = 'is-stall';
        tr.innerHTML = `<td>${d.label}</td><td>${d.count}</td><td>${new Date(d.first).toLocaleTimeString()}</td><td>${windowsOf(d.age)} 个窗口</td><td>${d.hint}</td>`;
        bbody.append(tr);
      }
      bTable.append(bbody); backlogSec.append(bTable);
    }
    // 超时/失败后的任务：abandoned 可由管理员重新打开回到 ready，stale 平台只允许取消。
    // 助手只列出与上报，不放处置按钮。
    const dead = tasks.filter(t => t.status === 'abandoned' || t.status === 'stale');
    if (dead.length) {
      const head = document.createElement('p'); head.className = 'agent-note';
      head.textContent = `超时/失败后待处理 ${dead.length} 条：abandoned 可回到 ready 重新领取，stale 平台只允许取消收口（无自动回收）。是否重新打开或取消由管理员窗口决定，助手只上报。`;
      // 之前只渲染前 8 条，剩下的任务在界面上根本点不到。加一个筛选框，
      // 默认仍是前 8 条，输入 task_id 或标题即可定位任意一条。
      const filter = document.createElement('input');
      filter.type = 'search'; filter.className = 'agent-filter';
      filter.placeholder = '按 task_id 或标题定位待处理任务';
      const list = document.createElement('div'); list.className = 'agent-dead-list';
      const renderDead = () => {
        const q = filter.value.trim().toLowerCase();
        const matched = q
          ? dead.filter(t => `${t.task_id} ${t.title || ''}`.toLowerCase().includes(q))
          : dead.slice(0, 8);
        list.textContent = '';
        for (const t of matched.slice(0, 40)) {
          const row = document.createElement('article'); row.className = 'agent-row';
          row.innerHTML = `<strong>${t.title || t.task_id}</strong>
            <span>${t.task_id} · ${STATE_LABEL[t.status] || t.status} · 持有者 ${t.owner || '无'}</span>`;
          row.append(reportAction({ task: t }, t.status === 'stale' ? 'stale' : 'abandoned'));
          list.append(row);
        }
        const note = document.createElement('p'); note.className = 'agent-note';
        if (!matched.length) note.textContent = '没有匹配的待处理任务。';
        else if (!q && dead.length > matched.length) note.textContent = `这里只列出前 ${matched.length} 条；输入 task_id 可定位其余 ${dead.length - matched.length} 条。`;
        else if (q && matched.length > 40) note.textContent = `匹配 ${matched.length} 条，只显示前 40 条。`;
        else note.textContent = '';
        if (note.textContent) list.append(note);
      };
      filter.addEventListener('input', renderDead);
      backlogSec.append(head, filter, list);
      renderDead();
    }
    if (!manual.length) { const p = document.createElement('p'); p.className = 'agent-note';
      p.textContent = '还没有登记的协调事项（例如等待 Qoder 签字的对账项）。可在下方登记，助手会按时限与目标负担决定是否催促。'; backlogSec.append(p); }
    for (const item of manual) {
      const row = document.createElement('article'); row.className = 'agent-row';
      const target = item.target || adminRecipient || null;
      const l = target ? load.get(target) : null;
      const busy = l ? (l.active + l.running + l.queued) : 0;
      const last = item.reminders.length ? item.reminders[item.reminders.length - 1] : null;
      const ripe = item.age >= URGE_WINDOWS * WINDOW_MS;
      const cooled = Boolean(last) && (now - last) < REMIND_COOLDOWN_MS;
      const canUrge = ripe && busy < LOAD_HOLD && !cooled && Boolean(target);
      const why = !target ? '没有收件人：请先在开工向导里指定管理员窗口（或登记时选一个目标）'
        : !ripe ? `未到催促窗口（已积压 ${windowsOf(item.age)}/${URGE_WINDOWS} 个窗口）`
        : busy >= LOAD_HOLD ? `${target} 当前在办+排队 ${busy} 条，暂缓催促以免影响其任务完成`
        : cooled ? `冷却中（最近催促 ${new Date(last).toLocaleTimeString()}）` : '可催促：不强制完成，只提醒安排';
      row.innerHTML = `<strong>${item.text}</strong>
        <span>目标 ${target || '未指定'} · 首次登记 ${new Date(item.first).toLocaleTimeString()} · 已积压 ${windowsOf(item.age)} 个窗口 · 已催促 ${item.reminders.length} 次</span>
        <p>${why}</p>`;
      const actions = document.createElement('div'); actions.className = 'agent-actions';
      const urge = actionButton(`催促 ${target || '目标'}（不强制完成）`, () => urgeBacklog(item));
      urge.disabled = !canUrge;
      actions.append(urge); row.append(actions); backlogSec.append(row);
    }
    // 登记表：输入框只是筛选/登记草稿，不是分派表单。目标默认是本项目的管理员窗口。
    const form = document.createElement('div'); form.className = 'agent-actions';
    const input = document.createElement('input'); input.type = 'text'; input.className = 'agent-input';
    input.placeholder = '登记协调事项，如：6 项 PENDING_ALIGNMENT 待 Qoder 签字';
    input.value = backlogDraft.text;
    input.addEventListener('input', () => { backlogDraft.text = input.value; });
    const targetSelect = document.createElement('select'); targetSelect.className = 'agent-input';
    targetSelect.innerHTML = '<option value="">收件人（默认管理员窗口）</option>' + ranking.map(r => `<option value="${escapeAttr(r.id)}">${escapeAttr(r.name)}</option>`).join('');
    targetSelect.value = backlogDraft.target;
    targetSelect.addEventListener('change', () => { backlogDraft.target = targetSelect.value; });
    form.append(input, targetSelect,
      actionButton('登记为积压项', () => registerBacklog(input.value, targetSelect.value)),
      actionButton('登记到平台群聊（附时间）', () => publishBacklog(), '把当前积压登记表写进项目群聊，作为平台侧留痕'));
    backlogSec.append(form); body.append(backlogSec);

    // 6. 中转播报
    const relay = section('播报', '事件与进度都是真的；只有语气参考人设。平台只接受管理员身份发布，所以消息作者是平台助手，不是成员本人。');
    const draft = draftNarration();
    if (!draft.lines.length) { const p = document.createElement('p'); p.className = 'agent-note'; p.textContent = '还没有巡检到可播报的状态跃迁。'; relay.append(p); }
    else {
      const preview = document.createElement('pre'); preview.className = 'agent-preview'; preview.textContent = draft.message.prompt;
      const publishBtn = document.createElement('button'); publishBtn.type = 'button'; publishBtn.className = 'primary'; publishBtn.textContent = '发布到项目群聊';
      publishBtn.addEventListener('click', () => publish(draft.message.prompt, draft.message.task_id));
      relay.append(preview, publishBtn);
    }
    body.append(relay);

    // 7. 待审查提醒 —— 审核是管理员窗口的动作，助手只提醒、不打开审核面。
    const review = section('等着审的', '机器验证不能替代人工验收。审核（领取 / 提交结论）是管理员窗口的动作；助手这里只列出待审任务，并把清单报给管理员。');
    const awaiting = tasks.filter(t => t.status === 'awaiting_review');
    if (!awaiting.length) { const p = document.createElement('p'); p.className = 'agent-note'; p.textContent = '当前没有待审查任务。'; review.append(p); }
    for (const task of awaiting) {
      const row = document.createElement('article'); row.className = 'agent-row';
      row.innerHTML = `<strong>${task.title || task.task_id}</strong><span>验证回执：${task.receipt_id || '无'} · 提交：${task.attempt_id || '无'}</span>`;
      row.append(reportAction({ task }, 'review'));
      review.append(row);
    }
    body.append(review);
    body.hidden = false;
  }

  async function publish(text, taskId) {
    try {
      await api('/messages', { body: text, mentions: [], ...(taskId ? { task_id: taskId } : {}), refs: [] });
      showAction(`已发布到项目群聊：${text.split('\n')[0].slice(0, 60)}…`);
    } catch (error) { showAction(`发布失败：${error.message}`, false); }
  }

  function actionButton(text, handler, title) {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = text; if (title) button.title = title;
    button.addEventListener('click', () => void handler());
    return button;
  }

  const openDispatch = () => {
    dialog.close();
    document.querySelector('#formalDispatchBtn, #formalDispatchPhoneBtn')?.click();
  };

  // 同名任务的分组只用于上报：保留哪一条、取消哪些是管理员的决定。
  // 这里给出的「建议保留」是确定性排序（已产出 > 可领取 > 已死），不触发任何写入。
  const DEDUP_RANK = { claimed: 3, in_progress: 3, verifying: 3, awaiting_review: 3, approved: 3, merging: 3, ready: 2, queued: 2, stale: 1 };
  function dedupPlan(group, load) {
    const rank = t => {
      const base = DEDUP_RANK[t.status] || 0;
      // 在办副本若持有者已经没有在跑的 worker（worker 被停或崩溃），它其实已经僵死，
      // 不该压过一条可以重新领取的 ready 副本。
      if (base === 3 && ACTIVE.has(t.status)) {
        const l = t.owner ? load.get(t.owner) : null;
        if (!l || !l.running) return 1.5;
      }
      return base;
    };
    const sorted = [...group.tasks].sort((a, b) => rank(b) - rank(a));
    const keep = sorted[0];
    const rest = sorted.slice(1);
    const orphan = rest.filter(t => ACTIVE.has(t.status) && !(t.owner && (load.get(t.owner) || {}).running));
    return { keep, rest, orphan,
      note: `按进度排序建议保留 ${keep.task_id}（${STATE_LABEL[keep.status] || keep.status}）；其余 ${rest.length} 条：${rest.map(t => t.task_id).join('、') || '无'}` +
        (orphan.length ? `（其中 ${orphan.map(t => t.task_id).join('、')} 的持有者已经没有在跑的 worker）` : '') };
  }

  // 收件人是本项目的管理员窗口（开工向导里选的那个外部窗口）。没有配置就明说，
  // 不自动挑一个成员顶替。正文是确定性推导的事实，不加评价、不替管理员做决定。
  // 上报的正文是完整事实，不能只靠 mention：网关的 mentions 只做提醒，正文同样要引用收件人。
  const REPORT_KIND = {
    staleness: '停滞 / 超时',
    routing: '无人可派 / 需要改派',
    duplicate: '重复派发',
    review: '待审查积压',
    abandoned: '已放弃任务待决定',
    stale: '租约失效待收口',
  };
  function reportText(item, kind) {
    const t = item.task || {};
    const lines = [`@${adminRecipient || '管理员'} 【平台助手上报 · ${REPORT_KIND[kind] || '异常'}】`,
      `任务：${t.title || t.task_id || '（未命名）'}（${t.task_id || '无 id'}）`,
      `状态：${STATE_LABEL[t.status] || t.status || '未知'}`];
    if (t.owner) lines.push(`持有者：${t.owner}`);
    if (item.age !== undefined) lines.push(`距上次实质进展：${Math.round(item.age / 1000)} 秒（${windowsOf(item.age)} 个进度窗口）`);
    if (item.note) lines.push(item.note);
    else if (item.text) lines.push(`线索：${item.text}`);
    lines.push('', '平台助手只做巡检与上报，不改变任务归属、不处置、不代替成员求助。请管理员决定是否重新规划。');
    return lines.join('\n');
  }
  function reportAction(item, kind) {
    const wrap = document.createElement('div'); wrap.className = 'agent-actions';
    const recipient = adminRecipient;
    // 按钮与提示上印登记名；@ 的还是 id（那是平台认的东西）。
    const recipientName = recipient ? nameOf(recipient) : '';
    const btn = actionButton(recipient ? `上报管理员（${recipientName}）` : '上报管理员（未配置收件人）',
      () => sendReport(item, kind),
      recipient ? `把这条上报写入项目群聊并 @ ${recipient}` : '本项目还没有指定管理员窗口：请先在开工向导里选择管理员');
    btn.disabled = !recipient;
    wrap.append(btn);
    return wrap;
  }
  async function sendReport(item, kind) {
    if (!adminRecipient) { showAction('上报失败：本项目还没有指定管理员窗口', false); return; }
    const t = item.task || {};
    try {
      await api('/messages', {
        body: reportText(item, kind),
        mentions: [adminRecipient],
        ...(t.task_id ? { task_id: t.task_id } : {}),
        refs: [],
      });
      showAction(`已上报 ${nameOf(adminRecipient)}：${REPORT_KIND[kind] || kind} · ${t.task_id || ''}`);
      // 上报即留痕：记进记忆清单，便于面板显示「已上报」。
      const key = `report:${kind}:${t.task_id || 'group'}`;
      const log = db.reminders[key] || (db.reminders[key] = []);
      log.push(Date.now()); saveStore(db);
      void refresh();
    } catch (error) { showAction(`上报失败：${error.message}`, false); }
  }

  // 把「请改派」这件事递到管理员那里。**只递请求**：改派由管理员窗口决定并
  // 执行（办公室既不派单，也不改任务归属）。
  async function requestReassign(task, target) {
    if (!adminRecipient) { showAction('请求失败：本项目还没有指定管理员窗口', false); return; }
    const usable = availability.filter(item => item.usable);
    const candidates = usable.map(item =>
      `${nameOf(item.harness_id)}（${item.model || '未知模型'}）`);
    const lines = [
      `@${adminRecipient} 平台助手转达的改派请求：`,
      `任务「${task.title || task.task_id}」（${task.task_id}）状态：${STATE_LABEL[task.status] || task.status}`,
      `上次持有者：${task.owner || '（无持有者）'}`,
      `现在能接活的窗口（平台侧事实）：${candidates.length ? candidates.join('、') : '（没有）'}`,
      `指定接手窗口：${target ? nameOf(target) : '由管理员按负载挑'}`,
      '',
      '这条消息只是请求：改派、收口、放弃由管理员窗口决定并执行，平台助手不派单。',
    ];
    const refs = ['reassign-request'];
    if (target) refs.push('reassign-to:' + target);
    try {
      await api('/messages', {
        body: lines.join('\n'),
        mentions: [adminRecipient],
        task_id: task.task_id,
        refs,
      });
      showAction(`已请求管理员改派 ${task.task_id}${target ? '（指定 ' + nameOf(target) + '）' : ''}`);
    } catch (error) { showAction(`请求失败：${error.message}`, false); }
  }

  // 人给一次机会：尝试预算用完的活，只有人能给它续一次。这是**人**的动作
  // （平台不给自己豁免，管理员窗口也不代劳）。
  async function grantBudgetOverride(task) {
    try {
      await api('/task/budget_override', {
        task_id: task.task_id,
        reason: '人给一次机会：尝试预算已用完',
      });
      showAction(`已给 ${task.task_id} 一次机会（预算豁免）`);
      void refresh();
    } catch (error) { showAction(`请求失败：${error.message}`, false); }
  }

  // 人的最终审核：批准方案 → 任务才出现。这是**人**的动作，平台助手只是
  // 把按钮摆在这儿，批不批由人决定。
  async function approvePlan(planId) {
    try {
      await api('/plan/approve', { plan_id: planId });
      showAction(`已批准方案 ${planId}：任务已创建，可以被领取`);
      void refresh();
    } catch (error) { showAction(`批准失败：${error.message}`, false); }
  }

  function registerBacklog(text, target) {
    const value = String(text || '').trim();
    if (!value) { showAction('登记失败：协调事项内容为空', false); return; }
    const key = 'coord:' + value.slice(0, 40);
    const entry = db.backlog[key] || (db.backlog[key] = { first: Date.now(), kind: 'coordination' });
    entry.text = value; entry.target = target || null; entry.last = Date.now();
    backlogDraft.text = '';
    saveStore(db);
    showAction(`已登记积压项（首次时间 ${new Date(entry.first).toLocaleTimeString()}）：${value.slice(0, 40)}`);
    void refresh();
  }

  async function urgeBacklog(item) {
    // 协调事项（例如等签字对账）的催促对象由登记人指定；没指定时交回管理员窗口。
    // 管理员在开工向导里选，平台助手不替用户挑一个成员顶替。
    const target = item.target || adminRecipient || null;
    if (!target) { showAction('催促失败：本项目还没有指定管理员窗口', false); return; }
    const message = createRoutedMessage({ sender: '平台助手',
      recipients: [{ instance_id: target, display_name: target, role: 'member' }],
      kind: 'backlog_reminder', subject: '积压事项催促（不强制完成）',
      body: `积压事项：${item.text}\n首次登记 ${new Date(item.first).toLocaleString()}，已积压 ${windowsOf(item.age)} 个进度窗口。\n\n这只是一条催促，不要求立刻完成，也不改变任何任务的归属与验收状态；请在不影响当前任务的前提下安排。`,
      action: 'remind' });
    try {
      await api('/messages', { body: message.prompt, mentions: [], refs: [] });
      const log = db.reminders[item.key] || (db.reminders[item.key] = []);
      log.push(Date.now()); saveStore(db);
      showAction(`已向 ${target} 发出催促（第 ${log.length} 次），已记入记忆清单，冷却 ${Math.round(REMIND_COOLDOWN_MS / 60000)} 分钟`);
    } catch (error) { showAction(`催促失败：${error.message}`, false); }
    void refresh();
  }

  async function publishBacklog() {
    if (!snapshot) { showAction('还没有巡检结果', false); return; }
    const lines = [
      ...snapshot.derived.filter(d => d.count).map(d => `· ${d.label} ${d.count} 条，首次出现 ${new Date(d.first).toLocaleString()}，已积压 ${windowsOf(d.age)} 个进度窗口`),
      ...snapshot.manual.map(m => `· [管理员登记] ${m.text}，首次登记 ${new Date(m.first).toLocaleString()}，已催促 ${m.reminders.length} 次`),
    ];
    if (!lines.length) { showAction('当前没有需要登记的积压项', false); return; }
    try {
      await api('/messages', { body: `积压登记（${new Date().toLocaleString()}）\n` + lines.join('\n'), mentions: [], refs: [] });
      showAction('积压登记已写入项目群聊（平台侧留痕，附时间）');
    } catch (error) { showAction(`登记失败：${error.message}`, false); }
  }

  async function refresh() {
    try {
      const [status, list, avail, poolData, planData] = await Promise.all([
        api('/status'),
        api('/tasks'),
        api('/availability').catch(error => ({ availability: [], error: String(error && error.message || error) })),
        api('/pool').catch(error => ({ error: String(error && error.message || error) })),
        api('/plan').catch(error => ({ error: String(error && error.message || error) })),
      ]);
      connected = Boolean(status.connected);
      harnesses = status.harnesses || [];
      availability = avail.availability || [];
      availabilityError = avail.error || null;
      pool = poolData && poolData.upstreams ? poolData : null;
      poolError = poolData && poolData.error ? poolData.error : null;
      planDoc = planData && planData.draft ? planData : null;
      planError = planData && planData.error ? planData.error : null;
      const groupTasks = list.tasks || [];
      groupTaskCount = groupTasks.length;
      // `/tasks` 是按 group 返回的：同一个群里并列着别的项目。先按当前项目的
      // taskIds ∪ 标题前缀过滤；下面的巡检、负担、积压、待审都只看这一份。
      tasks = groupTasks.filter(projectTaskMatch);
      // 成员范围只认「新建项目」时勾选的成员。`status.project_members` 是整群
      // 任务提到过的窗口并集（含管理员），拿它当范围就会把别的项目又拉回来。
      const scope = projectInfo();
      if (scope && Array.isArray(scope.members) && scope.members.length) setMemberScope(scope.members);
      patrol(Date.now());
      feedback(`巡检正常 · ${new Date(lastPatrolAt).toLocaleTimeString()} · 本项目 ${tasks.length} / 本群 ${groupTaskCount} 条任务 · 群 ${status.group_id}`);
      render();
    } catch (error) {
      connected = false;
      feedback(`无法读取正式平台：${error.message}`);
    }
  }

  function start() { void refresh(); if (timer) clearInterval(timer); timer = setInterval(() => void refresh(), POLL_MS); }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }
  // 开工确认后才知道管理员是谁（用户在向导里选的）。换管理员要重渲染，按钮与提示跟着变，
  // 但不清空巡检记录：同一个项目的进度是连续的。
  function setAdminHarness(id) {
    const next = typeof id === 'string' && id ? id : null;
    if (next === adminRecipient) return;
    adminRecipient = next;
    if (dialog.open) render();
  }

  const open = () => { if (!dialog.open) dialog.showModal(); start(); };
  button.addEventListener('click', open);
  if (phoneButton) phoneButton.addEventListener('click', open);
  $('[data-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', stop);
  dialog.addEventListener('cancel', stop);
  // 上一个项目留下的面板（按钮、对话框与运行中的轮询）必须随项目一起消失。
  dialog.addEventListener('close', () => { stop(); detach(); }, { once: true });
  const detach = () => { button.remove(); phoneButton?.remove(); };
  // 换项目时把前一个面板整体摘掉：按钮、对话框与轮询都跟着旧项目走。
  // 没有打开的对话框不派发 close 事件，此时直接移除。
  const unmount = () => { stop(); detach(); if (dialog.open) dialog.close(); else dialog.remove(); };

  Object.defineProperty(window, 'adminAgentState', { get: () => ({
    connected, pollMs: POLL_MS, stallWindows: STALL_WINDOWS,
    // 上报收件人：没有它时所有上报按钮都是禁用的。验证脚本要能直接读到这个事实，
    // 而不是去调用一个从未存在过的 `debug()`（那条断言因此在按钮全禁用时是死代码）。
    adminRecipient,
    tasks: tasks.length, inFlight: snapshot?.inflight.length ?? null,
    samples: Object.keys(db.samples).length, observed: db.observed, suggestions: snapshot?.suggestions.length ?? null,
    backlog: snapshot?.derived ?? null, manual: snapshot?.manual ?? null, reminders: db.reminders,
    deadQueues: snapshot?.deadQueues ?? null, dupes: snapshot?.dupes ?? null,
    dedup: (snapshot?.dupes ?? []).map(d => { const i = dedupPlan(d, snapshot.load); return { title: d.title, keep: i.keep.task_id, keepStatus: i.keep.status, reportOnly: i.rest.map(t => t.task_id) }; }),
    urgeWindows: URGE_WINDOWS, loadHold: LOAD_HOLD, remindCooldownMs: REMIND_COOLDOWN_MS,
  }) });

  return { open, unmount, setAdminHarness, debug: () => ({ connected, lastPatrolAt, adminRecipient }) };
}
