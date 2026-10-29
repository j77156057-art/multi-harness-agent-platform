// 「需要你处理」这一页 + 左栏红点：对**真平台**跑一遍，不拦接口。
//
// 为什么改成页：它原来是一个浮层，压在内容上（正好盖住额度池模型清单），而且
// 「先不管」只是关掉浮层——下一次刷新又弹回来，等于没有结论。现在：
//   · 左栏多一行「需要你处理」，还有事没结论时带一个红点；
//   · 点进去是一页，一件事一块，块里能直接选模型（就用这个模型）或「先不管」；
//   · 红点只在**处理掉**或**先不管**之后消失。
//
// 断言：
//   1. 左栏有「需要你处理」这一行；有待办时它带红点（.flat-conv-dot）；
//   2. 点进去是页面（.flat-alerts），列出每一件事，每块都有选模型与「先不管」；
//   3. 点「先不管」→ 那一块消失；全部不管掉之后红点也消失；
//   4. 页面不再是浮层：不存在固定定位的 .flat-model-trouble。
//
// 用法：
//   $env:OFFICE_URL='http://127.0.0.1:5173'; node scripts/verify-office-alerts.mjs
//   可选：OFFICE_ALERTS_SHOTS=<截图目录>
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const base = (process.env.OFFICE_URL || 'http://127.0.0.1:5173').replace(/\/+$/, '');
const shotDir = process.env.OFFICE_ALERTS_SHOTS || path.join(process.env.TEMP || '.', 'office-alerts');
fs.mkdirSync(shotDir, {recursive: true});

const browser = await chromium.launch();
const page = await browser.newPage({viewport: {width: 1440, height: 950}});
const errors = [];
page.on('pageerror', (error) => errors.push(String(error)));

// 「有待办」那条路：平台此刻可能没有待办（那件事已经过去了）。为了验红点与
// 「先不管」，**只**把 /messages 的应答加一条管理员报的模型失败 —— 平台数据不动，
// 其它接口照旧是真的。OFFICE_ALERTS_STUB=1 才这么做。
const STUB = process.env.OFFICE_ALERTS_STUB === '1';
if (STUB) {
  await page.route('**/api/formal/messages*', async (route) => {
    const response = await route.fetch();
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    const messages = Array.isArray(payload.messages) ? payload.messages : [];
    messages.push({
      message_id: 'stub-trouble-1',
      sender_type: 'harness',
      sender_id: process.env.OFFICE_ALERTS_STUB_SENDER || 'codex-window-admin',
      body: '这一条没有答上来。\n原因：ConnectionRefused: failed to connect to upstream',
      created_at: new Date().toISOString(),
      refs: [],
    });
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify({...payload, messages}),
    });
  });
}

const shot = async (name) => {
  const file = path.join(shotDir, `${name}.png`);
  await page.screenshot({path: file, fullPage: false});
  return file;
};

/** 左栏那一行现在长什么样（每次原子读，页面每 15 秒重画）。 */
const rowState = () =>
  page.evaluate(() => {
    const rows = [...document.querySelectorAll('#aoWorkspace .flat-conv')];
    const row = rows.find((node) => (node.querySelector('b') || {}).textContent?.includes('需要你处理'));
    if (!row) return null;
    return {
      text: row.innerText.replace(/\s+/g, ' ').trim(),
      dot: Boolean(row.querySelector('.flat-conv-dot')),
      on: row.classList.contains('is-on'),
    };
  });

const paneState = () =>
  page.evaluate(() => {
    const pane = document.querySelector('.flat-alerts');
    if (!pane) return null;
    return {
      summary: (pane.querySelector('.flat-summary') || {}).textContent || '',
      blocks: [...pane.querySelectorAll('.flat-alert')].map((block) => ({
        title: (block.querySelector('h3') || {}).textContent || '',
        hasSelect: Boolean(block.querySelector('[data-trouble-select]')),
        actions: [...block.querySelectorAll('button[data-act]')].map((b) => b.dataset.act),
      })),
      floating: document.querySelectorAll('.flat-model-trouble').length,
    };
  });

