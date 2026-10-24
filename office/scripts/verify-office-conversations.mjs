// 会话列表（微信那一列）：左栏有什么、点进去有什么。
//
// 自带静态服务器 + 拦全部 /api/formal/*（不连平台也能跑）。
// 断言：
//   1. 左栏是一列会话：项目任务 / 项目群聊 / 平台助手 / 管理员 / 每个成员窗口；
//      每行有名字 + 一行状态；
//   2. 点一个窗口 → 中间那栏是这个窗口的状态页（在线/模型/本项目里的活/最近一次尝试/轨迹）；
//   3. 点群聊 → 中间那栏是群消息（**展开**，不是空框）；
//   4. 点项目任务 → 中间那栏是任务表；点一条活 → 详情 +「← 返回任务表」。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.resolve(here, '../dist');
assert.ok(fs.existsSync(path.join(dist, 'index.html')), '先构建：npm run build');

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
};

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  let file = path.join(dist, decodeURIComponent(url.pathname));
  if (url.pathname === '/' || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    file = path.join(dist, 'index.html');
  }
  const body = fs.readFileSync(file);
  res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
  res.end(body);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;

const ADMIN = 'admin-window';
const MEMBER = 'codex-window';
const TASK = 'FD-0123456789abcdef';

const task = {
  task_id: TASK,
  title: '开工链路 · 收一条真实任务',
  group_id: 'g',
  status: 'in_progress',
  owner: MEMBER,
  assigned_harness_ids_json: `["${MEMBER}"]`,
  attempt_id: 'A-1',
  attempt_no: 1,
  max_attempts: 3,
  budget_override_by: null,
  escalated_at: null,
  state_changed_seq: 12,
  registration_version: 'reg-001',
  receipt_id: 'rc-1',
  epoch: 1,
  last_failure: null,
  supersedes: null,
  created_at: '2026-10-24T10:00:00Z',
  updated_at: '2026-10-24T10:30:00Z',
};

const second = {
  ...task,
  task_id: 'FD-fedcba9876543210',
  title: '开工链路 · 第二件事',
  status: 'awaiting_human',
  owner: ADMIN,
  updated_at: '2026-10-24T11:00:00Z',
};

// 一条**很长**的目标原文：末尾那句是用来证明「没有截断」的。
const GOAL =
  '把这一轮的办公室改动收口：每一条都要说清是谁做的、做到哪一步、失败了为什么。'
  + '最后再补一句收尾的话，用来看清楚它到底有没有被截断。';

