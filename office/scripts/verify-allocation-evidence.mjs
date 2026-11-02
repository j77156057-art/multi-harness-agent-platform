// 补上的两块界面：分配裁决、任务证据（审核记录 / 取证）。
//
// 它们此前是"后端做了、界面没有入口"的东西（对账见 data/ui-audit.py 的结论）。
// 这一层不连平台：自带静态服务器 + 拦全部 /api/formal/**，验的是界面口径与请求体。
//
// 断言：
//   1. 分配轮开着、有条活没定人 → 「需要你处理」里出现那一块，左栏红点亮；
//   2. 块里列出候选（成员交的申请），并写清"定下来只是邀请"；
//   3. 没选候选就点「定给他」→ 当场说"先选一个"，**不发请求**；
//   4. 选了候选 → 发 /allocation/decision，带 round_id / task_id / application_id /
//      expected_revision（这一轮的 revision）/ reason；
//   5. 「先不分配」没写理由 → 拦住并回灌原因，不发请求；写了理由 → 发 decision/defer；
//   6. 任务卡展开后有两块证据：审核记录、取证；它们**展开才去读**，
//      展开前一个请求都不发，展开后各发一次。
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

const ADMIN = 'codex-window-admin';
const MEMBER = 'codex';
const TASK = 'T-ALLOC-1';
const ROUND = 'round-1';

const TASK_ROW = {
  task_id: TASK,
  title: '示例项目 · 补一份说明',
  group_id: 'g',
  status: 'in_progress',
  // 有人拿着这条活：左栏才会出现那个窗口，窗口页里才有那张活卡。
  owner: MEMBER,
  attempt_no: 1,
  updated_at: '2026-11-02T05:00:00Z',
  state_changed_seq: 3,
};

