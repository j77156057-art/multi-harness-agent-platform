// 会话列表（微信那一列）：左栏有什么、点进去有什么。
//
// 自带静态服务器 + 拦全部 /api/formal/*（不连平台也能跑）。
// 断言：
//   1. 左栏是一列会话：项目任务 / 项目群聊 / 平台助手 / 管理员 / 每个成员窗口；
//      每行有名字 + 一行状态；
//   2. 点一个窗口 → 中间那栏是这个窗口的状态页（在线/模型/本项目里的活/最近一次尝试/轨迹）；
//   3. 点群聊 → 中间那栏是群消息（**展开**，不是空框）；
//   4. 点项目任务 → 中间那栏是任务表；点一条活 → 详情 +「← 返回任务表」。
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
const MEMBER = 'codex-window';
const TASK = 'FD-0123456789abcdef';

const task = {
  task_id: TASK,
  title: '开工链路 · 收一条真实任务',
  group_id: 'g',
  status: 'in_progress',
  owner: MEMBER,
  assigned_harness_ids_json: `["${MEMBER}"]`,
  attempt_id: 'A-1',
  attempt_no: 1,
  max_attempts: 3,
  budget_override_by: null,
  escalated_at: null,
  state_changed_seq: 12,
  registration_version: 'reg-001',
  receipt_id: 'rc-1',
  epoch: 1,
  last_failure: null,
  supersedes: null,
  created_at: '2026-10-24T10:00:00Z',
  updated_at: '2026-10-24T10:30:00Z',
};

const second = {
  ...task,
  task_id: 'FD-fedcba9876543210',
  title: '开工链路 · 第二件事',
  status: 'awaiting_human',
  owner: ADMIN,
  updated_at: '2026-10-24T11:00:00Z',
};

// 一条**很长**的目标原文：末尾那句是用来证明「没有截断」的。
const GOAL =
  '把这一轮的办公室改动收口：每一条都要说清是谁做的、做到哪一步、失败了为什么。'
  + '最后再补一句收尾的话，用来看清楚它到底有没有被截断。';

