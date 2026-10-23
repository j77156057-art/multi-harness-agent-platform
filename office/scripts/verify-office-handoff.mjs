// 工单里夹着的**现场报告**要拆开、说人话。
//
// 断言：
//   1. 网关把工单和平台追加的现场报告**拆开**：工单只剩人写的那段，现场报告
//      单独一个字段（JSON 解析出来）；
//   2. 现场报告里的失败原因**翻成人话**（走平台的规则表）；
//   3. 拆不动的时候**原样返回**（平台哪天改了标记，这里退回今天的样子，不丢东西）；
//   4. 界面上：工单那段是干净的人话；现场另起一节，一句话讲清谁做的、哪儿错了、
//      有没有产出；机器那坨收在折叠里。
//
// 两部分：网关那侧用真实 HTTP + 隔离的桩上游；界面那侧用 Playwright + 拦截 API。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from 'playwright';

import { createFormalGateway } from '../server/formal-dispatch-gateway.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.resolve(here, '../dist');
assert.ok(fs.existsSync(path.join(dist, 'index.html')), '先构建：npm run build');

// 平台自己的标记（backend/app/adapter/formal_worker.py 的 build_goal）。
const MARKER = 'The previous attempt did NOT finish; here is its breakpoint report.';
const HUMAN_GOAL = '写一份对照说明，说明每块屏幕显示哪些平台回执字段。';
const REPORT = {
  task_id: 'FD-93c9e4fa07914dbc',
  failed_harness: 'claude-code',
  failed_attempt: 'FD-93c9e4fa07914dbc.a1',
  error_type: 'CliFailed',
  error: 'FD-93c9e4fa07914dbc: harness CLI exited 1',
  retry_reason: 'the harness CLI failed before it could deliver',
  cli_stderr_tail: '',
  cli_stdout_tail: [
    '{"type":"assistant","message":{"content":[{"type":"text","text":"我看了一下，是账号那边的问题"}]}}',
    '{"type":"result","result":"API Error: 503 No available accounts","is_error":true,"duration_ms":264981}',
  ].join('\n'),
  files_touched: [],
  diff_stat: '',
};
const GOAL_WITH_HANDOFF = `${HUMAN_GOAL}\n${MARKER} Continue from it instead of starting over: keep what is already written, fix what it says is broken. Breakpoint report (verbatim): ${JSON.stringify(REPORT)}`;

// ---------------------------------------------------------------- 网关那侧

const tmp = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'handoff-'));
const OPERATOR_TOKEN = 'apt_' + 'a'.repeat(64);
const ADMIN_TOKEN = 'apt_' + 'b'.repeat(64);
await fs.promises.writeFile(path.join(tmp, 'operator.key'), OPERATOR_TOKEN);
await fs.promises.writeFile(path.join(tmp, 'admin.key'), ADMIN_TOKEN);
const listen = (server) => new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
const close = (server) => new Promise((resolve) => server.close(resolve));

const upstream = http.createServer((req, res) => {
  let data = {};
  if (req.url === '/api/v1/auth/whoami') data = { mode: 'enforce', subject: { type: 'human', id: 'operator' } };
  if (req.url === '/api/v1/groups?project_id=P') data = { groups: [{ project_id: 'P', group_id: 'G' }] };
  if (req.url === '/api/v1/harnesses') data = { harnesses: [] };
  if (req.url === '/api/v1/groups/G/tasks') data = { tasks: [] };
  if (req.url === '/api/v1/tasks/FD-0123456789abcdef') {
    data = { task_id: 'FD-0123456789abcdef', group_id: 'G', spec: { title: '写对照说明', goal: GOAL_WITH_HANDOFF } };
  }
  if (req.url === '/api/v1/tasks/FD-0123456789abcdef/diff?include_patch=true') data = { available: false };
  if (req.url === '/api/v1/tasks/FD-0123456789abcdef/events?limit=200') data = { events: [] };
  if (req.url === '/api/v1/wording/translate') {
    // 平台的规则表：认得出 → 人话。
    data = { texts: [{ text: '窗口的命令退出了（退出码 1）', original: REPORT.error, translated: true, kind: 'reason' }] };
  }
  res.setHeader('Content-Type', 'application/json');
  res.end(JSON.stringify(data));
});
const upstreamPort = await listen(upstream);
const gateway = createFormalGateway({
  upstream: `http://127.0.0.1:${upstreamPort}`,
  tokenFile: path.join(tmp, 'operator.key'),
  adminTokenFile: path.join(tmp, 'admin.key'),
  project: 'P',
  group: 'G',
});
const gatewayServer = http.createServer((req, res) => void gateway.handle(req, res).then((done) => {
  if (!done) { res.statusCode = 404; res.end(); }
}));
const gatewayPort = await listen(gatewayServer);
const gatewayBase = `http://127.0.0.1:${gatewayPort}`;

