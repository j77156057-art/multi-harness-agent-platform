// 「改档 / 撤档」：对**真平台**跑一遍，不拦接口。
//
// 为什么需要这两个口子：档位是人说的一句话，而人会点错。没有它们时，一条错标成
// "免费"的收费额度会一直按免费花下去（平台以为不花钱，就真花了）。
//
// 这个脚本挑一条**内置免费**的模型（默认 opencode-cli/beam-free）：
//   1. 点「改档」→「按收费」：池子里它的档位真的变成收费；
//   2. 再点「改档」→「撤档」：声明被撤掉，它回到内置的免费档；
// 跑完池子与开始时一致（内置档位撤不没，所以这条链是无痕的）。
//
// 用法：
//   $env:OFFICE_URL='http://127.0.0.1:5173'; node scripts/verify-office-model-retier.mjs
//   可选：OFFICE_RETIER_HARNESS=<窗口 id>、OFFICE_RETIER_CLI=<CLI>、
//        OFFICE_RETIER_MODEL=<CLI 自己的模型名>、OFFICE_RETIER_SHOTS=<截图目录>
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const base = (process.env.OFFICE_URL || 'http://127.0.0.1:5173').replace(/\/+$/, '');
const harness = process.env.OFFICE_RETIER_HARNESS || 'codex';
const cli = process.env.OFFICE_RETIER_CLI || 'opencode-cli';
const cliModel = process.env.OFFICE_RETIER_MODEL || 'opencode/beam-free';
const modelId = `${cli}/${String(cliModel).split('/').pop()}`;
const shotDir = process.env.OFFICE_RETIER_SHOTS || path.join(process.env.TEMP || '.', 'office-retier');
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

/** 这一行现在是什么样（页面每 15 秒重画，读要原子）。 */
const rowState = () =>
  page.evaluate((id) => {
    const input = document.querySelector(`input[name="flatModelMode"][data-model-id="${id}"]`);
    if (!input) return null;
    const row = input.closest('.flat-model-row');
    return {
      text: row ? row.innerText.replace(/\s+/g, ' ').trim() : '',
      mark: row && row.querySelector('.flat-model-mark')
        ? row.querySelector('.flat-model-mark').textContent.trim()
        : '',
      buttons: row ? [...row.querySelectorAll('button[data-act]')].map((b) => b.dataset.act) : [],
    };
  }, modelId);

const waitFor = async (predicate, what, timeoutMs = 60000) => {
  const started = Date.now();
  for (;;) {
    const state = await rowState();
    if (state && predicate(state)) return state;
    if (Date.now() - started > timeoutMs) throw new Error(`等不到：${what}`);
    await page.waitForTimeout(400);
  }
};

// 等额度池清单真的到页面上：第一次读要 spawn 三个 CLI 的名册（约 10~20 秒），
// 在这之前那一块写着「正在读取…」，行还没渲染出来。
const waitForPool = async (timeoutMs = 120000) => {
  const started = Date.now();
  for (;;) {
    const head = await page
      .locator('.flat-model-list > summary')
      .first()
      .innerText()
      .catch(() => '');
    if (head && !head.includes('正在读取')) return head;
    if (Date.now() - started > timeoutMs) throw new Error(`额度池清单没到（${head}）`);
    await page.waitForTimeout(500);
  }
};

const reveal = async () => {
  for (let attempt = 0; attempt < 16; attempt += 1) {
    const visible = await page
      .locator(`input[name="flatModelMode"][data-model-id="${modelId}"]`)
      .first()
      .isVisible()
      .catch(() => false);
    if (visible) return true;
    // 一次只点开**一个**收起的组：点一下就会整页重画，连着点后面几个会落在
    // 已经被换掉的节点上（点了等于没点）。
    const clicked = await page.evaluate(() => {
      const summaries = [
        ...document.querySelectorAll('.flat-model-list > summary, .flat-model-owner > summary'),
      ];
      const closed = summaries.find((s) => s.parentElement && !s.parentElement.open);
      if (!closed) return false;
      closed.click();
      return true;
    });
    if (!clicked) return false;
    await page.waitForTimeout(350);
  }
  return false;
};

const shot = async (name) => {
  const file = path.join(shotDir, `${name}.png`);
  await page.screenshot({path: file, fullPage: false});
  return file;
};

try {
  await page.goto(base, {waitUntil: 'domcontentloaded'});
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
  await waitForPool();

  assert.ok(await reveal(), `模型行要能点到（${modelId}）`);
  const start = await waitFor((state) => state.buttons.includes('edit-tier'), '这一行有「改档」');

  // 上一次跑剩的声明（脚本中途失败会留下）：先撤回内置档，再从头验。
  if (start.mark !== '免费') {
    await page.locator(`.flat-model-row:has(input[data-model-id="${modelId}"]) [data-act="edit-tier"]`).first().click();
    await page.waitForTimeout(300);
    await page.locator('[data-act="withdraw-model"]').first().click();
    await waitFor((state) => state.mark === '免费', '把上一轮留下的声明撤掉');
  }
  assert.ok(
    (await rowState()).mark === '免费',
    `一开始是内置的免费档，实际：${(await rowState()).text}`,
  );

  // 改档 → 按收费（界面上这一档的标记写的是「付费」）
  await page.locator(`input[name="flatModelMode"][data-model-id="${modelId}"]`).first().click();
  await page.waitForTimeout(200);
  await page.locator(`.flat-model-row:has(input[data-model-id="${modelId}"]) [data-act="edit-tier"]`).first().click();
  await page.waitForTimeout(300);
  await page.locator('[data-act="retier-model"][data-tier="metered"]').first().click();

  const metered = await waitFor((state) => state.mark === '付费', '档位变成付费');
  const declaration = posts.find((entry) => entry.name === '/pool/declaration');
  assert.ok(declaration, `要打 /pool/declaration，实际：${JSON.stringify(posts.map((p) => p.name))}`);
  assert.equal(declaration.body.tier, 'metered');
  assert.equal(declaration.body.cli, cli);
  const afterRetier = await shot('1-retiered');

  // 撤档 → 回到内置免费
  await page.locator(`.flat-model-row:has(input[data-model-id="${modelId}"]) [data-act="edit-tier"]`).first().click();
  await page.waitForTimeout(300);
  await page.locator('[data-act="withdraw-model"]').first().click();

  const back = await waitFor((state) => state.mark === '免费', '撤回内置免费档');
  const withdraw = posts.find((entry) => entry.name === '/pool/declaration/withdraw');
  assert.ok(withdraw, `要打 /pool/declaration/withdraw，实际：${JSON.stringify(posts.map((p) => p.name))}`);
  assert.equal(withdraw.body.model, cliModel);
  const afterWithdraw = await shot('2-withdrawn');

  console.log(JSON.stringify({
    harness,
    model: modelId,
    before: start.text,
    afterRetier: metered.text,
    afterWithdraw: back.text,
    shots: [afterRetier, afterWithdraw],
    errors,
  }, null, 2));
  assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
} finally {
  await browser.close();
}
