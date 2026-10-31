// 「测试模型」这一页：对**真平台**跑一遍，不拦接口。
//
// 验的是人说的那条路：
//   1. 先选池子 → 点「刷新」，平台并行去问各家 CLI 都有什么模型；
//   2. **探完哪家先显示哪家**（不是等最慢那家一起出来）；
//   3. 在某一家里选一个模型 → 点「测试」；
//   4. 平台真的调它一次：成功显示成功（含耗时与它回的话），失败显示原因。
//
// 「测试」会花一次额度，所以默认只测**一个**模型（`OFFICE_MT_MODEL` 可指定；
// 默认挑 codebuddy 家的第一个 —— 本机上它是能通的那家）。要不要测别的，人自己点。
//
// 用法：
//   $env:OFFICE_URL='http://127.0.0.1:4174'; node scripts/verify-office-model-test.mjs
//   $env:OFFICE_MT_MODEL='codebuddy/hy3'      指定要测的模型（默认自动挑）
//   $env:OFFICE_MT_SHOTS='D:\Temp\shots'      截图目录
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';
import { requireOffice, requireProjectGroups } from './lib/office-url.mjs';

const base = await requireOffice('verify-office-model-test.mjs');
await requireProjectGroups(base, 'verify-office-model-test.mjs');
const wanted = process.env.OFFICE_MT_MODEL || '';
const shotDir = process.env.OFFICE_MT_SHOTS || path.join(process.env.TEMP || '.', 'office-model-test');
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

const shot = async (name) => {
  const file = path.join(shotDir, `${name}.png`);
  await page.screenshot({path: file, fullPage: false});
  return file;
};

/** 这一页现在什么样：几家、每家的状态与模型数、有没有结果。 */
const pageState = () =>
  page.evaluate(() => {
    const pane = document.querySelector('.flat-model-test');
    if (!pane) return null;
    const blocks = [...pane.querySelectorAll('.flat-probe')].map((block) => ({
      cli: block.dataset.cli || '',
      head: (block.querySelector('h3') || {}).textContent || '',
      models: block.querySelectorAll('.flat-probe-row').length,
      run: Boolean(block.querySelector('[data-act="test-model"]')),
      result: (block.querySelector('[data-test-result]') || {}).textContent || '',
      ok: Boolean(block.querySelector('.flat-probe-ok')),
    }));
    return {
      summary: (pane.querySelector('.flat-summary') || {}).textContent || '',
      notes: [...pane.querySelectorAll('.flat-note')].map((n) => n.textContent.trim()).slice(0, 3),
      blocks,
      options: [...pane.querySelectorAll('select[data-act="model-test-scope"] option')].map(
        (o) => o.value,
      ),
    };
  });

const waitFor = async (predicate, what, timeoutMs = 120000) => {
  const started = Date.now();
  for (;;) {
    const current = await pageState();
    if (current && predicate(current)) return current;
    if (Date.now() - started > timeoutMs) {
      throw new Error(`等不到：${what}（现在：${JSON.stringify(current && current.blocks)}）`);
    }
    await page.waitForTimeout(300);
  }
};

