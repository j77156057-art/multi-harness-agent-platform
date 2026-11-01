/**
 * 转接的只读视图：把 `relay/` 那个信箱读成界面能用的一小份事实。
 *
 * 为什么要有它：转接跑起来之后，邮件都在磁盘上（`outbox/ threads/ returned/`），
 * 人打开办公室**一条都看不到** —— 转接出来的消息带 `direct`，项目群聊面板会把它
 * 过滤掉（那是 §19 的读权限，不动）。所以另给一处只读的地方：线程几跳、谁在里面、
 * 哪封没投出去、为什么。
 *
 * 三条口径：
 *   1. **只读**：这里一个字节都不写，也不碰 archive/ 里的信正文（看进度不需要正文）。
 *   2. **坏账本要露出来**：读不出来的线程不能悄悄消失 —— 那会让人以为"这条线索没了"。
 *      所以它照样出现，只是标成「账本读不出来」。
 *   3. **在不在跑是事实，不是猜**：relay worker 每次轮询都会碰一下
 *      `.worker-heartbeat`；心跳超过 5 分钟就当没在跑。菜单里不写"应该正在运行"。
 */
import fs from 'node:fs';
import path from 'node:path';

/** worker 每次轮询碰一下这个文件；界面据此判断"平台侧到底有没有在转接"。 */
export const HEARTBEAT_FILE = '.worker-heartbeat';
/** 转接项目自己那份说明：仓库是转接的事实源，项目不该只活在浏览器记忆里。 */
export const PROJECT_FILE = 'project.json';
/** 心跳超过这个时间就当没在跑（worker 默认每 5 秒轮询一次）。 */
export const RUNNING_WINDOW_MS = 5 * 60 * 1000;

const MAX_THREADS = 200;
const MAX_RETURNED = 100;
const MAX_PENDING = 200;

function names(dir, predicate) {
  try {
    return fs.readdirSync(dir).filter(predicate).sort();
  } catch {
    // 目录还不存在 = 这个项目还没开始转接，不是错误。
    return [];
  }
}

function readText(file) {
  try {
    // utf-8-sig：Windows 上编辑过的账本常带 BOM，剥掉才解析得动。
    return fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
  } catch {
    return null;
  }
}

/**
 * 退回信的原因：`.reason.md` 里那行 `- 原因：…`。
 *
 * 读不到就照实说"原因没说清楚"，不要编一句 —— 人得知道这封为什么没出去。
 */
function reasonOf(text) {
  if (!text) return '';
  const matched = /^-\s*原因[：:]\s*(.+)$/m.exec(text);
  if (matched) return matched[1].trim();
  const firstLine = text.split(/\r?\n/).find((line) => line.trim() && !line.startsWith('#'));
  return firstLine ? firstLine.trim().replace(/^-\s*/, '') : '';
}

function threadRow(file, name) {
  const text = readText(file);
  const fallbackId = name.replace(/\.json$/i, '');
  if (text === null) {
    return { thread_id: fallbackId, hops: 0, participants: [], updated_at: '', unreadable: '账本读不出来（文件打不开）' };
  }
  try {
    const payload = JSON.parse(text);
    return {
      thread_id: String(payload.thread_id || fallbackId),
      hops: Number(payload.hops || 0),
      participants: (Array.isArray(payload.participants) ? payload.participants : []).map(String).filter(Boolean),
      updated_at: String(payload.updated_at || payload.created_at || ''),
      unreadable: '',
    };
  } catch {
    // 坏账本照样列出来：它是"有一条线索，但是平台自己读不出来"，不是"没有这条线索"。
    return { thread_id: fallbackId, hops: 0, participants: [], updated_at: '', unreadable: '账本读不出来（内容不是合法 JSON）' };
  }
}

/**
 * 仓库里写明的那个转接项目（`relay/project.json`）。
 *
 * 为什么项目要落在仓库里：转接的信就在仓库里。换一台机器 clone 下来，浏览器历史是空的，
 * 但信箱里有 9 条线程 —— 这时候界面说"还没有项目"就是在撒谎。所以项目名与创建时间跟着
 * 信箱走，界面认仓库。**只认这一份文件，不扫全仓库**；也**不记成员、不记凭据**。
 */
