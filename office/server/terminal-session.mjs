// 终端会话管理：票据、PTY、空闲回收、脱敏日志。
//
// 为什么单独一个模块：网关那一坨已经很长，而这里的东西（一个 PTY 进程 + 一张一次
// 性票据 + 一个日志文件）有自己的生命周期，独立出来才能单独测。
//
// 三条纪律，都是实测换来的：
//   1. **只用 ConPTY，且关闭一律走 `taskkill`**。node-pty 在 Windows 上 `kill()`
//      会去 spawn 一个 helper 子进程枚举控制台进程表；服务端进程通常没有控制台，
//      那个 helper 直接崩 `AttachConsole failed`（2026-11-01 实测）。`taskkill
//      /PID <shell> /T /F` 干净、pid 确实消失。
//   2. **票据一次性 + 短命**。浏览器给 websocket 加不了自定义头，所以先用一次正常
//      的同源 POST 换票；票只活 60 秒，用一次就作废。
//   3. **日志脱敏**。人在终端里可能粘真 key —— 落盘前把凭据形状的串换掉，界面也要
//      明说"这个会话会留痕"。
//
// 这个模块不认识 HTTP：谁调它谁负责门禁（loopback / 同源 / 只给人开）。
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { randomBytes } from 'node:crypto';

//: vite 把 server/*.mjs 当 ESM 加载，CJS 依赖（node-pty）要经 createRequire 拿。
const requireFromHere = createRequire(import.meta.url);

//: 同时最多几个会话。终端是给人救急用的，不是给流水线当后端。
const MAX_SESSIONS = 2;
//: 多久没有输入就回收（毫秒）。留着不管会一直占着一个 shell。
const IDLE_TIMEOUT_MS = 15 * 60 * 1000;
//: 票据寿命。够浏览器拿到票再握手，不够被抄走复用。
const TICKET_TTL_MS = 60 * 1000;

//: 落盘前要遮掉的东西：平台凭据、常见 key 形状、PEM 头。
const SECRET_PATTERNS = [
  /apt_[a-f0-9]{64}/g,
  /\bsk-[A-Za-z0-9_-]{8,}/g,
  /\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}/g,
  /\bAKIA[0-9A-Z]{12,}/g,
  /\bAIza[0-9A-Za-z_-]{20,}/g,
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/g,
];
const REDACTED = '<hidden>';

export function redact(text) {
  let value = String(text ?? '');
  for (const pattern of SECRET_PATTERNS) value = value.replace(pattern, REDACTED);
  return value;
}

function loadPty() {
  try {
    return requireFromHere('node-pty');
  } catch {
    return null;
  }
}

/**
 * @param {object} options
 * @param {string} options.repoRoot   终端的工作目录
 * @param {string} options.dataDir    会话日志落盘处（<dataDir>/terminals）
 * @param {string} [options.shell]    用哪个 shell，默认 Windows 上是 powershell.exe
 * @param {number} [options.maxSessions]
 * @param {number} [options.idleTimeoutMs]
 * @param {() => number} [options.now] 注入时钟，测试用
 * @param {object} [options.ptyModule] 注入 node-pty，测试用
 */
