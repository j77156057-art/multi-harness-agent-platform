// 列表按「人」归堆。
//
// 断言：
//   1. 列表默认按窗口归堆，每块有名字、忙闲、条数；
//   2. 块名是**登记名**（"成员一"），不是 id（"m1"）—— id 对第一次用的人没意义；
//   3. 忙闲有依据：手上有在做的活就是"在忙"，做完了就是"闲着"；
//   4. 没人接过的活归到「还没人接」；
//   5. 归堆的行里不重复写"谁在做"（块头已经写了）；
//   6. 切到「按时间」就平铺，切回来又有分组 —— 两种看法都在；
//   7. 状态筛选与分组不打架（筛"正在做"后只剩在办的）。
//   8. 页面不报错。
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

const TASKS = [
  { task_id: 'FD-1', title: '示例项目 · 第一件事', group_id: 'g', status: 'merged', owner: null, attempt_no: 1, updated_at: '2026-10-22T10:00:00Z', state_changed_seq: 9 },
  { task_id: 'FD-2', title: '示例项目 · 第二件事', group_id: 'g', status: 'in_progress', owner: 'm1', attempt_no: 2, updated_at: '2026-10-22T11:00:00Z', state_changed_seq: 10 },
  { task_id: 'FD-3', title: '示例项目 · 第三件事', group_id: 'g', status: 'ready', owner: null, attempt_no: 0, updated_at: '2026-10-22T12:00:00Z', state_changed_seq: 11 },
];

// 谁接过这些活：从领取事件里看（平台合入之后会把 owner 清空）。
const EVENTS = [
  { seq: 1, aggregate_id: 'FD-1', aggregate_type: 'task', event_name: 'claim', actor_type: 'harness', actor_id: 'm1', payload: { attempt_id: 'FD-1.a1' }, created_at: '2026-10-22T09:00:00Z' },
  { seq: 2, aggregate_id: 'FD-2', aggregate_type: 'task', event_name: 'claim', actor_type: 'harness', actor_id: 'm1', payload: { attempt_id: 'FD-2.a1' }, created_at: '2026-10-22T10:30:00Z' },
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
    { harness_id: 'admin-window', name: '管理员', status: 'online', capabilities: ['coding'], credential_count: 1 },
  ],
  base_adapters: [],
};

const payloadFor = (pathname) => {
  if (pathname === '/status') return STATUS;
  if (pathname === '/tasks') return { tasks: TASKS };
  if (pathname === '/events') return { events: EVENTS, next_seq: 2, has_more: false };
  if (pathname === '/messages') return { messages: [] };
  if (pathname === '/availability') return { availability: [{ harness_id: 'm1', usable: true, model: 'gpt-5.5' }] };
  if (pathname === '/pool') return { upstreams: [{ name: 'openai' }], models: [], usage: { requests: 26 } };
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
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/formal/**', async route => {
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

  // 进办公室（有任务的那个项目）。
  const section = page.locator('[data-platform]');
  await section.waitFor({ state: 'visible', timeout: 20000 });
  await section
    .locator('.project-platform-item', { hasText: '示例项目' })
    .locator('.project-platform-open')
    .click();
  await page.waitForSelector('#aoWorkspace .flat-tasks', { timeout: 20000 });
  await page.waitForSelector('.flat-group', { timeout: 20000 });

  // 1. 默认按人归堆。
  const groups = page.locator('.flat-group');
  assert.equal(await groups.count(), 2, '两块：有人在做的那块 + 还没人接的那块');

  const busy = groups.filter({ has: page.locator('.flat-group-name', { hasText: '成员一' }) });
  assert.equal(await busy.count(), 1, '有活的那块归到做活的人名下');
  const busyText = await busy.innerText();
  assert.match(busyText, /在忙/, '手上有在做的活 → 在忙');
  assert.match(busyText, /手上 1 条/, '手上几条写清楚了');
  assert.match(busyText, /做完了 1 条/, '做完了几条写清楚了');
  assert.doesNotMatch(busyText, /\bm1\b/, '块里不印 id，只印登记名');

  // 2. 没人接过的活归到「还没人接」。
  const none = groups.filter({ has: page.locator('.flat-group-name', { hasText: '还没人接' }) });
  assert.equal(await none.count(), 1, '没人接的活有自己的一块');
  assert.match(await none.innerText(), /等人接/, '这一块等人接');

  // 3. 归堆的行里不重复写"谁在做"（块头已经写了）。
  const rowsText = await busy.locator('.flat-task').allInnerTexts();
  assert.equal(rowsText.length, 2, '成员一名下两件活');
  for (const text of rowsText) {
    assert.doesNotMatch(text, /成员一/, '行里不重复写是谁');
  }

  // 4. 切到「按时间」就平铺，切回来又有分组。
  await page.locator('.flat-view button', { hasText: '按时间' }).click();
  await page.waitForTimeout(400);
  assert.equal(await page.locator('.flat-group').count(), 0, '按时间看是平铺的');
  assert.equal(await page.locator('.flat-task').count(), 3, '平铺时三条活都在');
  await page.locator('.flat-view button', { hasText: '按人' }).click();
  await page.waitForSelector('.flat-group', { timeout: 10000 });
  assert.equal(await page.locator('.flat-group').count(), 2, '切回来又是按人归堆');

  // 5. 状态筛选与分组不打架。
  await page.locator('.flat-filters button', { hasText: '正在做' }).click();
  await page.waitForTimeout(400);
  assert.equal(await page.locator('.flat-task').count(), 1, '筛"正在做"只剩在办的那条');
  assert.equal(await page.locator('.flat-group').count(), 1, '筛完还是按人归堆');

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-by-person: ok');
} finally {
  await browser.close();
  server.close();
}
