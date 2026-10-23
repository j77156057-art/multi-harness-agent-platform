// 大厅的「平台上已有的项目」入口 + 默认收起的卡片。
//
// 断言：
//   1. 大厅里有一段「平台上已有的项目」，把平台上的活按「项目名 · 」归堆列出；
//   2. 每条都有「打开」，点下去能进那个项目的办公室（并且它进了本机历史）；
//   3. 没有本机历史时，空状态里的按钮写的是"打开这个项目（已有 N 件事）"，
//      不是"恢复最近项目：xxx"（读的人不知道"恢复"是什么）；
//   4. 额度池与"现在能干活的人"默认**收起** —— 它们是给运维看的事实，
//      第一次用的人不该一进来就被 prompt/completion tokens 糊脸；
//   5. 页面不报错。
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
  { task_id: 'FD-3', title: '另一个项目 · 唯一一件', group_id: 'g', status: 'ready', owner: null, attempt_no: 0, updated_at: '2026-10-22T12:00:00Z', state_changed_seq: 11 },
  { task_id: 'FD-4', title: '没有前缀的一条', group_id: 'g', status: 'ready', owner: null, attempt_no: 0, updated_at: '2026-10-22T12:00:00Z', state_changed_seq: 12 },
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

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname.startsWith('/api/formal')) {
    const route = url.pathname.replace('/api/formal', '');
    const json = (payload) => {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(payload));
    };
    if (route === '/status') return json(STATUS);
    if (route === '/tasks') return json({ tasks: TASKS });
    if (route === '/events') return json({ events: [], next_seq: 0, has_more: false });
    if (route === '/messages') return json({ messages: [] });
    if (route === '/availability') return json({ availability: [{ harness_id: 'm1', usable: true, model: 'gpt-5.5' }] });
    if (route === '/pool') return json({ upstreams: [{ name: 'openai' }], models: [], usage: { requests: 26 } });
    if (route === '/plan') return json({ plans: [], draft: null });
    return json({});
  }
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

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/formal/**', async route => {
    const pathname = new URL(route.request().url()).pathname.replace('/api/formal', '');
    const payload =
      pathname === '/status'
        ? STATUS
        : pathname === '/tasks'
          ? { tasks: TASKS }
          : pathname === '/events'
            ? { events: [], next_seq: 0, has_more: false }
            : pathname === '/messages'
              ? { messages: [] }
              : pathname === '/availability'
                ? { availability: [{ harness_id: 'm1', usable: true, model: 'gpt-5.5' }] }
                : pathname === '/pool'
                  ? { upstreams: [{ name: 'openai' }], models: [], usage: { requests: 26 } }
                  : pathname === '/plan'
                    ? { plans: [], draft: null }
                    : {};
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(payload),
    });
  });

  await page.goto(base);
  await page.evaluate(() => {
    localStorage.removeItem('agent-office.projects.v1');
    localStorage.removeItem('agent-office.active-project.v1');
  });
  await page.reload();

  // 1. 大厅里有「平台上已有的项目」，按项目名归堆。
  const section = page.locator('[data-platform]');
  await section.waitFor({ state: 'visible', timeout: 20000 });
  const rows = section.locator('.project-platform-item');
  assert.equal(await rows.count(), 2, '两个项目（没有前缀的那条不算项目）');
  const sectionText = await section.innerText();
  assert.match(sectionText, /示例项目/, '项目名在');
  assert.match(sectionText, /2 件事/, '这个项目有几件事在');
  assert.match(sectionText, /另一个项目/, '另一个项目也在');
  assert.match(sectionText, /1 件事/, '另一个项目只有一件事');

  // 2. 空状态里的按钮说的是人话。
  const recover = page.locator('[data-recover-project]');
  await recover.waitFor({ state: 'visible', timeout: 10000 });
  assert.match(await recover.innerText(), /打开这个项目（已有 \d+ 件事）/, '按钮说人话');
  assert.doesNotMatch(await recover.innerText(), /恢复最近项目/, '不再说"恢复"');

  // 3. 点「打开」进办公室，并且它进了本机历史。
  await section
    .locator('.project-platform-item', { hasText: '示例项目' })
    .locator('.project-platform-open')
    .click();
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });
  const cardText = await page.locator('.project-card').first().innerText();
  assert.match(cardText, /示例项目/, '本机历史里多了这个项目');

  // 4. 额度池与"现在能干活的人"默认收起。
  const folds = page.locator('.flat-card-fold > details');
  assert.ok((await folds.count()) >= 2, '额度池与可用窗口是可收起的卡片');
  for (let i = 0; i < (await folds.count()); i += 1) {
    assert.equal(await folds.nth(i).evaluate(node => node.open), false, '默认是收起的');
  }
  const poolSummary = await page.locator('.flat-card-fold > details').first().innerText();
  assert.match(poolSummary, /额度池/, '收起时也写着这是什么');

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-lobby: ok');
} finally {
  await browser.close();
  server.close();
}
