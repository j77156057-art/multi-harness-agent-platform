// 「没定档的模型，点一下就能用」：对**真平台**跑一遍，不拦接口。
//
// 为什么真跑：这条链有两段真实副作用 —— 先给模型定档（写进平台的额度池声明），
// 再把这个窗口的选择存到平台。任一段失败都会让人以为"换了模型"，而实际还在跑
// 窗口自带的那条。脚本跑完会把窗口的选择**恢复原样**，不留改动。
//
// 定档的取舍：平台目前没有"撤档"的入口，一次点击会留下一条声明。所以脚本**只挑
// CLI 自己命名为 `-free` 的模型** —— 万一留下痕迹，那条也是真的免费；它不会把
// 一个不知道价钱的模型写成"免费"（那会让平台以为不花钱而直接花掉）。
//
// 断言：
//   1. 名册里没定档的模型（`is-undeclared`）确实列在界面上；
//   2. 点「按免费用它」之后：这个窗口的当前使用变成「额度池：<那个模型>」；
//   3. 它从「没定档」变成可选的单选项（定档真的落地了）；
//   4. 恢复成「窗口自带模型」也成功（证明前一步不是碰巧）。
//
// 用法：
//   $env:OFFICE_URL='http://127.0.0.1:5173'; node scripts/verify-office-model-declare.mjs
//   可选：OFFICE_DECLARE_HARNESS=<窗口 id>、OFFICE_DECLARE_SHOTS=<截图目录>
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const base = (process.env.OFFICE_URL || 'http://127.0.0.1:5173').replace(/\/+$/, '');
const harness = process.env.OFFICE_DECLARE_HARNESS || 'codex';
const shotDir = process.env.OFFICE_DECLARE_SHOTS || path.join(process.env.TEMP || '.', 'office-declare');
fs.mkdirSync(shotDir, {recursive: true});

const browser = await chromium.launch();
const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
const errors = [];
page.on('pageerror', (error) => errors.push(String(error)));
const posts = [];
page.on('request', (request) => {
  if (request.method() !== 'POST') return;
  const url = new URL(request.url());
  if (!url.pathname.startsWith('/api/formal/')) return;
  let body = null;
  try {
    body = JSON.parse(request.postData() || 'null');
  } catch {
    body = null;
  }
  posts.push({name: url.pathname.replace('/api/formal', ''), body});
});

/** 一次读全模型块（页面每 15 秒重画，读要原子）。 */
const modelState = () =>
  page.evaluate(() => {
    const pane = document.querySelector('.flat-model');
    if (!pane) return null;
    const current = pane.querySelector('.flat-note');
    const undeclared = [...pane.querySelectorAll('.flat-model-row.is-undeclared')].map((node) => ({
      name: (node.querySelector('span') || {}).textContent || '',
      buttons: [...node.querySelectorAll('[data-act="use-undeclared"]')].map((button) => ({
        tier: button.dataset.tier,
        cli: button.dataset.cli,
        model: button.dataset.model,
        text: button.textContent.trim(),
      })),
    }));
    return {
      current: current ? current.textContent.trim() : '',
      undeclared,
      radioIds: [...pane.querySelectorAll('input[name="flatModelMode"][data-model-id]')].map(
        (input) => input.dataset.modelId,
      ),
      poolSummary: (pane.querySelector('.flat-model-list > summary') || {}).textContent || '',
    };
  });

const waitFor = async (predicate, what, timeoutMs = 60000) => {
  const started = Date.now();
  for (;;) {
    const state = await modelState();
    if (state && predicate(state)) return state;
    if (Date.now() - started > timeoutMs) throw new Error(`等不到：${what}`);
    await page.waitForTimeout(400);
  }
};

const shot = async (name) => {
  const file = path.join(shotDir, `${name}.png`);
  await page.screenshot({path: file, fullPage: false});
  return file;
};