const responses = {
  '/status': {
    configured: true,
    connected: true,
    project_id: 'p',
    group_id: 'g',
    operator: 'office-operator',
    admin: { configured: true, identity: ADMIN, error: null },
    project_members: [MEMBER],
    harnesses: [
      { harness_id: ADMIN, name: 'Codex · 管理员', status: 'online', registration_version: 'reg-001', provider: 'openai', credential_count: 1, capabilities: ['coding'], running_tasks: 1 },
      { harness_id: MEMBER, name: 'Codex CLI', status: 'online', registration_version: 'reg-001', provider: 'openai', credential_count: 1, capabilities: ['coding'], running_tasks: 1 },
      { harness_id: 'office-admin', name: '平台助手', status: 'online', registration_version: 'reg-001', provider: 'openai', credential_count: 1, capabilities: ['coding'], running_tasks: 0 },
      { harness_id: 'no-credential', name: '没有凭据的窗口', status: 'online', registration_version: 'reg-001', provider: 'openai', credential_count: 0, capabilities: ['coding'], running_tasks: 0 },
    ],
    base_adapters: [],
  },
  '/tasks': { tasks: [task, second] },
  [`/tasks/${TASK}`]: {
    task: {
      task_id: TASK,
      spec: { goal: GOAL, acceptance: ['跑得起来', '说清做了什么'], exclusive_paths: ['office/**'] },
    },
  },
  '/events': {
    events: [
      // payload 里混着机器字段（哈希）：界面上不该摆出来；「原因」是人看的，要摆。
      { aggregate_id: TASK, event_name: 'claim', actor_id: MEMBER, payload: { findings_hash: 'sha256:deadbeef', reason: '它没交出东西' }, created_at: '2026-10-24T10:05:00Z' },
      { aggregate_id: TASK, event_name: 'start', actor_id: MEMBER, payload: {}, created_at: '2026-10-24T10:06:00Z' },
    ],
    next_seq: 0,
    has_more: false,
  },
  '/messages': {
    messages: [
      { created_seq: 1, created_at: '2026-10-24T10:10:00Z', sender_type: 'human', sender_id: 'office-operator', body: '群里的话', refs: [], mentions: [] },
      // 成员窗口提「要改目标」：群里要一眼看见（等群主拍板）。
      { created_seq: 2, created_at: '2026-10-24T10:20:00Z', sender_type: 'harness', sender_id: 'codex-window', body: '这个目标做不到：要改成只做那半边', refs: ['goal-change'], mentions: [] },
    ],
  },
  '/availability': {
    availability: [
      { harness_id: ADMIN, provider: 'openai', adapter: 'codex', status: 'online', registration_version: 'reg-001', credential_count: 1, model: 'gpt-5', model_alias: null, model_source: 'cfg', degraded: false, last_probe_at: null, warnings: [], last_task_id: TASK, last_attempt_status: 'in_progress', recovered: false, usable: true, reasons: [] },
      { harness_id: MEMBER, provider: 'openai', adapter: 'codex', status: 'online', registration_version: 'reg-001', credential_count: 1, model: 'gpt-5', model_alias: null, model_source: 'cfg', degraded: false, last_probe_at: null, warnings: [], last_task_id: TASK, last_attempt_status: 'in_progress', recovered: false, usable: true, reasons: [] },
      { harness_id: 'office-admin', provider: 'openai', adapter: 'codex', status: 'online', registration_version: 'reg-001', credential_count: 1, model: 'gpt-5', model_alias: null, model_source: 'cfg', degraded: false, last_probe_at: null, warnings: [], last_task_id: null, last_attempt_status: null, recovered: false, usable: true, reasons: [] },
      { harness_id: 'no-credential', provider: 'openai', adapter: 'codex', status: 'online', registration_version: 'reg-001', credential_count: 0, model: null, model_alias: null, model_source: 'not-readable', degraded: false, last_probe_at: null, warnings: [], last_task_id: null, last_attempt_status: null, recovered: false, usable: false, reasons: [{ code: 'no_credential', detail: 'no platform credential' }] },
    ],
  },
  '/pool': {
    upstreams: [],
    models: [
      { id: 'qoder/Qwen3.8-Flash', owned_by: 'qoder:Qwen3.8-Flash', upstream: 'qoder:Qwen3.8-Flash', protocol: 'cli', tier: 'free' },
      { id: 'codebuddy/hy3', owned_by: 'codebuddy:hy3', upstream: 'codebuddy:hy3', protocol: 'cli', tier: 'free' },
      { id: 'gpt-5.6', owned_by: 'codex:geekspace', upstream: 'codex:geekspace', protocol: 'openai_responses', tier: 'metered' },
    ],
    usage: {
      upstreams: {},
      consumers: {
        'harness/codex-window': {
          requests: 4,
          prompt_tokens: 1000,
          completion_tokens: 400,
          cached_tokens: 200,
          generation_rate: 42.5,
          cache_hit_rate: 0.2,
          duration_ms: 9400,
          // 成员自报的那部分（自带模型跑的活，平台不在那条链路上）。
          reported: {
            requests: 2,
            prompt_tokens: 2000,
            completion_tokens: 300,
            cached_tokens: 1800,
            cache_hit_rate: 0.9,
          },
        },
      },
      requests: 4,
      tokens_unknown: 0,
    },
    timeline: {
      bucket: 'hour',
      hours: 24,
      buckets: [
        {
          at: '2026-10-24T10:00:00+00:00',
          requests: 4,
          prompt_tokens: 300,
          completion_tokens: 100,
          cached_tokens: 50,
          consumer_tokens: {
            'harness/codex-window': { prompt_tokens: 300, completion_tokens: 100, cached_tokens: 50 },
          },
          reported_consumer_tokens: {
            'harness/codex-window': { prompt_tokens: 900, completion_tokens: 150, cached_tokens: 800 },
          },
        },
      ],
    },
    inventory: { tiers: [], checked_at: '2026-10-24T10:00:00+00:00' },
    rosters: { codebuddy: ['hy3', 'step-5-preview'] },
  },
  '/plan': { plans: [], draft: null },
  '/pool/probe': {
    upstreams: [],
    models: [
      { id: 'qoder/Qwen3.8-Flash', owned_by: 'qoder:Qwen3.8-Flash', upstream: 'qoder:Qwen3.8-Flash', protocol: 'cli', tier: 'free' },
      { id: 'codebuddy/hy3', owned_by: 'codebuddy:hy3', upstream: 'codebuddy:hy3', protocol: 'cli', tier: 'free' },
      { id: 'gpt-5.6', owned_by: 'codex:geekspace', upstream: 'codex:geekspace', protocol: 'openai_responses', tier: 'metered' },
    ],
    usage: { upstreams: {}, consumers: {}, requests: 0, tokens_unknown: 0 },
    timeline: { bucket: 'hour', hours: 24, buckets: [] },
    inventory: { tiers: [], checked_at: '2026-10-24T10:00:00+00:00' },
    rosters: { codebuddy: ['hy3', 'step-5-preview'] },
  },
  '/model-selection': {
    selection: { harness_id: MEMBER, model_mode: 'pool', model_id: 'qoder/Qwen3.8-Flash', updated_at: '2026-10-24T11:00:00Z' },
  },
};

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const posted = [];
  await context.route('**/api/formal/**', async route => {
    const pathname = new URL(route.request().url()).pathname.replace('/api/formal', '');
    if (route.request().method() === 'POST') {
      const raw = route.request().postData() || '';
      let body = null;
      try { body = raw ? JSON.parse(raw) : null; } catch { body = null; }
      posted.push({ path: pathname, body });
    }
    const payload = responses[pathname] ?? {};
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(payload),
    });
  });

  await page.goto(base);
  const project = {
    id: 'project-conversations',
    index: '93',
    name: '会话回归',
    createdAt: '2026/10/24 00:00:00',
    phase: 'scene',
    source: 'platform',
    members: [],
    taskIds: [TASK, 'FD-fedcba9876543210'],
  };
  await page.evaluate(
    ([key, activeKey, value]) => {
      localStorage.setItem(key, JSON.stringify([value]));
      localStorage.setItem(activeKey, value.id);
    },
    ['agent-office.projects.v1', 'agent-office.active-project.v1', project],
  );
  await page.reload();
  const card = page.locator('.project-card', { hasText: '会话回归' }).first();
  await card.waitFor({ state: 'visible' });
  await card.locator('.project-card-open').click();
  await page.waitForSelector('#aoWorkspace .flat-convs', { timeout: 20000 });
  await page.waitForTimeout(1200);

  // 1. 左栏是一列会话
  const rows = await page.evaluate(() =>
    [...document.querySelectorAll('#aoWorkspace .flat-conv')].map((el) => ({
      name: (el.querySelector('b')?.textContent || '').trim(),
      status: (el.querySelector('span')?.textContent || '').trim(),
    })));
  console.log('会话列表:', rows);
  const names = rows.map((r) => r.name);
  assert.ok(names.includes('项目任务'), '有「项目任务」这一会话');
  assert.ok(names.includes('项目群聊'), '有「项目群聊」这一会话');
  assert.ok(names.includes('平台助手'), '有「平台助手」这一会话');
  assert.ok(names.some((n) => n.includes('管理员')), '有管理员窗口这一会话');
  assert.ok(names.includes('Codex CLI'), '有成员窗口这一会话');
  assert.ok(names.includes('没有凭据的窗口'), '连不能接活的窗口也在列（它同样是个对象）');
  for (const row of rows) {
    assert.ok(row.name, '每行都有名字');
    assert.ok(row.status, `「${row.name}」有一行状态`);
  }
  assert.ok(
    rows.find((r) => r.name === '没有凭据的窗口').status.includes('暂时无法领取任务'),
    '接不了活的窗口说清原因',
  );
  assert.ok(
    rows.find((r) => r.name === 'Codex CLI').status.includes('执行中'),
    '在跑的窗口说「正在做」',
  );

  // 2. 点一个窗口 → 中间那栏是它的状态页
  await page.locator('#aoWorkspace .flat-conv', { hasText: 'Codex CLI' }).first().click();
  await page.waitForTimeout(1200);
  const facts = await page.evaluate(() => {
    const dts = [...document.querySelectorAll('#aoWorkspace .flat-window .flat-facts dt')];
    const dds = [...document.querySelectorAll('#aoWorkspace .flat-window .flat-facts dd')];
    return dts.map((dt, i) => dt.textContent.trim() + ' = ' + (dds[i]?.textContent.trim() || ''));
  });
  console.log('窗口状态页:', facts);
  const factText = facts.join(' | ');
  assert.ok(factText.includes('状态'), '说了它在线还是离线');
  assert.ok(factText.includes('使用的模型'), '说了它用哪个模型');
  assert.ok(factText.includes('本项目里的任务'), '说了它在本项目里的活');
  assert.ok(factText.includes('最近一次尝试'), '说了它上一次在干什么');
  assert.ok(
    await page.locator('#aoWorkspace .flat-window .flat-events').count() > 0,
    '有它的轨迹',
  );
  // 轨迹读得懂：说了这是什么、每行带活的名字、机器字段不摆。
  const windowPaneText = await page.locator('#aoWorkspace .flat-window').innerText();
  assert.ok(windowPaneText.includes('它做过的动作'), '轨迹块说清了这叫什么');
  assert.ok(windowPaneText.includes('平台记录的本窗口动作'), '轨迹块说明了这是什么');
  assert.ok(
    windowPaneText.includes('开工链路 · 收一条真实任务'),
    '每一行带这条活的名字（动作 + 对象才是一句话）',
  );
  const windowPaneDom = await page
    .locator('#aoWorkspace .flat-window')
    .evaluate((el) => el.textContent || '');
  assert.ok(!windowPaneDom.includes('sha256:deadbeef'), '机器字段（哈希）不摆出来');
  assert.ok(windowPaneDom.includes('它没交出东西'), '给人看的字段（原因）摆出来');

  // 轨迹能点开看详细任务：点「查看任务详情」→ 跳到那条活的详情（目标原文在）。
  const jump = page.locator('#aoWorkspace .flat-window button[data-open-task]').first();
  assert.equal(await jump.count(), 1, '轨迹行有「查看任务详情」');
  assert.equal(await jump.innerText(), '查看任务详情', '按钮写的是「查看任务详情」');
  await jump.click();
  await page.waitForTimeout(2500);
  const detailText = await page.locator('#aoWorkspace .flat-detail').innerText();
  assert.ok(detailText.includes('把这一轮的办公室改动收口'), '跳到的是那条活的详情（目标原文在）');
  // 跳过去就停在详情了：回到任务表，后面的步骤照旧走。
  await page.locator('#aoWorkspace .flat-back').click();
  await page.waitForTimeout(800);


  // 3. 点群聊 → 中间那栏是群消息（展开的，不是空框）
  await page.locator('#aoWorkspace .flat-conv', { hasText: '项目群聊' }).first().click();
  await page.waitForTimeout(1000);
  const chatText = await page.locator('#aoWorkspace .flat-main .flat-chat-list').innerText();
  console.log('群聊正文:', chatText.slice(0, 60));
  assert.ok(chatText.includes('群里的话'), '群消息在中间那栏铺开了');
  assert.ok(
    await page.locator('#aoWorkspace .flat-main .flat-chat-form').count() === 1,
    '还能在中间那栏发言',
  );

  // 4. 点项目任务 → 任务表；点一条活 → 详情 + 返回
  await page.locator('#aoWorkspace .flat-conv', { hasText: '项目任务' }).first().click();
  await page.waitForTimeout(1000);
  assert.ok(
    await page.locator('#aoWorkspace .flat-main .flat-task').count() > 0,
    '任务表在中间那栏',
  );
  await page.locator('#aoWorkspace .flat-main .flat-task').first().click();
  await page.waitForTimeout(1500);
  assert.equal(await page.locator('#aoWorkspace .flat-back').count(), 1, '详情里有「返回任务表」');
  await page.locator('#aoWorkspace .flat-back').click();
  await page.waitForTimeout(800);
  assert.ok(
    await page.locator('#aoWorkspace .flat-main .flat-task').count() > 0,
    '返回之后又是任务表',
  );

  // 5. 详情上方留一条紧凑的活列表：换看另一条活不用先回任务表。
  await page.locator('#aoWorkspace .flat-main .flat-task').first().click();
  await page.waitForTimeout(1500);
  const switches = page.locator('#aoWorkspace .flat-switch');
  assert.equal(await switches.count(), 2, '详情上方有本项目的活列表');
  assert.equal(
    await page.locator('#aoWorkspace .flat-switch.is-on').count(),
    1,
    '当前那条被标出来',
  );
  const before = await page.locator('#aoWorkspace .flat-detail-head h2').innerText();
  await page.locator('#aoWorkspace .flat-switch', { hasText: '第二件事' }).first().click();
  await page.waitForTimeout(1500);
  const after = await page.locator('#aoWorkspace .flat-detail-head h2').innerText();
  console.log('切换前:', before, '→ 切换后:', after);
  assert.ok(after.includes('第二件事'), '点另一条活，详情直接切过去');

  // 6. 窗口页的活：一张张可展开的卡片，点开是**完整的目标原文**（不截断）。
  await page.locator('#aoWorkspace .flat-conv', { hasText: 'Codex CLI' }).first().click();
  await page.waitForTimeout(1200);
  const cards = page.locator('#aoWorkspace .flat-wtask');
  assert.equal(await cards.count(), 1, '这个窗口的那条活有一张卡片');
  assert.ok((await cards.first().innerText()).includes('收一条真实任务'), '卡片写的是这条活');
  assert.equal(await cards.first().evaluate((el) => el.open), false, '卡片默认是收起的');
  await cards.first().locator('summary').click();
  await page.waitForTimeout(1500);
  const goal = (await page.locator('#aoWorkspace .flat-wtask-goal').first().innerText()).trim();
  console.log('目标原文:', goal.slice(0, 40), '…');
  assert.ok(goal.includes('用来看清楚它到底有没有被截断'), '目标原文是**完整**的（末尾那句在）');
  assert.ok(goal.includes('每一条都要说清'), '目标原文的开头也在');
  const body = await page.locator('#aoWorkspace .flat-wtask-body').first().innerText();
  assert.ok(body.includes('跑得起来'), '展开后有验收标准');
  assert.ok(body.includes('office/**'), '展开后有「允许修改的路径」');

  // 办公室每 15 秒整页重画一次：展开的那条活必须**仍然是展开的**，
  // 否则用户正看着的详情会在下一轮刷新里从手里跑掉。
  await page.waitForTimeout(16000);
  const stillOpen = await page.locator('#aoWorkspace .flat-wtask').first().evaluate((el) => el.open);
  assert.equal(stillOpen, true, '重画之后展开的活仍然是展开的');
  assert.ok(
    (await page.locator('#aoWorkspace .flat-wtask-goal').first().innerText()).includes('用来看清楚它到底有没有被截断'),
    '重画之后目标原文还在（任务书重新读了一遍）',
  );

  // 7. 窗口页的 token 度量：每秒生成 / 总消耗 / 每小时 / 缓存命中率。
  await page.locator('#aoWorkspace .flat-conv', { hasText: 'Codex CLI' }).first().click();
  await page.waitForTimeout(1200);
  const metrics = await page.locator('#aoWorkspace .flat-facts').allInnerTexts();
  const metricText = metrics.join(' | ');
  console.log('度量:', metricText);
  assert.ok(metricText.includes('42.5 token/秒'), '写了每秒生成多少 token');
  assert.ok(metricText.includes('1,400'), '写了消耗总 token（千分位）');
  assert.ok(metricText.includes('400'), '写了最近一小时多少 token');
  assert.ok(metricText.includes('20%'), '写了缓存命中率');
  // 两笔账分开摆：额度池记的 / 成员自报的。
  assert.ok(metricText.includes('额度池：消耗总 token'), '额度池那笔单独一行');
  assert.ok(metricText.includes('成员自报：消耗总 token'), '成员自报那笔也摆出来');
  assert.ok(metricText.includes('2,300'), '成员自报的消耗总 token 也在（2000 + 300，千分位）');
  assert.ok(metricText.includes('90%'), '成员自报的缓存命中率也在');
  const usageBlock = await page
    .locator('#aoWorkspace .flat-window .flat-spec', { hasText: '用量' })
    .first()
    .innerText();
  assert.ok(
    usageBlock.includes('走额度池的调用由平台记账'),
    '写清了用量这块的两笔账各是什么',
  );

  // 8. 窗口页的模型选择：自带 / 额度池里的哪个模型，按额度属主分组折叠。
  await page.locator('#aoWorkspace .flat-conv', { hasText: 'Codex CLI' }).first().click();
  await page.waitForTimeout(1200);
  const modelBlock = page.locator('#aoWorkspace .flat-model');
  assert.equal(await modelBlock.count(), 1, '窗口页有「该窗口使用的模型」这一块');
  const modelDetails = modelBlock.locator('details.flat-model-list');
  assert.equal(await modelDetails.count(), 1, '池子里的模型是一个可折叠的清单');
  assert.equal(
    await modelDetails.evaluate((el) => el.open),
    false,
    '清单默认折着（二十几个模型铺开太长）',
  );
  const modelSummary = await modelDetails.locator('> summary').innerText();
  console.log('清单标题:', modelSummary);
  assert.ok(modelSummary.includes('3 家'), '标题写明几家（3 家）');
  assert.ok(modelSummary.includes('3 个可用'), '标题写明几个可用');
  assert.equal(
    await modelBlock.locator('button[data-act="probe-pool"]').count(),
    1,
    '有「重新探一遍」这个探针',
  );
  // 展开：按额度属主分组，每组默认是收着的。
  await modelDetails.locator('> summary').click();
  await page.waitForTimeout(600);
  const owners = modelBlock.locator('details.flat-model-owner');
  assert.equal(await owners.count(), 3, '按额度属主分成了 3 组');
  assert.equal(
    await owners.first().evaluate((el) => el.open),
    false,
    '每组默认是收着的（别弄太长）',
  );
  const ownerText = await owners.allInnerTexts();
  console.log('分组:', ownerText.join(' | '));
  assert.ok(ownerText.some((text) => text.includes('qoder')), 'qoder 自己一组');
  assert.ok(ownerText.some((text) => text.includes('codex')), 'codex 自己一组');
  // 展开 qoder 那组：里面是它自己的模型，每条带档位。
  const qoderGroup = owners.filter({ hasText: 'qoder' }).first();
  await qoderGroup.locator('> summary').click();
  await page.waitForTimeout(400);
  const qoderText = await qoderGroup.innerText();
  assert.ok(qoderText.includes('Qwen3.8-Flash'), 'qoder 组里是它自己的模型');
  assert.ok(qoderText.includes('免费'), '每条都标了它属于哪一档');
  // 名册里没定档的也摆出来了（codebuddy 的 step-5-preview）。
  const codebuddyGroup = owners.filter({ hasText: 'codebuddy' }).first();
  await codebuddyGroup.locator('> summary').click();
  await page.waitForTimeout(400);
  const codebuddyText = await codebuddyGroup.innerText();
  assert.ok(codebuddyText.includes('step-5-preview'), 'CLI 说它能用的也摆出来了');
  assert.ok(codebuddyText.includes('没定档'), '没定档的说清楚是「没定档」');
  assert.ok(
    await codebuddyGroup.locator('button[data-act="declare-model"]').count() >= 2,
    '没定档的旁边有「标成免费 / 标成收费」两个按钮',
  );

  // 选一个免费的池子模型再保存 → 平台收到的是那个模型。
  await modelBlock.locator('input[data-model-id="qoder/Qwen3.8-Flash"]').check();
  await modelBlock.locator('button[data-act="save-model-choice"]').click();
  await page.waitForTimeout(1500);
  const saved = posted.find((item) => item.path === '/model-selection');
  assert.ok(saved, '保存动作真的发到了平台');
  assert.equal(saved.body.harness_id, MEMBER, '保存的是这个窗口');
  assert.equal(saved.body.model_mode, 'pool', '保存的是「走额度池」');
  assert.equal(saved.body.model_id, 'qoder/Qwen3.8-Flash', '保存的是被选中的那个模型');
  assert.equal(saved.body.paid_consent, false, '免费模型不用点头');

  // 9. 收费模型：保存前要问一句，点头之后才发。
  const codexGroup = owners.filter({ hasText: 'codex' }).first();
  await codexGroup.locator('> summary').click();
  await page.waitForTimeout(400);
  await modelBlock.locator('input[data-model-id="gpt-5.6"]').check();
  await modelBlock.locator('button[data-act="save-model-choice"]').click();
  await page.waitForTimeout(900);
  const paidDialog = page.locator('#flatPaidModel');
  assert.equal(await paidDialog.count(), 1, '收费模型保存前要弹确认');
  assert.ok((await paidDialog.innerText()).includes('按量计费'), '确认框说清要花钱');
  await paidDialog.locator('button[data-paid="yes"]').click();
  await page.waitForTimeout(1500);
  const paid = posted.find((item) => item.path === '/model-selection' && item.body.paid_consent);
  assert.ok(paid, '点头之后才带 paid_consent 发出去');
  assert.equal(paid.body.model_id, 'gpt-5.6', '点的是哪个模型就是哪个');

  // 10. 探针：点了就重新探一遍，平台清缓存重读。
  await modelBlock.locator('button[data-act="probe-pool"]').click();
  await page.waitForTimeout(1500);
  assert.ok(
    posted.some((item) => item.path === '/pool/probe'),
    '探针真的发到了平台',
  );

  // 11. 当场定档：没定档的模型旁边有「标成免费 / 标成收费」。
  const declare = modelBlock.locator('button[data-act="declare-model"]').first();
  assert.ok((await declare.count()) === 1, '没定档的模型旁边有「标成…」的按钮');
  await declare.click();
  await page.waitForTimeout(1200);
  const declared = posted.find((item) => item.path === '/pool/declaration');
  assert.ok(declared, '定档真的发到了平台');
  assert.ok(declared.body.cli, '说的是哪家的 CLI');
  assert.ok(declared.body.model, '说的是哪个模型');
  assert.ok(['free', 'metered'].includes(declared.body.tier), '说的是哪一档');

  // 12. 状态栏：平台连不连得上、几个窗口在线。
  const conn = await page.locator('#aoConn').innerText();
  console.log('状态栏:', conn);
  assert.ok(conn.includes('已连接平台'), '状态栏说平台连上了');
  assert.ok(conn.includes('在线'), '状态栏给出窗口的在线数');

  // 13. 中途加入新的窗口：窗口页给一个「加入这个项目」，加入后通知管理员。
  const joinButton = page.locator('#aoWorkspace .flat-detail-actions button[data-act="add-member"]');
  assert.equal(await joinButton.count(), 1, '没在这个项目里的窗口有「加入这个项目」');
  await joinButton.click();
  await page.waitForTimeout(1200);
  const told = posted.find(
    (item) => item.path === '/messages' && (item.body.refs || []).includes('add-member'),
  );
  assert.ok(told, '加入以后要通知管理员');
  assert.ok(
    (told.body.refs || []).some((ref) => ref.startsWith('add-member-json:')),
    '通知里带的是哪个窗口',
  );
  assert.ok(await page.locator('#aoWorkspace .flat-note', { hasText: '已通知管理员' }).count() >= 1, '界面告诉人已经通知过了');

  // 14. 成员提「要改目标」的消息：群里一眼要看见（等群主拍板的事）。
  await page.locator('#aoWorkspace .flat-conv', { hasText: '项目群聊' }).first().click();
  await page.waitForTimeout(1200);
  const goalChat = page.locator('#aoWorkspace .flat-msg.is-goal-change');
  assert.equal(await goalChat.count(), 1, '群里那条「要改目标」的消息被标出来了');
  assert.ok(
    (await goalChat.innerText()).includes('要改目标'),
    '标记写的是「要改目标」',
  );
  assert.ok(
    (await goalChat.innerText()).includes('这个目标做不到'),
    '成员的原话在',
  );

  assert.deepEqual(errors, [], '页面没有报错');
  console.log('verify-office-conversations: ok');
} finally {
  await browser.close();
  server.close();
}
