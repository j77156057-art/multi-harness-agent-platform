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
