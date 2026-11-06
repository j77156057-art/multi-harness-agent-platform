// 项目创建申请的前端回归：全部 /api/formal/* 都由接口夹具回答，不连真实平台。
// 覆盖真实读取、人工门控、批准/拒绝、失败保留、空态与四档桌面布局。
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

const APPROVE_ID = `PCR-${'a'.repeat(32)}`;
const REJECT_ID = `PCR-${'b'.repeat(32)}`;
const APPROVED_ID = `PCR-${'c'.repeat(32)}`;
const REJECTED_ID = `PCR-${'d'.repeat(32)}`;
const CREATED_AT = '2026-11-03T01:02:03Z';

function requestRow(request_id, overrides = {}) {
  return {
    request_id,
    project_id: 'P-isolated-delivery',
    project_name: '隔离交付项目',
    group_id: 'G-isolated-delivery',
    group_name: '隔离交付主群',
    source_group_id: 'G-source',
    purpose: '隔离验证多窗口交付与人工授权链路',
    scope: '仅本项目任务、交付和验收记录；不得读取来源项目的凭据',
    expected_output: '一个可追踪的独立项目与主群，以及完整审批记录',
    requested_by_type: 'harness',
    requested_by_id: 'agent-window',
    status: 'pending',
    created_at: CREATED_AT,
    decided_by_type: null,
    decided_by_id: null,
    decided_at: null,
    decision_reason: null,
    ...overrides,
  };
}

function makeRows() {
  const rows = [
    requestRow(APPROVE_ID),
    requestRow(REJECT_ID, {
      project_id: 'P-budget-review',
      project_name: '预算复核项目',
      group_id: 'G-budget-review',
      group_name: '预算复核主群',
      purpose: '验证拒绝原因必须留痕',
    }),
    requestRow(APPROVED_ID, {
      project_id: 'P-approved',
      project_name: '已批准项目',
      group_id: 'G-approved',
      group_name: '已批准主群',
      status: 'approved',
      decision_reason: '历史批准：用途与范围明确',
      decided_by_type: 'human',
      decided_by_id: 'office-operator',
      decided_at: '2026-11-03T02:00:00Z',
    }),
    requestRow(REJECTED_ID, {
      project_id: 'P-rejected',
      project_name: '已拒绝项目',
      group_id: 'G-rejected',
      group_name: '已拒绝主群',
      status: 'rejected',
      decision_reason: '历史拒绝：当前预算不支持',
      decided_by_type: 'human',
      decided_by_id: 'office-operator',
      decided_at: '2026-11-03T02:01:00Z',
    }),
  ];
  // 足够长的真实夹具列表用来证明滚动发生在列表内部，长标识也不能撑破卡片。
  for (let index = 0; index < 10; index += 1) {
    rows.push(requestRow(`PCR-${index.toString(16).padStart(32, '0')}`, {
      project_id: `P-history-${index}-WITH_A_VERY_LONG_IDENTIFIER_THAT_MUST_WRAP`,
      project_name: `历史申请 ${index + 1}`,
      group_id: `G-history-${index}-WITH_A_VERY_LONG_IDENTIFIER_THAT_MUST_WRAP`,
      group_name: `历史主群 ${index + 1}`,
      status: index % 2 ? 'approved' : 'rejected',
      scope: `历史范围 ${index + 1}：SCOPE_WITHOUT_SPACES_${'X'.repeat(100)}`,
      decision_reason: index % 2 ? '历史批准说明' : '历史拒绝说明',
    }));
  }
  return rows;
}

let rows = makeRows();
let operatorType = 'human';
let failListReads = 0;
const failingActions = new Set();
const calls = [];
let listReads = 0;

