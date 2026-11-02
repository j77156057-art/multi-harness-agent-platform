// 「模型用不了」：怎么判出来、在哪一页说、选完存哪儿。
//
// 这里验的是**判定与落库**，不依赖平台（自带静态服务器 + 拦全部 /api/**）。
// 真平台上的同一件事（左栏红点、点进去那一页、先不管）由
// `verify-office-alerts.mjs` 对真数据验；两个脚本管的是两件事，都要有。
//
// 它原来测的是一个浮层（#flatModelTrouble）。那个浮层已经拆掉，改成左栏多一行
// 「需要你处理」+ 一整页（见 `src/flat-office.js` 的 `alertsPane()`）：浮层压在
// 内容上正好盖住额度池清单，而且「先不管」只是关掉浮层，刷新又弹回来 —— 等于没有
// 结论。所以这份脚本跟着改成验那一页，断言反而更细。
//
// 断言：
//   1. 平台上记着一条模型失败时，左栏那一行亮红点，并说清有几件事；
//   2. 点进去是**页面**（.flat-alerts），不是浮层（不存在 .flat-model-trouble）；
//   3. 两块各说清是谁的模型坏了、为什么（平台记着的原文），
//      且**成员在传阅意见里讨论 429 不算**它自己的模型坏了；
//   4. 选择器按额度属主分组，每条标出档位（免费/收费/兜底/你自己的 Key）；
//   5. 选一个免费模型点「就用这个模型」→ 打 /model-selection，mode=pool，
//      带的是选中的那个模型，免费的不问钱；
//   6. 处理掉的那件从这页消失，红点跟着少一件；另一件「先不管」之后红点灭；
//   7. 处理过的刷新回来不再亮（记在 localStorage，不是"看过了就没了"）。
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
const ASSISTANT = 'office-assistant';
const MEMBER = 'm1';
const GROUP = 'g';

const STATUS = {
  configured: true,
  connected: true,
  project_id: '112',
  group_id: GROUP,
  operator: 'office-operator',
  admin: { configured: true, identity: ADMIN, error: null },
  project_members: [],
  harnesses: [
    { harness_id: ADMIN, name: 'Codex · 管理员', status: 'online', capabilities: ['coding'], credential_count: 1 },
    { harness_id: ASSISTANT, name: '平台助手', status: 'online', capabilities: ['coding'], credential_count: 1 },
    { harness_id: MEMBER, name: '成员一', status: 'online', capabilities: ['coding'], credential_count: 1 },
  ],
  base_adapters: [],
};

const POOL = {
  upstreams: [
    { name: 'codebuddy:minimax-m3.1-flash', protocol: 'cli', models: ['minimax-m3.1-flash'], origin: 'cli' },
    { name: 'qoder:Qwen3.8-Flash', protocol: 'cli', models: ['Qwen3.8-Flash'], origin: 'cli' },
    { name: 'mmx:MiniMax-M3.1-Flash-Preview', protocol: 'cli', models: ['MiniMax-M3.1-Flash-Preview'], origin: 'cli' },
  ],
  models: [
    { id: 'codebuddy/minimax-m3.1-flash', upstream: 'codebuddy:minimax-m3.1-flash', protocol: 'cli', tier: 'free' },
    { id: 'qoder/Qwen3.8-Flash', upstream: 'qoder:Qwen3.8-Flash', protocol: 'cli', tier: 'free' },
    { id: 'MiniMax-M3.1-Flash-Preview', upstream: 'mmx:MiniMax-M3.1-Flash-Preview', protocol: 'cli', tier: 'reserve' },
    { id: 'relay/paid-one', upstream: 'relay:paid-one', protocol: 'openai_chat', tier: 'metered' },
  ],
  usage: { requests: 0, upstreams: {}, consumers: {} },
  inventory: { tiers: [] },
  timeline: { hours: [] },
  rosters: {},
};

// 一条活卡在模型上（平台自己补的那句「模型用不了：…」就在 `last_failure.reason` 里）。
const TASK = {
  task_id: 'FD-0123456789abcdef',
  title: '示例项目 · 第一件事',
  group_id: GROUP,
  status: 'ready',
  owner: ADMIN,
  attempt_no: 1,
  updated_at: '2026-10-28T04:00:00Z',
  state_changed_seq: 9,
  last_failure: {
    reason:
      'retryable failure (CliFailed): the harness CLI failed before it could deliver'
      + '（模型用不了：ERROR stream disconnected before completion: error sending request'
      + ' for url (http://127.0.0.1:8402/responses)）',
    at: '2026-10-28T03:59:00Z',
  },
};

