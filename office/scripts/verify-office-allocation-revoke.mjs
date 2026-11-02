// 「撤销一份还没被接走的分配」：界面上看得见、点得着、点错也不糊弄人。
//
// 断言：
//   1. 详情页里写着这条活定给了谁，以及**还没开工**；
//   2. 没写理由就点 → 按钮不发请求，把原因回灌在块里；
//   3. 写理由后第一次点只是"确认撤销"（不发请求），第二次点才发；
//   4. 发出去的是 `POST /allocation/revoke`，带 task_id 与理由；
//   5. 平台拒绝时把原话带出来，按钮退回未确认状态（不让人以为撤成了）；
//   6. 已经开工的分配：**没有按钮**，并说明此时该用取消/释放。
//
// 只读脚本：`/api/**` 全部拦截，不往正式平台写任何东西。
import { chromium } from 'playwright';
import { requireOffice } from './lib/office-url.mjs';
import assert from 'node:assert/strict';

const BASE = await requireOffice('verify-office-allocation-revoke.mjs', process.env.OFFICE_URL);
const TASK = 'T-REVOKE-1';

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  const revokes = [];
  let assignment = {
    allocation_id: 'al-1',
    task_id: TASK,
    harness_id: 'qoder-member-1',
    state: 'accepted',
    revision: 2,
    accepted_at: '2026-11-02T10:00:00',
    claimed_at: null,
    revoked_at: null,
  };
  let revocable = true;
  let refuse = null;

  page.on('pageerror', (error) => errors.push(error.message));

  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    const json = (body, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/formal/status') {
      return json({
        configured: true,
        connected: true,
        project_id: 'p',
        group_id: 'g',
        operator: 'office-operator',
        admin: { configured: true, identity: 'codex-window-admin', error: null },
        project_members: [],
        harnesses: [
          { harness_id: 'codex-window-admin', name: 'Codex · 管理员', status: 'online', capabilities: ['coding'] },
          { harness_id: 'qoder-member-1', name: 'Qoder 成员1', status: 'online', capabilities: ['coding'] },
        ],
        base_adapters: [],
      });
    }
    if (url.pathname === '/api/formal/tasks') {
      return json({
        tasks: [
          {
            task_id: TASK,
            title: '写一份变更说明',
            // 归属：办公室按 `project_label` / `taskIds` / 标题前缀判断"这条活属不属于
            // 这个项目"。少了它，任务表是空的 —— 那不是页面坏了，是数据没落在范围内。
            project_label: '撤销回归',
            status: 'ready',
            owner: 'qoder-member-1',
            updated_at: '2026-11-02T10:05:00',
          },
        ],
      });
    }
    if (url.pathname.startsWith('/api/formal/tasks/') && url.pathname.endsWith('/output')) {
      return json({ output: null });
    }
    if (url.pathname === `/api/formal/tasks/${TASK}`) {
      return json({
        task: { task_id: TASK, title: '写一份变更说明', status: 'ready', spec: {} },
        diff: { files: [] },
        events: { events: [] },
      });
    }
    if (url.pathname === '/api/formal/allocation/assignment') {
      return json({ task_id: TASK, assignment, round_id: 'alloc-1', revocable });
    }
    if (url.pathname === '/api/formal/allocation/revoke' && route.request().method() === 'POST') {
      revokes.push(route.request().postDataJSON());
      if (refuse) return json({ error: refuse }, 409);
      // 撤销成功：之后的读面必须反映"已经撤了"，否则界面会一直挂着按钮。
      assignment = { ...assignment, state: 'revoked', revoked_at: '2026-11-02T10:10:00' };
      revocable = false;
      return json({ allocation_id: 'al-1', state: 'revoked' });
    }
    if (url.pathname === '/api/formal/allocation/rounds') return json({ rounds: [] });
    if (url.pathname === '/api/formal/availability') return json({ availability: [] });
    if (url.pathname === '/api/formal/messages') return json({ messages: [] });
    if (url.pathname === '/api/formal/events') return json({ events: [] });
    return json({});
  });

  // 进一个平台项目（办公室这一页才有任务可点）
  await page.addInitScript(() => {
    const project = {
      id: 'project-revoke',
      index: '41',
      name: '撤销回归',
      createdAt: '2026/11/02 00:00:00',
      phase: 'scene',
      source: 'platform',
      members: [],
      taskIds: ['T-REVOKE-1'],
    };
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([project]));
    localStorage.setItem('agent-office.active-project.v1', project.id);
  });

  await page.goto(BASE);
  await page.waitForSelector('.project-card-open', { timeout: 20000 });
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });

  // 打开这条活：办公室是"列表 → 详情"两页，看完详情要先退回列表，否则 `.flat-task`
  // 根本不在 DOM 里（第一次踩在这里：以为页面没渲染任务，其实是还停在详情页）。
  const openTask = async () => {
    const back = page.locator('#aoWorkspace [data-act="back-tasks"]');
    if (await back.count()) await back.first().click();
    await page.locator('#aoWorkspace .flat-task').first().click();
    await page.locator('#aoWorkspace .flat-assignment').first().waitFor({ state: 'visible' });
  };

  await openTask();

  const block = page.locator('#aoWorkspace .flat-assignment');
  await block.waitFor({ state: 'visible', timeout: 20000 });
  const text = await block.innerText();
  assert.match(text, /这条活定给了谁/, '块要说清它是什么');
  assert.match(text, /Qoder 成员1/, '写出定给了谁（用人话，不写 harness id）');
  assert.match(text, /还没开工/, '写出它还没被接走');
  assert.match(text, /撤销/, '有撤销入口');

  // 可选：留一张图给人看这块长什么样（`OFFICE_SHOT=<路径>` 时才拍，平时不产生文件）。
  if (process.env.OFFICE_SHOT) {
    await page.screenshot({ path: process.env.OFFICE_SHOT, fullPage: false });
  }

  // 2. 没写理由：不发请求，把原因说在块里
  const button = block.locator('[data-act="alloc-revoke"]');
  await button.click();
  await page.waitForTimeout(200);
  assert.equal(revokes.length, 0, '没写理由不该发请求');
  assert.match(await block.locator('[data-assignment-error]').innerText(), /理由/, '要说清缺理由');

  // 3/4. 写了理由：第一次点只是确认，第二次才发
  await block.locator('[data-assignment-reason]').fill('定错人了，改给另一个窗口');
  await button.click();
  await page.waitForTimeout(200);
  assert.equal(revokes.length, 0, '第一次点只是确认，不该发请求');
  assert.match(await button.innerText(), /确认撤销/, '按钮要变成确认');

  await button.click();
  await page.waitForTimeout(600);
  assert.equal(revokes.length, 1, '第二次点才真的撤销');
  assert.equal(revokes[0].task_id, TASK);
  assert.equal(revokes[0].reason, '定错人了，改给另一个窗口');

  // 撤完这块只剩"已撤销"，按钮没了
  await page.waitForFunction(
    () => !document.querySelector('#aoWorkspace .flat-assignment [data-act="alloc-revoke"]'),
    undefined,
    { timeout: 10000 },
  );

  // 5. 平台拒绝：原话带出来，按钮退回未确认
  assignment = {
    allocation_id: 'al-2',
    task_id: TASK,
    harness_id: 'qoder-member-1',
    state: 'accepted',
    revision: 3,
    claimed_at: null,
  };
  revocable = true;
  refuse = 'assignment al-2 was already claimed by claim-9; revoke only covers unclaimed assignments';
  await openTask();
  const again = block.locator('[data-act="alloc-revoke"]');
  await block.locator('[data-assignment-reason]').fill('再试一次');
  await again.click();
  await again.click();
  await page.waitForTimeout(600);
  const note = await block.locator('[data-assignment-error]').innerText();
  assert.match(note, /没能撤销/, '要说没成');
  assert.match(note, /already claimed/, '平台原话要带出来，不要替它翻译掉');
  assert.match(await again.innerText(), /^撤销这份授权$/, '按钮退回未确认');

  // 6. 已经开工的不给按钮
  refuse = null;
  assignment = {
    allocation_id: 'al-3',
    task_id: TASK,
    harness_id: 'qoder-member-1',
    state: 'accepted',
    revision: 4,
    claimed_at: '2026-11-02T10:20:00',
  };
  revocable = false;
  await openTask();
  const claimedText = await block.innerText();
  assert.match(claimedText, /已开工/, '要说清已经开工');
  assert.match(claimedText, /取消或释放/, '告诉人这时候该用哪条路');
  assert.equal(await block.locator('[data-act="alloc-revoke"]').count(), 0, '已开工的不给撤销按钮');

  assert.deepEqual(errors, [], '页面不该报错');
  console.log(JSON.stringify({ checks: 17, result: 'PASS' }));
} finally {
  await browser.close();
}
