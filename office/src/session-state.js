/**
 * 刷新之后还该在的东西：**没提交完的草稿**，和**刚才看到哪一页**。
 *
 * 为什么需要：「装 CLI」的探测结果已经在 `install-cache.js` 里跨重载保住了，同一类
 * 毛病还有两处 ——
 *   · 表单草稿：`drafts` 只活在内存里，每 15 秒的重画不会丢，但整页重载（刷新、
 *     换标签页、开发时热重载）会丢。正在填「加一个 key」的地址、刚在群聊输入框里
 *     敲了一段的半句话，都会消失。
 *   · 视图状态：筛选、排序、每页条数、翻到第几页、看的是哪个会话、哪些折叠卡片
 *     开着 —— 刷新后全回到默认。人筛出「待领取」正要看，一刷新就回到「全部」。
 *
 * 存哪里，两半的答案不一样：
 *   · 草稿进 **sessionStorage** —— 它是「这一次没做完的事」，关掉标签页就该消失。
 *     放 localStorage 会让同一台电脑上的下一个人看到上一个人没提交的半句话。
 *   · 视图状态也进 sessionStorage —— 它描述的是「这个人这一会儿在看什么」，
 *     不是这台机器的客观事实（不像装了哪些 CLI）。关掉标签页再开，回到默认视图
 *     是合理的，也很安全。
 *
 * **凭据一个字节都不落盘。** 两条防线：
 *   1. 字段名像凭据就跳过：拆开名字按词匹配 `key` / `token` / `secret` /
 *      `password` / `credential` / `auth` / `cookie`，另外整串匹配
 *      `apikey`、`accesskey`、`secretkey`、`privatekey`、`authorization`。
 *      所以 `userkey-key` 永远不存（这是用户明确要求的那一条），而 `userkey-label`、
 *      `userkey-base` 这类不是凭据的照样存 —— 刷新后不用重填地址，只补 key 就行。
 *   2. **文本里像凭据的也不存**：有人会把 key 贴进群聊输入框，那不该被存下来。
 *      命中常见凭据形状（`sk-…`、`ghp_…`、`AKIA…`、`AIza…`、PEM 头、
 *      `Bearer …`）就跳过这一格 —— 那一格只是不跨刷新，别的照常。
 *
 * 读回来的东西一律当**不可信输入**校验：坏了就按没存处理，绝不抛异常把整屏带崩。
 */

const DRAFT_KEY = 'agent-office.drafts.v1';
const VIEW_KEY = 'agent-office.view.v1';

// 字段名里出现这些**词**（按 - 和 _ 拆开算）就当凭据。
const SECRET_WORDS = new Set([
  'key',
  'keys',
  'token',
  'secret',
  'secretkey',
  'password',
  'passwd',
  'pwd',
  'credential',
  'credentials',
  'auth',
  'authorization',
  'cookie',
  'apikey',
]);

// 拆不开也认得的写法。
const SECRET_SUBSTRINGS = [
  'apikey',
  'accesskey',
  'secretkey',
  'privatekey',
  'authorization',
  'bearer',
];

// 文本里的凭据形状。宁可少存一格，也不要存错一格。
const SECRET_VALUE_PATTERNS = [
  /\bsk-[A-Za-z0-9_-]{8,}/,
  /\bsk_(live|test)_[A-Za-z0-9]{8,}/,
  /\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}/,
  /\bgithub_pat_[A-Za-z0-9_]{16,}/,
  /\bAKIA[0-9A-Z]{12,}/,
  /\bAIza[0-9A-Za-z_-]{20,}/,
  /\bxox[baprs]-[A-Za-z0-9-]{8,}/,
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  /\bBearer\s+[A-Za-z0-9._-]{16,}/,
];

// 视图状态里认得的取值：只认这几组，别的当没存（避免旧版本留下一个不存在的
// 选项让界面画出一片空白）。
const VIEW_FILTERS = new Set(['all', 'waiting', 'active', 'review', 'failed']);
const VIEW_SORTS = new Set(['updated', 'status', 'attempts', 'title']);
const VIEW_GROUPS = new Set(['person', 'time']);
const VIEW_PAGE_SIZES = new Set([20, 50, 200]);
const VIEW_KINDS = new Set(['tasks', 'group', 'assistant', 'window', 'alerts', 'model-test']);

// 折叠卡片的键最多记这么多条：卡片键一般是个固定名字，但万一某个键里带任务号，
// 无上限地攒下去会把存储塞满。
const MAX_FOLDS = 200;

