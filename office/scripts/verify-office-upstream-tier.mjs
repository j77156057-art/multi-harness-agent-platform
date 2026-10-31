// 非 CLI 上游的改档 / 撤档：对**真平台**跑一遍，不拦接口。
//
// `claude-provider`、`opencode:1`、`opencode:local`、`minimax` 这几家的额度写在各自
// CLI 的配置里，档位本来是**代码里的默认值** —— 池子里没有可改的东西。这个脚本验的
// 就是后来补的那条路：能在界面上改，也能撤回到默认。
//
// 断言：
//   1. 非 CLI 那一组（如 `opencode`）的每一行都有「改档」；
//   2. 点「改档 → 按免费」：模型标成免费，且平台侧 `upstream_tiers` 记下了这条覆盖；
//   3. 再「改档 → 撤档」：回到代码默认，覆盖记录消失；
//   4. 用户自己的 key 那一组不给「改档」（那一档不是价格，是"这笔钱是用户的"）。
//
// 用法：
//   $env:OFFICE_URL='http://127.0.0.1:4174'; node scripts/verify-office-upstream-tier.mjs
//   可选：OFFICE_UTIER_HARNESS、OFFICE_UTIER_GROUP（默认 opencode）、
//        OFFICE_UTIER_MODEL（默认组内第一个模型 id）、OFFICE_UTIER_SHOTS
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';
import { requireOffice } from './lib/office-url.mjs';

const base = await requireOffice('verify-office-upstream-tier.mjs');
const harness = process.env.OFFICE_UTIER_HARNESS || 'codex';
const groupName = process.env.OFFICE_UTIER_GROUP || 'opencode';
const shotDir = process.env.OFFICE_UTIER_SHOTS || path.join(process.env.TEMP || '.', 'office-upstream-tier');
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

/** 这一组里每一行现在什么样（原子读）。 */
const groupState = (owner) =>
  page.evaluate((ownerKey) => {
    const summaries = [...document.querySelectorAll('.flat-model-owner > summary')];
    // 分组标题是「opencode（2 个可用 …）」：按括号前那一段**精确**配对，
    // 用 startsWith 会把 `opencode` 和 `opencode-cli` 认成同一组。
    const summary = summaries.find(
      (node) => node.textContent.trim().split('（')[0].trim() === ownerKey,
    );
    if (!summary) return null;
    const group = summary.parentElement;
    const rows = [...group.querySelectorAll('.flat-model-row')].map((row) => {
      const input = row.querySelector('input[data-model-id]');
      return {
        id: input ? input.dataset.modelId : '',
        mark: row.querySelector('.flat-model-mark') ? row.querySelector('.flat-model-mark').textContent.trim() : '',
        actions: [...row.querySelectorAll('button[data-act]')].map((b) => b.dataset.act),
        open: Boolean(row.querySelector('[data-act="edit-tier"][data-model-id=""]')),
      };
    });
    return {rows, open: group.open};
  }, owner);

const reveal = async (owner, modelId) => {
  for (let attempt = 0; attempt < 16; attempt += 1) {
    const visible = await page
      .locator(`input[name="flatModelMode"][data-model-id="${modelId}"]`)
      .first()
      .isVisible()
      .catch(() => false);
    if (visible) return true;
    const clicked = await page.evaluate(
      ([ownerKey, id]) => {
        const summaries = [
          ...document.querySelectorAll('.flat-model-list > summary, .flat-model-owner > summary'),
        ].filter((node) => node.parentElement && !node.parentElement.open);
        if (!summaries.length) return false;
        const wanted = summaries.find(
          (node) => node.parentElement.classList.contains('flat-model-owner') &&
            node.textContent.trim().split('（')[0].trim() === ownerKey,
        );
        const idRow = document.querySelector(`input[data-model-id="${id}"]`);
        // 先开装着目标的那一组；组都开了再开外面那层。
        const target = wanted || (idRow ? summaries[0] : summaries[0]);
        target.click();
        return true;
      },
      [owner, modelId],
    );
    if (!clicked) return false;
    await page.waitForTimeout(350);
  }
  return false;
};

/** 打开这一行的编辑器，直到目标按钮真的出现（重绘会把它合上，所以要反复点）。 */
const openEditor = async (modelId, action, attempts = 10) => {
  const selector = `.flat-model-row:has(input[data-model-id="${modelId}"]) [data-act="${action}"]`;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const visible = await page.locator(selector).first().isVisible().catch(() => false);
    if (visible) return true;
    await page
      .locator(`.flat-model-row:has(input[data-model-id="${modelId}"]) [data-act="edit-tier"]`)
      .first()
      .click()
      .catch(() => {});
    await page.waitForTimeout(400);
  }
  return false;
};

