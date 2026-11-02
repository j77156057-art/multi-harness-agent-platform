// 「默认通过 + 留痕」：approved 的任务详情页给人一次点头（留痕）和一次否决（打回）。
//
// 断言：
//   1. 这块写着"默认放行，不需要你再点一次同意" —— 门槛没变高；
//   2. 两颗按钮各管一头：**我确认，可以合入**（只留痕、不改状态）/ **打回返工**（改状态）；
//   3. 确认：一次点击就发 `POST /human/confirm_release`，带 task_id 与备注；
//   4. 确认之后那块变成"已确认"，确认按钮消失（否则人会以为自己没点上、又点一遍）；
//   5. 打回：没写原因不发请求；第一次点只是确认，第二次才发；打回后那块整体消失；
//   6. 已经打过回的（非 approved）没有这一块。
//
// 只读脚本：`/api/**` 全部拦截，不往正式平台写任何东西。
import { chromium } from 'playwright';
import { requireOffice } from './lib/office-url.mjs';
import assert from 'node:assert/strict';

const BASE = await requireOffice('verify-office-approved-return.mjs', process.env.OFFICE_URL);
const TASK = 'T-APPROVED-1';

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  const confirms = [];
  const sends = [];
  let status = 'approved';
  let decisions = [];

  page.on('pageerror', (error) => errors.push(error.message));

  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    const json = (body, code = 200) =>
      route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
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
            status,
            owner: 'qoder-member-1',
            project_label: '打回回归',
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
        task: { task_id: TASK, title: '写一份变更说明', status, spec: {} },
        diff: { files: [] },
        events: { events: [] },
        decisions: { decisions },
      });
    }
    if (url.pathname === '/api/formal/allocation/assignment') {
      return json({ task_id: TASK, assignment: null, round_id: null, revocable: false });
    }
    if (url.pathname === '/api/formal/human/confirm_release') {
      const body = route.request().postDataJSON();
      confirms.push(body);
      decisions = [
        {
          decision_id: 'dec-confirm',
          actor: 'office-operator',
          answer: body.note || 'confirmed',
          scope: 'approved_release',
          decided_seq: 1,
        },
      ];
      return json({ task_id: TASK, decision_id: 'dec-confirm', already: false, status });
    }
    if (url.pathname === '/api/formal/human/reopen_for_changes') {
      sends.push(route.request().postDataJSON());
      status = 'changes_requested';
      return json({ task_id: TASK, status, event: 'reopen_for_changes' });
    }
    if (url.pathname === '/api/formal/availability') return json({ availability: [] });
    if (url.pathname === '/api/formal/messages') return json({ messages: [] });
    if (url.pathname === '/api/formal/events') return json({ events: [] });
    return json({});
  });

  await page.addInitScript(() => {
    const project = {
      id: 'project-approved',
      index: '42',
      name: '打回回归',
      createdAt: '2026/11/02 00:00:00',
      phase: 'scene',
      source: 'platform',
      members: [],
      taskIds: ['T-APPROVED-1'],
    };
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([project]));
    localStorage.setItem('agent-office.active-project.v1', project.id);
  });

  const openTask = async () => {
    const back = page.locator('#aoWorkspace [data-act="back-tasks"]');
    if (await back.count()) await back.first().click();
    await page.locator('#aoWorkspace .flat-task').first().click();
    await page.locator('#aoWorkspace .flat-approved').first().waitFor({ state: 'visible' });
  };

  await page.goto(BASE);
  await page.waitForSelector('.project-card-open', { timeout: 20000 });
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });
  await openTask();

  const block = page.locator('#aoWorkspace .flat-approved');
  const text = await block.innerText();
  assert.match(text, /默认放行/, '要说清默认通过');
  assert.match(text, /不需要你再点一次「同意」/, '门槛没变高：不点也能过');
  // 两颗按钮各管一头：确认（留痕，不改状态）/ 打回（改状态）
  assert.equal(await block.locator('[data-act="confirm-release"]').count(), 1, '有「我确认」');
  assert.equal(await block.locator('[data-act="return-for-changes"]').count(), 1, '有「打回返工」');

  if (process.env.OFFICE_SHOT) {
    await page.screenshot({ path: process.env.OFFICE_SHOT, fullPage: false });
  }

  // 3/4. 确认：一次点击就发，带备注；之后那块说"已确认"，按钮消失
  await block.locator('[data-approved-note]').fill('验收标准第 1~3 条我核过了');
  await block.locator('[data-act="confirm-release"]').click();
  await page.waitForTimeout(600);
  assert.equal(confirms.length, 1, '确认要发出去');
  assert.equal(confirms[0].task_id, TASK);
  assert.equal(confirms[0].note, '验收标准第 1~3 条我核过了');
  await page.waitForFunction(
    () => !document.querySelector('#aoWorkspace .flat-approved [data-act="confirm-release"]'),
    undefined,
    { timeout: 10000 },
  );
  assert.match(await block.innerText(), /已确认这一版可以合入/, '确认过之后要看得见痕迹');
  // 确认不改状态：任务还在 approved，打回仍然可用
  assert.equal(await block.locator('[data-act="return-for-changes"]').count(), 1, '确认之后仍然可以打回');

  // 5. 打回：没原因不发、两步确认、发完这块消失
  const back = block.locator('[data-act="return-for-changes"]');
  await back.click();
  await page.waitForTimeout(200);
  assert.equal(sends.length, 0, '没写原因不该发请求');
  assert.match(await block.locator('[data-approved-error]').innerText(), /原因/, '要说清缺原因');

  await block.locator('[data-approved-reason]').fill('第 3 条验收标准没对上');
  await back.click();
  await page.waitForTimeout(200);
  assert.equal(sends.length, 0, '第一次点只是确认');
  assert.match(await back.innerText(), /确认打回/, '按钮要变成确认');

  await back.click();
  await page.waitForTimeout(600);
  assert.equal(sends.length, 1, '第二次点才真的打回');
  assert.equal(sends[0].reason, '第 3 条验收标准没对上');

  // 6. 打回之后不再是 approved，那块整体消失
  await page.waitForFunction(
    () => !document.querySelector('#aoWorkspace .flat-approved'),
    undefined,
    { timeout: 10000 },
  );

  assert.deepEqual(errors, [], '页面不该报错');
  console.log(JSON.stringify({ checks: 16, result: 'PASS' }));
} finally {
  await browser.close();
}
