// 左栏窗口行右键 → 给这一家 Harness 再开一个窗口。
//
// 断言：
//   1. 右键窗口行会弹出菜单，且菜单里写的是**这一家**的名字；
//   2. 点菜单会打 POST /harnesses，instance_id 是 `<brand>-member-<n>`、
//      名字是 `<品牌> · 新增N`；
//   3. 新窗口注册完会被加进本项目（与「加入这个项目」同一个动作）；
//   4. 不在窗口行上右键不会弹菜单。
//
// 自带静态服务器 + 拦截全部 /api/formal/*：不连平台。
//
// 为什么要这个脚本：办公室跑在 Electron 的内嵌浏览器里，那边右键可能被宿主
// 吃掉（DOM 收不到 contextmenu），所以"我记得能弹出来"和"真的能弹"不是一回事。
// 这里用 Playwright 发真实的 DOM 右键事件，验的是代码本身。
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

const ADMIN = 'admin-window';
const GROUP = 'g';
const TASK = 'FD-0123456789abcdef';

const AVAILABILITY = [
  {
    harness_id: ADMIN, name: 'Codex · 管理员', status: 'online', usable: false,
    model: 'x', model_mode: 'cli', model_id: null, credential_count: 1,
  },
  {
    harness_id: 'qoder-member-1', name: 'Qoder · 成员1', status: 'online', usable: true,
    model: 'qwen', model_mode: 'cli', model_id: null, credential_count: 1,
  },
  {
    harness_id: 'qoder-member-2', name: 'Qoder · 成员2', status: 'online', usable: true,
    model: 'qwen', model_mode: 'cli', model_id: null, credential_count: 1,
  },
];

const status = {
  configured: true,
  connected: true,
  project_id: '111',
  group_id: GROUP,
  operator: 'office-operator',
  admin: { configured: true, identity: ADMIN, error: null },
  project_members: ['qoder-member-1', 'qoder-member-2'],
  harnesses: [
    { harness_id: ADMIN, name: 'Codex · 管理员', status: 'online', capabilities: ['coding'], credential_count: 1, registration_version: 'reg-001' },
    { harness_id: 'qoder', name: 'Qoder CLI CN', status: 'online', capabilities: ['coding'], credential_count: 1, registration_version: 'reg-001' },
    { harness_id: 'qoder-member-1', name: 'Qoder · 成员1', status: 'online', capabilities: ['coding'], credential_count: 1, registration_version: 'reg-001' },
    { harness_id: 'qoder-member-2', name: 'Qoder · 成员2', status: 'online', capabilities: ['coding'], credential_count: 1, registration_version: 'reg-001' },
  ],
  base_adapters: [],
};

const serve = async () => {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const file = path.join(dist, url.pathname === '/' ? 'index.html' : url.pathname);
    if (!file.startsWith(dist) || !fs.existsSync(file)) {
      res.writeHead(404);
      res.end('not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
};

const { server, base } = await serve();
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const posts = [];

  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    const name = url.pathname.replace('/api/formal', '');
    const json = (body, code = 200) =>
      route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      posts.push({ name, body });
      if (name === '/harnesses/provision') {
        return json({
          harness_id: body.harness_id,
          started: true,
          pid: 4242,
          credential: 'issued',
          note: '已签发平台凭据；已启动 worker（pid 4242）。它接到活时平台会给它开自己的工作树。',
        });
      }
      return json({ instance_id: body.instance_id, registered: true });
    }
    if (name === '/status') return json(status);
    if (name === '/availability') return json({ availability: AVAILABILITY });
    if (name === '/tasks') {
      return json({
        tasks: [
          {
            task_id: TASK, title: '示例项目 · 一件事', group_id: GROUP, status: 'ready',
            owner: 'qoder-member-1', attempt_no: 0, updated_at: '2026-10-28T06:00:00Z',
            state_changed_seq: 3,
          },
        ],
      });
    }
    if (name === '/messages') return json({ messages: [] });
    if (name === '/pool') return json({ upstreams: [], models: [], usage: {}, inventory: {}, timeline: {} });
    if (name === '/plan') return json({ draft: null, plans: [] });
    if (name === '/events') return json({ events: [], next_seq: 0, has_more: false });
    if (name === '/assistant/runs') return json({ runs: [] });
    if (name === '/user-keys') return json({ keys: [], pending: null });
    if (name === '/installers') return json({ probed: false, installers: [], summary: {} });
    return json({});
  });

  await page.addInitScript(() => {
    const project = {
      id: 'verify-ctx', index: '9', name: '示例项目', createdAt: '2026/10/28 00:00:00',
      phase: 'scene', source: 'platform', members: [], taskIds: [],
    };
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([project]));
    localStorage.setItem('agent-office.active-project.v1', project.id);
  });

  await page.goto(base);
  await page.waitForSelector('.project-card-open', { timeout: 30000 });
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-conv[data-harness="qoder-member-1"]', { timeout: 30000 });
  await page.waitForTimeout(800);

  const menu = page.locator('.flat-ctxmenu');

  // 1. 右键窗口行 → 菜单弹出来，写的是这一家
  await page.locator('#aoWorkspace .flat-conv[data-harness="qoder-member-1"]').click({ button: 'right' });
  await page.waitForTimeout(300);
  assert.equal(await menu.isVisible(), true, '右键窗口行要弹出菜单');
  const menuText = await menu.innerText();
  assert.ok(menuText.includes('Qoder'), `菜单要说清是哪一家：${menuText}`);
  assert.ok(menuText.includes('再开一个窗口'), `菜单要说清做什么：${menuText}`);

  // 2. 点它 → 打 /harnesses，id 与名字都按约定
  await menu.locator('[data-ctx-add]').click();
  await page.waitForTimeout(1200);
  const register = posts.find((item) => item.name === '/harnesses');
  assert.ok(register, `要 POST /harnesses，实际：${JSON.stringify(posts)}`);
  assert.equal(register.body.source_harness_id, 'qoder', '注册要挂在同一家 Harness 上');
  assert.equal(register.body.instance_id, 'qoder-member-3', '新窗口 id 要接在同品牌后面');
  assert.ok(
    /^Qoder CLI CN · 新增\d+$/.test(register.body.name),
    `名字要是「<Harness 名> · 新增N」，实际：${register.body.name}`,
  );

  // 3. 注册完加进本项目
  const stored = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('agent-office.projects.v1') || '[]'),
  );
  assert.ok(
    (stored[0]?.members || []).includes('qoder-member-3'),
    `新窗口要进本项目成员：${JSON.stringify(stored[0]?.members)}`,
  );

  // 4. 注册完还要让它**真能干活**：调 provision，并把结果说出来
  const provision = posts.find((item) => item.name === '/harnesses/provision');
  assert.ok(provision, `要调 /harnesses/provision，实际：${JSON.stringify(posts.map((p) => p.name))}`);
  assert.equal(provision.body.harness_id, 'qoder-member-3');
  const note = await page.locator('.flat-note-row').first().innerText().catch(() => '');
  assert.ok(
    note.includes('能接活') || note.includes('worker'),
    `要把"已经能接活"说出来，实际：${note}`,
  );

  // 4. 不在窗口行上右键 → 不弹
  await page.keyboard.press('Escape');
  await page.locator('#aoWorkspace .flat-conv').first().click({ button: 'right' });
  await page.waitForTimeout(200);
  assert.equal(await menu.isVisible(), false, '项目任务那一行右键不该弹这个菜单');

  assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
  console.log('verify-office-add-member: ok');
} finally {
  await browser.close();
  server.close();
}
