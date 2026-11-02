// 「默认通过，只在不同意时打回」：approved 的任务详情页给人一次否决权。
//
// 断言：
//   1. approved 的任务上写着"默认放行"，并且**只有「打回返工」这一颗按钮**（没有"同意"）；
//   2. 没写原因就点 → 不发请求，把原因说在块里；
//   3. 写了原因：第一次点只是确认，第二次才发；
//   4. 发出去的是 `POST /human/reopen_for_changes`，带 task_id 与理由；
//   5. 打回之后任务不再 approved，那块就消失（不留在界面上当摆设）；
//   6. 非 approved 的任务根本没有这一块。
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
  const sends = [];
  let status = 'approved';

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
      });
    }
    if (url.pathname === '/api/formal/allocation/assignment') {
      return json({ task_id: TASK, assignment: null, round_id: null, revocable: false });
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

  await page.goto(BASE);
  await page.waitForSelector('.project-card-open', { timeout: 20000 });
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });
  await page.locator('#aoWorkspace .flat-task').first().click();

  const block = page.locator('#aoWorkspace .flat-approved');
  await block.waitFor({ state: 'visible', timeout: 20000 });
  const text = await block.innerText();
  assert.match(text, /默认放行/, '要说清默认通过，不用再点同意');
  assert.match(text, /打回/, '要有打回的入口');
  // 只有一颗按钮：默认通过，所以没有"同意"按钮可点。
  const buttons = await block.locator('button').count();
  assert.equal(buttons, 1, `这块只该有一颗按钮，实际 ${buttons} 颗`);
  assert.doesNotMatch(text, /点击?同意|确认通过/, '默认通过不该还要人点一次同意');

  if (process.env.OFFICE_SHOT) {
    await page.screenshot({ path: process.env.OFFICE_SHOT, fullPage: false });
  }

  const button = block.locator('[data-act="return-for-changes"]');
  await button.click();
  await page.waitForTimeout(200);
  assert.equal(sends.length, 0, '没写原因不该发请求');
  assert.match(await block.locator('[data-approved-error]').innerText(), /原因/, '要说清缺原因');

  await block.locator('[data-approved-reason]').fill('第 3 条验收标准没对上');
  await button.click();
  await page.waitForTimeout(200);
  assert.equal(sends.length, 0, '第一次点只是确认');
  assert.match(await button.innerText(), /确认打回/, '按钮要变成确认');

  await button.click();
  await page.waitForTimeout(600);
  assert.equal(sends.length, 1, '第二次点才真的打回');
  assert.equal(sends[0].task_id, TASK);
  assert.equal(sends[0].reason, '第 3 条验收标准没对上');

  // 打回之后不再是 approved，这块应当消失
  await page.waitForFunction(
    () => !document.querySelector('#aoWorkspace .flat-approved'),
    undefined,
    { timeout: 10000 },
  );

  assert.deepEqual(errors, [], '页面不该报错');
  console.log(JSON.stringify({ checks: 12, result: 'PASS' }));
} finally {
  await browser.close();
}