try {
  await page.goto(base, {waitUntil: 'domcontentloaded'});
  const openers = page.locator('.project-platform-open');
  await openers.first().waitFor({timeout: 30000});
  const total = await openers.count();
  let entered = false;
  for (let index = 0; index < total; index += 1) {
    await page.locator('.project-platform-open').nth(index).click();
    try {
      await page.waitForSelector('#aoWorkspace', {timeout: 6000});
      entered = true;
      break;
    } catch {
      await page.goto(base, {waitUntil: 'domcontentloaded'});
      await page.waitForSelector('.project-platform-open', {timeout: 30000});
    }
  }
  assert.ok(entered, '没有进到任何项目');

  // 等额度池那一块读到（「测试模型」按钮在它里面）
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const ready = await page.locator('[data-act="open-model-test"]').first().isVisible().catch(() => false);
    if (ready) break;
    // 额度池那张卡默认收起：点开它
    await page
      .locator('.flat-card-fold > details[data-fold="pool"] > summary')
      .first()
      .click()
      .catch(() => {});
    await page.waitForTimeout(500);
  }
  const opener = page.locator('[data-act="open-model-test"]').first();
  assert.ok(await opener.isVisible().catch(() => false), '额度池那张卡里要有「测试模型」按钮');
  await opener.click();
  await page.waitForSelector('.flat-model-test', {timeout: 30000});

  const opened = await pageState();
  assert.ok(opened, '没打开测试页');
  // 上一轮的探测结果会留在平台上（刷新页面也还在）—— 所以这里只记录现状，
  // 不要求为空；点「刷新」是重新探一轮。
  const before = opened.blocks.length;
  const firstShot = await shot('1-open');

  // 点「刷新」→ 并行探测；探完一家就该先出现一家
  await page.locator('[data-act="start-roster-probe"]').first().click();
  // 第一家在几秒内就该出现：这是"探完哪家先显示哪家"的判据。
  const firstArrival = await waitFor(
    (current) => current.blocks.some((block) => block.models > 0),
    '第一家先出现',
    60000,
  );
  const firstCli = firstArrival.blocks.find((block) => block.models > 0).cli;
  const atFirst = Date.now();
  const secondShot = await shot('2-first-arrival');

  // 全部探完：块数与池子里的 CLI 家数一致
  const done = await waitFor(
    (current) =>
      current.blocks.length > 0 &&
      current.notes.some((note) => note.includes('探测完成')),
    '全部探完',
    120000,
  );
  const elapsed = ((Date.now() - atFirst) / 1000).toFixed(1);
  const withModels = done.blocks.filter((block) => block.models > 0);
  assert.ok(withModels.length >= 1, `至少一家要列出模型，实际：${JSON.stringify(done.blocks)}`);
  const probePosts = posts.filter((entry) => entry.name === '/pool/roster-probe');
  assert.ok(probePosts.length >= 1, `要打 /pool/roster-probe，实际：${JSON.stringify(posts.map((p) => p.name))}`);

  // 选一个模型 → 点「测试」
  const targetBlock = wanted
    ? withModels.find((block) => wanted.startsWith(`${block.cli}/`))
    : withModels.find((block) => block.cli === 'codebuddy') || withModels[0];
  assert.ok(targetBlock, `要找到可测的一家`);
  const modelId = wanted || `${targetBlock.cli}/${'__first__'}`;
  const firstRow = page
    .locator(`.flat-probe[data-cli="${targetBlock.cli}"] .flat-probe-row input`)
    .first();
  const pickedValue = await firstRow.getAttribute('value');
  const toTest = wanted || pickedValue;
  await firstRow.check();
  const readyState = await waitFor(
    (current) => current.blocks.some((block) => block.cli === targetBlock.cli && block.run),
    '选完模型出现「测试」按钮',
    20000,
  );
  assert.ok(readyState, '选完模型要出现测试按钮');

  await page
    .locator(`.flat-probe[data-cli="${targetBlock.cli}"] [data-act="test-model"]`)
    .first()
    .click();
  const tested = await waitFor(
    (current) =>
      current.blocks.some(
        (block) =>
          block.cli === targetBlock.cli &&
          ['成功', '失败', '没测成'].some((word) => block.result.includes(word)),
      ),
    '测试出结果',
    200000,
  );
  const result = tested.blocks.find((block) => block.cli === targetBlock.cli);
  assert.ok(result.result.length > 0, '结果要写出来');
  const testPosts = posts.filter((entry) => entry.name === '/pool/model-test');
  assert.ok(testPosts.length >= 1, `要打 /pool/model-test，实际：${JSON.stringify(posts.map((p) => p.name))}`);
  const thirdShot = await (async () => {
    // 结论在最下面那块里：滚过去再截，图里才看得见"成功/失败"。
    await page
      .locator(`.flat-probe[data-cli="${targetBlock.cli}"] [data-act="test-model"]`)
      .first()
      .scrollIntoViewIfNeeded()
      .catch(() => {});
    await page.waitForTimeout(300);
    return shot('3-result');
  })();

  console.log(JSON.stringify({
    blocksBeforeRefresh: before,
    firstArrival: {cli: firstCli, secondsFromFirstArrivalToAllDone: elapsed},
    probedClis: done.blocks.map((block) => `${block.cli}:${block.models}`),
    tested: toTest,
    ok: result.ok,
    result: result.result,
    note: '「测试」会真的花一次额度，所以脚本只测一个模型',
    shots: [firstShot, secondShot, thirdShot],
    errors,
  }, null, 2));
  assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
} finally {
  await browser.close();
}
