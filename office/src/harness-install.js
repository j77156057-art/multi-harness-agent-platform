/**
 * 「这台电脑上还缺哪个 CLI」—— 只负责把平台给的事实画成人能读的一屏。
 *
 * 平台那一侧（`GET /harnesses/installers`）回答两件事：官方从哪装，以及
 * 这台机器上现在有没有。这里不猜任何东西：
 *
 *   · 状态词只从 `ready / missing / version_failed / unsupported / unknown` 里来，
 *     认不出的状态照实写出来，不折成「正常」；
 *   · 一条命令都不替人执行 —— 安装命令是给人复制去自己终端里跑的；
 *   · 「装好了」与「能干活」分开说：版本读得出来只证明命令在，登录与 provider
 *     配置还归人（`login_note`）。
 *
 * 为什么要单独一屏：陌生人把仓库 clone 到另一台电脑上，第一步就不是读代码，
 * 而是「这六个 CLI 我去哪装」。这件事埋在文档里等于没有。
 */

import { shortenPath } from './cli-transcript.js';
import { escapeHtml as escape } from './html-escape.js';

/** 平台给的状态 -> 人读的一句话。顺序就是该先看哪个。 */
export const INSTALL_STATUS = {
  missing: { label: '未安装', tone: 'bad' },
  version_failed: { label: '装了但读不到版本', tone: 'warn' },
  ready: { label: '已找到', tone: 'ok' },
  unsupported: { label: '这个系统官方不支持', tone: 'muted' },
  unknown: { label: '还没探测', tone: 'muted' },
};

/** 分组时谁在前面：缺的那几个才是人现在要动的事。 */
const STATUS_ORDER = ['missing', 'version_failed', 'unknown', 'ready', 'unsupported'];

export function statusOf(row) {
  const key = String(row?.status || 'unknown');
  return INSTALL_STATUS[key] || { label: key || '未知状态', tone: 'muted' };
}

