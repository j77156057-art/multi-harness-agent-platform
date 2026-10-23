// 办公室补回的三件事：任务书、成员动态表、成员求助；外加"管理员对话关不掉"的回归。
//
// 断言：
//   1. 「与管理员对话」对话框能用 × 关掉（它挂在 body 上，办公室的点击委托听不到，
//      之前因此关不掉）；
//   2. 右栏有「任务书」，打开后是方案定稿的正文；
//   3. 右栏有「成员」，打开动态表后：提交/返修/放弃/通过率/中位耗时都按平台事件算，
//      平台自己的执行者（sweeper 之类）不算成员；
//   4. 成员求助列的是**还没结论**的 help_requested，已经有 help_resolved 的不算。
//
// 只读脚本：API 全部拦截，不往正式平台写任何东西。
import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const BASE = process.env.OFFICE_URL || 'http://127.0.0.1:4174';
const ADMIN = 'codex-window-admin';

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));

  const events = [
    { seq: 1, event_name: 'claim', aggregate_type: 'task', aggregate_id: 'FD-1', actor_type: 'harness', actor_id: 'claude-code', payload: {}, created_at: '2026-10-22T10:00:00' },
    { seq: 2, event_name: 'submit_result', aggregate_type: 'task', aggregate_id: 'FD-1', actor_type: 'harness', actor_id: 'claude-code', payload: {}, created_at: '2026-10-22T10:02:00' },
    { seq: 3, event_name: 'request_changes', aggregate_type: 'task', aggregate_id: 'FD-1', actor_type: 'harness', actor_id: ADMIN, payload: {}, created_at: '2026-10-22T10:03:00' },
    { seq: 4, event_name: 'claim', aggregate_type: 'task', aggregate_id: 'FD-1', actor_type: 'harness', actor_id: 'claude-code', payload: {}, created_at: '2026-10-22T10:10:00' },
    { seq: 5, event_name: 'submit_result', aggregate_type: 'task', aggregate_id: 'FD-1', actor_type: 'harness', actor_id: 'claude-code', payload: {}, created_at: '2026-10-22T10:12:00' },
    { seq: 5, event_name: 'claim', aggregate_type: 'task', aggregate_id: 'FD-2', actor_type: 'harness', actor_id: 'claude-code', payload: {}, created_at: '2026-10-22T10:15:00' },
    { seq: 6, event_name: 'abandon', aggregate_type: 'task', aggregate_id: 'FD-2', actor_type: 'harness', actor_id: 'claude-code', payload: {}, created_at: '2026-10-22T10:20:00' },
    { seq: 7, event_name: 'help_requested', aggregate_type: 'task', aggregate_id: 'FD-1', actor_type: 'harness', actor_id: 'claude-code', payload: { help_id: 'help-1', reason: '接口签名对不上，需要人确认' }, created_at: '2026-10-22T10:21:00' },
    { seq: 8, event_name: 'help_requested', aggregate_type: 'task', aggregate_id: 'FD-1', actor_type: 'harness', actor_id: 'opencode', payload: { help_id: 'help-2', reason: '依赖装不上' }, created_at: '2026-10-22T10:22:00' },
    { seq: 9, event_name: 'help_resolved', aggregate_type: 'task', aggregate_id: 'FD-1', actor_type: 'harness', actor_id: 'opencode', payload: { help_id: 'help-2' }, created_at: '2026-10-22T10:25:00' },
  ];

  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/formal/status') {
      return json({
        configured: true, connected: true, project_id: '112', group_id: 'g', operator: 'operator',
        admin: { configured: true, identity: ADMIN, error: null },
        project_members: [],
        harnesses: [
          { harness_id: ADMIN, name: 'Codex · 管理员', status: 'online', capabilities: ['coding'] },
          { harness_id: 'claude-code', name: 'Claude Code', status: 'online', capabilities: ['coding'] },
          { harness_id: 'opencode', name: 'OpenCode', status: 'online', capabilities: ['coding'] },
        ],
        base_adapters: [],
      });
    }
    if (url.pathname === '/api/formal/tasks') {
      return json({
        tasks: [
          {
            task_id: 'FD-1', title: '回归 · 做一个东西', group_id: 'g', status: 'awaiting_review',
            owner: 'claude-code', attempt_no: 2, updated_at: '2026-10-22T10:12:00',
            acceptance: '成员完成后提交。', state_changed_seq: 9,
          },
          {
            task_id: 'FD-3', title: '回归 · 还没人接的那条', group_id: 'g', status: 'ready',
            owner: null, attempt_no: 0, updated_at: '2026-10-22T10:30:00',
            acceptance: '成员完成后提交。', state_changed_seq: 9,
          },
          {
            task_id: 'FD-2', title: '回归 · 被放弃的那条', group_id: 'g', status: 'abandoned',
            owner: null, attempt_no: 1, updated_at: '2026-10-22T10:20:00',
            acceptance: '成员完成后提交。', state_changed_seq: 9,
            last_failure: { reason: 'CLI 不可用' },
          },
        ],
      });
    }
    if (url.pathname === '/api/formal/events') return json({ events, next_seq: 9, has_more: false });
    if (url.pathname === '/api/formal/plan') {
      return json({
        draft: {
          plan_id: 'plan-1', status: 'draft',
          document: { plan_id: 'plan-1', title: '回归方案', markdown: '# 任务书正文\n\n- 一条任务\n- 验收：能跑通' },
        },
        plans: [],
      });
    }
    if (url.pathname === '/api/formal/messages') return json({ messages: [] });
    if (url.pathname === '/api/formal/availability') return json({ availability: [] });
    return json({});
  });

  await page.goto(BASE);
  await page.locator('[data-new-project]').click();
  await page.waitForSelector('.project-create-dialog[open]');
  await page.locator('[data-project-name]').fill('回归');
  await page.locator('[data-confirm-create]').click();
  await page.waitForSelector('#aoWorkspace .flat-task', { timeout: 20000 });
  await page.waitForTimeout(1500);

  // 1. 管理员对话：能开，也能用 × 关掉
  await page.locator('.flat-card-admin [data-act="open-admin-chat"]').click();
  const adminDialog = page.locator('#flatAdminChat');
  await adminDialog.waitFor({ state: 'visible' });
  await page.locator('#flatAdminChat [data-act="close-admin-chat"]').click();
  await page.waitForTimeout(400);
  assert.equal(await adminDialog.evaluate((d) => d.open), false, '管理员对话要用 × 关得掉');

  // 2. 任务书
  const briefCard = page.locator('.flat-card-brief');
  await briefCard.waitFor({ state: 'visible' });
  await briefCard.locator('[data-act="open-brief"]').click();
  const brief = page.locator('#flatBrief');
  await brief.waitFor({ state: 'visible' });
  const briefText = await brief.innerText();
  assert.match(briefText, /谁接的/, '任务书里每条任务都写着谁接的');
  assert.match(briefText, /做得怎么样/, '任务书里每条任务都写着做得怎么样');
  // 人名用**登记名**（"Claude Code"），不是窗口 id（"claude-code"）——
  // 断的是"接过的人看得见"这件事，不是他长什么样。
  assert.match(briefText, /Claude Code/, '接过的人在任务书里（写的是登记名）');
  assert.match(briefText, /打回返修/, '审核结论在任务书里');
  assert.match(briefText, /CLI 不可用/, '没完成的原因在任务书里');
  assert.match(briefText, /任务书正文/, '方案原文仍然在后面');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);

  // 3. 成员动态表
  const memberCard = page.locator('.flat-card-members');
  await memberCard.waitFor({ state: 'visible' });
  await memberCard.locator('[data-act="open-members"]').click();
  const members = page.locator('#flatMembers');
  await members.waitFor({ state: 'visible' });
  const table = await members.innerText();
  assert.match(table, /Claude Code/, '动态表里有成员');
  assert.match(table, /50%/, 'Claude Code 两次提交、一次返修、一次放弃 → 通过率 2/4');
  assert.match(table, /120s/, '中位耗时 = 提交时刻 - 领取时刻（两次都是 120s）');
  assert.doesNotMatch(table, /sweeper/, '平台自己的执行者不是成员，不进表');
  assert.doesNotMatch(table, /Codex · 管理员/, '管理员不是成员，不进表');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);

  // 3.5 任务行里也要看得到谁接过（即使现在没人拿着）
  const rows = await page.locator('#aoWorkspace .flat-task').allInnerTexts();
  assert.ok(rows.some((r) => r.includes('之前是')), '没人拿着的任务也写明之前是谁接的');

  // 3.8 没人接的活：请管理员派出去（办公室不代点，只发一条 @管理员 的群消息）
  const posted = [];
  // 重新打开任务书（前面已经关掉它去看成员表了）
  await page.locator('[data-act="open-brief"]').click();
  await brief.waitFor({ state: 'visible' });
  await page.route('**/api/formal/messages', (route) => {
    if (route.request().method() === 'POST') {
      posted.push(route.request().postDataJSON());
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message_id: 'm', seq: 1 }) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
  });
  await brief.locator('[data-act="ask-admin-dispatch"]').click();
  await page.waitForTimeout(1200);
  assert.equal(posted.length, 1, '只发一条消息');
  assert.deepEqual(posted[0].mentions, [ADMIN], '消息 @ 管理员');
  assert.ok(posted[0].refs.includes('dispatch-request'), '消息带 dispatch-request 标记');
  assert.match(posted[0].body, /FD-3/, '消息里列了没人接的任务');
  assert.doesNotMatch(posted[0].body, /FD-1/, '已经有人拿着的不在请求里');

  // 4. 成员求助：只列还没结论的
  const helpText = await memberCard.innerText();
  assert.match(helpText, /接口签名对不上/, '还没结论的求助在列');
  assert.doesNotMatch(helpText, /依赖装不上/, '已经有 help_resolved 的求助不算待处理');
  assert.match(helpText, /1 条求助待处理/, '待处理求助有计数');

  assert.deepEqual(errors, [], '页面报错');
  console.log('verify-office-brief-members: ok');
} finally {
  await browser.close();
}