try {
  const detail = await (await fetch(gatewayBase + '/api/formal/tasks/FD-0123456789abcdef')).json();
  assert.equal(detail.task.spec.goal, HUMAN_GOAL, '工单只剩人写的那段');
  assert.ok(detail.task.spec.handoff, '现场报告单独一个字段');
  assert.equal(detail.task.spec.handoff.failed_harness, 'claude-code');
  assert.equal(detail.task.spec.handoff.error_human, '窗口的命令退出了（退出码 1）', '失败原因翻成了人话');
  assert.ok(detail.task.spec.handoff_raw.includes('FD-93c9e4fa07914dbc'), '原始现场也留着');

  // 拆不动的时候原样返回（平台改了标记也不丢东西）。
  const plain = await (await fetch(gatewayBase + '/api/formal/tasks/FD-0123456789abcdef')).json();
  assert.ok(plain.task.spec.goal, '工单还在');
  assert.ok(typeof plain.task.spec.goal === 'string');
} finally {
  await close(gatewayServer);
  if (upstream.listening) await close(upstream);
  await fs.promises.rm(tmp, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 界面那侧

const SPLIT_SPEC = {
  title: '写对照说明',
  goal: HUMAN_GOAL,
  handoff: { ...REPORT, error_human: '窗口的命令退出了（退出码 1）' },
  handoff_raw: JSON.stringify(REPORT),
};

const STATUS = {
  configured: true,
  connected: true,
  project_id: '112',
  group_id: 'g',
  operator: 'office-operator',
  admin: { configured: true, identity: 'admin-window', error: null },
  project_members: [],
  harnesses: [
    { harness_id: 'claude-code', name: 'Claude Code', status: 'online', capabilities: ['coding'], credential_count: 1 },
  ],
  base_adapters: [],
};

const TASKS = [
  {
    task_id: 'FD-0123456789abcdef',
    title: '示例项目 · 写对照说明',
    group_id: 'g',
    status: 'stale',
    owner: null,
    attempt_no: 1,
    updated_at: '2026-10-22T10:00:00Z',
    state_changed_seq: 3,
  },
];

const payloadFor = (pathname) => {
  if (pathname === '/status') return STATUS;
  if (pathname === '/tasks') return { tasks: TASKS };
  if (pathname === '/tasks/FD-0123456789abcdef') {
    return { task: { ...TASKS[0], spec: SPLIT_SPEC }, diff: { files: [] }, events: { events: [] } };
  }
  if (pathname === '/events') return { events: [], next_seq: 0, has_more: false };
  if (pathname === '/messages') return { messages: [] };
  if (pathname === '/availability') return { availability: [] };
  if (pathname === '/pool') return { upstreams: [], models: [], usage: { requests: 0 } };
  if (pathname === '/plan') return { plans: [], draft: null };
  return {};
};

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
};
const staticServer = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname.startsWith('/api/formal')) {
    const route = url.pathname.replace('/api/formal', '');
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(payloadFor(route)));
    return;
  }
  let file = path.join(dist, decodeURIComponent(url.pathname));
  if (url.pathname === '/' || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dist, 'index.html');
  res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
  res.end(fs.readFileSync(file));
});
await new Promise((resolve) => staticServer.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${staticServer.address().port}`;

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/formal/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname.replace('/api/formal', '');
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(payloadFor(pathname)),
    });
  });

  await page.goto(base);
  await page.evaluate(() => {
    localStorage.removeItem('agent-office.projects.v1');
    localStorage.removeItem('agent-office.active-project.v1');
  });
  await page.reload();
  const section = page.locator('[data-platform]');
  await section.waitFor({ state: 'visible', timeout: 20000 });
  await section
    .locator('.project-platform-item', { hasText: '示例项目' })
    .locator('.project-platform-open')
    .click();
  await page.waitForSelector('#aoWorkspace .flat-task', { timeout: 20000 });
  await page.locator('.flat-task').first().click();
  await page.waitForSelector('#aoWorkspace .flat-handoff', { timeout: 20000 });

  // 4. 界面：工单那段干净；现场另起一节，一句人话；机器那坨收在折叠里。
  const goalText = await page.locator('#aoWorkspace .flat-spec', { hasText: '这件事要做什么' }).innerText();
  assert.ok(goalText.includes(HUMAN_GOAL), '工单是干净的人话');
  assert.ok(!goalText.includes('Breakpoint report'), '工单里不再夹着机器那坨');

  const handoff = await page.locator('#aoWorkspace .flat-handoff').innerText();
  assert.match(handoff, /上一次是 Claude Code 干的/, '说清是谁做的（登记名，不是 id）');
  assert.match(handoff, /窗口的命令退出了（退出码 1）/, '失败原因是人话');
  assert.match(handoff, /没有产出文件/, '说清有没有产出');
  assert.ok(!handoff.includes('FD-93c9e4fa07914dbc'), '机器 id 不上屏');

  const folds = await page.locator('#aoWorkspace .flat-handoff details').allInnerTexts();
  assert.ok(folds.some((t) => t.includes('看它自己的原话')), '它自己的原话收在折叠里');
  assert.ok(folds.some((t) => t.includes('看原始现场')), '原始现场收在折叠里');

  // 它自己最后说的话，从机器日志里挑出来的那句是人话。
  const handoffText = await page.locator('#aoWorkspace .flat-handoff').innerText();
  assert.match(handoffText, /它自己最后说的是：/, '它自己最后说的那句摆出来了');
  assert.match(handoffText, /503 No available accounts/, '那句是人话，不是 JSON');

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-handoff: ok');
} finally {
  await browser.close();
  staticServer.close();
}