function storage() {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** 这个名字是不是一节凭据（拆开按词匹配 + 整串匹配）。 */
export function isSecretDraft(name) {
  const text = String(name || '').trim();
  if (!text) return false;
  const lower = text.toLowerCase();
  for (const needle of SECRET_SUBSTRINGS) {
    if (lower.replace(/[-_\s]/g, '').includes(needle)) return true;
  }
  const words = lower.split(/[-_\s.]+/).filter(Boolean);
  return words.some((word) => SECRET_WORDS.has(word));
}

/** 这段文本看起来像不像一节凭据。 */
export function looksLikeSecret(value) {
  if (typeof value !== 'string' || !value) return false;
  return SECRET_VALUE_PATTERNS.some((pattern) => pattern.test(value));
}

/** 这一格草稿能不能落盘：名字不是凭据，内容也不像凭据。 */
export function isPersistableDraft(name, value) {
  if (!name) return false;
  if (typeof value !== 'string' || !value) return false;
  if (isSecretDraft(name)) return false;
  return !looksLikeSecret(value);
}

/** 读草稿：`{[名字]: 值}`，坏的/凭据的一律丢掉。 */
export function readDrafts() {
  const store = storage();
  if (!store) return {};
  try {
    const raw = store.getItem(DRAFT_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const kept = {};
    for (const [name, value] of Object.entries(parsed)) {
      if (isPersistableDraft(name, value)) kept[name] = value;
    }
    return kept;
  } catch {
    return {};
  }
}

/** 写草稿：只写能落盘的那几格；一格都不剩就把键删掉（别留个空壳）。 */
export function writeDrafts(fields) {
  const store = storage();
  if (!store) return false;
  try {
    const kept = {};
    for (const [name, value] of Object.entries(fields || {})) {
      if (isPersistableDraft(name, value)) kept[name] = value;
    }
    if (!Object.keys(kept).length) {
      store.removeItem(DRAFT_KEY);
      return true;
    }
    store.setItem(DRAFT_KEY, JSON.stringify(kept));
    return true;
  } catch {
    // 存不下（隐私模式、配额）只意味着这一格不跨刷新，不影响正事。
    return false;
  }
}

/** 忘掉一格草稿（提交成功后调用，否则重画会把刚发出去的话又填回输入框）。 */
export function forgetDraft(name) {
  const store = storage();
  if (!store || !name) return;
  try {
    const kept = readDrafts();
    if (!(name in kept)) return;
    delete kept[name];
    writeDrafts(kept);
  } catch {
    // 忘不掉最坏就是重画时又填回去，下次提交还会再忘一遍。
  }
}

/** 清空全部草稿（测试与「别处重置」用）。 */
export function clearDrafts() {
  try {
    storage()?.removeItem(DRAFT_KEY);
  } catch {
    // 清不掉就算了：readDrafts 自己会挡住凭据。
  }
}

function positiveInt(value, allowed) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < 1) return null;
  if (allowed && !allowed.has(number)) return null;
  return number;
}

function foldList(raw) {
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  for (const item of raw) {
    if (typeof item !== 'string' || !item) continue;
    seen.add(item);
    if (seen.size >= MAX_FOLDS) break;
  }
  return [...seen];
}

/**
 * 读视图状态：只返回**认得出来**的字段，认不出的直接不带 —— 调用方保留自己的默认值。
 * 没存过就返回 `{}`（等价于"按默认摆"）。
 */
export function readViewState() {
  const store = storage();
  if (!store) return {};
  try {
    const raw = store.getItem(VIEW_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const kept = {};
    if (VIEW_FILTERS.has(parsed.filter)) kept.filter = parsed.filter;
    if (VIEW_SORTS.has(parsed.sort)) kept.sort = parsed.sort;
    if (VIEW_GROUPS.has(parsed.groupBy)) kept.groupBy = parsed.groupBy;
    const size = positiveInt(parsed.pageSize, VIEW_PAGE_SIZES);
    if (size) kept.pageSize = size;
    const page = positiveInt(parsed.page);
    if (page && page <= 10000) kept.page = page;
    if (parsed.view && typeof parsed.view === 'object' && VIEW_KINDS.has(parsed.view.kind)) {
      const id = typeof parsed.view.id === 'string' && parsed.view.id ? parsed.view.id : null;
      // `window` 必须带 id：没有 id 的窗口页画不出来。
      if (parsed.view.kind !== 'window' || id) kept.view = { kind: parsed.view.kind, id };
    }
    const open = foldList(parsed.openFolds);
    const closed = foldList(parsed.closedFolds);
    if (open.length) kept.openFolds = open;
    if (closed.length) kept.closedFolds = closed;
    return kept;
  } catch {
    return {};
  }
}

/** 写视图状态；写失败不影响界面。 */
export function writeViewState(viewState) {
  const store = storage();
  if (!store) return false;
  try {
    store.setItem(VIEW_KEY, JSON.stringify(viewState || {}));
    return true;
  } catch {
    return false;
  }
}

/** 清空视图状态（测试用）。 */
export function clearViewState() {
  try {
    storage()?.removeItem(VIEW_KEY);
  } catch {
    // 同上。
  }
}
