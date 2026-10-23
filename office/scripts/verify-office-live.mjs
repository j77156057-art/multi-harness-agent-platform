// 正在跑的那条活：办公室要能看出它**在自己动**。
//
// 断言：
//   1. 列表里正在跑的活带一个心跳点（.flat-pulse）；
//   2. 详情里是"它现在正在做什么"，并写着"刚刚还在动 · 每 4 秒自己更新"；
//   3. **输出长了，页面自己跟上**（不刷新、不点任何东西）—— 平台的 /output
//      读的是 worker 正在写的那个日志文件的尾巴，办公室 4 秒取一次；
//   4. 页面不报错。
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

const TASK = {
  task_id: 'FD-8665828938c488fd',
  title: '只读观察回归 · 正在做的一件事',
  group_id: 'g',
  status: 'in_progress',
  owner: 'codex-window-admin',
  attempt_no: 1,
  max_attempts: 3,
  updated_at: '2026-10-23T10:00:00Z',
  state_changed_seq: 9,
};

const STATUS = {
  configured: true,
  connected: true,
  project_id: '112',
  group_id: 'g',
  operator: 'office-operator',
  admin: { configured: true, identity: 'codex-window-admin', error: null },
  project_members: [],
  harnesses: [
    { harness_id: 'codex-window-admin', name: 'Codex 窗口管理员', status: 'online', capabilities: ['coding'], credential_count: 1 },
  ],
  base_adapters: [],
};

// 输出会**长出来**：每取一次多一行，模拟 worker 正在写日志。
let ticks = 0;
const lines = () => {
  ticks += 1;
  return Array.from({ length: ticks }, (_, index) => `第 ${index + 1} 行：正在写代码`).join('\n');
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
    if (route === '/tasks') return json({ tasks: [TASK] });
    if (route === '/events') return json({ events: [], next_seq: 0, has_more: false });
    if (route === '/messages') return json({ messages: [] });
    if (route === '/availability') return json({ availability: [] });
    if (route === '/pool') return json({ upstreams: [], models: [], usage: { requests: 0 } });
    if (route === '/plan') return json({ plans: [], draft: null });
    if (route === '/user-keys') return json({ keys: [], pending: null });
    if (route === '/wording/translate') return json({ texts: [] });
    if (route === `/tasks/${TASK.task_id}`) {
      return json({ task: { ...TASK, spec: {} }, events: [], diff: { files: [] } });
    }
    if (route === `/tasks/${TASK.task_id}/output`) {
      return json({
        output: {
          task_id: TASK.task_id,
          attempt_id: `${TASK.task_id}.a1`,
          current: true,
          attempt_status: 'in_progress',
          harness_id: 'codex-window-admin',
          stdout: lines(),
          stderr: '',
          stdout_bytes: 42,
          stdout_lines: ticks,
          updated_at: new Date().toISOString(),
        },
      });
    }
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
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(base);
  // 注入一个已经在进行中的项目（不建任务、不发消息），再点开它。
  await page.evaluate(() => {
    const project = {
      id: 'project-live-regression',
      index: '99',
      name: '只读观察回归',
      createdAt: '2026/10/22 00:00:00',
      phase: 'scene',
      source: 'platform',
      members: [],
      taskIds: [],
    };
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([project]));
    localStorage.setItem('agent-office.active-project.v1', project.id);
  });
  await page.reload();
  const card = page.locator('.project-card', { hasText: '只读观察回归' }).first();
  await card.waitFor({ state: 'visible', timeout: 20000 });
  await card.locator('.project-card-open').click();
  await page.waitForSelector('.flat-task', { timeout: 20000 });

  // 1. 列表里正在跑的活带心跳点。
  assert.equal(await page.locator('.flat-task .flat-pulse').count(), 1, '正在跑的活有心跳点');

  // 2. 详情里是"它现在正在做什么"，并写着它还在动。
  await page.locator('.flat-task').first().click();
  const live = page.locator('#flatLive');
  await live.waitFor({ state: 'visible', timeout: 20000 });
  const head = await live.innerText();
  assert.match(head, /它现在正在做什么/, '详情写的是"正在做什么"');
  assert.match(head, /还在动/, '写着它还在动');

  // 3. 输出长了，页面自己跟上（不刷新、不点）。
  const before = await live.innerText();
  await page.waitForTimeout(9000);
  const after = await page.locator('#flatLive').innerText();
  assert.notEqual(after, before, '输出长了，页面要自己跟上（4 秒一轮）');
  assert.match(after, /第 \d+ 行/, '看到的是新长出来的那几行');

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-live: ok');
} finally {
  await browser.close();
  server.close();
}
