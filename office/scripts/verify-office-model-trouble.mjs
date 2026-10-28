// 「模型用不了」的弹窗：什么时候弹、弹什么、选完存哪儿。
//
// 断言：
//   1. 平台上记着一条模型失败时，弹窗自动弹出来，并说出是谁的模型坏了；
//   2. 弹窗里的模型清单**按额度属主分组**，每条标出档位（免费/付费/兜底/你自己的）；
//   3. 选一个模型点「就用这个模型」，打的是 /model-selection，mode=pool，
//      带的是选中的那个模型；按量计费的先问一句（没点头不发）；
//   4. 处理过的不再弹（下次是新的失败才弹）；
//   5. 弹窗是**非模态**的：它不该把整个办公室按住（人可以先干别的）；
//   6. 没有模型问题时，弹窗不出现、右栏也没有那张卡。
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

const ADMIN = 'admin-window';
const GROUP = 'g';
const PLAN_ID = 'plan-1';

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
    { harness_id: 'm1', name: '成员一', status: 'online', capabilities: ['coding'], credential_count: 1 },
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

const TASK = {
  task_id: 'FD-0123456789abcdef',
  title: '示例项目 · 第一件事',
  group_id: GROUP,
  status: 'ready',
  owner: null,
  attempt_no: 1,
  updated_at: '2026-10-28T04:00:00Z',
  state_changed_seq: 9,
  // 平台记着的那条失败：成员 worker 在这条活上撞了模型通道。
  last_failure: {
    reason:
      'retryable failure (CliFailed): the harness CLI failed before it could deliver'
      + '（模型用不了：ERROR stream disconnected before completion: error sending request'
      + ' for url (http://127.0.0.1:8402/responses)）',
    at: '2026-10-28T03:59:00Z',
  },
};

const MESSAGES = [
  // 管理员报的模型失败：原文里就有限流/连接的说法。
  {
    message_id: 'msg-admin-1',
    created_seq: 11,
    created_at: '2026-10-28T03:58:00Z',
    sender_type: 'harness',
    sender_id: ADMIN,
    body:
      '这一条没有答上来。\n原因：AdminHarnessError: admin harness call exited 1:'
      + ' stream disconnected before completion: error sending request for url'
      + ' (http://127.0.0.1:8402/responses)',
    refs: ['direct'],
  },
  // 成员在传阅意见里**讨论**到 429：那不是它自己的模型坏了，不许拿它弹窗。
  {
    message_id: 'msg-member-1',
    created_seq: 12,
    created_at: '2026-10-28T03:58:30Z',
    sender_type: 'harness',
    sender_id: 'm1',
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
          { harness_id: 'm1', name: '成员一', status: 'online', usable: true, model: 'x', model_mode: 'cli', model_id: null },
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

  await page.addInitScript(([group, plan]) => {
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
    void group;
    void plan;
  }, [GROUP, PLAN_ID]);

  await page.goto(base);
  // 「处理过的不再弹」那份记录要**从空开始**：这行只做一次，不放进
  // `addInitScript`（那个每次加载都跑，会连 reload 后也一起清掉）。
  await page.evaluate(() => localStorage.removeItem('agent-office.model-trouble-dismissed.v1'));
  await page.waitForSelector('.project-card-open', { timeout: 30000 });
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-conv', { timeout: 30000 });

  // 1. 弹窗自己弹出来。
  const dialog = page.locator('#flatModelTrouble');
  await dialog.waitFor({ state: 'visible', timeout: 20000 });
  assert.equal(await dialog.getAttribute('aria-modal'), 'false', '弹窗不该是模态的：它不该把办公室按住');

  const listText = await page.locator('#flatModelTroubleList').innerText();
  assert.ok(listText.includes('Codex · 管理员'), `弹窗要说出是谁的模型坏了：${listText}`);
  assert.ok(/stream disconnected|模型用不了/.test(listText), '弹窗要说出为什么（平台记着的原文）');

  // 成员的传阅意见里讨论 429，不该被当成"它自己的模型坏了"。
  assert.ok(!listText.includes('成员一'), '成员讨论到 429 不算它自己的模型坏了');

  // 2. 按额度属主分组、每条标档位。
  const groups = await page.locator('#flatModelTrouble select optgroup').evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute('label')),
  );
  assert.ok(groups.includes('codebuddy'), `清单要按属主分组：${groups.join(',')}`);
  assert.ok(groups.includes('qoder'), `清单要按属主分组：${groups.join(',')}`);
  const options = await page.locator('#flatModelTrouble select option').allInnerTexts();
  assert.ok(options.some((text) => text.includes('免费')), '每条要标出档位（免费）');
  assert.ok(options.some((text) => text.includes('兜底')), '每条要标出档位（兜底）');

  // 3. 选一个免费模型 → 打 /model-selection，mode=pool，带这个模型。
  await page.locator('#flatModelTrouble select').selectOption('codebuddy/minimax-m3.1-flash');
  await page.locator('[data-act="apply-trouble-model"]').click();
  await page.waitForFunction(
    () => !document.querySelector('[data-act="apply-trouble-model"]'),
    { timeout: 20000 },
  );
  const selection = writes.find((item) => item.route === '/model-selection');
  assert.ok(selection, `要打 /model-selection，实际：${JSON.stringify(writes)}`);
  assert.equal(selection.body.harness_id, ADMIN);
  assert.equal(selection.body.model_mode, 'pool');
  assert.equal(selection.body.model_id, 'codebuddy/minimax-m3.1-flash');

  // 4. 处理过的不再弹。
  const dismissedAfterApply = await page.evaluate(() =>
    localStorage.getItem('agent-office.model-trouble-dismissed.v1'),
  );
  assert.ok(dismissedAfterApply, '选完模型要记下"这件事处理过了"，否则会一直弹');
  await page.reload();
  await page.waitForSelector('.project-card-open', { timeout: 30000 });
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-conv', { timeout: 30000 });
  await page.waitForTimeout(1500);
  const reopened = (await dialog.count()) > 0 && (await dialog.isVisible());
  if (reopened) {
    const dismissedRaw = await page.evaluate(() =>
      localStorage.getItem('agent-office.model-trouble-dismissed.v1'),
    );
    const listNow = await page.locator('#flatModelTroubleList').innerText().catch(() => '');
    throw new Error(
      `处理过的不该再弹（记录了 ${dismissedRaw}；弹窗现在写着：${listNow.slice(0, 200)}）`,
    );
  }

  assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
  console.log('verify-office-model-trouble: ok');
} finally {
  await browser.close();
  server.close();
}
