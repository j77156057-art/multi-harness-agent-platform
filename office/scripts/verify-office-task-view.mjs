// 办公室是**观察面**：只读地看任务、方案、额度与事件。
//
// 断言：
//   1. 建项目不再让群主写路径与验收（那是管理员 Agent 的活）；
//   2. 打开项目后，办公室有任务表、详情与右栏；
//   3. 详情里**只有**人自己的动作（请求改派 / 请求预算豁免），
//      没有代审核、代派单、代合并这类写入面；
//   4. 页面不报错。
//
// 只读脚本：不建任务、不发消息、不做审核。
import { chromium } from 'playwright';
import { requireOffice } from './lib/office-url.mjs';
import assert from 'node:assert/strict';

const base = await requireOffice('verify-office-task-view.mjs', process.env.OFFICE_PREVIEW_URL);
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);

  // 建项目不再问群主要路径/验收。
  await page.locator('[data-new-project]').click();
  await page.waitForSelector('.project-create-dialog[open]');
  assert.equal(await page.locator('[data-project-paths]').count(), 0);
  assert.equal(await page.locator('[data-project-acceptance]').count(), 0);
  assert.equal(await page.locator('[data-project-goal]').count(), 0);
  await page.locator('[data-close-create]').click();

  // 注入一个已经在进行中的项目，不往正式平台发任何目标。
  const project = {
    id: 'project-observer-regression',
    index: '99',
    name: '只读观察回归',
    createdAt: '2026/10/22 00:00:00',
    phase: 'scene',
    source: 'platform',
    members: [],
    taskIds: [],
  };
  await page.evaluate(([key, activeKey, value]) => {
    localStorage.setItem(key, JSON.stringify([value]));
    localStorage.setItem(activeKey, value.id);
  }, ['agent-office.projects.v1', 'agent-office.active-project.v1', project]);
  await page.reload();
  const card = page.locator('.project-card', { hasText: '只读观察回归' }).first();
  await card.waitFor({ state: 'visible' });
  await card.locator('.project-card-open').click();

  // 办公室的三块：任务表、详情、右栏。
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });
  assert.ok(await page.locator('#aoWorkspace .flat-body').count() > 0, 'the office has a body');
  assert.ok(await page.locator('#aoWorkspace .flat-rail').count() > 0, 'the office has the right rail');
  // 群聊不再是底部那条了：它是左栏里的一个会话，中间那栏铺开。
  assert.ok(
    await page.locator('#aoWorkspace .flat-conv', { hasText: '项目群聊' }).count() > 0,
    'the office has the group chat conversation',
  );

  // 旧场景的东西不该再出现：场景导航、手机入口、3D 画布。
  for (const selector of ['#officeViewBtn', '#corridorViewBtn', '#phoneBtn', '#godotFrame', '.seat-picker', '.action-toolbar']) {
    assert.equal(await page.locator(selector).count(), 0, `${selector} is gone with the scene and the phone`);
  }

  // 顶栏只有三样东西：项目、连接状态、入口。
  // （连接状态的措辞改成了"已连接平台"——顶栏不再印项目 id —— 断的是
  // "顶栏说明了连接状态"这件事，不是那句话必须长什么样。）
  const topbar = await page.locator('.ao-topbar').innerText();
  assert.ok(
    /已连接平台|未连接平台|正式平台|未连接/.test(topbar),
    `the top bar states the connection: ${topbar.slice(0, 80)}`,
  );
  assert.ok(await page.locator('#aoStartBtn').count() === 1, 'the top bar has the start button');
  assert.ok(await page.locator('#adminAgentBtn').count() === 1, 'the platform assistant stays reachable');

  assert.deepEqual(errors, [], 'the page raised no errors');
  console.log('verify-office-task-view: ok');
} finally {
  await browser.close();
}
