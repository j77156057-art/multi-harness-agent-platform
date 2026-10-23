// 群聊摘要：最近群里在说什么。
//
// 断言：
//   1. 群聊顶上有一段摘要，不只是"180 条"三个字；
//   2. 摘要说清一共多少、谁说的（你 / 管理员窗口 / 其他成员）；
//   3. 今天有没有新消息（按本机时区的今天，跟顶上那段「今天」一个口径）；
//   4. 摘要与消息里的**任务号**换成任务名、**窗口 id**换成登记名 ——
//      人读到的是"哪条活、哪个窗口"，不是编号；
//   5. 消息的说话人也用登记名，不是 id；
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
};

const now = new Date();
const hoursAgo = (n) => new Date(now.getTime() - n * 3600 * 1000).toISOString();
// 「今天」按**本机时区**算：拿本地零点当基准，别用"N 小时前"（凌晨跑的时候
// 那可能是昨天）。
const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
const at = (offsetMinutes) => new Date(midnight.getTime() + offsetMinutes * 60000).toISOString();

const TASKS = [
  {
    task_id: 'FD-0123456789abcdef',
    title: '示例项目 · 写对照说明',
    group_id: 'g',
    status: 'in_progress',
    owner: 'm1',
    attempt_no: 1,
    updated_at: hoursAgo(1),
    state_changed_seq: 3,
  },
];

const MESSAGES = [
  { message_id: 'm1', sender_type: 'human', sender_id: 'office-operator', body: '开工了', created_at: at(-120), created_seq: 1 },
  { message_id: 'm2', sender_type: 'harness', sender_id: 'm1', body: '成员回了一条', created_at: at(-60), created_seq: 2 },
  {
    message_id: 'm3',
    sender_type: 'harness',
    sender_id: 'admin-window',
    body: '管理员 Harness 改派：FD-0123456789abcdef → FD-fedcba9876543210（管理员把这条活分给更闲的窗口：原执行者 → m1）',
    created_at: at(1),
    created_seq: 3,
  },
  { message_id: 'm4', sender_type: 'human', sender_id: 'office-operator', body: '今天又来一条', created_at: at(2), created_seq: 4 },
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
    { harness_id: 'admin-window', name: '管理员窗口', status: 'online', capabilities: ['coding'], credential_count: 1 },
  ],
  base_adapters: [],
};

const payloadFor = (pathname) => {
  if (pathname === '/status') return STATUS;
  if (pathname === '/tasks') return { tasks: TASKS };
  if (pathname === '/events') return { events: [], next_seq: 0, has_more: false };
  if (pathname === '/messages') return { messages: MESSAGES };
  if (pathname === '/availability') return { availability: [] };
  if (pathname === '/pool') return { upstreams: [], models: [], usage: { requests: 0 } };
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
  if (url.pathname === '/' || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dist, 'index.html');
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
  const section = page.locator('[data-platform]');
  await section.waitFor({ state: 'visible', timeout: 20000 });
  await section
    .locator('.project-platform-item', { hasText: '示例项目' })
    .locator('.project-platform-open')
    .click();
  await page.waitForSelector('#aoWorkspace .flat-chat-digest', { timeout: 20000 });
  await page.waitForTimeout(800);

  const digest = (await page.locator('.flat-chat-digest').innerText()).trim();
  assert.match(digest, /群里 4 条消息/, '说清一共多少条');
  assert.match(digest, /你 2 条/, '人说的几条');
  assert.match(digest, /管理员窗口 1 条/, '管理员窗口说的几条');
  assert.match(digest, /其他成员 1 条/, '其他成员说的几条');
  assert.match(digest, /今天新来了 2 条/, '今天有没有新消息（本机时区）');
  assert.match(digest, /最后一条是/, '说的是最后一条');

  // 4. 任务号 → 任务名、窗口 id → 登记名
  const messages = await page.locator('.flat-msg').allInnerTexts();
  const reassign = messages.find((text) => text.includes('改派')) || '';
  assert.match(reassign, /「写对照说明」/, '任务号换成了任务名');
  assert.doesNotMatch(reassign, /FD-0123456789abcdef/, '机器编号不上屏');
  assert.match(reassign, /成员一/, '窗口 id 换成了登记名');

  // 5. 说话人也是登记名
  assert.ok(messages.some((text) => text.includes('管理员窗口')), '说话人用登记名');

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-chat-digest: ok');
} finally {
  await browser.close();
  server.close();
}