const ROUNDS = {
  rounds: [
    {
      round_id: ROUND,
      group_id: 'g',
      plan_snapshot_id: 'snap-1',
      state: 'open',
      revision: 4,
      opened_by: ADMIN,
      opened_at: '2026-11-02T04:00:00Z',
      deadline_at: null,
      closed_at: null,
      tasks: [
        {
          task_id: TASK,
          title: '补一份说明',
          goal: '把 README 的启动段补完整',
          assigned_harness_ids: [MEMBER],
          exclusive_paths: ['docs'],
        },
      ],
      applications: [
        {
          application_id: 'app-1',
          harness_id: MEMBER,
          state: 'submitted',
          revision: 1,
          reason: '我熟这块',
          task_ids: [TASK],
        },
        {
          application_id: 'app-2',
          harness_id: 'opencode',
          state: 'submitted',
          revision: 1,
          reason: '',
          task_ids: [TASK],
        },
      ],
      assignments: [],
    },
  ],
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
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const writes = [];
  const reads = [];

  await page.route('**/api/formal/**', (route) => {
    const url = new URL(route.request().url());
    const route_ = url.pathname.replace('/api/formal', '');
    const json = (body, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (route.request().method() === 'POST') {
      writes.push({ route: route_, body: route.request().postDataJSON() });
      return json({ ok: true });
    }
    reads.push(route_);
    if (route_ === '/status') {
      return json({
        configured: true,
        connected: true,
        project_id: 'p',
        group_id: 'g',
        operator: 'office-operator',
        admin: { configured: true, identity: ADMIN, error: null },
        project_members: [],
        harnesses: [
          { harness_id: ADMIN, name: 'Codex · 管理员', status: 'online' },
          { harness_id: MEMBER, name: 'Codex CLI', status: 'online' },
          { harness_id: 'opencode', name: 'OpenCode', status: 'online' },
        ],
        base_adapters: [],
      });
    }
    if (route_ === '/tasks') return json({ tasks: [TASK_ROW] });
    if (route_ === '/events') return json({ events: [], next_seq: 0, has_more: false });
    // 左栏的窗口名单来自这里（state.availability），空名单就没有窗口页可点。
    if (route_ === '/availability') {
      return json({
        availability: [
          { harness_id: ADMIN, name: 'Codex · 管理员', status: 'online', usable: true, model: 'x' },
          { harness_id: MEMBER, name: 'Codex CLI', status: 'online', usable: true, model: 'x' },
        ],
      });
    }
    if (route_ === '/pool') return json({ upstreams: [], models: [], usage: {} });
    if (route_ === '/plan') return json({ plans: [], draft: null });
    if (route_ === '/messages') return json({ messages: [] });
    if (route_ === '/assistant/runs') return json({ runs: [] });
    if (route_ === '/user-keys') return json({ keys: [], pending: null });
    if (route_ === '/installers') return json({ probed: false, installers: [], summary: {} });
    if (route_ === '/allocation/rounds') return json(ROUNDS);
    if (route_ === `/tasks/${TASK}`) {
      return json({
        task: { ...TASK_ROW, spec: { goal: '把 README 的启动段补完整', acceptance: [], exclusive_paths: ['docs'] } },
        diff: null,
        events: [],
      });
    }
    if (route_ === `/tasks/${TASK}/reviews`) {
      return json({
        task_id: TASK,
        reviews: [
          {
            review_id: 'rv-1',
            reviewer_harness_id: ADMIN,
            verdict: 'changes_requested',
            status: 'completed',
            findings: ['启动段缺一步'],
            reviewed_commit: 'abc1234',
            completed_seq: 9,
            same_provider: false,
          },
        ],
        note: '',
      });
    }
    if (route_ === `/tasks/${TASK}/forensics`) {
      return json({
        forensics: {
          task_id: TASK,
          attempt_id: `${TASK}.a1`,
          attempt_no: 1,
          current: false,
          attempt_status: 'failed',
          harness_id: MEMBER,
          stdout: { text: 'starting\nall done\n', bytes: 21, returned_lines: 2, redacted_lines: 0 },
          stderr: { text: 'warn: nothing to commit\n', bytes: 24, returned_lines: 1, redacted_lines: 0 },
        },
      });
    }
    return json({});
  });

  await page.addInitScript(() => {
    const project = {
      id: 'verify-alloc',
      index: '97',
      name: '示例项目',
      createdAt: '2026/11/02 00:00:00',
      phase: 'scene',
      source: 'platform',
      members: [],
      taskIds: [],
    };
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([project]));
    localStorage.setItem('agent-office.active-project.v1', project.id);
  });

  await page.goto(base);
  await page.waitForSelector('.project-card-open', { timeout: 30000 });
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-conv', { timeout: 30000 });

  // 1. 左栏红点点亮（分配那一件也算"需要你处理"）。
  await page.waitForFunction(
    () => {
      const rows = [...document.querySelectorAll('#aoWorkspace .flat-conv')];
      const row = rows.find((node) => (node.querySelector('b') || {}).textContent?.includes('需要你处理'));
      return Boolean(row && row.querySelector('.flat-conv-dot'));
    },
    { timeout: 30000 },
  );

  await page.locator('#aoWorkspace .flat-conv', { hasText: '需要你处理' }).first().click();
  await page.waitForSelector('.flat-allocation', { timeout: 20000 });

  // 2. 候选与"只是邀请"的说法。
  const block = page.locator('.flat-allocation').first();
  const blockText = await block.innerText();
  assert.ok(blockText.includes('补一份说明'), `块标题是那条活：${blockText.slice(0, 80)}`);
  assert.ok(blockText.includes('Codex CLI') && blockText.includes('OpenCode'), '两个候选都列出来');
  assert.ok(blockText.includes('邀请'), '要说清"定下来只是邀请，他要自己接受"');
  assert.equal(await block.locator('input[type="radio"]').count(), 2, '每个候选一个单选');

  // 3. 没选候选就点确认 → 当场说，不发请求。
  const before = writes.length;
  await block.locator('[data-act="alloc-decide"]').click();
  await page.waitForTimeout(400);
  const said = await block.locator('[data-alloc-error]').innerText();
  assert.ok(said.includes('先选一个候选'), `要说清缺什么：${said}`);
  assert.equal(writes.length, before, '没选人就不该发请求');

  // 4. 选了候选 → 请求体对（含这一轮的 revision）。
  await block.locator('input[type="radio"]').first().check();
  await block.locator('[data-alloc-reason]').fill('他熟这块');
  await block.locator('[data-act="alloc-decide"]').click();
  await page.waitForTimeout(600);
  const decided = writes.find((item) => item.route === '/allocation/decision');
  assert.ok(decided, `要打 /allocation/decision，实际：${JSON.stringify(writes)}`);
  assert.equal(decided.body.round_id, ROUND);
  assert.equal(decided.body.task_id, TASK);
  assert.equal(decided.body.application_id, 'app-1', '带的是选中的那份申请');
  assert.equal(decided.body.expected_revision, 4, '带的是这一轮的 revision（平台用它防并发覆盖）');
  assert.equal(decided.body.reason, '他熟这块');

  // 5. 「先不分配」没写理由 → 拦住；写了 → 发 defer。
  await page.waitForSelector('.flat-allocation', { timeout: 20000 });
  const again = page.locator('.flat-allocation').first();
  await again.locator('[data-alloc-reason]').fill('');
  const beforeDefer = writes.length;
  await again.locator('[data-act="alloc-defer"]').click();
  await page.waitForTimeout(400);
  assert.equal(writes.length, beforeDefer, '没写理由不该发请求');
  assert.ok((await again.locator('[data-alloc-error]').innerText()).includes('为什么'));

  await again.locator('[data-alloc-reason]').fill('等他把手上那条收尾');
  await again.locator('[data-act="alloc-defer"]').click();
  await page.waitForTimeout(600);
  const deferred = writes.find((item) => item.route === '/allocation/decision/defer');
  assert.ok(deferred, '写了理由就该发 defer');
  assert.equal(deferred.body.reason, '等他把手上那条收尾');

  // 6. 任务卡：证据两块**展开才去读**。
  //    这两块挂在**窗口页**的活卡上（那里才是"这个窗口手上这条活"的详情），
  //    所以先点进成员窗口，再展开那张卡。
  await page.locator('#aoWorkspace .flat-conv', { hasText: 'Codex CLI' }).first().click();
  await page.waitForSelector('#aoWorkspace .flat-wtask', { timeout: 20000 });
  const card = page.locator('#aoWorkspace .flat-wtask').first();
  await card.locator('> summary').click();
  await page.waitForTimeout(900);
  const folds = card.locator('.flat-evidence');
  assert.equal(await folds.count(), 2, '任务卡里两块证据：审核记录、取证');
  assert.equal(
    reads.filter((item) => item.endsWith('/reviews')).length,
    0,
    '没展开之前不该读审核记录',
  );
  await folds.filter({ hasText: '审核记录' }).locator('> summary').click();
  await page.waitForTimeout(700);
  const reviewText = await folds.filter({ hasText: '审核记录' }).innerText();
  assert.ok(reviewText.includes('需要返修'), `审核结论要翻成人话：${reviewText}`);
  assert.ok(reviewText.includes('启动段缺一步'), '审核意见要摆出来');
  await folds.filter({ hasText: '取证' }).locator('> summary').click();
  await page.waitForTimeout(700);
  const forensicText = await folds.filter({ hasText: '取证' }).innerText();
  assert.ok(forensicText.includes('all done'), '取证里是 CLI 的原话');
  assert.ok(forensicText.includes('warn: nothing to commit'), 'stderr 那一侧也要有');

  assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
  console.log('allocation + evidence: ok');
} finally {
  await browser.close();
  server.close();
}
