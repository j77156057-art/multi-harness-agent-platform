// 顶上那段「今天」。
//
// 断言：
//   1. 顶上有一段说"今天"的话，不是光给一堆总数；
//   2. 今天新来的、做完了的、没做成的，各算各的（用**本机时区**的今天，不是 UTC）；
//   3. 现在有窗口在干活的，说得出几个窗口在干；
//   4. 额度池的用量单独一句（额度是全平台的，不混进这个项目的账）；
//   5. 今天没动静时，也要说清"多久没动静了"，不能空着；
//   6. 页面不报错。
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

// 本机时区的"今天"（各小时都取一点，跨时区也落在今天里）。
const now = new Date();
const hoursAgo = (n) => new Date(now.getTime() - n * 3600 * 1000).toISOString();
const todayISO = hoursAgo(1);
const yesterdayISO = hoursAgo(30);

const TASKS = [
  { task_id: 'FD-1', title: '示例项目 · 今天新来的', group_id: 'g', status: 'ready', owner: null, attempt_no: 0, created_at: todayISO, updated_at: todayISO, state_changed_seq: 1 },
  { task_id: 'FD-2', title: '示例项目 · 今天做完的', group_id: 'g', status: 'merged', owner: 'm1', attempt_no: 1, created_at: yesterdayISO, updated_at: todayISO, state_changed_seq: 2 },
  { task_id: 'FD-3', title: '示例项目 · 今天没做成的', group_id: 'g', status: 'abandoned', owner: null, attempt_no: 1, created_at: yesterdayISO, updated_at: todayISO, state_changed_seq: 3 },
  { task_id: 'FD-4', title: '示例项目 · 现在在干的', group_id: 'g', status: 'in_progress', owner: 'm1', attempt_no: 2, created_at: yesterdayISO, updated_at: todayISO, state_changed_seq: 4 },
  { task_id: 'FD-5', title: '示例项目 · 前天做完的', group_id: 'g', status: 'merged', owner: 'm1', attempt_no: 1, created_at: yesterdayISO, updated_at: yesterdayISO, state_changed_seq: 5 },
];

// 额度时间线：从昨天傍晚到此刻，每小时一个桶（**盖过今天零点**，这样"今天"
// 才算得全；平台真给的是最近 24 小时，早晨时会盖不满，那时说的是"最近一天"）。
const BUCKETS = [];
for (let i = 25; i >= 0; i -= 1) {
  BUCKETS.push({ at: hoursAgo(i + 1).replace(/\d{2}:\d{2}:\d{2}/, '00:00:00'), requests: i === 0 ? 3 : 0, upstreams: {}, consumers: {} });
}

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
  ],
  base_adapters: [],
};

const POOL = {
  upstreams: [{ name: 'openai' }],
  models: [],
  usage: { requests: 3, upstreams: {}, consumers: {} },
  timeline: { bucket: 'hour', hours: 24, from: BUCKETS[0].at, to: BUCKETS[BUCKETS.length - 1].at, outside_window: 0, buckets: BUCKETS },
  inventory: null,
};

let tasks = TASKS;

const payloadFor = (pathname) => {
  if (pathname === '/status') return STATUS;
  if (pathname === '/tasks') return { tasks };
  if (pathname === '/events') return { events: [], next_seq: 0, has_more: false };
  if (pathname === '/messages') return { messages: [] };
  if (pathname === '/availability') return { availability: [] };
  if (pathname === '/pool') return POOL;
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
  /** 开一个办公室页面：拦截 API、进场、打开那个有任务的项目。 */
  const openOffice = async () => {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
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
    const section = page.locator('[data-platform]');
    await section.waitFor({ state: 'visible', timeout: 20000 });
    await section
      .locator('.project-platform-item', { hasText: '示例项目' })
      .locator('.project-platform-open')
      .click();
    await page.waitForSelector('#aoWorkspace .flat-today', { timeout: 20000 });
    return page;
  };

  const errors = [];
  const page = await openOffice();
  // 额度是慢的一批（要问 provider 余额），等它到了再看完整的那段话。
  await page.waitForFunction(() => {
    const el = document.querySelector('.flat-today');
    return el && el.textContent.includes('额度池');
  }, { timeout: 30000 });

  const today = (await page.locator('.flat-today').innerText()).trim();
  assert.match(today, /今天/, '顶上有一段说今天的话');
  assert.match(today, /新接了 1 件事/, '今天新来的一件算进去了');
  assert.match(today, /做完了 1 件/, '今天做完的一件算进去了（昨天做完的不算）');
  assert.match(today, /没做成 1 件/, '今天没做成的一件算进去了');
  assert.match(today, /1 个窗口在干活/, '现在有几个窗口在干，说得清');
  assert.match(today, /额度池今天用了 3 次/, '额度单独一句，用的是今天的时间线');
  await page.close();

  // 换一批任务：今天什么动静都没有 —— 也要说清"多久没动静了"。
  tasks = TASKS.map((task) => ({ ...task, created_at: yesterdayISO, updated_at: yesterdayISO }));
  const quietPage = await openOffice();
  await quietPage.waitForTimeout(1200);
  const quiet = (await quietPage.locator('.flat-today').innerText()).trim();
  assert.match(quiet, /今天这个项目还没有动静/, '今天没动静时说出来');
  assert.match(quiet, /最新的一条是 .*前更新/, '还要说清多久没动静了');
  await quietPage.close();

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-today: ok');
} finally {
  await browser.close();
  server.close();
}
