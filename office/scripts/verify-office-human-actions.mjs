// 办公室的「人自己的动作」按钮：状态门控与网关放行都要能被机械断言。
//
// 三件事，一件都不能靠人眼：
//
//   1. 前端的状态集合必须与后端 `backend/app/human/service.py` 的守卫**逐个相等** ——
//      从后端源码里解析出来比对（不是手抄一份），否则两边一漂移就没人知道；
//   2. 每个状态下该出现哪些按钮；
//   3. 网关把这些按钮的请求送到哪个后端端点、带了什么字段。
//
// 纯 node（无浏览器、无在线平台），所以任何机器上都能跑 —— 这条是
// `docs/dev-convention.md` 里那条"前端检查不在本仓门禁内"的自保手段。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  CANCEL_STATES,
  REOPEN_STATES,
  RESUME_STATES,
  humanDecisionActions,
  releaseNeedsOverride,
} from '../src/human-actions.js';
import { createFormalGateway } from '../server/formal-dispatch-gateway.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '../..');
const read = relative => fs.readFileSync(path.join(repoRoot, relative), 'utf8');

const checks = [];
// 断言可以是异步的（网关那一段要等 fetch），所以这里 await 一次 ——
// 不 await 的话，失败会变成未处理的 rejection，脚本还会打印"全过"。
const check = async (name, fn) => {
  await fn();
  checks.push(name);
};

// --- 1. 后端的状态集合（从源码解析，不手抄） -------------------------------

/** `TaskStatus` 枚举：成员名 → 它的字符串值。 */
const taskStatusValues = () => {
  const source = read('backend/app/spec.py');
  const body = source.slice(source.indexOf('class TaskStatus'));
  const values = new Map();
  for (const match of body.matchAll(/^\s{4}([A-Z_]+)\s*=\s*"([a-z_]+)"/gm)) {
    values.set(match[1], match[2]);
  }
  assert.ok(values.size >= 20, `the TaskStatus enum parsed (${values.size} members)`);
  return values;
};

/** 一段源码里出现的 `TaskStatus.X` → 状态值。 */
const statusesIn = (source, values) => {
  const found = new Set();
  for (const match of source.matchAll(/TaskStatus\.([A-Z_]+)/g)) {
    const value = values.get(match[1]);
    assert.ok(value, `unknown TaskStatus member: ${match[1]}`);
    found.add(value);
  }
  return found;
};

/** 取出一个函数（或一段常量赋值）的源码。 */
const sliceBetween = (source, from, to) => {
  const start = source.indexOf(from);
  assert.ok(start >= 0, `found ${from}`);
  const end = to ? source.indexOf(to, start) : source.length;
  return source.slice(start, end >= 0 ? end : source.length);
};

/** 取出一个方法体的源码（到下一个同缩进的 `def` 为止）。 */
const sliceMethod = (source, name) => {
  const start = source.indexOf(`    def ${name}(`);
  assert.ok(start >= 0, `found def ${name}`);
  const next = source.indexOf('\n    def ', start + 1);
  return source.slice(start, next >= 0 ? next : source.length);
};

const values = taskStatusValues();
const humanService = read('backend/app/human/service.py');

const backendCancel = new Set([
  ...statusesIn(
    sliceBetween(humanService, '_CANCEL_STATES = frozenset(', '_HUMAN_CANCEL_STATES = frozenset('),
    values,
  ),
  ...statusesIn(
    sliceBetween(humanService, '_HUMAN_CANCEL_STATES = frozenset(', 'def _now()'),
    values,
  ),
  // `queued` 有自己的一条边（queue_cancel），写在 cancel() 的函数体里而不在
  // 那两个常量里 —— 只看常量会漏掉它。
  ...statusesIn(sliceMethod(humanService, 'cancel'), values),
]);
const backendResume = statusesIn(
  sliceBetween(humanService, 'def resume(', 'def reopen('),
  values,
);
const backendReopen = statusesIn(
  sliceBetween(humanService, 'def reopen(', 'def grant_attempt_budget_override('),
  values,
);

await check('the cancel states mirror the backend guard exactly', () => {
  assert.deepEqual([...CANCEL_STATES].sort(), [...backendCancel].sort());
});

await check('the resume states mirror the backend guard exactly', () => {
  assert.deepEqual([...RESUME_STATES].sort(), [...backendResume].sort());
});

await check('the reopen states mirror the backend guard exactly', () => {
  assert.deepEqual([...REOPEN_STATES].sort(), [...backendReopen].sort());
});

// --- 2. 每个状态该出现哪些按钮 ---------------------------------------------

await check('awaiting_human offers answering, reopening and cancelling', () => {
  assert.deepEqual(humanDecisionActions({ status: 'awaiting_human' }), [
    'resume',
    'reopen',
    'cancel',
  ]);
});

await check('escalated offers releasing only — escalating is not cancelling', () => {
  // `escalated` 不在 cancel 的守卫里：升级不是取消（旧人操作面也这么注释过）。
  assert.deepEqual(humanDecisionActions({ status: 'escalated' }), ['release']);
});

await check('abandoned offers reopening only', () => {
  assert.deepEqual(humanDecisionActions({ status: 'abandoned' }), ['reopen']);
});

await check('in_progress offers cancelling only', () => {
  assert.deepEqual(humanDecisionActions({ status: 'in_progress' }), ['cancel']);
});

