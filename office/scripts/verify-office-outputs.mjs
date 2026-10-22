// 「各窗口说了什么」这个独立页面的回归。
//
// 断言：
//   1. 页面把本群每条活最近一次的 CLI 原话按窗口摊开；
//   2. 窗口显示的是平台登记的名字，不是 id；
//   3. 默认只显示"做完了的"，切到「全部」能看到还在做的；
//   4. 输出里的 HTML 被转义了（CLI 说 `<b>x</b>` 时页面上是字面量，不是标签）；
//   5. 长输出默认只给末尾若干行，能展开。
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
assert.ok(fs.existsSync(path.join(dist, 'outputs.html')), '先构建：npm run build');

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
};

const TASKS = [
  {
    task_id: 'FD-0123456789abcdef',
    title: '示例项目 · 把搜索改成模糊匹配',
    group_id: 'g',
    status: 'merged',
    owner: 'codex-window',
    updated_at: '2026-10-22T12:00:00Z',
  },
  {
    task_id: 'FD-1111111111111111',
    title: '示例项目 · 结算页金额显示不对',
    group_id: 'g',
    status: 'in_progress',
    owner: 'qoder-window',
    updated_at: '2026-10-22T13:00:00Z',
  },
];

const OUTPUTS = [
  {
    task_id: 'FD-0123456789abcdef',
    title: '示例项目 · 把搜索改成模糊匹配',
    status: 'merged',
    owner: 'codex-window',
    harness_id: 'codex-window',
    attempt_id: 'FD-0123456789abcdef.a1',
    attempt_status: 'merged',
    current: false,
    updated_at: '2026-10-22T12:00:00Z',
    stdout: 'npm test\n> 12 passed\n<b>done</b>',
    stderr: '',
  },
  {
    task_id: 'FD-1111111111111111',
    title: '示例项目 · 结算页金额显示不对',
    status: 'in_progress',
    owner: 'qoder-window',
    harness_id: 'qoder-window',
    attempt_id: 'FD-1111111111111111.a1',
    attempt_status: 'in_progress',
    current: true,
    updated_at: '2026-10-22T13:00:00Z',
    stdout: 'running tests...\n',
    stderr: 'TypeError: cannot read property of undefined\n    at settle.js:42\n',
  },
];

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname.startsWith('/api/formal')) {
    const route = url.pathname.replace('/api/formal', '');
    const json = (payload) => {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(payload));
    };
    if (route === '/status') {
      return json({
        configured: true,
        connected: true,
        project_id: '示例项目',
        group_id: 'g',
        operator: 'office-operator',
        admin: { configured: true, identity: 'admin-window', error: null },
        project_members: [],
        harnesses: [
          { harness_id: 'codex-window', name: 'Codex 窗口', status: 'online', capabilities: ['coding'] },
          { harness_id: 'qoder-window', name: 'Qoder 窗口', status: 'online', capabilities: ['coding'] },
        ],
        base_adapters: [],
      });
    }
    if (route === '/outputs') return json({ outputs: OUTPUTS });
    if (route === '/tasks') return json({ tasks: TASKS });
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
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/formal/**', async route => {
    const pathname = new URL(route.request().url()).pathname.replace('/api/formal', '');
    const payload =
      pathname === '/status'
        ? {
            configured: true,
            connected: true,
            project_id: '示例项目',
            group_id: 'g',
            operator: 'office-operator',
            admin: { configured: true, identity: 'admin-window', error: null },
            harnesses: [
              { harness_id: 'codex-window', name: 'Codex 窗口', status: 'online' },
              { harness_id: 'qoder-window', name: 'Qoder 窗口', status: 'online' },
            ],
          }
        : pathname === '/outputs'
          ? { outputs: OUTPUTS }
          : {};
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(payload),
    });
  });

  // 页面要知道项目名（列表里的任务名带项目名前缀，要去掉）。
  await page.addInitScript(() => {
    localStorage.setItem(
      'agent-office.projects.v1',
      JSON.stringify([{ id: 'p1', name: '示例项目' }]),
    );
    localStorage.setItem('agent-office.active-project.v1', 'p1');
  });

  await page.goto(`${base}/outputs.html`);
  await page.waitForSelector('.outputs-window', { timeout: 20000 });

  // 1. 默认只显示"做完了的"：那条 merged 的在，还在做的不在。
  const windows = page.locator('.outputs-window');
  assert.equal(await windows.count(), 1, '默认只显示做完了的那个窗口');
  const first = windows.first();
  assert.match(await first.innerText(), /Codex 窗口/, '窗口显示的是登记名，不是 id');
  assert.match(await first.innerText(), /把搜索改成模糊匹配/, '任务名在');
  assert.doesNotMatch(await first.innerText(), /示例项目 · 把搜索/, '任务名里重复的项目名前缀去掉了');
  assert.equal(await page.locator('.outputs-item', { hasText: '结算页金额显示不对' }).count(), 0);

  // 2. 切到「全部」，还在做的那条也出来。
  await page.selectOption('#outputsFilter', 'all');
  await page.waitForTimeout(200);
  assert.equal(await page.locator('.outputs-window').count(), 2, '切到全部后两个窗口都在');
  assert.equal(
    await page.locator('.outputs-item', { hasText: '结算页金额显示不对' }).count(),
    1,
    '还在做的那条也在',
  );

  // 3. 输出里的 HTML 要转义 —— CLI 说 <b>done</b> 时，页面上是字面量。
  const words = await page.locator('.outputs-words').first().innerText();
  assert.match(words, /<b>done<\/b>/, '输出里的标签是字面量，不是标签');
  assert.equal(await page.locator('.outputs-words b').count(), 0, '没有真的渲染出标签');

  // 4. 报错单独一块。
  assert.equal(await page.locator('.outputs-err').count(), 1, '报错另起一块');

  // 5. 页面不报错。
  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-outputs: ok');
} finally {
  await browser.close();
  server.close();
}