const waitFor = async (read, predicate, what, timeoutMs = 30000) => {
  const started = Date.now();
  for (;;) {
    const state = await read();
    if (state && predicate(state)) return state;
    if (Date.now() - started > timeoutMs) throw new Error(`等不到：${what}`);
    await page.waitForTimeout(300);
  }
};

try {
  await page.goto(base, {waitUntil: 'domcontentloaded'});
  const openers = page.locator('.project-platform-open');
  await openers.first().waitFor({timeout: 30000});
  await openers.first().click();
  await page.waitForSelector('#aoWorkspace', {timeout: 30000});

  // 1. 左栏有这一行
  const row = await waitFor(rowState, () => true, '左栏出现「需要你处理」');
  assert.ok(row.text.includes('需要你处理'), `左栏要有这一行，实际：${row.text}`);

  // 数据是分批到的（消息那一批慢）：注入了一条待办时要等红点亮起来再点，
  // 否则读到的是"消息还没到"的空状态，验的就成了空页面。
  let rowLive = row;
  if (STUB) {
    rowLive = await waitFor(rowState, (state) => state.dot === true, '红点亮起');
  } else {
    await page.waitForTimeout(3000);
    rowLive = await rowState();
  }

  // 2. 点进去是一页
  await page.locator('#aoWorkspace .flat-conv', {hasText: '需要你处理'}).first().click();
  const pane = await waitFor(paneState, () => true, '打开这一页');
  assert.equal(pane.floating, 0, '不该再有固定定位的浮层');
  const firstShot = await shot('1-alerts-page');

  if (!pane.blocks.length) {
    assert.equal(rowLive.dot, false, '没有待办时不该有红点');
    console.log(JSON.stringify({
      row: rowLive.text,
      dot: rowLive.dot,
      blocks: 0,
      note: '这个项目现在没有待你处理的事；只验了页面与红点不亮。',
      shots: [firstShot],
      errors,
    }, null, 2));
    assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
    process.exit(0);
  }

  assert.equal(rowLive.dot, true, `有待办时红点要亮，实际：${rowLive.text}`);
  for (const block of pane.blocks) {
    assert.ok(block.hasSelect, `每一块都要能选模型：${block.title}`);
    assert.ok(block.actions.includes('apply-trouble-model'), '每一块都要有「就用这个模型」');
    assert.ok(block.actions.includes('dismiss-trouble'), '每一块都要有「先不管」');
  }

  // 3. 先不管 → 那一块消失；全部不管掉 → 红点灭
  let remaining = pane.blocks.length;
  const cleared = [];
  if (process.env.OFFICE_ALERTS_APPLY === '1') {
    // 「处理掉」那条路：真给这个窗口换成一个额度池里的模型。它会**改动平台**
    // （这个窗口以后真的按那个模型跑），所以默认不跑，要跑得显式说一声。
    const select = page.locator('.flat-alert [data-trouble-select]').first();
    const option = await select.locator('option').nth(1).getAttribute('value');
    await select.selectOption(option);
    await page.locator('.flat-alert [data-act="apply-trouble-model"]').first().click();
    await page.waitForTimeout(2500);
    const rowAfterApply = await rowState();
    cleared.push(`处理掉之后：${rowAfterApply ? rowAfterApply.text : '(读不到)'}`);
    remaining = (await paneState()).blocks.length;
  }
  while (remaining > 0) {
    const before = await paneState();
    const target = before.blocks[0];
    await page.locator('.flat-alert [data-act="dismiss-trouble"]').first().click();
    await page.waitForTimeout(700);
    const after = await paneState();
    if (after.blocks.length === before.blocks.length) {
      throw new Error(`点了「先不管」那件事还在：${target.title}`);
    }
    cleared.push(target.title);
    remaining = after.blocks.length;
  }
  const rowAfter = await waitFor(rowState, (state) => state.dot === false, '红点消失');
  assert.ok(
    rowAfter.text.includes('没有待你处理的事'),
    `红点灭了，这一行也要说清楚，实际：${rowAfter.text}`,
  );
  const secondShot = await shot('2-cleared');

  console.log(JSON.stringify({
    rowBefore: rowLive.text,
    cleared,
    rowAfter: rowAfter.text,
    shots: [firstShot, secondShot],
    errors,
  }, null, 2));
  assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
} finally {
  await browser.close();
}
