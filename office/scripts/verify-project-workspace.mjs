import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { requireOffice } from './lib/office-url.mjs';

// 大厅与项目卡：**建一个项目、进得去、回得来、刷新还在**。
//
// 这条脚本 2026-11-02 重写过一次，原因是它已经和界面对不上了，而且长期没人发现：
// 它属于「没起界面就跳过」的那一批（`npm run verify` 里 24 个），所以红着也没人看见。
//
// 三处旧假设都去掉了：
// 1. **大厅不必是空的**。仓库里声明的转接项目（`relay/project.json`）会作为一张卡出现，
//    换台机器 clone 下来就有一张；平台上已有的项目另有一块。原来断言 `[data-empty]` 可见，
//    在有卡片的机器上必红。现在只断言"空态与条数一致"。
// 2. **项目卡不按序号找**。原来写死 `[data-project-id="project-001"]`，大厅里已经有卡时
//    序号会顺延。现在按项目名找自己刚建的那张。
// 3. **没有"手机开机"这一步了**。`#phoneDialog` / `#returnProjectsBtn` / `data-project-phase=phone`
//    已经从界面里删掉：建完项目直接进开工向导，回大厅走顶栏的 `#aoLobbyBtn`。
//
// 项目源固定用**演示沙盘**（`mock`）：这条脚本验的是大厅这套壳，不该每跑一次就在正式平台上
// 留一个 E2E 项目。平台那一路由 `verify-office-project-scope.mjs` 覆盖。

const base = await requireOffice('verify-project-workspace.mjs');
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const projectName = `E2E-PROJECT-${Date.now()}`;
  const cards = page.locator('[data-history] .project-card');

  await page.goto(`${base}/`);
  await page.waitForSelector('#aoLobby:not([hidden])');
  assert.equal(await page.locator('#aoWorkspace').getAttribute('hidden'), '');

  const before = await cards.count();
  assert.equal(
    await page.locator('[data-empty]').isVisible(),
    before === 0,
    `空态文案只应在一张项目卡都没有时出现，实际有 ${before} 张卡`,
  );
  assert.equal(await page.locator('.project-create-dialog').isVisible(), false);

  // 建项目是一个真模态：必须关上时不可见、打开时可见，不能常驻成一个 display:flex 遮罩。
  await page.locator('[data-new-project]').click();
  await page.waitForSelector('.project-create-dialog[open]');
  assert.equal(await page.locator('.project-create-dialog').isVisible(), true);
  const memberValues = await page
    .locator('[data-project-members] input')
    .evaluateAll(nodes => nodes.map(node => node.value));
  // 不写死条数：这台机器注册了几个窗口是**环境**，不是界面契约（本地实测 14 个，写 9 就红）。
  // 真正要守的是"列出来的都是成员、不是管理员"，以及命名的那两个必须在。
  assert.ok(memberValues.length > 0, '成员列表不该是空的');
  for (const administrator of ['codex-window-admin', 'qoder-window-admin', 'office-admin']) {
    assert.equal(memberValues.includes(administrator), false, `管理员 ${administrator} 不该出现在成员里`);
  }
  assert.equal(memberValues.includes('claude-code'), true);
  assert.equal(memberValues.includes('qoder'), true);

  await page.locator('[data-project-source]').selectOption('mock');
  await page.locator('[data-project-name]').fill(projectName);
  await page.locator('[data-confirm-create]').click();

  // 建完**直接进开工配置**：创建框关掉、大厅让位、开工向导打开。
  await page.waitForSelector('#openingDialog[open]');
  assert.equal(await page.locator('.project-create-dialog').evaluate(node => node.open), false);
  assert.equal(await page.locator('#aoLobby').getAttribute('hidden'), '');
  assert.equal(await page.locator('#aoWorkspace').isVisible(), true);

  // 关掉向导应当只是关掉它，不留下一个半开的遮罩，也不把人踢回大厅。
  await page.locator('#openingCloseBtn').click();
  await page.waitForFunction(() => !document.querySelector('#openingDialog')?.open);
  assert.equal(await page.locator('#openingDialog').isVisible(), false);
  assert.equal(await page.locator('#aoWorkspace').isVisible(), true);

  // 回大厅：卡片多了一张，就是刚建的那个。
  await page.locator('#aoLobbyBtn').click();
  await page.waitForSelector('#aoLobby:not([hidden])');
  assert.equal(await page.locator('body').getAttribute('data-project-phase'), 'lobby');
  assert.equal(await cards.count(), before + 1);
  assert.equal(await page.locator('[data-empty]').isVisible(), false);
  const created = page.locator('[data-history] .project-card', { hasText: projectName });
  assert.equal(await created.count(), 1);

  // 再进来一次：不该重复建，也不该把创建框弹出来；进的是同一个项目。
  await created.locator('.project-card-open').click();
  await page.waitForSelector('#aoWorkspace:not([hidden])');
  assert.equal(await page.locator('.project-create-dialog').isVisible(), false);
  assert.equal(await page.locator('#openingDialog').evaluate(node => node.open), false);
  assert.equal(await cards.count(), before + 1);

  // 项目历史按源站持久化，不是只在内存里。
  await page.reload();
  await page.waitForSelector('#aoLobby:not([hidden])');
  assert.equal(await cards.count(), before + 1);
  assert.match(await created.textContent(), new RegExp(projectName));

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks: 21, result: 'PASS' }));
} finally {
  await browser.close();
}
