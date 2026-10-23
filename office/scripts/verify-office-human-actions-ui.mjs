// 浏览器实测：按钮**真的**在界面上、点了**真的**打到平台的端点上。
//
// 这一条不能省：`verify-office-human-actions.mjs` 验的是门控与网关，
// 验不了"这个按钮有没有被渲染出来、点了之后请求长什么样"。页面里
// 少挂一个事件监听器，纯逻辑测试照样全绿。
//
// 自带一个静态服务器 + 拦截全部 /api/formal/*：不连平台、不需要 4174，
// 所以任何机器上都能跑（浏览器二进制仍要本地装好 playwright）。
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

const TASK_ID = 'FD-0123456789abcdef';
const ESCALATED_ID = 'FD-2222222222222222';
const PLAN_ID = 'P-1';

const task = {
  task_id: TASK_ID,
  title: '只读观察回归 · 等人回答的活',
  group_id: 'g',
  status: 'awaiting_human',
  owner: 'codex-window',
  assigned_harness_ids_json: '["codex-window"]',
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
  created_at: '2026-10-22T10:00:00Z',
  updated_at: '2026-10-22T11:00:00Z',
};

// 预算用完的升级任务：放行它必须显式带上"再给一次机会"。
const escalated = {
  ...task,
  task_id: ESCALATED_ID,
  title: '只读观察回归 · 预算用完的升级任务',
  status: 'escalated',
  attempt_no: 3,
  max_attempts: 3,
  state_changed_seq: 20,
};

