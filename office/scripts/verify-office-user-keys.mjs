// 额度池里的「自己的 API key」：加、藏、问、点头。
//
// 断言：
//   1. 自己的 key 区块在额度池卡片里；列表上有名字和地址，**没有 key 本身**；
//   2. 平台来问的时候，界面上是一张「平台来问你了」的卡片，三个按钮都在
//      （先不用 / 同意用一次 / 以后都用它）；
//   3. 点「同意用一次」打的是 approve + mode=once，点头之后问题消失、
//      列表变成「已同意用一次」；
//   4. 「加一个 key」的表单在重画之后**不丢**（办公室每 15 秒重画一次，
//      不记下来的话，用户填到一半的 key 会在半路上消失）；
//   5. 窄屏（1180 以下）也够得着额度池 —— 它以前是直接 `display:none` 的，
//      那样"加自己的 key"这个功能在笔记本上就没有入口；
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

const SECRET = 'sk-真值不该出现在界面上-1234';

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
  upstreams: [
    { name: 'user:k1', protocol: 'openai_chat', base_url: 'http://127.0.0.1:4180/v1', models: ['mavis-fake-model'], origin: '你自己的 key', note: '你自己的 key —— 平台不会自己花，要你点了同意' },
  ],
  models: [{ id: 'mavis-fake-model', upstream: 'user:k1', protocol: 'openai_chat', tier: 'user' }],
  usage: { requests: 0, upstreams: {}, consumers: {} },
  inventory: { tiers: [] },
  timeline: { hours: [] },
};

// 平台侧的假账本：key 只以遮罩的形式出去。
const ledger = {
  keys: [
    { id: 'k1', label: '我的测试 key', protocol: 'openai_chat', base_url: 'http://127.0.0.1:4180/v1', models: ['mavis-fake-model'], approval: 'none', created_at: '2026-10-23T02:51:24', used_at: null, requests: 0, note: '' },
    { id: 'k2', label: '要删掉的那个', protocol: 'openai_chat', base_url: 'https://api.example.com/v1', models: ['gpt-x'], approval: 'none', created_at: '2026-10-23T02:40:00', used_at: null, requests: 0, note: '' },
  ],
  pending: {
    id: 'r1',
    key_id: 'k1',
    label: '我的测试 key',
    at: '2026-10-23T02:52:32',
    reason: 'the free and metered quotas could not serve this request',
    task_id: '',
    model: 'mavis-fake-model',
  },
};

const calls = [];