try {
  await page.goto(base, {waitUntil: 'domcontentloaded'});

  // 进有这个窗口的那个项目（不按名字猜）。
  const rowSelector = `#aoWorkspace .flat-conv[data-harness="${harness}"]`;
  const openers = page.locator('.project-platform-open');
  await openers.first().waitFor({timeout: 30000});
  const total = await openers.count();
  let entered = false;
  for (let index = 0; index < total; index += 1) {
    await page.locator('.project-platform-open').nth(index).click();
    try {
      await page.waitForSelector(rowSelector, {timeout: 6000});
      entered = true;
      break;
    } catch {
      await page.goto(base, {waitUntil: 'domcontentloaded'});
      await page.waitForSelector('.project-platform-open', {timeout: 30000});
    }
  }
  assert.ok(entered, `没有哪个项目里有这个窗口（${harness}）`);
  await page.locator(rowSelector).click();
  await page.waitForSelector('.flat-model', {timeout: 30000});

  // 展开额度池模型清单（长清单默认收起）。必须**点**开：折叠状态记在界面里，
  // 直接改 DOM 的 open 会在下一次重画（15 秒）时被抹掉；而每点一次都会重画，
  // 所以这里收敛着来：点开所有收起的组 → 看目标是不是可见 → 再点一次。
  const openAllGroups = () =>
    page.evaluate(() => {
      const summaries = [
        ...document.querySelectorAll('.flat-model-list > summary, .flat-model-owner > summary'),
      ];
      for (const summary of summaries) {
        if (summary.parentElement && !summary.parentElement.open) summary.click();
      }
    });

  const revealGroup = async (selector) => {
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const visible = await page
        .locator(selector)
        .first()
        .isVisible()
        .catch(() => false);
      if (visible) return true;
      await openAllGroups();
      await page.waitForTimeout(400);
    }
    return false;
  };


  const before = await waitFor((state) => state.undeclared.length > 0, '界面上有没定档的模型');
  // 只挑 CLI 自己说是免费的（名字以 -free 结尾），见文件头。
  const target = before.undeclared.find(
    (row) => row.buttons.some((button) => button.tier === 'free') && /-free$/i.test(String(row.name)),
  );
  if (!target) {
    const printed = await shot('1-list');
    console.log(JSON.stringify({
      harness,
      declared: null,
      note: '这个池子里没有「未定档、而且名字带 -free」的模型；只验了列表与分组，没有真去定档。',
      undeclared: before.undeclared.length,
      poolSummary: before.poolSummary,
      shots: [printed],
      errors,
    }, null, 2));
    assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
    process.exit(0);
  }
  const button = target.buttons.find((entry) => entry.tier === 'free');
  // 模型 id 是 `<CLI 名>/<短名>`：CLI 名来自那一列的 data-cli（`opencode-cli`），
  // 不是模型名自己的前缀（`opencode`）。
  const cli = String(button.cli || '');
  const modelId = `${cli}/${String(button.model).split('/').pop()}`;
  const targetSelector = `[data-act="use-undeclared"][data-tier="free"][data-model="${button.model}"]`;
  assert.ok(await revealGroup(targetSelector), `「按免费用它」要能点到（${button.model}）`);
  const firstShot = await shot('1-before');

  // 点它：定档 + 选中 + 保存，一次做完。
  await page.locator(targetSelector).first().click();

  const after = await waitFor(
    (state) => state.current.includes('额度池') && state.current.includes(String(button.model).split('/').pop()),
    '当前使用变成额度池里的那个模型',
  );
  const declaration = posts.find((entry) => entry.name === '/pool/declaration');
  const selection = posts.find((entry) => entry.name === '/model-selection');
  assert.ok(declaration, `要打 /pool/declaration，实际：${JSON.stringify(posts.map((p) => p.name))}`);
  assert.equal(declaration.body.model, button.model);
  assert.equal(declaration.body.tier, 'free');
  assert.ok(selection, `要打 /model-selection，实际：${JSON.stringify(posts.map((p) => p.name))}`);
  assert.equal(selection.body.model_mode, 'pool');
  assert.equal(selection.body.model_id, modelId);
  assert.equal(selection.body.paid_consent, false, '免费的不该带收费点头');

  // 定档真的落地：它从「没定档」变成可选的单选项。
  const stillUndeclared = after.undeclared.some((row) => row.name === button.model);
  assert.equal(stillUndeclared, false, '定档之后不该还挂在「没定档」里');
  assert.ok(
    after.radioIds.includes(modelId),
    `定档之后要成为可选项 ${modelId}，实际：${JSON.stringify(after.radioIds)}`,
  );
  const secondShot = await shot('2-after-declare');

  // 恢复原样：切回窗口自带模型并保存。
  await page.locator('input[name="flatModelMode"][value="cli"]').first().check();
  await page.locator('[data-act="save-model-choice"]').first().click();
  await waitFor((state) => state.current.includes('窗口自带模型'), '恢复成窗口自带模型');
  const restored = await modelState();
  const thirdShot = await shot('3-restored');

  console.log(JSON.stringify({
    harness,
    declared: `${cli} · ${button.model} · ${button.tier}`,
    modelId,
    currentAfterUse: after.current,
    currentAfterRestore: restored.current,
    poolSummary: after.poolSummary,
    shots: [firstShot, secondShot, thirdShot],
    errors,
  }, null, 2));
  assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
} finally {
  await browser.close();
}