export function readRelayProject(base) {
  const text = readText(path.join(base, PROJECT_FILE));
  if (text === null) return { project: null, project_note: '' };
  try {
    const payload = JSON.parse(text);
    const name = String(payload.name || '').trim();
    if (!name) return { project: null, project_note: '项目说明里没有名字' };
    return {
      project: {
        name,
        created_at: String(payload.created_at || ''),
        updated_at: String(payload.updated_at || ''),
      },
      project_note: '',
    };
  } catch {
    // 读不出来就说读不出来：静默当成"没有项目"会让人以为记录丢了。
    return { project: null, project_note: `${PROJECT_FILE} 读不出来（内容不是合法 JSON）` };
  }
}

/**
 * 把项目名写进仓库（`relay/project.json`）。只在人新建转接项目时调一次。
 *
 * 幂等：已经有这份文件时保留 `created_at`，只更新名字与 `updated_at` —— 改名不该把
 * "什么时候建的"改掉。
 */
export function writeRelayProject({ root, name, now = new Date() } = {}) {
  const base = path.resolve(String(root || '.'));
  const clean = String(name || '').trim().slice(0, 80);
  if (!clean) throw new Error('relay_project_name_required');
  fs.mkdirSync(base, { recursive: true });
  const file = path.join(base, PROJECT_FILE);
  let created = now.toISOString();
  const existing = readText(file);
  if (existing !== null) {
    try {
      const payload = JSON.parse(existing);
      if (payload.created_at) created = String(payload.created_at);
    } catch {
      // 旧文件坏了就当没有：现在这份是新的。
    }
  }
  const payload = { name: clean, created_at: created, updated_at: now.toISOString() };
  fs.writeFileSync(file, JSON.stringify(payload, null, 1) + '\n', { encoding: 'utf8' });
  return payload;
}

/**
 * 读一份转接概况。
 *
 * @param {{root: string, now?: number}} options `root` 是信箱根目录（`relay/`）。
 */
export function readRelayOverview({ root, now = Date.now() } = {}) {
  const base = path.resolve(String(root || '.'));
  const exists = (() => {
    try {
      return fs.statSync(base).isDirectory();
    } catch {
      return false;
    }
  })();
  if (!exists) {
    return {
      running: false,
      heartbeat_at: '',
      root: base,
      threads: [],
      returned: [],
      pending: 0,
      project: null,
      project_note: '',
      note: '这台机器上还没有转接信箱（relay/），说明平台侧没在跑转接。',
    };
  }

  const threads = names(path.join(base, 'threads'), (name) => /\.json$/i.test(name))
    .slice(-MAX_THREADS)
    .map((name) => threadRow(path.join(base, 'threads', name), name))
    .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));

  const returned = names(path.join(base, 'returned'), (name) => /\.reason\.md$/i.test(name))
    .slice(-MAX_RETURNED)
    .reverse()
    .map((name) => {
      const letter = name.replace(/\.reason\.md$/i, '');
      const file = path.join(base, 'returned', name);
      const text = readText(file);
      const hasLetter = fs.existsSync(path.join(base, 'returned', letter));
      return {
        letter,
        reason: reasonOf(text) || '（这份原因文件里没写清楚，去 relay/returned/ 看原文）',
        has_letter: hasLetter,
      };
    });

  const pending = names(path.join(base, 'outbox'), (name) => /\.md$/i.test(name)).slice(0, MAX_PENDING).length;

  let heartbeatAt = '';
  let running = false;
  try {
    const stat = fs.statSync(path.join(base, HEARTBEAT_FILE));
    heartbeatAt = stat.mtime.toISOString();
    running = now - stat.mtime.getTime() <= RUNNING_WINDOW_MS;
  } catch {
    running = false;
  }

  const { project, project_note } = readRelayProject(base);

  return {
    running,
    heartbeat_at: heartbeatAt,
    root: base,
    threads,
    returned,
    pending,
    project,
    project_note,
    note: '',
  };
}
