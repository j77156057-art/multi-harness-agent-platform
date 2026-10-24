// 开工向导的措辞：第一次用的人走的第一条路上，不该出现机器词。
//
// 自带静态服务器 + 拦全部 /api/formal/*（不连平台也能跑）。
// 断言：
//   1. 成员栏不说 Harness / instance_id / 容量(宿主)；管理员栏说清是必选；
//   2. 不选管理员就点「查看配置摘要」→ 提示是人话，不说「唯一管理员」；
//   3. 摘要里没有「请宿主确认」「普通打工人」「（harness）」，也没有实例 id；
//      说明句说的是「管理员窗口 / 成员窗口」。
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
        name: 'Codex · 管理员',
        status: 'online',
        registration_version: 'reg-001',
        provider: 'openai',
        credential_count: 1,
        capabilities: ['coding'],
        running_tasks: 0,
      },
      {
        harness_id: 'codex-window',
        name: 'Codex CLI',
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
  '/messages': { messages: [] },
  '/availability': { availability: [] },
  '/pool': { upstreams: [], models: [], usage: {} },
  '/plan': { plans: [], draft: null },
};

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/api/formal/**', async route => {
    const pathname = new URL(route.request().url()).pathname.replace('/api/formal', '');
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(responses[pathname] ?? {}),
    });
  });

  await page.goto(base);
  const project = {
    id: 'project-wizard-copy',
    index: '94',
    name: '向导措辞复验',
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
  const card = page.locator('.project-card', { hasText: '向导措辞复验' }).first();
  await card.waitFor({ state: 'visible' });
  await card.locator('.project-card-open').click();
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });

  // 打开开工向导
  await page.locator('#aoStartBtn').click();
  await page.waitForSelector('#openingDialog[open]', { timeout: 20000 });
  await page.waitForTimeout(1200);

  const labels = await page.evaluate(() =>
    [...document.querySelectorAll('#openingDialog label.oop-label')].map(e => e.textContent.trim()));
  console.log('向导栏目标题:', labels);

  // 1. 机器词不许出现在栏目标题里
  const labelText = labels.join(' | ');
  assert.ok(!labelText.includes('Harness'), '栏目标题不说 Harness');
  assert.ok(!labelText.includes('instance_id'), '栏目标题不说 instance_id');
  assert.ok(!labelText.includes('容量(宿主)'), '栏目标题不说「容量(宿主)」');
  assert.ok(!labelText.includes('实例'), '栏目标题不说「实例」');
  assert.ok(labelText.includes('必选'), '管理员那一栏说清是必选');
  assert.ok(labelText.includes('成员窗口'), '成员那一栏说的是「成员窗口」');

  // 2. 不选管理员 → 提示是人话
  await page.evaluate(() => {
    const dlg = document.querySelector('#openingDialog');
    for (const el of dlg.querySelectorAll('*')) if (el.scrollHeight > el.clientHeight + 20) el.scrollTop = el.scrollHeight;
  });
  await page.locator('#openingDialog textarea.oop-goal').click();
  await page.locator('#openingDialog textarea.oop-goal').type('给项目补一份启动说明', { delay: 5 });
  await page.locator('#openingDialog button.oop-btn', { hasText: '查看配置摘要' }).click();
  await page.waitForTimeout(800);
  const errs = await page.evaluate(() =>
    [...document.querySelectorAll('#openingDialog .oop-err')].map(e => e.textContent.trim()));
  console.log('不选管理员时的提示:', errs);
  assert.ok(errs.length > 0, '不选管理员会被拦下');
  assert.ok(errs.join(' ').includes('请先选择管理员窗口'), '提示说清要做什么');
  assert.ok(!errs.join(' ').includes('唯一管理员'), '提示不说「唯一管理员」这种词');

  // 3. 选管理员 + 选一个成员 → 摘要
  await page.locator('#openingDialog [data-admin="' + ADMIN + '"]').first().click();
  await page.waitForTimeout(400);
  const memberChip = page.locator('#openingDialog [data-worker="codex-window"]').first();
  if (await memberChip.count()) {
    const cls = (await memberChip.getAttribute('class')) || '';
    if (!cls.split(/\s+/).includes('on')) await memberChip.click();
  }
  await page.waitForTimeout(400);
  await page.locator('#openingDialog button.oop-btn', { hasText: '查看配置摘要' }).click();
  await page.waitForTimeout(1000);

  const summary = await page.evaluate(() => {
    const dlg = document.querySelector('#openingDialog');
    const dts = [...dlg.querySelectorAll('.oop-sum-list dt')];
    const dds = [...dlg.querySelectorAll('.oop-sum-list dd')];
    return {
      title: [...dlg.querySelectorAll('.oop-sum-title')].map(e => e.textContent.trim()),
      rows: dts.map((dt, i) => dt.textContent.trim() + ' = ' + (dds[i] ? dds[i].textContent.trim() : '')),
      note: [...dlg.querySelectorAll('.oop-sum-note')].map(e => e.textContent.trim()),
    };
  });
  console.log('摘要标题:', summary.title);
  console.log('摘要行:', summary.rows);
  console.log('摘要说明:', summary.note);

  const sumText = summary.title.join(' ') + ' ' + summary.rows.join(' ') + ' ' + summary.note.join(' ');
  assert.ok(!sumText.includes('请宿主确认'), '摘要不说「请宿主确认」');
  assert.ok(!sumText.includes('普通打工人'), '摘要不说「普通打工人」');
  assert.ok(!sumText.includes('harness'), '摘要不说 harness');
  assert.ok(!/\[[a-z0-9-]+\]/.test(summary.rows.join(' ')), '摘要不带实例 id 方括号');
  assert.ok(summary.rows.join(' ').includes('参与成员'), '摘要里那一行叫「参与成员」');
  assert.ok(summary.note.join(' ').includes('管理员窗口'), '说明句说的是管理员窗口');

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-opening-copy: ok');
} finally {
  await browser.close();
  server.close();
}
