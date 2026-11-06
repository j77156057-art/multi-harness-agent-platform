// 浏览器实测：开工向导的管理员候选（P2-7）。
//
// 验两件事，缺一条这个缺陷都会回来：
//   1. **现读**：页面加载时注册表里还没有 qoder；点「开工配置」前注册表多出 qoder，
//      向导里必须能看到它。用模块级快照的实现会在这里变红（快照只有加载时那一次）。
//   2. **规则**：候选不是"把所有注册过的都塞进来"。没有平台凭据、不能跑 coding、
//      平台助手身份、没有 registration_version 的模板，都不该出现在候选里。
//
// 自带静态服务器 + 拦截全部 /api/formal/*：不连平台、不需要 4174。
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
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;

// 页面加载时就在册的老窗口。
const before = [
  {
    harness_id: 'codex-window',
    name: 'Codex 窗口',
    status: 'online',
    registration_version: 'reg-001',
    credential_count: 1,
    capabilities: ['coding'],
  },
];

// 点「开工配置」之前才注册的窗口，以及四个**不该进候选**的样本。
const after = [
  ...before,
  {
    harness_id: 'qoder',
    name: 'Qoder CLI CN',
    status: 'online',
    registration_version: 'reg-002',
    credential_count: 1,
    capabilities: ['coding'],
  },
  // 已停用：账号还在册，但平台凭据被吊销（credential_count 归零）。
  {
    harness_id: 'no-credential',
    name: '已停用的窗口',
    status: 'offline',
    registration_version: 'reg-003',
    credential_count: 0,
    capabilities: ['coding'],
  },
  {
    harness_id: 'docs-window',
    name: '只写文档的窗口',
    status: 'online',
    registration_version: 'reg-004',
    credential_count: 1,
    capabilities: ['docs'],
  },
  {
    harness_id: 'office-admin',
    name: '平台助手',
    status: 'online',
    registration_version: 'reg-005',
    credential_count: 1,
    capabilities: ['coding'],
  },
  {
    harness_id: 'legacy-template',
    name: '适配器模板',
    status: 'online',
    credential_count: 1,
    capabilities: ['coding'],
  },
];

const statusCalls = [];
// 项目里留一个"注册表里没有"的旧成员：旧实现会把 project.members 拼进候选，
// 于是这个已经不存在的窗口会混进管理员名单；新实现只认注册表。
const project = {
  id: 'p-p27',
  index: '01',
  name: 'P2-7 回归项目',
  createdAt: '2026/11/05 00:00:00',
  phase: 'scene',
  source: 'platform',
  members: ['ghost-member'],
  taskIds: [],
};
// 页面加载完再把 qoder「注册」进来：之后的每一次 /status 都能看到它，
// 打开向导前的那一次必须把它带进候选。用开关而不是请求序号 —— 页面首屏
// 不止一个组件会读 /status，按次数切会切错。
let registered = false;
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/formal/**', async (route) => {
    const { pathname } = new URL(route.request().url());
    const route_ = pathname.replace('/api/formal', '');
    if (route_ === '/status') {
      statusCalls.push(statusCalls.length);
      const harnesses = registered ? after : before;
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({
          configured: true,
          connected: true,
          project_id: 'P-1',
          group_id: 'G-1',
          admin: { configured: true, identity: 'codex-window', error: null },
          harnesses,
          base_adapters: [],
        }),
      });
      return;
    }
    const json = (payload) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify(payload),
      });
    if (route_ === '/tasks') return json({ tasks: [] });
    if (route_.startsWith('/events')) return json({ events: [], next_seq: 0, has_more: false });
    if (route_ === '/availability') return json({ availability: [] });
    if (route_ === '/plan') return json({ plans: [], draft: null });
    if (route_ === '/messages') return json({ messages: [] });
    if (route_ === '/pool') return json({ upstreams: [], models: [], usage: {} });
    if (route_ === '/project-creation-requests') return json({ requests: [] });
    if (route_ === '/user-keys') return json({ keys: [] });
    if (route_.startsWith('/installers')) return json({ installers: [] });
    return json({});
  });

  await page.goto(base);
  await page.evaluate(
    ([key, activeKey, value]) => {
      localStorage.setItem(key, JSON.stringify([value]));
      localStorage.setItem(activeKey, value.id);
    },
    ['agent-office.projects.v1', 'agent-office.active-project.v1', project],
  );
  await page.reload();
  const card = page.locator('.project-card', { hasText: 'P2-7 回归项目' }).first();
  await card.waitFor({ state: 'visible', timeout: 10000 });
  await card.locator('.project-card-open').click();
  await page.locator('#aoWorkspace .flat-head').waitFor({ state: 'visible', timeout: 20000 });

  // 首屏读的是旧名单：qoder 还没注册，此时候选里不可能有它。
  const callsBeforeOpen = statusCalls.length;
  assert.ok(callsBeforeOpen >= 1, '首屏应读过注册表');

  // 现在「注册」qoder（注册表变了），再点「开工配置」。
  registered = true;
  await page.locator('#aoStartBtn').click();
  await page.locator('[data-admin]').first().waitFor({ state: 'visible', timeout: 10000 });

  assert.ok(
    statusCalls.length > callsBeforeOpen,
    '打开向导前必须再读一次注册表（不能吃完首屏快照）',
  );

  const chip = (id) => page.locator(`[data-admin="${id}"]`);
  assert.equal(await chip('qoder').count(), 1, '新注册的窗口必须出现在管理员候选里');
  assert.match(await chip('qoder').innerText(), /Qoder/, '候选要显示注册表里的名字，不是裸 id');
  assert.equal(await chip('codex-window').count(), 1, '页面加载时就在册的窗口仍在候选里');

  assert.equal(await chip('no-credential').count(), 0, '没有平台凭据的窗口不能当管理员');
  assert.equal(await chip('docs-window').count(), 0, '不能跑 coding 的窗口不能当管理员');
  assert.equal(await chip('office-admin').count(), 0, '平台助手是模型身份，不是外部管理员窗口');
  assert.equal(await chip('legacy-template').count(), 0, '没有 registration_version 的是适配器模板');
  assert.equal(await chip('ghost-member').count(), 0, '不在注册表里的旧成员不能靠 project.members 混进候选');

  assert.deepEqual(errors, [], `页面不应有脚本错误：${errors.join(' | ')}`);
  console.log(
    `verify-opening-admin-candidates: 通过（注册表读取 ${statusCalls.length} 次；` +
      'qoder 进候选，4 类不该出现的都没出现）',
  );
} finally {
  await browser.close();
  server.close();
}
