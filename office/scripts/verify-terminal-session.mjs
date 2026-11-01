// 内置终端：会话管理这一层的校验（票据、脱敏、上限、空闲回收、真 PTY）。
//
// 为什么先测这一层而不是直接开浏览器：这一层是**安全边界**（票一次性、短命、
// 会话有上限、日志脱敏），用假 PTY 就能把边界钉死，不必真起 shell。
// 最后两条才起真 shell —— 那里验的是"这台机器上终端真能用"，不是逻辑。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

import { createTerminalSessions, redact } from '../server/terminal-session.mjs';

const checks = [];
const check = async (name, fn) => {
  await fn();
  checks.push(name);
};

/** 假 PTY：只记下写了什么，不真的起进程。 */
function fakePty({ failSpawn = false } = {}) {
  const written = [];
  let onDataHandler = null;
  const module = {
    spawn() {
      if (failSpawn) throw new Error('spawn refused');
      return {
        pid: 4242,
        onData(handler) { onDataHandler = handler; },
        onExit() {},
        write(chunk) { written.push(chunk); },
        resize() {},
        kill() { throw new Error('kill() must not be used on win32'); },
      };
    },
  };
  return { module, written, emit: (text) => onDataHandler?.(text) };
}

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'terminal-sessions-'));
const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'terminal-repo-'));

const sessionsWith = (overrides = {}) => createTerminalSessions({ repoRoot, dataDir, ...overrides });

await check('a ticket works once and never again', () => {
  const sessions = sessionsWith({ ptyModule: fakePty().module });
  const { ticket } = sessions.issueTicket();
  assert.equal(sessions.consumeTicket(ticket), true, 'first use must pass');
  assert.equal(sessions.consumeTicket(ticket), false, 'the same ticket must be refused');
  assert.equal(sessions.consumeTicket('t-forged'), false, 'a forged ticket must be refused');
  assert.equal(sessions.consumeTicket(''), false, 'an empty ticket must be refused');
});

await check('a ticket stops working after 60 seconds', () => {
  let clock = 1_000_000;
  const sessions = sessionsWith({ ptyModule: fakePty().module, now: () => clock });
  const { ticket } = sessions.issueTicket();
  clock += 61_000;
  assert.equal(sessions.consumeTicket(ticket), false, 'an expired ticket must be refused');
});

await check('credentials are redacted before they reach the log', () => {
  const token = 'apt_' + 'a'.repeat(64);
  assert.equal(redact(`token: ${token}`), 'token: <hidden>');
  assert.equal(redact('key sk-abcdefgh12345678 here'), 'key <hidden> here');
  assert.equal(redact('-----BEGIN RSA PRIVATE KEY-----'), '<hidden>');
  // 正常命令不许被误伤。
  assert.equal(redact('npm install -g @openai/codex'), 'npm install -g @openai/codex');
});

await check('the log on disk never contains the token that was typed', () => {
  const fake = fakePty();
  const sessions = sessionsWith({ ptyModule: fake.module });
  const opened = sessions.openSession({ cols: 80, rows: 24 });
  assert.equal(opened.ok, true);
  const token = 'apt_' + 'b'.repeat(64);
  fake.emit(`here it is ${token}\n`);
  const logged = fs.readFileSync(opened.logPath, 'utf8');
  assert.equal(logged.includes(token), false, 'the log must not contain the token');
  assert.match(logged, /<hidden>/);
  sessions.close(opened.session.id, 'test');
});

await check('at most two terminals at once', () => {
  const sessions = sessionsWith({ ptyModule: fakePty().module });
  assert.equal(sessions.openSession().ok, true);
  assert.equal(sessions.openSession().ok, true);
  const third = sessions.openSession();
  assert.equal(third.ok, false);
  assert.equal(third.reason, 'cap');
  sessions.closeAll();
});

await check('an idle terminal is reclaimed', () => {
  let clock = 5_000_000;
  const sessions = sessionsWith({ ptyModule: fakePty().module, now: () => clock });
  const opened = sessions.openSession();
  assert.equal(sessions.activeCount(), 1);
  clock += 15 * 60 * 1000 + 1;
  const swept = sessions.sweepIdle();
  assert.deepEqual(swept, [opened.session.id]);
  assert.equal(sessions.activeCount(), 0);
});

await check('a spawn failure is reported as spawn_failed', () => {
  const sessions = sessionsWith({ ptyModule: fakePty({ failSpawn: true }).module });
  const opened = sessions.openSession();
  assert.equal(opened.ok, false);
  assert.equal(opened.reason, 'spawn_failed');
});

await check('availability names the shell when the pty loads', () => {
  const sessions = sessionsWith({ ptyModule: fakePty().module });
  const availability = sessions.availability();
  assert.equal(availability.available, true);
  assert.ok(availability.shell, 'availability must say which shell it will run');
});

// ---------------------------------------------------------------- 真 PTY
let realPty = null;
try {
  realPty = createRequire(import.meta.url)('node-pty');
} catch {
  realPty = null;
}

const waitFor = (predicate, timeoutMs = 8000) => new Promise((resolve) => {
  const started = Date.now();
  const tick = setInterval(() => {
    if (predicate() || Date.now() - started > timeoutMs) {
      clearInterval(tick);
      resolve(predicate());
    }
  }, 100);
});

if (realPty) {
  await check('a real shell echoes what we type (pty is usable on this machine)', async () => {
    const sessions = createTerminalSessions({ repoRoot, dataDir, ptyModule: realPty });
    const opened = sessions.openSession({ cols: 100, rows: 30 });
    assert.equal(opened.ok, true, opened.message || 'openSession failed');
    let output = '';
    opened.session.term.onData((data) => { output += data; });
    sessions.write(opened.session.id, 'Write-Output pty-ok\r');
    const seen = await waitFor(() => output.includes('pty-ok'));
    const pid = opened.session.term.pid;
    sessions.close(opened.session.id, 'test');
    assert.equal(seen, true, `no echo seen; output was ${JSON.stringify(output.slice(0, 200))}`);
    if (process.platform === 'win32') {
      // teardown 必须真的把 shell 收掉：Windows 上走 taskkill，不走 node-pty 的 kill()。
      const gone = await waitFor(() => {
        try { process.kill(pid, 0); return false; } catch { return true; }
      }, 5000);
      assert.equal(gone, true, `pid ${pid} is still alive: teardown did not work`);
    }
  });
} else {
  console.log('terminal session: node-pty not loadable here, real-shell checks skipped');
}

console.log(`terminal session: ${checks.length}/${checks.length} PASS`);

// 真 PTY 会留下一个不随 shell 退出而释放的 conpty 句柄，事件循环因此不肯结束 ——
// 脚本会「9/9 PASS 但永远不退出」，在自动化里表现为超时（2026-11-02 实测：shell 已经
// 被收掉、PASS 也印了，node 进程仍活着）。验完就明确退出，别让通过看起来像卡住。
process.exit(0);