/** 按状态分组；组内按 harness_id 排，保证每次刷新顺序一致。 */
export function groupByStatus(rows) {
  const groups = new Map();
  for (const row of rows || []) {
    const key = String(row?.status || 'unknown');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  const ordered = [];
  for (const key of STATUS_ORDER) {
    if (groups.has(key)) ordered.push({ status: key, rows: groups.get(key) });
  }
  // 平台以后加了新状态词，也要露出来（不能悄悄丢掉一行）。
  for (const [key, value] of groups) {
    if (!STATUS_ORDER.includes(key)) ordered.push({ status: key, rows: value });
  }
  for (const group of ordered) {
    group.rows.sort((a, b) => String(a.harness_id).localeCompare(String(b.harness_id)));
  }
  return ordered;
}

/**
 * 这份清单是不是「只有官方安装方式、还没探测这台机器」。
 *
 * 平台的字段名是 `guidance_only`（snake_case，和它其余字段一致）。之前这里读的是
 * `guidanceOnly`，永远读到 undefined，于是「还没探测」被说成「0 个已找到」——
 * 那是在替这台机器断言一件没查过的事。两种写法都认，但平台的写法优先。
 */
export function guidanceOnly(payload) {
  if (!payload) return false;
  if (typeof payload.guidance_only === 'boolean') return payload.guidance_only;
  return Boolean(payload.guidanceOnly);
}

/**
 * 一句话概括：几个能用、几个缺。
 *
 * 还没探测时不这么说 —— 那是「还没探测」，说成「全都缺」或「0 个已找到」都是骗人。
 */
export function summaryLine(payload) {
  const rows = (payload && payload.installers) || [];
  if (!rows.length) return '平台没有登记任何可安装的 Harness CLI。';
  if (guidanceOnly(payload)) {
    return `下面是 ${rows.length} 个 Harness CLI 的官方安装方式；这台机器还没探测。`;
  }
  const counts = (payload && payload.summary) || {};
  const ready = Number(counts.ready || 0);
  const missing = Number(counts.missing || 0);
  const broken = Number(counts.version_failed || 0);
  const parts = [`${ready} 个已找到`];
  if (missing) parts.push(`${missing} 个未安装`);
  if (broken) parts.push(`${broken} 个装了但读不到版本`);
  const unsupported = Number(counts.unsupported || 0);
  if (unsupported) parts.push(`${unsupported} 个在这个系统上官方不支持`);
  return `这台机器上 ${parts.join('、')}（共 ${rows.length} 个）。`;
}

/** 探测的是哪台机器 —— 后端在哪台，就探哪台。含糊过去会让人以为探的是自己这台。 */
export function hostLine(payload) {
  const platform = (payload && payload.platform) || {};
  if (!platform.os) return '';
  const os = { windows: 'Windows', macos: 'macOS', linux: 'Linux' }[platform.os] || platform.os;
  const arch = platform.arch ? ` · ${platform.arch}` : '';
  return `探测的是运行平台的这台机器（${os}${arch}）。`;
}

/**
 * 一条 CLI 该显示成什么。
 *
 * 注意 `verified`：只有平台明确说探测过、且状态是 ready，才算「命令在」。
 * 其余一律不当成可用 —— 尤其 `unknown` 不能被读成「应该没问题」。
 */
export function rowView(row) {
  const status = statusOf(row);
  return {
    id: String(row?.harness_id || ''),
    name: String(row?.display_name || row?.harness_id || ''),
    provider: String(row?.provider || ''),
    status: String(row?.status || 'unknown'),
    statusLabel: status.label,
    tone: status.tone,
    // 「装上能不能在这个平台上接活」。旧后端没有这个字段：那时清单里只有
    // 平台能驱动的几种，所以缺省为 true 才是对的。
    driven: row?.driven !== false,
    command: String(row?.command || ''),
    foundCommand: row?.found_command ? String(row.found_command) : '',
    path: row?.path ? String(row.path) : '',
    version: row?.version ? String(row.version) : '',
    detail: row?.detail ? String(row.detail) : '',
    officialUrl: String(row?.official_url || ''),
    installCommand: String(row?.install_command || ''),
    verifyCommand: String(row?.verify_command || ''),
    loginNote: String(row?.login_note || ''),
    notes: Array.isArray(row?.notes) ? row.notes.map(String) : [],
    supported: row?.supported !== false,
    verified: String(row?.status || '') === 'ready',
  };
}

export function rowsView(payload) {
  return ((payload && payload.installers) || []).map(rowView);
}

/**
 * 「装好之后还差什么」—— 平台查不到登录状态，所以只能提醒，不能断言。
 * 说成「还要登录」而不是「没登录」，因为后者是平台不知道的事。
 */
export function leftoverHint(view) {
  if (!view || !view.verified) return '';
  return view.loginNote
    ? `命令已可用；是否已登录平台查不到，需要你确认：${view.loginNote}`
    : '命令已可用；平台不读登录状态，是否已登录需要你自己确认。';
}

/**
 * 一条 CLI 的完整说明（HTML 片段）。
 *
 * 顺序就是人要做的事的顺序：**这是什么 → 官方从哪拿 → 装什么命令 → 怎么自检 →
 * 装完还差什么**。「装完还差什么」放最后，因为那一步平台查不到，只能提醒。
 *
 * 这段 HTML 给两处共用（大厅的「快速开始」与办公室的「开始使用」）—— 一处改、
 * 两处一样，免得同一台机器在两屏里有两种说法。
 */
export function installRowHtml(view, { showStatus = false } = {}) {
  const bits = [];
  if (view.version) bits.push(`版本 ${view.version}`);
  if (view.foundCommand) bits.push(`命令 ${view.foundCommand}`);
  if (view.path) bits.push(shortenPath(view.path));
  const hint = leftoverHint(view);
  return `<li>
      <b>${escape(view.name)}${showStatus ? ` <span class="flat-tier">${escape(view.statusLabel)}</span>` : ''}</b>
      <span>${escape(bits.join(' · ') || view.provider || '')}</span>
      ${view.detail ? `<span class="flat-note">${escape(view.detail)}</span>` : ''}
      ${
        view.officialUrl
          ? `<a href="${escape(view.officialUrl)}" target="_blank" rel="noreferrer noopener">官方页面</a>`
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

/** 默认哪几组是展开的：缺的与装了但读不到版本的 —— 那才是人现在要动的事。 */
export const OPEN_STATUSES = ['missing', 'version_failed'];

/**
 * 一句话说清「装上它能不能在这里接活」。
 *
 * 清单里有两类 CLI：平台有适配器的（装上、登录，就能接任务），和市面上常见但
 * 平台还没接上的（能单独用，装了也不会在这里接活）。这句话必须出现，否则人按
 * 清单装了一圈，回到办公室发现没人可派。
 */
export function drivenLine(payload) {
  const rows = rowsView(payload);
  if (!rows.length) return '';
  const driven = rows.filter((row) => row.driven).length;
  const reference = rows.length - driven;
  if (!reference) return `这 ${rows.length} 个都能在本平台接任务。`;
  if (!driven) return `这 ${rows.length} 个都还没有适配器，装上也只能单独使用。`;
  return `其中 ${driven} 个平台可以直接驱动（装上并登录后可接任务），另外 ${reference} 个只能单独使用。`;
}

/**
 * 按状态分组的一串 CLI（HTML 片段）。
 *
 * 两张单子分开摆：
 *   1. **平台能驱动的**，按状态分组 —— 缺的那几组默认展开，因为那是人现在要动的事；
 *   2. **市面上常见的其他 CLI**，单独一组默认收起 —— 装着有用，但在这里接不了任务。
 *
 * 混在一起就得逐条读状态和归属；分开之后"我该装哪个"是一眼的事。
 */
export function installGroupsHtml(payload, { openStatuses = OPEN_STATUSES } = {}) {
  const rows = rowsView(payload);
  const open = new Set(openStatuses);
  const driven = groupByStatus(rows.filter((row) => row.driven))
    .map(
      (group) => `<details class="flat-pool-item"${open.has(group.status) ? ' open' : ''}>
        <summary><b>${escape(statusOf({ status: group.status }).label)}</b><span>${group.rows.length} 个</span></summary>
        <ul class="flat-list">${group.rows.map((row) => installRowHtml(row)).join('')}</ul>
      </details>`,
    )
    .join('');
  const reference = rows.filter((row) => !row.driven);
  const others = reference.length
    ? `<details class="flat-pool-item">
        <summary><b>市面上其他 CLI</b><span>${reference.length} 个</span></summary>
        <p class="flat-note">这些 CLI 可以单独使用；本平台还没有把它们接上，装上也不会在这里接任务。安装方式同样来自各家官方渠道。</p>
        <ul class="flat-list">${reference.map((row) => installRowHtml(row, { showStatus: true })).join('')}</ul>
      </details>`
    : '';
  return driven + others;
}