const MESSAGES = [
  // 平台助手**自己**报的失败：这也算它自己的模型坏了。
  {
    message_id: 'msg-assistant-1',
    created_seq: 10,
    created_at: '2026-10-28T03:57:00Z',
    sender_type: 'harness',
    sender_id: ASSISTANT,
    body: '这一条没有答上来。\n原因：ConnectionReset: connection reset by peer',
    refs: ['direct'],
  },
  // 成员在传阅意见里**讨论**到 429：那不是它自己的模型坏了，不许拿它亮红点。
  {
    message_id: 'msg-member-1',
    created_seq: 11,
    created_at: '2026-10-28T03:58:30Z',
    sender_type: 'harness',
    sender_id: MEMBER,
    body: '第 2 轮传阅意见（方案 plan-1）\n\n【不清楚】限流 429 的时候要不要重试？',
    refs: ['circulation-review'],
  },
];

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
  const writes = [];

  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    const route_ = url.pathname.replace('/api/formal', '');
    const json = (body, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

    if (route.request().method() === 'POST') {
      writes.push({ route: route_, body: route.request().postDataJSON() });
      return json({ harness_id: ADMIN, model_mode: 'pool' });
    }
    if (route_ === '/status') return json(STATUS);
    if (route_ === '/tasks') return json({ tasks: [TASK] });
    if (route_ === '/messages') return json({ messages: MESSAGES });
    if (route_ === '/availability') {
      return json({
        availability: [
          { harness_id: ADMIN, name: 'Codex · 管理员', status: 'online', usable: false, model: 'codebuddy/minimax-m3.1-flash', model_mode: 'pool', model_id: 'codebuddy/minimax-m3.1-flash' },
          { harness_id: ASSISTANT, name: '平台助手', status: 'online', usable: false, model: null, model_mode: 'pool', model_id: null },
          { harness_id: MEMBER, name: '成员一', status: 'online', usable: true, model: 'x', model_mode: 'cli', model_id: null },
        ],
      });
    }
    if (route_ === '/pool') return json(POOL);
    if (route_ === '/plan') return json({ draft: null, plans: [] });
    if (route_ === '/events') return json({ events: [], next_seq: 0, has_more: false });
    if (route_ === '/assistant/runs') return json({ runs: [] });
    if (route_ === '/user-keys') return json({ keys: [], pending: null });
    if (route_ === '/installers') return json({ probed: false, installers: [], summary: {} });
    return json({});
  });

  await page.addInitScript(() => {
    const project = {
      id: 'verify-trouble',
      index: '99',
      name: '示例项目',
      createdAt: '2026/10/28 00:00:00',
      phase: 'scene',
      source: 'platform',
      members: [],
      taskIds: [],
    };
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([project]));
    localStorage.setItem('agent-office.active-project.v1', project.id);
  });

  /** 左栏「需要你处理」那一行现在长什么样（页面每 15 秒重画，所以每次原子读）。 */
  const rowState = () =>
    page.evaluate(() => {
      const rows = [...document.querySelectorAll('#aoWorkspace .flat-conv')];
      const row = rows.find((node) => (node.querySelector('b') || {}).textContent?.includes('需要你处理'));
      if (!row) return null;
      return {
        text: row.innerText.replace(/\s+/g, ' ').trim(),
        dot: Boolean(row.querySelector('.flat-conv-dot')),
      };
    });

  /** 这一页现在有什么：几块、每块是谁、写着什么。 */
  const paneState = () =>
    page.evaluate(() => {
      const pane = document.querySelector('.flat-alerts');
      if (!pane) return null;
      return {
        summary: (pane.querySelector('.flat-summary') || {}).textContent || '',
        blocks: [...pane.querySelectorAll('.flat-alert')].map((block) => ({
          harness: block.dataset.harness || '',
          title: (block.querySelector('h3') || {}).textContent || '',
          text: block.innerText.replace(/\s+/g, ' ').trim(),
        })),
        floating: document.querySelectorAll('.flat-model-trouble').length,
      };
    });

  const waitFor = async (read, predicate, what, timeoutMs = 30000) => {
    const started = Date.now();
    for (;;) {
      const state = await read();
      if (state && predicate(state)) return state;
      if (Date.now() - started > timeoutMs) throw new Error(`等不到：${what}`);
      await page.waitForTimeout(300);
    }
  };

  await page.goto(base);
  // 「处理过的不再弹」那份记录要**从空开始**：这行只做一次，不放进 `addInitScript`
  // （那个每次加载都跑，会连 reload 之后也一起清掉，于是"刷新不亮"就验不出来了）。
  await page.evaluate(() => localStorage.removeItem('agent-office.model-trouble-dismissed.v1'));
  await page.waitForSelector('.project-card-open', { timeout: 30000 });
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-conv', { timeout: 30000 });

  // 1. 左栏那一行亮起来：两件事（一条活 + 平台助手自己报的失败），成员那条不算。
  //
  // 等文案，不等红点：红点在**第一件**数据到达时就亮了（任务与消息是两批各到的），
  // 那时数出来的必然是 1 —— 拿它去断言"2 件"只会验到一个竞态。
  const row = await waitFor(
    rowState,
    (state) => state.dot === true && state.text.includes('2 件事在等你'),
    '左栏说清有两件事等着',
  );
  assert.ok(row.text.includes('需要你处理'), `左栏要有这一行，实际：${row.text}`);

  // 2. 点进去是一页，不是浮层。
  await page.locator('#aoWorkspace .flat-conv', { hasText: '需要你处理' }).first().click();
  const pane = await waitFor(paneState, (state) => state.blocks.length > 0, '打开需要你处理那一页');
  assert.equal(pane.floating, 0, '不该再有固定定位的浮层（那会盖住额度池清单）');

  // 3. 谁、为什么。成员讨论 429 不算。
  const byHarness = new Map(pane.blocks.map((block) => [block.harness, block]));
  const adminBlock = byHarness.get(ADMIN);
  const assistantBlock = byHarness.get(ASSISTANT);
  assert.ok(adminBlock, `要说清是管理员那个窗口的模型坏了，实际：${pane.blocks.map((b) => b.title).join(' / ')}`);
  assert.ok(adminBlock.title.includes('Codex · 管理员'), '块标题是那个窗口的名字');
  assert.ok(/stream disconnected|模型用不了/.test(adminBlock.text), `要说清为什么（平台记着的原文）：${adminBlock.text}`);
  assert.ok(assistantBlock, '平台助手自己报的失败也算它自己的模型坏了');
  assert.ok(/connection reset/i.test(assistantBlock.text), `助手那块带它报的原文：${assistantBlock.text}`);
  assert.ok(
    !pane.blocks.some((block) => block.harness === MEMBER),
    '成员在传阅意见里讨论到 429 不算它自己的模型坏了',
  );

  // 4. 换模型的选择器：按额度属主分组，每条标档位。
  const select = page.locator(`.flat-alert[data-harness="${ADMIN}"] [data-trouble-select]`);
  assert.equal(await select.count(), 1, '每块都带一个「换成哪个模型」的选择器');
  const groups = await select
    .locator('optgroup')
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('label')));
  assert.ok(groups.includes('codebuddy'), `选择器要按属主分组：${groups.join(',')}`);
  assert.ok(groups.includes('qoder'), `选择器要按属主分组：${groups.join(',')}`);
  const options = await select
    .locator('option')
    .evaluateAll((nodes) => nodes.map((node) => String(node.textContent || '')));
  assert.ok(options.some((text) => text.includes('免费')), '每条要标出档位（免费）');
  assert.ok(options.some((text) => text.includes('兜底')), '每条要标出档位（兜底）');

  // 5. 选一个免费模型 → 打 /model-selection，mode=pool，带这个模型，不问钱。
  await select.selectOption('codebuddy/minimax-m3.1-flash');
  await page.locator(`.flat-alert[data-harness="${ADMIN}"] [data-act="apply-trouble-model"]`).click();
  await page.waitForTimeout(2000);
  const selection = writes.find((item) => item.route === '/model-selection');
  assert.ok(selection, `要打 /model-selection，实际：${JSON.stringify(writes)}`);
  assert.equal(selection.body.harness_id, ADMIN, '换的是那个窗口的模型');
  assert.equal(selection.body.model_mode, 'pool', '走的是额度池');
  assert.equal(selection.body.model_id, 'codebuddy/minimax-m3.1-flash', '带的是选中的那个模型');
  assert.equal(selection.body.paid_consent, false, '免费的不该点头');

  // 6. 处理掉的那件消失；另一件「先不管」之后红点灭。
  const afterApply = await waitFor(
    paneState,
    (state) => state.blocks.length === 1,
    '处理掉的那件事从这一页消失',
  );
  assert.equal(afterApply.blocks[0].harness, ASSISTANT, '剩下的是没处理的那件');
  await page.locator('.flat-alert [data-act="dismiss-trouble"]').first().click();
  const cleared = await waitFor(rowState, (state) => state.dot === false, '红点消失');
  assert.ok(
    cleared.text.includes('没有待你处理的事'),
    `红点灭了，这一行也要说清楚，实际：${cleared.text}`,
  );

  // 7. 刷新回来不再亮：记的是"这件事有结论了"，不是"看过了"。
  await page.reload();
  await page.waitForSelector('.project-card-open', { timeout: 30000 });
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-conv', { timeout: 30000 });
  await page.waitForTimeout(2000);
  const back = await rowState();
  assert.ok(back, '刷新后左栏还在');
  assert.equal(back.dot, false, `处理过的刷新回来不该再亮，实际：${back.text}`);
  await page.locator('#aoWorkspace .flat-conv', { hasText: '需要你处理' }).first().click();
  const paneAfter = await waitFor(paneState, (state) => state.blocks.length === 0, '这一页空了');

  const dismissed = await page.evaluate(() =>
    localStorage.getItem('agent-office.model-trouble-dismissed.v1'),
  );
  assert.ok(dismissed && dismissed !== '[]', '两次结论都记下来了，否则刷新会弹回来');
  assert.ok(
    paneAfter.summary.includes('现在没有需要你处理的事'),
    `这一页要说清现在没有事，实际：${paneAfter.summary}`,
  );

  assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
  console.log('verify-office-model-trouble: ok');
} finally {
  await browser.close();
  server.close();
}