const responses = {
  '/status': () => ({
    configured: true,
    connected: true,
    project_id: 'P-source',
    group_id: 'G-source',
    operator: 'office-operator',
    operator_type: operatorType,
    admin: { configured: true, identity: 'admin-window', error: null },
    project_members: ['agent-window'],
    harnesses: [],
    base_adapters: [],
  }),
  '/tasks': () => ({ tasks: [] }),
  '/events': () => ({ events: [], next_seq: 0, has_more: false }),
  '/messages': () => ({ messages: [] }),
  '/availability': () => ({ availability: [] }),
  '/pool': () => ({ upstreams: [], models: [], usage: {} }),
  '/plan': () => ({ plans: [], draft: null }),
  '/assistant/runs': () => ({ runs: [] }),
  '/user-keys': () => ({ keys: [], pending: null }),
  '/allocation/rounds': () => ({ rounds: [] }),
};

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/formal/**', async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname.replace('/api/formal', '');
    const raw = request.postData();
    calls.push({ path: pathname, method: request.method(), body: raw ? JSON.parse(raw) : null });
    const fulfill = (status, payload) => route.fulfill({
      status,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(payload),
    });

    if (pathname === '/project-creation-requests' && request.method() === 'GET') {
      listReads += 1;
      if (failListReads > 0) {
        failListReads -= 1;
        await fulfill(503, { error: 'project request list temporarily unavailable' });
        return;
      }
      await fulfill(200, { requests: rows });
      return;
    }
    const action = /^\/project-creation-requests\/([^/]+)\/(approve|reject)$/.exec(pathname);
    if (action && request.method() === 'POST') {
      if (failingActions.has(pathname)) {
        await fulfill(409, { error: 'project creation request changed; approval conflict' });
        return;
      }
      const row = rows.find(item => item.request_id === action[1]);
      if (!row) {
        await fulfill(404, { error: 'unknown request' });
        return;
      }
      const body = JSON.parse(raw || '{}');
      row.status = action[2] === 'approve' ? 'approved' : 'rejected';
      row.decision_reason = body.reason;
      row.decided_by_type = 'human';
      row.decided_by_id = 'office-operator';
      row.decided_at = '2026-11-03T03:00:00Z';
      await fulfill(200, { request: row });
      return;
    }
    await fulfill(200, responses[pathname] ? responses[pathname]() : {});
  });

  const project = {
    id: 'project-request-regression',
    index: '97',
    name: '项目申请回归',
    createdAt: '2026/11/03 00:00:00',
    phase: 'scene',
    source: 'platform',
    members: [],
    taskIds: [],
  };
  await page.addInitScript(value => {
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([value]));
    localStorage.setItem('agent-office.active-project.v1', value.id);
  }, project);
  await page.goto(base);
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });

  const openAlerts = async () => {
    if (!await page.locator('[data-project-requests]').count()) {
      await page.locator('#aoWorkspace .flat-convs .flat-conv', { hasText: '需要你处理' }).click();
    }
    await page.waitForSelector('[data-project-requests][aria-busy="false"]', { timeout: 20000 });
  };
  const row = id => page.locator(`[data-request-id="${id}"]`);
  const refreshList = async () => {
    await page.locator('[data-act="refresh-project-requests"]').click();
    await page.waitForSelector('[data-project-requests][aria-busy="false"]');
  };

  await openAlerts();
  await row(APPROVE_ID).waitFor({ state: 'visible' });
  assert.equal(await page.locator('.flat-project-request').count(), 14, '真实夹具的 14 条申请全部列出');
  const pendingText = await row(APPROVE_ID).innerText();
  for (const expected of [
    '待批准', 'agent-window', '隔离验证多窗口交付与人工授权链路',
    '仅本项目任务、交付和验收记录；不得读取来源项目的凭据',
    '一个可追踪的独立项目与主群，以及完整审批记录', 'decision_reason',
  ]) {
    assert.ok(pendingText.includes(expected), `待批行包含：${expected}`);
  }
  assert.equal(await row(APPROVE_ID).locator('time').getAttribute('datetime'), CREATED_AT, 'created_at 原值保留');
  assert.match(await row(APPROVED_ID).innerText(), /已批准[\s\S]*已创建项目 P-approved[\s\S]*已创建主群 G-approved/);
  assert.match(await row(REJECTED_ID).innerText(), /已拒绝[\s\S]*历史拒绝：当前预算不支持/);
  assert.match(await page.locator('.flat-convs .flat-conv', { hasText: '需要你处理' }).innerText(), /2 件事在等你/);
  assert.equal(await page.locator('.flat-rail-lead strong').innerText(), '2', '待批计数进入 decisions');
  assert.match(await page.locator('.flat-project-requests-summary').innerText(), /2 待批 · 6 已批准 · 6 已拒绝/);

  // human 批准：四个创建参数可修订，勾选前不能提交；动作失败保留待批旧行。
  await row(APPROVE_ID).locator('[data-act="open-approve-request"]').click();
  await page.waitForSelector('#flatProjectRequestDecision[open]');
  const dialog = page.locator('#flatProjectRequestDecision');
  for (const [field, expected] of Object.entries({
    project_id: 'P-isolated-delivery',
    project_name: '隔离交付项目',
    group_id: 'G-isolated-delivery',
    group_name: '隔离交付主群',
  })) {
    assert.equal(await dialog.locator(`[data-confirm-field="${field}"]`).inputValue(), expected, `批准回显 ${field}`);
  }
  assert.equal(await dialog.locator('[data-act="confirm-approve-request"]').isDisabled(), true, '未勾选确认时批准按钮禁用');
  await dialog.locator('[data-approve-reason]').fill('用途与范围明确，同意隔离交付');
  const approvePath = `/project-creation-requests/${APPROVE_ID}/approve`;
  failingActions.add(approvePath);
  await dialog.locator('[data-request-confirm]').check();
  assert.equal(await dialog.locator('[data-act="confirm-approve-request"]').isEnabled(), true, '勾选后才允许批准');
  await dialog.locator('[data-act="confirm-approve-request"]').click();
  await page.waitForFunction(() => document.querySelector('[data-project-request-dialog-error]')?.textContent.includes('批准失败'));
  assert.match(await row(APPROVE_ID).innerText(), /待批准/, '动作失败仍保留待批旧数据');
  assert.equal(await page.locator('[data-project-requests-action-error]').count(), 1, '主区也显示动作错误');
  failingActions.delete(approvePath);
  const readsBeforeApprove = listReads;
  await dialog.locator('[data-act="confirm-approve-request"]').click();
  await page.waitForSelector('#flatProjectRequestDecision:not([open])', { state: 'attached' });
  await row(APPROVE_ID).locator('.flat-project-request-created').waitFor({ state: 'visible' });
  assert.ok(listReads > readsBeforeApprove, '批准成功后重新拉取真实列表');
  assert.match(await row(APPROVE_ID).innerText(), /已批准[\s\S]*已创建项目 P-isolated-delivery[\s\S]*已创建主群 G-isolated-delivery/);
  assert.match(await row(APPROVE_ID).innerText(), /用途与范围明确，同意隔离交付/);
  const approveCall = calls.find(call => call.path === approvePath && call.method === 'POST');
  assert.deepEqual(approveCall.body, {
    reason: '用途与范围明确，同意隔离交付',
    project_id: 'P-isolated-delivery', project_name: '隔离交付项目',
    group_id: 'G-isolated-delivery', group_name: '隔离交付主群',
  }, '批准携带确认参数，decided_by 由网关决定');
  assert.equal(await row(APPROVE_ID).locator('[data-act="open-approve-request"]').count(), 0, '批准后动作消失');

  // human 拒绝：空白原因只在页面报错，不发请求；有效原因提交后重读为已拒绝。
  await row(REJECT_ID).locator('[data-act="open-reject-request"]').click();
  await page.waitForSelector('#flatProjectRequestDecision[open]');
  assert.equal(await dialog.locator('[data-project-request-confirm-wrap]').isVisible(), false, '拒绝不显示创建确认勾选');
  await dialog.locator('[data-reject-reason]').fill('   ');
  const rejectPath = `/project-creation-requests/${REJECT_ID}/reject`;
  const rejectCallsBeforeBlank = calls.filter(call => call.path === rejectPath).length;
  assert.equal(await dialog.locator('[data-act="confirm-reject-request"]').isDisabled(), true, '空白拒绝原因禁用提交');
  assert.equal(calls.filter(call => call.path === rejectPath).length, rejectCallsBeforeBlank, '空白拒绝原因没有发请求');
  await dialog.locator('[data-reject-reason]').fill('当前预算不支持另建项目');
  const readsBeforeReject = listReads;
  await dialog.locator('[data-act="confirm-reject-request"]').click();
  await page.waitForSelector('#flatProjectRequestDecision:not([open])', { state: 'attached' });
  await page.waitForFunction(id => document.querySelector(`[data-request-id="${id}"]`)?.classList.contains('is-rejected'), REJECT_ID);
  assert.ok(listReads > readsBeforeReject, '拒绝成功后重新拉取真实列表');
  assert.match(await row(REJECT_ID).innerText(), /已拒绝[\s\S]*当前预算不支持另建项目/);
  const rejectCall = calls.find(call => call.path === rejectPath && call.method === 'POST');
  assert.deepEqual(rejectCall.body, {
    reason: '当前预算不支持另建项目',
    project_id: 'P-budget-review', project_name: '预算复核项目',
    group_id: 'G-budget-review', group_name: '预算复核主群',
  });
  assert.equal(await page.locator('.flat-rail-lead strong').innerText(), '0', '两条待批处理完后 decisions 归零');

  // 非 human（含 harness）只读：即使有待批行也绝不渲染批准/拒绝按钮。
  rows = makeRows();
  operatorType = 'harness';
  await page.reload();
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });
  await openAlerts();
  await row(APPROVE_ID).waitFor({ state: 'visible' });
  assert.equal(await page.locator('[data-act="open-approve-request"]').count(), 0, 'harness 没有批准按钮');
  assert.equal(await page.locator('[data-act="open-reject-request"]').count(), 0, 'harness 没有拒绝按钮');

  // 列表读取失败：14 条旧数据一条不少；重试成功后才清错误。
  const beforeFailure = await page.locator('.flat-project-request').allInnerTexts();
  failListReads = 1;
  await refreshList();
  await page.locator('[data-project-requests-error]').waitFor({ state: 'visible' });
  assert.deepEqual(await page.locator('.flat-project-request').allInnerTexts(), beforeFailure, '读取失败完整保留旧 rows');
  assert.match(await page.locator('[data-project-requests-error]').innerText(), /保留上次成功读取的数据/);
  await page.locator('[data-act="retry-project-requests"]').click();
  await page.waitForSelector('[data-project-requests-error]', { state: 'detached' });
  assert.equal(await page.locator('.flat-project-request').count(), 14, '重试后仍显示真实列表');

  // 真空态只在成功读到空数组时出现，不拿它冒充加载或失败。
  rows = [];
  await refreshList();
  await page.locator('[data-project-requests-empty]').waitFor({ state: 'visible' });
  assert.equal(await page.locator('[data-project-requests-empty]').innerText(), '暂无项目创建申请。');
  assert.equal(await page.locator('.flat-project-request').count(), 0);
  assert.match(await page.locator('.flat-project-requests-summary').innerText(), /暂无申请/);

  // 四档 900px 高桌面：页面不横溢出、申请行不重叠、长列表只在内部滚动。
  rows = makeRows();
  operatorType = 'human';
  await refreshList();
  await row(APPROVE_ID).waitFor({ state: 'visible' });
  for (const width of [1600, 1440, 1280, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    await page.waitForTimeout(180);
    const report = await page.evaluate(() => {
      const section = document.querySelector('[data-project-requests]');
      const list = section.querySelector('.flat-project-request-list');
      const main = document.querySelector('.flat-body > .flat-main');
      const body = document.querySelector('.flat-body');
      const rail = document.querySelector('.flat-rail');
      const rect = element => {
        const value = element.getBoundingClientRect();
        return { left: value.left, right: value.right, top: value.top, bottom: value.bottom };
      };
      const rowsInList = [...list.querySelectorAll('.flat-project-request')];
      const boxes = rowsInList.map(rect);
      const overlaps = [];
      for (let first = 0; first < boxes.length; first += 1) {
        for (let second = first + 1; second < boxes.length; second += 1) {
          const a = boxes[first];
          const b = boxes[second];
          const overlapX = Math.min(a.right, b.right) - Math.max(a.left, b.left);
          const overlapY = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          if (overlapX > 2 && overlapY > 2) overlaps.push([first, second]);
        }
      }
      const a = rect(body);
      const b = rect(rail);
      const railOverlap = Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2
        && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2;
      const spills = [...section.querySelectorAll('*')].filter(element => {
        const style = getComputedStyle(element);
        return style.overflowX === 'visible' && element.scrollWidth > element.clientWidth + 2;
      }).map(element => element.className || element.tagName);
      const s = rect(section);
      const m = rect(main);
      const l = rect(list);
      return {
        pageWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        overlaps,
        railOverlap,
        spills,
        sectionInsideMain: s.left >= m.left - 1 && s.right <= m.right + 1,
        rowsInsideList: rowsInList.every(element => {
          const r = rect(element);
          return r.left >= l.left - 1 && r.right <= l.right + 1;
        }),
        localScroll: list.scrollHeight > list.clientHeight,
        listHorizontalOverflow: list.scrollWidth > list.clientWidth + 1,
      };
    });
    assert.ok(report.pageWidth <= report.viewportWidth + 1, `${width}×900：页面无横向溢出`);
    assert.deepEqual(report.overlaps, [], `${width}×900：申请行互不重叠`);
    assert.equal(report.railOverlap, false, `${width}×900：主区与右栏不重叠`);
    assert.deepEqual(report.spills, [], `${width}×900：申请内容不横向溢出`);
    assert.equal(report.sectionInsideMain, true, `${width}×900：申请区留在主栏内`);
    assert.equal(report.rowsInsideList, true, `${width}×900：申请行不越出局部列表`);
    assert.equal(report.localScroll, true, `${width}×900：长列表使用局部滚动`);
    assert.equal(report.listHorizontalOverflow, false, `${width}×900：局部列表无横向滚动`);
  }

  assert.deepEqual(errors, [], '页面没有未捕获异常');
  console.log('verify-office-project-requests: ok');
} finally {
  await browser.close();
  server.close();
}