await check('a healthy running task offers nothing', () => {
  assert.deepEqual(humanDecisionActions({ status: 'in_progress', attempt_no: 1 }), ['cancel']);
  assert.deepEqual(humanDecisionActions({ status: 'merged' }), []);
  assert.deepEqual(humanDecisionActions({ status: 'cancelled' }), []);
  // 待领取的活还没人做：人可以放弃它（后端守卫里有 ready）。
  assert.deepEqual(humanDecisionActions({ status: 'ready' }), ['cancel']);
  assert.deepEqual(humanDecisionActions({}), []);
});

await check('adjudicate stays out of the owner face', () => {
  for (const status of ['awaiting_human', 'escalated', 'abandoned', 'in_progress']) {
    assert.ok(
      !humanDecisionActions({ status }).includes('adjudicate'),
      `adjudicate is the administrator's move (${status})`,
    );
  }
});

await check('the budget override is asked for explicitly, not discovered via 409', () => {
  const spent = { status: 'escalated', attempt_no: 3, max_attempts: 3 };
  assert.equal(releaseNeedsOverride(spent), true);
  assert.equal(releaseNeedsOverride({ ...spent, budget_override_by: 'human-1' }), false);
  assert.equal(releaseNeedsOverride({ status: 'escalated', attempt_no: 1, max_attempts: 3 }), false);
  assert.equal(releaseNeedsOverride({ status: 'awaiting_human', attempt_no: 3, max_attempts: 3 }), false);
});

// --- 3. 网关把请求送到哪里 ---------------------------------------------------

const OPERATOR = 'apt_' + '1'.repeat(64);

const tempToken = contents => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'office-human-'));
  const file = path.join(dir, 'token.key');
  fs.writeFileSync(file, contents);
  return file;
};

const seen = [];
globalThis.fetch = async (target, init = {}) => {
  const url = new URL(String(target));
  const body = init.body ? JSON.parse(init.body) : null;
  seen.push({ path: url.pathname, method: init.method || 'GET', body });
  const json = (status, payload) =>
    new Response(JSON.stringify(payload), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  if (url.pathname === '/api/v1/auth/whoami') {
    return json(200, { mode: 'enforce', subject: { type: 'human', id: 'office-operator' } });
  }
  if (url.pathname === '/api/v1/groups') {
    return json(200, { groups: [{ group_id: 'g', project_id: 'p' }] });
  }
  if (url.pathname === '/api/v1/plans/P-1/reject') {
    return json(200, { plan_id: 'P-1', rejected: ['T-2'] });
  }
  return json(200, { ok: true });
};

const gateway = createFormalGateway({
  upstream: 'http://127.0.0.1:8848',
  tokenFile: tempToken(OPERATOR),
  project: 'p',
  group: 'g',
});

const exchange = async (route, body) => {
  let status = null;
  let payload = null;
  const request = {
    url: `/api/formal${route}`,
    method: 'POST',
    socket: { localPort: 4999, remoteAddress: '127.0.0.1' },
    headers: {
      host: '127.0.0.1:4999',
      origin: 'http://127.0.0.1:4999',
      'x-office-formal': '1',
      'content-type': 'application/json',
    },
    async *[Symbol.asyncIterator]() {
      yield Buffer.from(JSON.stringify(body));
    },
  };
  const response = {
    writeHead: code => {
      status = code;
    },
    end: text => {
      payload = JSON.parse(String(text));
    },
  };
  assert.equal(await gateway.handle(request, response), true);
  return { status, payload };
};

const lastCall = () => seen[seen.length - 1];

await check('the gateway sends a rejection to the platform with the operator as the decider', async () => {
  const { status } = await exchange('/plan/reject', { plan_id: 'P-1', reason: '范围太大' });
  assert.equal(status, 200);
  assert.equal(lastCall().path, '/api/v1/plans/P-1/reject');
  assert.equal(lastCall().method, 'POST');
  assert.equal(lastCall().body.decided_by, 'office-operator');
  assert.equal(lastCall().body.reason, '范围太大');
});

await check('the gateway refuses a rejection without a reason', async () => {
  const { status, payload } = await exchange('/plan/reject', { plan_id: 'P-1', reason: '  ' });
  assert.equal(status, 400);
  assert.equal(payload.error, 'invalid_plan_fields');
});

await check('the gateway sends the answering move to human.resume', async () => {
  const { status } = await exchange('/human/resume', { task_id: 'FD-0123456789abcdef', answer: '按第二条做' });
  assert.equal(status, 200);
  assert.equal(lastCall().path, '/api/v1/human/resume');
  assert.equal(lastCall().body.answer, '按第二条做');
  assert.equal(lastCall().body.decided_by, 'office-operator');
});

await check('the gateway sends the reopening move to human.reopen', async () => {
  const { status } = await exchange('/human/reopen', { task_id: 'FD-0123456789abcdef' });
  assert.equal(status, 200);
  assert.equal(lastCall().path, '/api/v1/human/reopen');
});

await check('the gateway sends the releasing move to human.release, override included', async () => {
  const { status } = await exchange('/human/release', {
    task_id: 'FD-0123456789abcdef',
    reason: '换个窗口再试',
    budget_override: true,
  });
  assert.equal(status, 200);
  assert.equal(lastCall().path, '/api/v1/human/release');
  assert.equal(lastCall().body.budget_override, true);
});

await check('the gateway sends the cancelling move to human.cancel', async () => {
  const { status } = await exchange('/human/cancel', {
    task_id: 'FD-0123456789abcdef',
    reason: '方向不对',
  });
  assert.equal(status, 200);
  assert.equal(lastCall().path, '/api/v1/human/cancel');
  assert.equal(lastCall().body.reason, '方向不对');
});

console.log(`office human actions: ${checks.length}/${checks.length} PASS`);
