// 转接概况（relay-overview）：界面要用的那几件事读得准不准。
//
// 这一层不碰平台、不起进程、只读磁盘，所以全部在这里钉死：
// 在不在跑（心跳）、线程几跳、坏账本要露出来、退回的原因读得到、待投几封。
// 真界面的那一步在浏览器里验（见交付说明），这里只验事实来源。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { HEARTBEAT_FILE, PROJECT_FILE, RUNNING_WINDOW_MS, clearRelayProject, readRelayOverview, readRelayProject, writeRelayProject } from '../server/relay-overview.mjs';

const checks = [];
const check = (name, fn) => {
  fn();
  checks.push(name);
};

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'relay-overview-'));
const write = (relative, text) => {
  const file = path.join(root, relative);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text, 'utf8');
};

check('a missing mailbox says so instead of pretending it is empty', () => {
  const overview = readRelayOverview({ root: path.join(root, 'nowhere') });
  assert.equal(overview.running, false);
  assert.deepEqual(overview.threads, []);
  assert.ok(overview.note.includes('没在跑转接'), overview.note);
});

check('no heartbeat means not running, however many threads exist', () => {
  write('threads/thr-aaaaaaaa.json', JSON.stringify({ thread_id: 'thr-aaaaaaaa', hops: 2, participants: ['a', 'b'], updated_at: '2026-01-02T00:00:00+00:00' }));
  const overview = readRelayOverview({ root });
  assert.equal(overview.threads.length, 1);
  assert.equal(overview.running, false);
});

check('a fresh heartbeat means running; an old one does not', () => {
  const stale = Date.now() - RUNNING_WINDOW_MS - 60_000;
  write(HEARTBEAT_FILE, new Date(Date.now()).toISOString());
  fs.utimesSync(path.join(root, HEARTBEAT_FILE), stale / 1000, stale / 1000);
  assert.equal(readRelayOverview({ root }).running, false, 'a heartbeat older than the window must read as stopped');

  fs.writeFileSync(path.join(root, HEARTBEAT_FILE), new Date().toISOString(), 'utf8');
  assert.equal(readRelayOverview({ root }).running, true);
});

check('a broken ledger shows up as unreadable instead of disappearing', () => {
  write('threads/thr-bbbbbbbb.json', '{ 这不是 json');
  const overview = readRelayOverview({ root });
  const broken = overview.threads.find((row) => row.thread_id === 'thr-bbbbbbbb');
  assert.ok(broken, 'a corrupt ledger must still be listed');
  assert.ok(broken.unreadable.includes('读不出来'), broken.unreadable);
});

check('threads come newest-first so the live conversation is on top', () => {
  write('threads/thr-cccccccc.json', JSON.stringify({ thread_id: 'thr-cccccccc', hops: 6, participants: ['a', 'c'], updated_at: '2026-03-01T00:00:00+00:00' }));
  const overview = readRelayOverview({ root });
  assert.equal(overview.threads[0].thread_id, 'thr-cccccccc');
  assert.equal(overview.threads[0].hops, 6);
});

check('a return carries its reason and the letter it belongs to', () => {
  write('returned/20260101-000000-one.md', '来自 a\n\n给 b：\n喂\n');
  write('returned/20260101-000000-one.md.reason.md', '# 这封没有投出去\n\n- 原因：和「b」在 10 分钟内已经来回 7 次（上限 6）\n- 原文件：one.md\n');
  const overview = readRelayOverview({ root });
  const row = overview.returned.find((item) => item.letter === '20260101-000000-one.md');
  assert.ok(row, 'the returned row must be listed');
  assert.ok(row.reason.includes('已经来回 7 次'), row.reason);
  assert.equal(row.has_letter, true);
});

check('a reason file with nothing readable does not get an invented reason', () => {
  write('returned/20260101-000001-two.md.reason.md', '# 空\n');
  const overview = readRelayOverview({ root });
  const row = overview.returned.find((item) => item.letter === '20260101-000001-two.md');
  assert.ok(row.reason.includes('没写清楚'), row.reason);
  assert.equal(row.has_letter, false);
});

check('only letters count as pending', () => {
  write('outbox/hello.md', '来自 a\n\n给 b：\n喂\n');
  write('outbox/notes.txt', 'not a letter');
  assert.equal(readRelayOverview({ root }).pending, 1);
});

// ---------------------------------------------------------- 项目跟着仓库走

check('a relay project is written to the repo and read back', () => {
  const written = writeRelayProject({ root, name: '  仓库里的转接项目  ' });
  assert.equal(written.name, '仓库里的转接项目', '名字要去掉首尾空格');
  assert.ok(fs.existsSync(path.join(root, PROJECT_FILE)));
  const overview = readRelayOverview({ root });
  assert.equal(overview.project.name, '仓库里的转接项目');
});

check('renaming keeps the original created_at', () => {
  const first = readRelayProject(root).project;
  const second = writeRelayProject({ root, name: '改过名字的项目' });
  // 改名不该把"什么时候建的"改掉 —— 那是这个项目在仓库里的出生时间。
  assert.equal(second.created_at, first.created_at);
  assert.equal(readRelayProject(root).project.name, '改过名字的项目');
});

check('a broken project file is reported, not silently treated as missing', () => {
  write(PROJECT_FILE, '{ 这不是 json');
  const result = readRelayProject(root);
  assert.equal(result.project, null);
  assert.ok(result.project_note.includes('读不出来'), result.project_note);
});

check('an empty project name is refused', () => {
  assert.throws(() => writeRelayProject({ root, name: '   ' }), /relay_project_name_required/);
});

check('clearing the project note leaves every letter alone', () => {
  writeRelayProject({ root, name: '要清掉的项目' });
  const before = readRelayOverview({ root });
  assert.ok(before.threads.length > 0, 'precondition: the mailbox has threads');

  const result = clearRelayProject({ root });

  assert.equal(result.removed, true);
  const after = readRelayOverview({ root });
  assert.equal(after.project, null);
  // 「移除项目」不该删掉真发生过的事：线程、退回、待投都要原样在。
  assert.deepEqual(after.threads, before.threads);
  assert.deepEqual(after.returned, before.returned);
  assert.equal(after.pending, before.pending);
});

check('clearing a project note that is not there is not an error', () => {
  assert.equal(clearRelayProject({ root }).removed, false);
});

console.log(`relay overview: ${checks.length}/${checks.length} PASS`);
// 这里没有句柄要留：读完就退，别让一条自动化跑完还挂着（与 terminal 那条同一个教训）。
process.exit(0);
