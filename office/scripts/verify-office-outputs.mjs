// 「各窗口说了什么」这个独立页面的回归。
//
// 断言：
//   1. 页面把每条活**每一次尝试**的 CLI 原话按窗口摊开；
//   2. 分组的键是**这一次是谁做的**（改派过之后与活现在的主人不是一回事）；
//   3. 窗口显示的是平台登记的名字，不是 id；
//   4. 默认只显示"做完了的"，切到「全部」能看到没做成的那些轮次；
//   5. 输出里的 HTML 被转义了（CLI 说 `<b>x</b>` 时页面上是字面量，不是标签）；
//   6. 每次尝试标着"第 N 次尝试"。
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
    attempt_no: 1,
    updated_at: '2026-10-22T12:00:00Z',
  },
  {
    task_id: 'FD-1111111111111111',
    title: '示例项目 · 结算页金额显示不对',
    group_id: 'g',
    status: 'in_progress',
    owner: 'qoder-window',
    attempt_no: 3,
    updated_at: '2026-10-22T13:00:00Z',
  },
];

const OUTPUTS = [
  {
    task_id: 'FD-0123456789abcdef',
    title: '示例项目 · 把搜索改成模糊匹配',
    status: 'merged',
    owner: 'codex-window',
    updated_at: '2026-10-22T12:00:00Z',
    attempt_total: 1,
    attempts: [
      {
        attempt_no: 1,
        attempt_id: 'FD-0123456789abcdef.a1',
        attempt_status: 'verifying',
        outcome: '交上去了',
        reason: '',
        harness_id: 'codex-window',
        current: false,
        stdout: 'npm test\n> 12 passed\n<b>done</b>',
        stderr: '',
      },
    ],
  },
  {
    // 这条活做过三次：前两次是 codex 做的（都放弃了），第三次换成了 qoder。
    // 页面要按**这一次是谁做的**分组，而不是按活现在的主人。
    task_id: 'FD-1111111111111111',
    title: '示例项目 · 结算页金额显示不对',
    status: 'abandoned',
    owner: 'qoder-window',
    updated_at: '2026-10-22T13:00:00Z',
    attempt_total: 3,
    attempts: [
      {
        attempt_no: 1,
        attempt_id: 'FD-1111111111111111.a1',
        attempt_status: 'in_progress',
        outcome: '放弃了',
        reason: '接口签名对不上，改不动',
        harness_id: 'codex-window',
        current: false,
        stdout: 'pytest -q\nFAILED test_amount\n第一次就挂了。',
        stderr: 'TypeError: cannot read properties of undefined\n',
      },
      {
        attempt_no: 2,
        attempt_id: 'FD-1111111111111111.a2',
        attempt_status: 'in_progress',
        outcome: '放弃了',
        reason: '还是同一个错',
        harness_id: 'codex-window',
        current: false,
        stdout: 'pytest -q\nFAILED test_amount\n第二次还是同一个错。',
        stderr: '',
      },
      {
        attempt_no: 3,
        attempt_id: 'FD-1111111111111111.a3',
        attempt_status: 'in_progress',
        outcome: '',
        reason: '',
        harness_id: 'qoder-window',
        current: true,
        stdout: 'pytest -q\nrunning...\n',
        stderr: 'TypeError: cannot read properties of undefined\n    at settle.js:42:19\n',
      },
    ],
  },
];