// 让"点头"先失败一次：界面必须看得见这次失败（以前 state.error 从来没被
// 渲染过，于是点"同意用一次"只给人"点了没反应"）。
let failApprove = false;

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname.startsWith('/api/formal')) {
    const route = url.pathname.replace('/api/formal', '');
    const json = (payload) => {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(payload));
    };
    if (route === '/status') return json(STATUS);
    if (route === '/tasks') {
      return json({
        tasks: [
          { task_id: 'FD-1', title: '示例项目 · 第一件事', group_id: 'g', status: 'merged', owner: null, attempt_no: 1, updated_at: '2026-10-22T10:00:00Z', state_changed_seq: 9 },
        ],
      });
    }
    if (route === '/events') return json({ events: [], next_seq: 0, has_more: false });
    if (route === '/messages') return json({ messages: [] });
    if (route === '/availability') return json({ availability: [] });
    if (route === '/pool') return json(POOL);
    if (route === '/plan') return json({ plans: [], draft: null });
    if (route === '/user-keys') {
      if (req.method === 'GET') return json({ keys: ledger.keys, pending: ledger.pending });
      let raw = '';
      req.on('data', (chunk) => (raw += chunk));
      req.on('end', () => {
        calls.push({ method: 'POST', route, body: raw });
        const body = JSON.parse(raw || '{}');
        const key = {
          id: 'k3',
          label: String(body.label || ''),
          protocol: String(body.protocol || 'openai_chat'),
          base_url: String(body.base_url || ''),
          models: body.models || [],
          approval: 'none',
          created_at: '2026-10-23T03:00:00',
          used_at: null,
          requests: 0,
          note: '',
        };
        ledger.keys.push(key);
        json({ key, masked: 'sk-…1234' });
      });
      return undefined;
    }
    const keyRoute = /^\/user-keys\/([^/]+)(\/(approve|deny))?$/.exec(route);
    if (keyRoute) {
      const id = keyRoute[1];
      const action = keyRoute[3];
      let raw = '';
      req.on('data', (chunk) => (raw += chunk));
      req.on('end', () => {
        calls.push({ method: req.method, route, body: raw });
        if (failApprove && action === 'approve') {
          // 模拟"平台那边没成"：界面必须**看得见**这次失败。
          res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ error: 'approve_failed' }));
          return;
        }
        if (req.method === 'DELETE') {
          ledger.keys = ledger.keys.filter((key) => key.id !== id);
          if (ledger.pending?.key_id === id) ledger.pending = null;
          return json({ removed: true });
        }
        const body = JSON.parse(raw || '{}');
        const key = ledger.keys.find((item) => item.id === id);
        if (key && action === 'approve') key.approval = String(body.mode || 'once');
        if (key && action === 'deny') key.approval = 'none';
        if (ledger.pending?.key_id === id) ledger.pending = null;
        return json({ key });
      });
      return undefined;
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
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));

  // 进办公室：大厅里点「打开」第一个平台项目。
  await page.goto(base);
  await page.evaluate(() => {
    localStorage.removeItem('agent-office.projects.v1');
    localStorage.removeItem('agent-office.active-project.v1');
  });
  await page.reload();
  await page.waitForSelector('[data-platform] .project-platform-open', { timeout: 20000 });
  await page.locator('[data-platform] .project-platform-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });

  // 1. 额度池里有「自己的 API key」这一块；列表上有名字和地址，没有 key 本身。
  const pool = page.locator('details[data-fold="pool"]');
  await pool.locator('> summary').click();
  const userKeys = page.locator('.flat-userkeys');
  await userKeys.waitFor({ state: 'visible', timeout: 10000 });
  const listText = await page.locator('.flat-userkeys-list').innerText();
  assert.match(listText, /我的测试 key/, '列表上有用户自己起的名字');
  assert.match(listText, /OpenAI 兼容/, '协议是人话，不是 openai_chat');
  assert.doesNotMatch(listText, /openai_chat/, '机器协议名不上界面');
  assert.doesNotMatch(listText, new RegExp(SECRET.slice(0, 12)), 'key 本身不出现在界面上');
  assert.doesNotMatch(listText, /sk-/, '界面上看不到 key 的样子');

  // 2. 平台来问的时候，是一张问人的卡片，三个按钮都在；
  //    而且它在右栏**最上面** —— 埋在额度池长卡片底下就等于没这一问。
  const ask = page.locator('.flat-userkey-ask');
  await ask.waitFor({ state: 'visible', timeout: 10000 });
  const askText = await ask.innerText();
  assert.match(askText, /免费额度用完了/, '卡片说的是人话');
  assert.match(askText, /我的测试 key/, '说的是哪一个 key');
  assert.doesNotMatch(askText, /the free and metered quotas/, '平台的英文理由不上界面');
  assert.equal(await ask.locator('button[data-act="deny-user-key"]').count(), 1, '有「先不用」');
  assert.equal(await ask.locator('button[data-act="approve-user-key"][data-mode="once"]').count(), 1, '有「同意用一次」');
  assert.equal(await ask.locator('button[data-act="approve-user-key"][data-mode="always"]').count(), 1, '有「以后都用它」');
  const askCard = page.locator('.flat-card-ask');
  assert.equal(await askCard.count(), 1, '平台在问的时候有一张专门的卡');
  const railChildren = await page.locator('.flat-rail > *').evaluateAll((nodes) =>
    nodes.map((node) => node.className),
  );
  assert.match(railChildren[0] || '', /flat-card-ask/, '这张卡排在右栏第一个');

  // 3. 点「同意用一次」：先让它失败一次 —— 界面必须**看得见**这次失败，
  //    而不是"点了没反应"（以前 state.error 从来没被渲染过）。
  failApprove = true;
  await ask.locator('button[data-act="approve-user-key"][data-mode="once"]').click();
  const banner = page.locator('.flat-error');
  await banner.waitFor({ state: 'visible', timeout: 10000 });
  assert.match(await banner.innerText(), /没成/, '失败的提示要看得见');
  assert.equal(
    await page.locator('.flat-userkey-ask').count(),
    1,
    '没点成之前问题还在（人可以再点一次）',
  );

  //    再点一次：这次成了 —— approve + mode=once，问题消失、列表变已同意。
  failApprove = false;
  await page.locator('button[data-act="approve-user-key"][data-mode="once"]').click();
  await page.waitForFunction(
    () => !document.querySelector('.flat-userkey-ask'),
    undefined,
    { timeout: 15000 },
  );
  const approved = calls.find((call) => call.route.endsWith('/approve'));
  assert.ok(approved, '点过「同意用一次」就该有 approve 请求');
  assert.equal(JSON.parse(approved.body).mode, 'once', '同意的是"用一次"');
  const afterText = await page.locator('.flat-userkeys-list').innerText();
  assert.match(afterText, /已同意用一次/, '列表说清楚这一次是点头过的');

  // 4. 正在填的表单不能被重画抹掉：填了名字，再触发一次重画（删掉另一个 key），
  //    名字还在，折叠也还开着。
  const add = page.locator('details[data-fold="userkey-add"]');
  await add.locator('> summary').click();
  await page.locator('[data-draft="userkey-label"]').fill('我正在填的名字');
  await page.locator('.flat-userkey button[data-act="drop-user-key"]').first().click();
  await page.waitForFunction(
    () => document.querySelector('[data-draft="userkey-label"]')?.value === '我正在填的名字',
    undefined,
    { timeout: 15000 },
  );
  assert.equal(
    await page.locator('details[data-fold="userkey-add"]').evaluate((node) => node.open),
    true,
    '重画之后折叠还开着',
  );

  // 5. 窄屏也够得着：以前 .flat-rail 在 1180 以下是 display:none，额度池整个没了。
  await page.setViewportSize({ width: 1000, height: 900 });
  await page.waitForTimeout(500);
  const railVisible = await page
    .locator('.flat-rail')
    .evaluate((node) => getComputedStyle(node).display !== 'none');
  assert.equal(railVisible, true, '窄屏下右栏不能消失 —— 额度池在里面');

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-user-keys: ok');
} finally {
  await browser.close();
  server.close();
}
