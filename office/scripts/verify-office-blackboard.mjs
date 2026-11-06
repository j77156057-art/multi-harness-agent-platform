// 共享黑板：任务行上的「已做多久」+ 顶部「待协助」。
//
// 断言：
//   1. 有还没释放的领取时，行里写「已做 N 分钟」；
//   2. 有人接着但算不出起点（历史任务，claim 已释放）时写「已做 —」，
//      不写 0 分钟、不写负数；
//   3. 无人领取的活**不写**这一段（没有计时起点，写「—」只是噪音）；
//   4. 顶部计数条有「待协助」一格，数字与筛选口径一致；
//   5. 点「待协助」只剩 needs_help 的那条。
//
// 自带静态服务器 + 拦截全部 /api/formal/*：不连平台。
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
};

// 10 分半前领的：向下取整正好「已做 10 分钟」，不会因为脚本跑得慢而跳档。
const CLAIMED_AT = new Date(Date.now() - 10.5 * 60 * 1000).toISOString();

const TASKS = [
  {
    task_id: 'FD-live', title: '示例项目 · 在办的一件', group_id: 'g', status: 'in_progress',
    owner: 'm1', attempt_no: 1, claimed_at: CLAIMED_AT,
    updated_at: '2026-10-22T11:00:00Z', state_changed_seq: 10,
  },
  {
    // 历史任务：owner 还在，但那次领取早释放了 —— 平台给的 claimed_at 是 null。
    task_id: 'FD-old', title: '示例项目 · 历史一件', group_id: 'g', status: 'in_progress',
    owner: 'm1', attempt_no: 2, claimed_at: null,
    updated_at: '2026-10-22T10:00:00Z', state_changed_seq: 9,
  },
  {
    task_id: 'FD-help', title: '示例项目 · 求助一件', group_id: 'g', status: 'needs_help',
    owner: 'm2', attempt_no: 1, claimed_at: null,
    updated_at: '2026-10-22T12:00:00Z', state_changed_seq: 11,
  },
  {
    task_id: 'FD-ready', title: '示例项目 · 待领一件', group_id: 'g', status: 'ready',
    owner: null, attempt_no: 0, claimed_at: null,
    updated_at: '2026-10-22T12:30:00Z', state_changed_seq: 12,
  },
];

const STATUS = {
  configured: true,
  connected: true,
  project_id: '112',
  group_id: 'g',
  operator: 'office-operator',
  admin: { configured: true, identity: 'admin-window', error: null },
  project_members: [],
  harnesses: [
    { harness_id: 'm1', name: '成员一', status: 'online', capabilities: ['coding'], credential_count: 1 },
    { harness_id: 'm2', name: '成员二', status: 'online', capabilities: ['coding'], credential_count: 1 },
    { harness_id: 'admin-window', name: '管理员', status: 'online', capabilities: ['coding'], credential_count: 1 },
  ],
  base_adapters: [],
};

const payloadFor = (pathname) => {
  if (pathname === '/status') return STATUS;
  if (pathname === '/tasks') return { tasks: TASKS };
  if (pathname === '/events') return { events: [], next_seq: 0, has_more: false };
  if (pathname === '/messages') return { messages: [] };
  if (pathname === '/availability') return { availability: [{ harness_id: 'm1', usable: true, model: 'gpt-5.5' }] };
  if (pathname === '/pool') return { upstreams: [{ name: 'openai' }], models: [], usage: { requests: 0 } };
  if (pathname === '/plan') return { plans: [], draft: null };
  return {};
};

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname.startsWith('/api/formal')) {
    const route = url.pathname.replace('/api/formal', '');
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(payloadFor(route)));
    return;
  }
  let file = path.join(dist, decodeURIComponent(url.pathname));
  if (url.pathname === '/' || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    file = path.join(dist, 'index.html');
  }
  res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
  res.end(fs.readFileSync(file));
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;

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

  // 进办公室（平台项目卡是从任务的 project_label / 标题前缀反推出来的）。
  const section = page.locator('[data-platform]');
  await section.waitFor({ state: 'visible', timeout: 20000 });
  await section
    .locator('.project-platform-item', { hasText: '示例项目' })
    .locator('.project-platform-open')
    .click();
  await page.waitForSelector('#aoWorkspace .flat-tasks', { timeout: 20000 });

  // 切到平铺，行数与顺序更好断言（分组视图不影响内容，只是多一层壳）。
  await page.locator('.flat-view button', { hasText: '按时间' }).click();
  await page.waitForTimeout(400);

  const rows = await page.locator('.flat-task').allInnerTexts();
  assert.equal(rows.length, 4, '四条活都在');

  const rowFor = (needle) => {
    const text = rows.find((row) => row.includes(needle));
    assert.ok(text, `没找到「${needle}」那一行`);
    return text;
  };

  // 1. 有还没释放的领取 → 已做 N 分钟。
  assert.match(rowFor('在办的一件'), /已做 10 分钟/, '活跃领取按分钟计时');

  // 2. 历史任务（claim 已释放）→ 已做 —，不是 0 分钟、不是负数。
  const historic = rowFor('历史一件');
  assert.match(historic, /已做 —/, '算不出起点就写「—」');
  assert.doesNotMatch(historic, /已做 0 分钟|已做 -\d/, '不许拿 0 或负数冒充');

  // 3. 有人接着的求助行同样是「已做 —」；无人领取的活整段不出现。
  assert.match(rowFor('求助一件'), /已做 —/, '求助中的行也有起点说明');
  assert.doesNotMatch(rowFor('待领一件'), /已做/, '没人领就没有计时这一段');

  // 4. 顶部计数条有「待协助」，数字与 needs_help 条数一致。
  const ledgerHelp = await page.evaluate(() => {
    const li = [...document.querySelectorAll('li')]
      .find((node) => node.querySelector('span')?.textContent?.trim() === '待协助');
    return li ? li.querySelector('b')?.textContent?.trim() : null;
  });
  assert.equal(ledgerHelp, '1', '计数条里「待协助」是 1');

  // 5. 筛选条上的「待协助」只留 needs_help。
  await page.locator('.flat-filters button', { hasText: '待协助' }).click();
  await page.waitForTimeout(400);
  const filtered = await page.locator('.flat-task').allInnerTexts();
  assert.equal(filtered.length, 1, '筛「待协助」只剩一条');
  assert.match(filtered[0], /求助一件/, '剩的正是 needs_help 那条');

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-blackboard: PASS（已做 10 分钟 / 已做 — / 无 owner 不显示 / 待协助 计数与筛选）');
} finally {
  await browser.close();
  server.close();
}
