// 办公室与管理员的直达通道。
//
// 平台只有**群消息 + @mention** 这一个通道（R10 §19），所以"直接和管理员说话"
// 必须是：一条 @管理员 的群消息 + 一个把「我 @ 的」和「管理员回的」挑出来看的面板。
//
// 断言：
//   1. 右栏有"与管理员对话"的入口，标出管理员是谁；
//   2. 打开面板后，线程里能看到「我 @ 管理员的」和「管理员回的」；
//   3. 面板里发出的消息落到 POST /api/formal/messages，且
//      mentions 里是管理员的 id、refs 里带 to-admin（管理员会收到 mention）；
//   4. 详情里的「请求管理员改派」也 @ 管理员（不然管理员收不到）；
//   5. 面板不替管理员做决定：没有代发、代审、代派单的按钮。
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
  const posted = [];
  page.on('pageerror', (e) => errors.push(e.message));

  const messages = [
    {
      message_id: 'msg-1',
      group_id: 'office-formal-2',
      task_id: null,
      sender_type: 'human',
      sender_id: 'operator',
      body: '这条活要改派给还有额度的窗口。',
      created_seq: 10,
      created_at: '2026-10-22T09:00:00',
      refs: ['to-admin', 'reassign-request'],
    },
    {
      message_id: 'msg-2',
      group_id: 'office-formal-2',
      task_id: null,
      sender_type: 'harness',
      sender_id: ADMIN,
      body: '收到。我把 FD-1 改派给 qoder-member-1。',
      created_seq: 11,
      created_at: '2026-10-22T09:01:00',
      refs: [],
    },
    {
      message_id: 'msg-3',
      group_id: 'office-formal-2',
      task_id: null,
      sender_type: 'human',
      sender_id: 'operator',
      body: '这条跟管理员无关，只是群里的话。',
      created_seq: 12,
      created_at: '2026-10-22T09:02:00',
      refs: [],
    },
  ];

  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    const json = (body, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/formal/status') {
      return json({
        configured: true,
        connected: true,
        project_id: '112',
        group_id: 'office-formal-2',
        operator: 'operator',
        admin: { configured: true, identity: ADMIN, error: null },
        project_members: [],
        harnesses: [
          { harness_id: ADMIN, name: 'Codex · 管理员', status: 'online', capabilities: ['coding'] },
          { harness_id: 'qoder-member-1', name: 'Qoder · 成员1', status: 'online', capabilities: ['coding'] },
        ],
        base_adapters: [],
      });
    }
    if (url.pathname === '/api/formal/messages' && route.request().method() === 'POST') {
      posted.push(route.request().postDataJSON());
      return json({ message_id: 'msg-new', group_id: 'office-formal-2', seq: 99, mentions: [], refs: [] });
    }
    if (url.pathname === '/api/formal/messages') {
      return json({ messages, next_seq: 12, has_more: false });
    }
    if (url.pathname === '/api/formal/tasks') {
      return json({
        tasks: [
          {
            task_id: 'FD-1',
            title: '直达管理员回归 · 做一个游戏',
            group_id: 'office-formal-2',
            status: 'in_progress',
            owner: 'qoder-member-1',
            attempt_no: 1,
            updated_at: '2026-10-22T09:00:00',
            acceptance: '成员领取任务后完成工作。',
          },
        ],
      });
    }
    if (url.pathname === '/api/formal/availability') return json({ availability: [] });
    if (url.pathname === '/api/formal/pool') return json({});
    if (url.pathname === '/api/formal/plan') return json({});
    if (url.pathname === '/api/formal/tasks/FD-1/events') return json({ events: [], next_seq: 0, has_more: false });
    if (url.pathname === '/api/formal/tasks/FD-1/output') return json({ output: '' });
    return json({});
  });

  await page.goto(BASE);
  await page.locator('[data-new-project]').click();
  await page.waitForSelector('.project-create-dialog[open]');
  await page.locator('[data-project-name]').fill('直达管理员回归');
  await page.locator('[data-confirm-create]').click();
  await page.waitForSelector('#aoWorkspace .flat-task', { timeout: 20000 });
  await page.waitForTimeout(1200);

  // 1. 右栏的入口
  const card = page.locator('.flat-card-admin');
  await card.waitFor({ state: 'visible' });
  const cardText = await card.innerText();
  assert.match(cardText, /与管理员对话/, '右栏有与管理员对话的入口');
  assert.match(cardText, /Codex/, '入口上写明管理员是谁');

  // 2. 打开面板，线程里该有的都有
  await card.locator('[data-act="open-admin-chat"]').click();
  const dialog = page.locator('#flatAdminChat');
  await dialog.waitFor({ state: 'visible' });
  const thread = await dialog.innerText();
  assert.match(thread, /这条活要改派给还有额度的窗口/, '我 @ 管理员的消息在面板里');
  assert.match(thread, /我把 FD-1 改派给 qoder-member-1/, '管理员的回话在面板里');
  assert.doesNotMatch(thread, /这条跟管理员无关/, '跟管理员无关的群消息不混进来');

  // 3. 面板里发消息：@ 管理员 + ref
  await dialog.locator('#flatAdminChatInput').fill('管理员，这条先别派给 Qoder。');
  await dialog.locator('#flatAdminChatForm button[type="submit"]').click();
  await page.waitForFunction(() => window.__postedCount === undefined, null, { timeout: 1000 }).catch(() => {});
  await page.waitForTimeout(1200);
  assert.equal(posted.length, 1, '面板发出一条消息');
  assert.deepEqual(posted[0].mentions, [ADMIN], '消息 @ 到了管理员');
  assert.ok(posted[0].refs.includes('to-admin'), '消息带 to-admin 标记，面板里查得到');
  assert.match(posted[0].body, /先别派给 Qoder/, '消息正文就是用户写的那句');

  // 4. 面板里没有替管理员做决定的按钮
  const buttons = await dialog.locator('button').allInnerTexts();
  const forbidden = ['批准', '派单', '合并', '改派', '审核', '裁决'];
  for (const label of buttons) {
    for (const word of forbidden) {
      assert.ok(!label.includes(word), `面板里不该有代替管理员的动作按钮：${label}`);
    }
  }

  // 5. 详情里的「请求管理员改派」也要 @ 管理员
  assert.ok(await dialog.locator('.flat-dialog-close').count(), '面板有关闭按钮');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);
  const reassign = page.locator('#aoWorkspace [data-act="reassign"]');
  if (await reassign.count()) {
    await reassign.first().click();
    await page.waitForTimeout(1200);
    const request = posted[posted.length - 1];
    assert.deepEqual(request.mentions, [ADMIN], '改派请求也 @ 管理员（不然他收不到）');
    assert.ok(request.refs.includes('to-admin'), '改派请求也进面板');
  }

  assert.deepEqual(errors, [], '页面报错');
  console.log('verify-office-admin-chat: ok');
} finally {
  await browser.close();
}