export function createTerminalSessions({
  repoRoot,
  dataDir,
  shell,
  maxSessions = MAX_SESSIONS,
  idleTimeoutMs = IDLE_TIMEOUT_MS,
  now = () => Date.now(),
  ptyModule = null,
} = {}) {
  const root = path.resolve(repoRoot || '.');
  const logDir = path.join(path.resolve(dataDir || path.join(root, 'data')), 'terminals');
  const shellPath = shell || (process.platform === 'win32' ? 'powershell.exe' : '/bin/bash');
  const shellArgs = process.platform === 'win32' ? ['-NoLogo'] : [];

  const tickets = new Map(); // ticket -> expiresAt
  const sessions = new Map(); // id -> { term, logPath, lastInputAt, closed }

  let pty = ptyModule;
  let ptyError = '';

  function ensurePty() {
    if (pty) return pty;
    if (ptyError) return null;
    const module = loadPty();
    if (!module) {
      ptyError = 'node-pty is not installed (or it failed to load on this machine)';
      return null;
    }
    pty = module;
    return pty;
  }

  /** 这台机器上终端到底能不能用；不能用就把原因带出去，界面照实说。 */
  function availability() {
    const module = ensurePty();
    if (!module) return { available: false, reason: ptyError || 'node-pty is not installed' };
    return { available: true, shell: shellPath };
  }

  function issueTicket() {
    const ticket = 't-' + randomBytes(16).toString('hex');
    tickets.set(ticket, now() + TICKET_TTL_MS);
    // 顺手清掉过期的，票据表不该无限长。
    for (const [key, expiresAt] of tickets) {
      if (expiresAt <= now()) tickets.delete(key);
    }
    return { ticket, expires_in: Math.round(TICKET_TTL_MS / 1000) };
  }

  /** 票据只能用一次；过期/伪造/重用都算没用过。 */
  function consumeTicket(ticket) {
    const key = String(ticket || '');
    if (!key || !tickets.has(key)) return false;
    const expiresAt = tickets.get(key);
    tickets.delete(key);
    return expiresAt > now();
  }

  function activeCount() {
    return sessions.size;
  }

  function openSession({ cols = 100, rows = 30 } = {}) {
    const module = ensurePty();
    if (!module) {
      return { ok: false, reason: 'unavailable', message: ptyError || 'node-pty is not installed' };
    }
    if (sessions.size >= maxSessions) {
      return { ok: false, reason: 'cap', message: `already ${sessions.size} terminal(s) open` };
    }
    // 环境变量剔掉平台自己的那份：终端是给人用的 shell，不该继承令牌路径之类的配置。
    const env = {};
    for (const [key, value] of Object.entries(process.env)) {
      if (key.startsWith('AP_') || key.startsWith('OFFICE_FORMAL_')) continue;
      env[key] = value;
    }
    let term;
    try {
      term = module.spawn(shellPath, shellArgs, {
        name: 'xterm-color',
        cols: Math.max(20, Number(cols) || 100),
        rows: Math.max(5, Number(rows) || 30),
        cwd: root,
        env,
      });
    } catch (error) {
      return { ok: false, reason: 'spawn_failed', message: String((error && error.message) || error) };
    }
    const id = 'tm-' + randomBytes(4).toString('hex');
    fs.mkdirSync(logDir, { recursive: true });
    const logPath = path.join(logDir, `${id}.log`);
    const session = { id, term, logPath, lastInputAt: now(), closed: false };
    sessions.set(id, session);
    term.onData((data) => {
      appendLog(session, data);
    });
    term.onExit(() => {
      session.closed = true;
    });
    return { ok: true, session, logPath, shell: shellPath };
  }

  function appendLog(session, data) {
    try {
      fs.appendFileSync(session.logPath, redact(data), 'utf8');
    } catch {
      // 日志写不进去不该影响终端本身。
    }
  }

  function touch(id) {
    const session = sessions.get(id);
    if (session) session.lastInputAt = now();
  }

  function write(id, data) {
    const session = sessions.get(id);
    if (!session || session.closed) return false;
    session.lastInputAt = now();
    try {
      session.term.write(String(data ?? ''));
      return true;
    } catch {
      return false;
    }
  }

  function resize(id, cols, rows) {
    const session = sessions.get(id);
    if (!session || session.closed) return false;
    try {
      session.term.resize(Math.max(20, Number(cols) || 100), Math.max(5, Number(rows) || 30));
      return true;
    } catch {
      return false;
    }
  }

  /** 关掉一个会话。Windows 上只走 taskkill（见文件头第 1 条）。 */
  function close(id, reason = 'closed') {
    const session = sessions.get(id);
    if (!session) return { closed: false };
    session.closed = true;
    sessions.delete(id);
    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/PID', String(session.term.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
      } else {
        session.term.kill();
      }
    } catch {
      // 进程可能已经自己退了；这里报错没有意义。
    }
    return { closed: true, reason };
  }

  /** 回收空闲会话；返回被关掉的那些 id（测试要用）。 */
  function sweepIdle() {
    const closedIds = [];
    for (const [id, session] of [...sessions]) {
      if (now() - session.lastInputAt >= idleTimeoutMs) {
        close(id, 'idle');
        closedIds.push(id);
      }
    }
    return closedIds;
  }

  function closeAll() {
    for (const id of [...sessions.keys()]) close(id, 'shutdown');
  }

  return {
    availability,
    issueTicket,
    consumeTicket,
    openSession,
    write,
    resize,
    touch,
    close,
    closeAll,
    sweepIdle,
    activeCount,
    _sessions: sessions,
    _tickets: tickets,
  };
}
