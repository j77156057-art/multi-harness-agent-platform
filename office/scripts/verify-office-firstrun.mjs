// 第一次用的人有路：空项目的空状态、下一步、术语与私聊的等待提示。
//
// 自带一个静态服务器 + 拦截全部 /api/formal/*：不连平台、不需要 4174，
// 所以任何机器上都能跑（浏览器二进制仍要本地装好 playwright）。
//
// 断言（每条都是"第一次用的人**看得到**的东西"，不是内部状态）：
//   1. 大厅那三步引导跟界面上真的按钮对得上（步骤里出现「新建项目」）；
//   2. 项目配置表单里没有机器词：成员栏不说 Harness，范围说明不摆项目号/群号；
//   3. 空项目：顶部那句话给出下一步；没有活可筛时**不出现**筛选条与分页条；
//      左栏说清「还没有任务」；中间那栏给一个能点的下一步，点了真的打开开工向导；
//   4. 私聊：自己那条还没人回时，页面说「在等」；管理员回了就不再说。
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
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;

const ADMIN = 'admin-window';
// 私聊线程：先是主人问了一句，后来管理员回了。这两条之间的差别就是"在等"那条提示。
let directThread = [
  {
    created_seq: 1,
    created_at: '2026-10-24T10:00:00Z',
    sender_type: 'human',
    sender_id: 'office-operator',
    body: '在吗？',
    refs: ['direct'],
    mentions: [ADMIN],
  },
];

const responses = {
  '/status': {
    configured: true,
    connected: true,
    project_id: 'p',
    group_id: 'g',
    operator: 'office-operator',
    admin: { configured: true, identity: ADMIN, error: null },
    project_members: [],
    harnesses: [
      {
        harness_id: ADMIN,
        name: '管理员窗口',
        status: 'online',
        registration_version: 'reg-001',
        provider: 'openai',
        credential_count: 1,
        capabilities: ['coding'],
        running_tasks: 0,
      },
      {
        harness_id: 'codex-window',
        name: 'Codex 窗口',
        status: 'online',
        registration_version: 'reg-001',
        provider: 'openai',
        credential_count: 1,
        capabilities: ['coding'],
        running_tasks: 0,
      },
    ],
    base_adapters: [],
  },
  '/tasks': { tasks: [] },
  '/events': { events: [], next_seq: 0, has_more: false },
  '/availability': { availability: [] },
  '/pool': { upstreams: [], models: [], usage: {} },
  '/plan': { plans: [], draft: null },
};

const browser = await chromium.launch({ headless: true });
try {
  // 拦截挂在 **context** 上，不是 page 上：私聊页是另开的一个页面，
  // 挂在 page 上的话那一条根本不会被拦（它会拿到静态服务器返回的 index.html）。
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/api/formal/**', async route => {
    const pathname = new URL(route.request().url()).pathname.replace('/api/formal', '');
    const payload = pathname === '/messages' ? { messages: directThread } : responses[pathname] ?? {};
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(payload),
    });
  });

  await page.goto(base);

  // 1. 大厅那三步跟真的按钮对得上。
  const steps = await page.locator('.project-lobby-steps').innerText();
  assert.ok(steps.includes('新建项目'), '引导第一步说的是界面上真有的那个按钮');
  assert.ok(steps.includes('开工配置'), '引导说的是真正要用的那个入口');

  // 2. 项目配置表单：机器词不许出现在第一次用的人眼前。
  await page.locator('[data-new-project]').click();
  await page.waitForSelector('dialog[open]', { timeout: 10000 });
  const legend = await page.locator('dialog[open] legend').innerText();
  assert.ok(!legend.includes('Harness'), '成员栏不说 Harness');
  assert.ok(legend.includes('执行成员'), '成员栏说的是人话');
  const scope = await page.locator('.project-create-scope').innerText();
  assert.ok(!/\bp\s*\/\s*g\b/.test(scope), '范围说明不摆项目号/群号');
  assert.ok(scope.includes('可执行'), '范围说明说的是"谁还能接活"');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);

  // 3. 空项目：没有活可筛时，界面不该摆一堆没对象的开关。
  const project = {
    id: 'project-firstrun',
    index: '95',
    name: '第一次用的项目',
    createdAt: '2026/10/24 00:00:00',
    phase: 'scene',
    source: 'platform',
    members: [],
    taskIds: [],
  };
  await page.evaluate(
    ([key, activeKey, value]) => {
      localStorage.setItem(key, JSON.stringify([value]));
      localStorage.setItem(activeKey, value.id);
    },
    ['agent-office.projects.v1', 'agent-office.active-project.v1', project],
  );
  await page.reload();
  const card = page.locator('.project-card', { hasText: '第一次用的项目' }).first();
  await card.waitFor({ state: 'visible' });
  await card.locator('.project-card-open').click();
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });
  await page.waitForTimeout(800);

  const guide = await page.locator('#aoWorkspace .flat-guide').first().innerText();
  assert.ok(guide.includes('尚无任务'), '顶部说清现在是什么');
  assert.ok(guide.includes('开工配置'), '顶部说清下一步点哪里');
  assert.equal(await page.locator('#aoWorkspace .flat-filters').count(), 0, '没有活可筛时不摆筛选条');
  assert.equal(await page.locator('#aoWorkspace .flat-pager').count(), 0, '没有东西可翻时不摆分页条');
  const empty = await page.locator('#aoWorkspace .flat-empty').first().innerText();
  assert.ok(empty.includes('暂无任务'), '左栏说清还没有活');

  const start = page.locator('#aoWorkspace .flat-start');
  assert.equal(await start.count(), 1, '中间那栏给一个能点的下一步');
  assert.ok((await start.innerText()).includes('配置'), '那个按钮说清自己要干什么');
  await start.click();
  await page.waitForTimeout(1200);
  assert.equal(await page.locator('#openingDialog[open]').count(), 1, '点了真的把人送到开工向导');

  // 4. 私聊：自己那条还没人回 → 说「在等」；回了 → 不再说。
  const chat = await context.newPage();
  await chat.goto(base + '/direct.html');
  await chat.waitForSelector('.direct-list', { timeout: 15000 });
  await chat.waitForTimeout(800);
  const waiting = await chat.locator('.direct-waiting').count();
  assert.equal(waiting, 1, '自己那条还没人回，页面说在等');
  assert.ok((await chat.locator('.direct-waiting').innerText()).includes('等待管理员回复'), '那句话说的是等谁');

  directThread = [
    ...directThread,
    {
      created_seq: 2,
      created_at: '2026-10-24T10:01:00Z',
      sender_type: 'harness',
      sender_id: ADMIN,
      body: '在的。',
      refs: ['direct'],
      mentions: ['office-operator'],
    },
  ];
  await chat.reload();
  await chat.waitForSelector('.direct-list', { timeout: 15000 });
  await chat.waitForTimeout(800);
  assert.equal(await chat.locator('.direct-waiting').count(), 0, '管理员回了就不再显示「在等」');

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-firstrun: ok');
} finally {
  await browser.close();
  server.close();
}
