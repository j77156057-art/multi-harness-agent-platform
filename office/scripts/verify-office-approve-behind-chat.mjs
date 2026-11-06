// 浏览器实测：「与管理员对话」打开时，运维抽屉里的「批准，开始做」仍然点得到（P2-8）。
//
// 2026-11-05 的 human-sim 里，这个抽屉按钮被「与管理员对话」的模态层拦了 15 次
// （Playwright 报 intercepts pointer events），只能改用 DOM 直点。这一条守两件事：
//   1. 对话面板不再是模态（`matches(':modal') === false`）；
//   2. 面板开着时用**真实点击**（Playwright 默认的可命中性检查，不是 force）点批准，
//      请求真的打到 /plan/approve。
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

const PLAN_ID = 'P-1';
const status = {
  configured: true,
  connected: true,
  project_id: 'P-1',
  group_id: 'G-1',
  admin: { configured: true, identity: 'codex-window', error: null },
  applicant: { configured: true, identity: 'codex-member-1', error: null },
  harnesses: [
    {
      harness_id: 'codex-window',
      name: 'Codex 窗口',
      status: 'online',
      registration_version: 'reg-001',
      credential_count: 1,
      capabilities: ['coding'],
    },
    {
      harness_id: 'codex-member-1',
      name: 'Codex 成员窗口',
      status: 'online',
      registration_version: 'reg-002',
      credential_count: 1,
      capabilities: ['coding'],
    },
  ],
  base_adapters: [],
};

const plan = {
  plans: [{ plan_id: PLAN_ID, status: 'draft', plan_hash: 'h', created_at: '2026-11-05T09:00:00Z' }],
  draft: { plan_id: PLAN_ID, status: 'draft', document: { tasks: 3, markdown_path: '/tmp/plan.md' } },
};

const project = {
  id: 'p-p28',
  index: '01',
  name: 'P2-8 回归项目',
  createdAt: '2026/11/05 00:00:00',
  phase: 'scene',
  source: 'platform',
  members: [],
  taskIds: [],
};

const approved = [];
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/formal/**', async (route) => {
    const request = route.request();
    const { pathname } = new URL(request.url());
    const target = pathname.replace('/api/formal', '');
    const json = (payload) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify(payload),
      });

    if (target === '/plan/approve') {
      approved.push(request.postDataJSON());
      return json({ plan_id: PLAN_ID, status: 'approved' });
    }
    if (target === '/status') return json(status);
    if (target === '/tasks') return json({ tasks: [] });
    if (target.startsWith('/events')) return json({ events: [], next_seq: 0, has_more: false });
    if (target === '/availability') return json({ availability: [] });
    if (target === '/plan') return json(plan);
    if (target === '/messages') return json({ messages: [] });
    if (target === '/pool') return json({ upstreams: [], models: [], usage: {} });
    if (target === '/project-creation-requests') return json({ requests: [] });
    if (target === '/user-keys') return json({ keys: [] });
    if (target.startsWith('/installers')) return json({ installers: [] });
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
  const card = page.locator('.project-card', { hasText: 'P2-8 回归项目' }).first();
  await card.waitFor({ state: 'visible', timeout: 10000 });
  await card.locator('.project-card-open').click();
  await page.locator('#aoWorkspace .flat-head').waitFor({ state: 'visible', timeout: 20000 });

  const approve = page.locator('.flat-rail [data-act="approve-plan"]');
  await approve.waitFor({ state: 'visible', timeout: 10000 });

  // 打开「与管理员对话」——缺陷现场就是这个面板开着的时候。
  await page.locator('.flat-rail [data-act="open-admin-chat"]').click();
  const chat = page.locator('#flatAdminChat');
  await chat.waitFor({ state: 'visible', timeout: 5000 });
  assert.equal(
    await page.evaluate(() => document.querySelector('#flatAdminChat')?.matches(':modal')),
    false,
    '对话面板不能是模态：模态会让后面的运维抽屉整片 inert',
  );
  // 非模态 <dialog> 不会自动居中；这条盯住 CSS 的 fixed + margin:auto，
  // 否则面板会掉到页面末尾，虽然"点得到"但其实人看不见。
  const chatBox = await chat.boundingBox();
  assert.ok(
    chatBox && chatBox.y >= 0 && chatBox.y + chatBox.height <= 961,
    '对话面板要留在视口内',
  );
  assert.ok(Math.abs(chatBox.x + chatBox.width / 2 - 720) <= 2, '对话面板应水平居中');

  // 真实点击（有可命中性检查）：被任何一层挡住都会超时。
  await approve.click({ timeout: 5000 });
  await page.waitForTimeout(300);

  assert.equal(approved.length, 1, '「批准，开始做」应真的发出一次 /plan/approve');
  assert.equal(approved[0].plan_id, PLAN_ID, '批准的是当前草案');
  assert.equal(await chat.isVisible(), true, '批准之后对话面板仍开着（没有被迫关掉）');
  assert.deepEqual(errors, [], `页面不应有脚本错误：${errors.join(' | ')}`);
  console.log('verify-office-approve-behind-chat: 通过（面板开着，真实点击批准成功）');
} finally {
  await browser.close();
  server.close();
}