const responses = {
  '/status': {
    configured: true,
    connected: true,
    project_id: 'p',
    group_id: 'g',
    operator: 'office-operator',
    admin: { configured: true, identity: ADMIN, error: null },
    project_members: [MEMBER],
    harnesses: [
      { harness_id: ADMIN, name: 'Codex · 管理员', status: 'online', registration_version: 'reg-001', provider: 'openai', credential_count: 1, capabilities: ['coding'], running_tasks: 1 },
      { harness_id: MEMBER, name: 'Codex CLI', status: 'online', registration_version: 'reg-001', provider: 'openai', credential_count: 1, capabilities: ['coding'], running_tasks: 1 },
      { harness_id: 'office-admin', name: '平台助手', status: 'online', registration_version: 'reg-001', provider: 'openai', credential_count: 1, capabilities: ['coding'], running_tasks: 0 },
      { harness_id: 'no-credential', name: '没有凭据的窗口', status: 'online', registration_version: 'reg-001', provider: 'openai', credential_count: 0, capabilities: ['coding'], running_tasks: 0 },
    ],
    base_adapters: [],
  },
  '/tasks': { tasks: [task, second] },
  [`/tasks/${TASK}`]: {
    task: {
      task_id: TASK,
      spec: { goal: GOAL, acceptance: ['跑得起来', '说清做了什么'], exclusive_paths: ['office/**'] },
    },
  },
  '/events': {
    events: [
      { aggregate_id: TASK, event_name: 'claim', actor_id: MEMBER, payload: {}, created_at: '2026-10-24T10:05:00Z' },
      { aggregate_id: TASK, event_name: 'start', actor_id: MEMBER, payload: {}, created_at: '2026-10-24T10:06:00Z' },
    ],
    next_seq: 0,
    has_more: false,
  },
  '/messages': {
    messages: [
      { created_seq: 1, created_at: '2026-10-24T10:10:00Z', sender_type: 'human', sender_id: 'office-operator', body: '群里的话', refs: [], mentions: [] },
    ],
  },
  '/availability': {
    availability: [
      { harness_id: ADMIN, provider: 'openai', adapter: 'codex', status: 'online', registration_version: 'reg-001', credential_count: 1, model: 'gpt-5', model_alias: null, model_source: 'cfg', degraded: false, last_probe_at: null, warnings: [], last_task_id: TASK, last_attempt_status: 'in_progress', recovered: false, usable: true, reasons: [] },
      { harness_id: MEMBER, provider: 'openai', adapter: 'codex', status: 'online', registration_version: 'reg-001', credential_count: 1, model: 'gpt-5', model_alias: null, model_source: 'cfg', degraded: false, last_probe_at: null, warnings: [], last_task_id: TASK, last_attempt_status: 'in_progress', recovered: false, usable: true, reasons: [] },
      { harness_id: 'office-admin', provider: 'openai', adapter: 'codex', status: 'online', registration_version: 'reg-001', credential_count: 1, model: 'gpt-5', model_alias: null, model_source: 'cfg', degraded: false, last_probe_at: null, warnings: [], last_task_id: null, last_attempt_status: null, recovered: false, usable: true, reasons: [] },
      { harness_id: 'no-credential', provider: 'openai', adapter: 'codex', status: 'online', registration_version: 'reg-001', credential_count: 0, model: null, model_alias: null, model_source: 'not-readable', degraded: false, last_probe_at: null, warnings: [], last_task_id: null, last_attempt_status: null, recovered: false, usable: false, reasons: [{ code: 'no_credential', detail: 'no platform credential' }] },
    ],
  },
  '/pool': { upstreams: [], models: [], usage: {} },
  '/plan': { plans: [], draft: null },
};

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/api/formal/**', async route => {
    const pathname = new URL(route.request().url()).pathname.replace('/api/formal', '');
    const payload = responses[pathname] ?? {};
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(payload),
    });
  });

  await page.goto(base);
  const project = {
    id: 'project-conversations',
    index: '93',
    name: '会话回归',
    createdAt: '2026/10/24 00:00:00',
    phase: 'scene',
    source: 'platform',
    members: [],
    taskIds: [TASK, 'FD-fedcba9876543210'],
  };
  await page.evaluate(
    ([key, activeKey, value]) => {
      localStorage.setItem(key, JSON.stringify([value]));
      localStorage.setItem(activeKey, value.id);
    },
    ['agent-office.projects.v1', 'agent-office.active-project.v1', project],
  );
  await page.reload();
  const card = page.locator('.project-card', { hasText: '会话回归' }).first();
  await card.waitFor({ state: 'visible' });
  await card.locator('.project-card-open').click();
  await page.waitForSelector('#aoWorkspace .flat-convs', { timeout: 20000 });
  await page.waitForTimeout(1200);

  // 1. 左栏是一列会话
  const rows = await page.evaluate(() =>
    [...document.querySelectorAll('#aoWorkspace .flat-conv')].map((el) => ({
      name: (el.querySelector('b')?.textContent || '').trim(),
      status: (el.querySelector('span')?.textContent || '').trim(),
    })));
  console.log('会话列表:', rows);
  const names = rows.map((r) => r.name);
  assert.ok(names.includes('项目任务'), '有「项目任务」这一会话');
  assert.ok(names.includes('项目群聊'), '有「项目群聊」这一会话');
  assert.ok(names.includes('平台助手'), '有「平台助手」这一会话');
  assert.ok(names.some((n) => n.includes('管理员')), '有管理员窗口这一会话');
  assert.ok(names.includes('Codex CLI'), '有成员窗口这一会话');
  assert.ok(names.includes('没有凭据的窗口'), '连不能接活的窗口也在列（它同样是个对象）');
  for (const row of rows) {
    assert.ok(row.name, '每行都有名字');
    assert.ok(row.status, `「${row.name}」有一行状态`);
  }
  assert.ok(
    rows.find((r) => r.name === '没有凭据的窗口').status.includes('接不了活'),
    '接不了活的窗口说清原因',
  );
  assert.ok(
    rows.find((r) => r.name === 'Codex CLI').status.includes('正在做'),
    '在跑的窗口说「正在做」',
  );

  // 2. 点一个窗口 → 中间那栏是它的状态页
  await page.locator('#aoWorkspace .flat-conv', { hasText: 'Codex CLI' }).first().click();
  await page.waitForTimeout(1200);
  const facts = await page.evaluate(() => {
    const dts = [...document.querySelectorAll('#aoWorkspace .flat-window .flat-facts dt')];
    const dds = [...document.querySelectorAll('#aoWorkspace .flat-window .flat-facts dd')];
    return dts.map((dt, i) => dt.textContent.trim() + ' = ' + (dds[i]?.textContent.trim() || ''));
  });
  console.log('窗口状态页:', facts);
  const factText = facts.join(' | ');
  assert.ok(factText.includes('状态'), '说了它在线还是离线');
  assert.ok(factText.includes('用的模型'), '说了它用哪个模型');
  assert.ok(factText.includes('本项目里的活'), '说了它在本项目里的活');
  assert.ok(factText.includes('最近一次尝试'), '说了它上一次在干什么');
  assert.ok(
    await page.locator('#aoWorkspace .flat-window .flat-events').count() > 0,
    '有它的轨迹',
  );

  // 3. 点群聊 → 中间那栏是群消息（展开的，不是空框）
  await page.locator('#aoWorkspace .flat-conv', { hasText: '项目群聊' }).first().click();
  await page.waitForTimeout(1000);
  const chatText = await page.locator('#aoWorkspace .flat-main .flat-chat-list').innerText();
  console.log('群聊正文:', chatText.slice(0, 60));
  assert.ok(chatText.includes('群里的话'), '群消息在中间那栏铺开了');
  assert.ok(
    await page.locator('#aoWorkspace .flat-main .flat-chat-form').count() === 1,
    '还能在中间那栏发言',
  );

  // 4. 点项目任务 → 任务表；点一条活 → 详情 + 返回
  await page.locator('#aoWorkspace .flat-conv', { hasText: '项目任务' }).first().click();
  await page.waitForTimeout(1000);
  assert.ok(
    await page.locator('#aoWorkspace .flat-main .flat-task').count() > 0,
    '任务表在中间那栏',
  );
  await page.locator('#aoWorkspace .flat-main .flat-task').first().click();
  await page.waitForTimeout(1500);
  assert.equal(await page.locator('#aoWorkspace .flat-back').count(), 1, '详情里有「返回任务表」');
  await page.locator('#aoWorkspace .flat-back').click();
  await page.waitForTimeout(800);
  assert.ok(
    await page.locator('#aoWorkspace .flat-main .flat-task').count() > 0,
    '返回之后又是任务表',
  );

  // 5. 详情上方留一条紧凑的活列表：换看另一条活不用先回任务表。
  await page.locator('#aoWorkspace .flat-main .flat-task').first().click();
  await page.waitForTimeout(1500);
  const switches = page.locator('#aoWorkspace .flat-switch');
  assert.equal(await switches.count(), 2, '详情上方有本项目的活列表');
  assert.equal(
    await page.locator('#aoWorkspace .flat-switch.is-on').count(),
    1,
    '当前那条被标出来',
  );
  const before = await page.locator('#aoWorkspace .flat-detail-head h2').innerText();
  await page.locator('#aoWorkspace .flat-switch', { hasText: '第二件事' }).first().click();
  await page.waitForTimeout(1500);
  const after = await page.locator('#aoWorkspace .flat-detail-head h2').innerText();
  console.log('切换前:', before, '→ 切换后:', after);
  assert.ok(after.includes('第二件事'), '点另一条活，详情直接切过去');

  // 6. 窗口页的活：一张张可展开的卡片，点开是**完整的目标原文**（不截断）。
  await page.locator('#aoWorkspace .flat-conv', { hasText: 'Codex CLI' }).first().click();
  await page.waitForTimeout(1200);
  const cards = page.locator('#aoWorkspace .flat-wtask');
  assert.equal(await cards.count(), 1, '这个窗口的那条活有一张卡片');
  assert.ok((await cards.first().innerText()).includes('收一条真实任务'), '卡片写的是这条活');
  assert.equal(await cards.first().evaluate((el) => el.open), false, '卡片默认是收起的');
  await cards.first().locator('summary').click();
  await page.waitForTimeout(1500);
  const goal = (await page.locator('#aoWorkspace .flat-wtask-goal').first().innerText()).trim();
  console.log('目标原文:', goal.slice(0, 40), '…');
  assert.ok(goal.includes('用来看清楚它到底有没有被截断'), '目标原文是**完整**的（末尾那句在）');
  assert.ok(goal.includes('每一条都要说清'), '目标原文的开头也在');
  const body = await page.locator('#aoWorkspace .flat-wtask-body').first().innerText();
  assert.ok(body.includes('跑得起来'), '展开后有验收标准');
  assert.ok(body.includes('office/**'), '展开后有「只允许改这些地方」');

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-conversations: ok');
} finally {
  await browser.close();
  server.close();
}