const STATUS = {
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
    const payload = pathname === '/status' ? STATUS : pathname === '/outputs' ? { outputs: OUTPUTS } : {};
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

  // 1. 默认是「全部」：三次尝试都在（一条 merged 的活 + 两次失败的尝试）。
  assert.equal(await page.locator('.outputs-attempt').count(), 4, '默认摊开全部尝试（1 + 3）');
  assert.match(await page.locator('.outputs-window').first().innerText(), /Codex 窗口/, '窗口显示登记名');
  assert.match(
    await page.locator('.outputs-window').first().innerText(),
    /把搜索改成模糊匹配/,
    '任务名在',
  );
  assert.doesNotMatch(
    await page.locator('.outputs-window').first().innerText(),
    /示例项目 · 把搜索/,
    '任务名里重复的项目名前缀去掉了',
  );
  assert.match(
    await page.locator('.outputs-attempt-head').first().innerText(),
    /第 1 次尝试/,
    '每次尝试标着第几次',
  );
  // 每一轮的结果来自事件：交上去了 / 放弃了（带原因）。
  const roundHeads = await page.locator('.outputs-attempt-head').allInnerTexts();
  assert.ok(
    roundHeads.some(text => text.includes('已提交')),
    `交上去的那轮标出来了：${JSON.stringify(roundHeads)}`,
  );
  assert.ok(
    roundHeads.some(text => text.includes('已放弃')),
    `失败的那轮标出来了：${JSON.stringify(roundHeads)}`,
  );
  assert.match(
    await page.locator('.outputs-reason').first().innerText(),
    /它当时说/,
    '失败的原因也在',
  );

  // 1b. 筛「做完了的」：只看活已经做成的那些；「没做成的」看放弃/卡住/取消的。
  await page.selectOption('#outputsFilter', 'done');
  await page.waitForTimeout(250);
  assert.equal(await page.locator('.outputs-attempt').count(), 1, '做完了的只有那一轮');
  await page.selectOption('#outputsFilter', 'failed');
  await page.waitForTimeout(250);
  assert.equal(await page.locator('.outputs-attempt').count(), 3, '没做成的三轮');
  await page.selectOption('#outputsFilter', 'all');
  await page.waitForTimeout(250);

  // 2. 切到「全部」：每一次尝试都在，而且按**这一次是谁做的**分组 ——
  //    那条活现在的主人是 qoder，但前两轮是 codex 做的。
  await page.selectOption('#outputsFilter', 'all');
  await page.waitForTimeout(300);
  // 一次尝试 = 一个 .outputs-attempt；同一条活的多次尝试收在同一个 .outputs-item 里。
  assert.equal(await page.locator('.outputs-attempt').count(), 4, '四次尝试全摊开');
  assert.equal(await page.locator('.outputs-item').count(), 3, '两条活（其中一条在两个窗口名下）');
  const groups = await page.locator('.outputs-window > header').allInnerTexts();
  assert.ok(
    groups.some(text => text.includes('Codex 窗口') && text.includes('3 次')),
    `codex 做了三次（其中两次失败）：${JSON.stringify(groups)}`,
  );
  assert.ok(
    groups.some(text => text.includes('Qoder 窗口') && text.includes('1 次')),
    `qoder 做了第三次：${JSON.stringify(groups)}`,
  );
  const codexItems = page.locator('.outputs-window', { hasText: 'Codex 窗口' }).locator('.outputs-item');
  assert.equal(await codexItems.count(), 2, 'codex 名下两条活');
  const settlement = page
    .locator('.outputs-item', { hasText: '结算页金额显示不对' })
    .first();
  assert.equal(await settlement.locator('.outputs-attempt').count(), 2, '同一条活在这个窗口下的两次尝试');
  const attemptHeads = await settlement.locator('.outputs-attempt-head').allInnerTexts();
  // 组内按尝试序号倒序：同一条活里也是新的尝试在前。
  assert.match(attemptHeads[0], /第 2 次尝试/, '同一条活的多次尝试，新的在前');
  assert.match(attemptHeads[1], /第 1 次尝试/, '第 1 次尝试也在');
  assert.match(attemptHeads.join('\n'), /这一轮：已放弃/, '失败的那一轮标出来了');

  // 3. 输出里的 HTML 要转义 —— CLI 说 <b>done</b> 时，页面上是字面量。
  const words = await page
    .locator('.outputs-item', { hasText: '把搜索改成模糊匹配' })
    .locator('.outputs-words')
    .innerText();
  assert.match(words, /<b>done<\/b>/, '输出里的标签是字面量，不是标签');
  assert.equal(await page.locator('.outputs-words b').count(), 0, '没有真的渲染出标签');

  // 4. 报错单独一块（两次失败各有一次报错）。
  assert.equal(await page.locator('.outputs-err').count(), 2, '报错各自一块');

  // 5. 页面不报错。
  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-outputs: ok');
} finally {
  await browser.close();
  server.close();
}