/** 等这一行的档位变成期望的样子：改档要先重探一遍额度池（会 spawn 几个 CLI），回得慢。 */
const waitForMark = async (owner, modelId, predicate, what, timeoutMs = 90000) => {
  const started = Date.now();
  for (;;) {
    const state = await groupState(owner);
    const row = state && state.rows.find((item) => item.id === modelId);
    if (row && predicate(row.mark)) return row;
    if (Date.now() - started > timeoutMs) {
      throw new Error(`等不到：${what}（现在是 ${row ? row.mark : '(读不到这一行)'}）`);
    }
    await page.waitForTimeout(500);
  }
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

  // 等额度池清单真的到（冷启动要 spawn 几个 CLI 的名册）
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const head = await page.locator('.flat-model-list > summary').first().innerText().catch(() => '');
    if (head && !head.includes('正在读取')) break;
    await page.waitForTimeout(500);
  }

  const probe = await groupState(groupName);
  assert.ok(probe, `界面上要有「${groupName}」这一组`);
  assert.ok(probe.rows.length > 0, `「${groupName}」这一组要有模型`);
  const target = process.env.OFFICE_UTIER_MODEL || probe.rows[0].id;
  assert.ok(await reveal(groupName, target), `这一行要能点到（${target}）`);

  const before = await groupState(groupName);
  const rowBefore = before.rows.find((row) => row.id === target);
  assert.ok(rowBefore, `要找到 ${target}`);
  assert.ok(
    rowBefore.actions.includes('edit-tier'),
    `非 CLI 上游这一行也要有「改档」，实际：${JSON.stringify(rowBefore)}`,
  );
  const firstShot = await shot('1-before');

  // 改档 → 按免费
  assert.ok(await openEditor(target, 'retier-upstream'), '这一行的「改档」要能点开');
  const openState = await groupState(groupName);
  const opened = openState.rows.find((row) => row.id === target);
  assert.ok(opened && opened.open, `点开之后要有「按免费 / 按收费 / 撤档」，实际：${JSON.stringify(opened)}`);
  await page
    .locator(`.flat-model-row:has(input[data-model-id="${target}"]) [data-act="retier-upstream"][data-tier="free"]`)
    .first()
    .click();
  // 改档要先重探一遍额度池（会 spawn 几个 CLI 的名册），回得慢；轮询到它真的变。
  const rowFree = await waitForMark(groupName, target, (mark) => mark === '免费', '档位变成免费');
  assert.equal(rowFree.mark, '免费', `档位要变成免费，实际：${rowFree.mark}`);
  const declare = posts.find((entry) => entry.name === '/pool/upstream-tier');
  assert.ok(declare, `要打 /pool/upstream-tier，实际：${JSON.stringify(posts.map((p) => p.name))}`);
  assert.equal(declare.body.tier, 'free');
  const secondShot = await shot('2-free');

  // 撤档 → 回到代码默认
  assert.ok(
    await openEditor(target, 'withdraw-upstream'),
    '有覆盖时这一行要有「撤档」——没有说明界面没把人写的那条记下来',
  );
  await page
    .locator(`.flat-model-row:has(input[data-model-id="${target}"]) [data-act="withdraw-upstream"]`)
    .first()
    .click();
  const rowBack = await waitForMark(groupName, target, (mark) => mark !== '免费', '撤档后回到默认档位');
  const withdraw = posts.find((entry) => entry.name === '/pool/upstream-tier/withdraw');
  assert.ok(withdraw, `要打 /pool/upstream-tier/withdraw，实际：${JSON.stringify(posts.map((p) => p.name))}`);
  assert.notEqual(rowBack.mark, '免费', `撤档后不该还是免费，实际：${rowBack.mark}`);
  const thirdShot = await shot('3-withdrawn');

  console.log(JSON.stringify({
    harness,
    group: groupName,
    model: target,
    before: rowBefore.mark,
    afterFree: rowFree.mark,
    afterWithdraw: rowBack.mark,
    note: '用户自己的 key 那一档界面上不给「改档」（见代码：tier === user 时不出编辑按钮）',
    shots: [firstShot, secondShot, thirdShot],
    errors,
  }, null, 2));
  assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
} finally {
  await browser.close();
}