const responses = {
  '/status': {
    configured: true,
    connected: true,
    project_id: 'p',
    group_id: 'g',
    operator: 'office-operator',
    admin: { configured: true, identity: 'admin-window', error: null },
    project_members: ['codex-window'],
    harnesses: [
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
  '/tasks': { tasks: [task, escalated] },
  '/events': { events: [], next_seq: 0, has_more: false },
  '/messages': { messages: [] },
  '/availability': { availability: [] },
  '/pool': { upstreams: [], models: [], usage: {} },
  '/plan': {
    plans: [{ plan_id: PLAN_ID, status: 'draft', plan_hash: 'h', created_at: '2026-10-22T09:00:00Z' }],
    draft: { plan_id: PLAN_ID, status: 'draft', document: { tasks: 1, markdown_path: '/tmp/plan.md' } },
  },
  [`/tasks/${TASK_ID}`]: { task, diff: { files: [] }, events: [] },
  [`/tasks/${TASK_ID}/output`]: { output: null },
  [`/tasks/${TASK_ID}/verification`]: {},
};

const browser = await chromium.launch({ headless: true });
const calls = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/formal/**', async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname.replace('/api/formal', '');
    const raw = request.postData();
    calls.push({ path: pathname, method: request.method(), body: raw ? JSON.parse(raw) : null });
    const payload = responses[pathname] ?? {};
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(payload),
    });
  });

  // 塞一个本机项目，直接进办公室。
  const project = {
    id: 'project-human-actions',
    index: '98',
    name: '只读观察回归',
    createdAt: '2026/10/22 00:00:00',
    phase: 'scene',
    source: 'platform',
    members: [],
    taskIds: [TASK_ID],
  };
  await page.goto(base);
  await page.evaluate(
    ([key, activeKey, value]) => {
      localStorage.setItem(key, JSON.stringify([value]));
      localStorage.setItem(activeKey, value.id);
    },
    ['agent-office.projects.v1', 'agent-office.active-project.v1', project],
  );
  await page.reload();
  const card = page.locator('.project-card', { hasText: '只读观察回归' }).first();
  await card.waitFor({ state: 'visible' });
  await card.locator('.project-card-open').click();
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });

  // 详情里的「人自己的动作」。
  const detail = page.locator('.flat-detail-actions');
  await detail.locator('[data-act="decide-resume"]').waitFor({ state: 'visible' });
  assert.equal(await detail.locator('[data-act="decide-resume"]').count(), 1, '有「回答并恢复」');
  assert.equal(await detail.locator('[data-act="decide-reopen"]').count(), 1, '有「重新打开」');
  assert.equal(await detail.locator('[data-act="decide-cancel"]').count(), 1, '有「取消这条活」');
  assert.equal(await detail.locator('[data-act="decide-release"]').count(), 0, '等待回答的活没有「放行」');
  assert.equal(await detail.locator('[data-act="decide-adjudicate"]').count(), 0, '裁决不在群主面');

  // 点「我来回答」：先问清楚，再打平台。
  await detail.locator('[data-act="decide-resume"]').click();
  await page.waitForSelector('#flatDecision[open]');
  // 勾选框只在"放行"且预算用完时才该出现 —— 它之前在每个对话框里都露着
  // （hidden 属性被 display:grid 盖掉了），那是靠肉眼看出来的。
  assert.equal(
    await page.locator('[data-decision-override-wrap]').isVisible(),
    false,
    '回答的对话框里不该问预算豁免',
  );
  // 对话框不该是半个屏幕高的空盒子。
  const dialogHeight = await page.locator('#flatDecision').evaluate(node => node.getBoundingClientRect().height);
  assert.ok(dialogHeight < 400, `处置对话框按内容收着（实际 ${Math.round(dialogHeight)}px）`);
  await page.locator('[data-decision-text]').fill('按第二条做');
  await page.locator('[data-decision-confirm]').click();
  await page.waitForTimeout(400);
  const resume = calls.find(call => call.path === '/human/resume');
  assert.ok(resume, '回答走了 /human/resume');
  assert.equal(resume.method, 'POST');
  assert.equal(resume.body.task_id, TASK_ID);
  assert.equal(resume.body.answer, '按第二条做');

  // 预算用完的升级任务：放行时必须显式问"再给一次机会"。
  await page.locator('.flat-task', { hasText: '预算用完的升级任务' }).first().click();
  await page.waitForTimeout(800);
  const escalatedDetail = page.locator('.flat-detail-actions');
  await escalatedDetail.locator('[data-act="decide-release"]').waitFor({ state: 'visible' });
  await escalatedDetail.locator('[data-act="decide-release"]').click();
  await page.waitForSelector('#flatDecision[open]');
  assert.equal(
    await page.locator('[data-decision-override-wrap]').isVisible(),
    true,
    '预算用完的活放行前要问一次"再给一次机会"',
  );
  await page.locator('[data-decision-text]').fill('换个窗口再试');
  await page.locator('[data-decision-override]').check();
  await page.locator('[data-decision-confirm]').click();
  await page.waitForTimeout(400);
  const release = calls.find(call => call.path === '/human/release');
  assert.ok(release, '放行走了 /human/release');
  assert.equal(release.body.budget_override, true, '勾了"再给一次机会"就带上它');
  assert.equal(release.body.reason, '换个窗口再试');

  // 方案卡片：批准旁边要有驳回。
  const rail = page.locator('.flat-rail');
  await rail.locator('[data-act="approve-plan"]').waitFor({ state: 'visible' });
  assert.equal(await rail.locator('[data-act="reject-plan"]').count(), 1, '有「驳回方案」');
  await rail.locator('[data-act="reject-plan"]').click();
  await page.waitForSelector('#flatDecision[open]');
  await page.locator('[data-decision-text]').fill('范围太大');
  await page.locator('[data-decision-confirm]').click();
  await page.waitForTimeout(400);
  const reject = calls.find(call => call.path === '/plan/reject');
  assert.ok(reject, '驳回走了 /plan/reject');
  assert.equal(reject.method, 'POST');
  assert.equal(reject.body.plan_id, PLAN_ID);
  assert.equal(reject.body.reason, '范围太大');

  assert.deepEqual(errors, [], '页面没有报错');

  // 版式不塌：按钮多了一排，窄屏下不能把详情面板顶出横向滚动条。
  for (const width of [1600, 1200, 900]) {
    await page.setViewportSize({ width, height: 950 });
    await page.waitForTimeout(200);
    const overflow = await page.evaluate(() => {
      const root = document.documentElement;
      const actions = document.querySelector('.flat-detail-actions');
      const box = actions ? actions.getBoundingClientRect() : null;
      const wide = actions
        ? [...actions.querySelectorAll('button')].filter(
            button => button.getBoundingClientRect().right > box.right + 1,
          ).length
        : 0;
      return {
        scrollWidth: root.scrollWidth,
        clientWidth: root.clientWidth,
        wide,
      };
    });
    assert.ok(
      overflow.scrollWidth <= overflow.clientWidth + 1,
      `${width}px：页面没有横向滚动条（${overflow.scrollWidth} > ${overflow.clientWidth}）`,
    );
    assert.equal(overflow.wide, 0, `${width}px：动作按钮没有溢出详情面板`);
  }

  console.log('verify-office-human-actions-ui: ok');
} finally {
  await browser.close();
  server.close();
}
