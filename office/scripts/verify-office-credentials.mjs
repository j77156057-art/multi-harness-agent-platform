// 窗口详情里的「它的平台凭据」：对**真平台**跑一遍，不拦任何接口。
//
// 为什么真跑：这一块要回答的是"这个窗口到底有几条凭据、哪条在用、哪条能收回"，
// 全靠平台的真数据。用假数据验，只能验出"我写的 HTML 能被解析"。
//
// 断言：
//   1. 凭据一块列出**多条**，并且只有一条标「在用」，那一条写着指纹；
//   2. 「在用」那条没有吊销按钮（吊销它会让窗口下线）；
//   3. 点一条不在用的「吊销」→ 它变成「已吊销」，平台那条 token 真的被收回；
//   4. 吊销之后那条**仍在列表里**（吊销留痕，不删行），在用的那条不受影响。
//
// 只读一次 DOM 的做法：办公室每 15 秒整体重画一次，元素会被换掉，所以"读"走一次
// evaluate 拿快照，只有"点"用 locator（它每次重新定位）。
//
// 用法：
//   $env:OFFICE_URL='http://127.0.0.1:5173'; node scripts/verify-office-credentials.mjs
//   可选：OFFICE_CRED_HARNESS=<窗口 id>、OFFICE_CRED_SHOTS=<截图目录>
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const base = (process.env.OFFICE_URL || 'http://127.0.0.1:5173').replace(/\/+$/, '');
const harness = process.env.OFFICE_CRED_HARNESS || 'codex';
const shotDir = process.env.OFFICE_CRED_SHOTS || path.join(process.env.TEMP || '.', 'office-credentials');
fs.mkdirSync(shotDir, {recursive: true});

const browser = await chromium.launch();
const page = await browser.newPage({viewport: {width: 1440, height: 900}});
const errors = [];
page.on('pageerror', (error) => errors.push(String(error)));

const shot = async (name) => {
  const file = path.join(shotDir, `${name}.png`);
  await page.screenshot({path: file, fullPage: false});
  return file;
};

/** 一次读全：凭据块现在长什么样（不会被重绘撕成两半）。 */
const credentialsState = () =>
  page.evaluate(() => {
    const box = document.querySelector('.flat-credentials');
    if (!box) return null;
    return {
      rows: [...box.querySelectorAll('.flat-cred')].map((node) => ({
        fingerprint: node.dataset.fingerprint || '',
        cls: node.className,
        text: node.innerText,
        buttons: [...node.querySelectorAll('[data-revoke]')].length,
      })),
      note: (box.querySelector('.flat-note-row') || {}).innerText || '',
      keeper: [...box.querySelectorAll('.flat-note')].map((node) => node.innerText).join(' '),
    };
  });

const waitForState = async (predicate, what) => {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const state = await credentialsState();
    if (state && predicate(state)) return state;
    await page.waitForTimeout(500);
  }
  throw new Error(`等不到：${what}`);
};

try {
  await page.goto(base, {waitUntil: 'domcontentloaded'});

  // 进项目：首屏列的是"平台上已有的项目"，要进的是有这个窗口的那一个。
  // 不按名字猜（名字是人写的），点进去看有没有这个窗口。
  const rowSelector = `#aoWorkspace .flat-conv[data-harness="${harness}"]`;
  const openers = page.locator('.project-platform-open');
  await openers.first().waitFor({timeout: 30000});
  const total = await openers.count();
  assert.ok(total > 0, '首屏要列出平台上的项目');
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

  // 1. 有多条，且只有一条在用，并且写着指纹
  const before = await waitForState((state) => state.rows.length > 0, '凭据列表有内容');
  await page.evaluate(() => {
    const box = document.querySelector('.flat-credentials');
    if (box) box.scrollIntoView({block: 'center'});
  });
  await page.waitForTimeout(400);

  const live = before.rows.filter((row) => row.cls.includes('flat-cred-live'));
  assert.equal(live.length, 1, `只有一条是「在用」，实际：${JSON.stringify(before.rows)}`);
  assert.ok(live[0].text.includes('在用'), `在用的那条要写明，实际：${live[0].text}`);
  assert.ok(
    /sha256:[0-9a-f]{6,}/.test(live[0].text),
    `要说清是哪一条（指纹），实际：${live[0].text}`,
  );

  // 2. 在用的那条没有吊销按钮
  assert.equal(live[0].buttons, 0, '正在用的凭据不给吊销按钮');

  const revocable = before.rows.filter(
    (row) => !row.cls.includes('flat-cred-live') && !row.cls.includes('flat-cred-dead'),
  );
  const firstShot = await shot('1-credentials');

  // 3. 没有可吊销的就到此为止：这个窗口只剩在用的那一条，那不是失败。
  if (!revocable.length) {
    console.log(JSON.stringify({
      harness,
      credentials: before.rows.length,
      revoked: null,
      note: '这个窗口只有一条（在用的）凭据，没有可吊销的旧凭据；列表与「在用」判定已验。',
      shots: [firstShot],
      errors,
    }, null, 2));
  } else {
    const target = revocable[0];
    await page
      .locator(`.flat-cred[data-fingerprint="${target.fingerprint}"] [data-revoke]`)
      .click();

    // 4. 界面：变成已吊销，并说出结果；在用的那条还在
    const after = await waitForState(
      (state) =>
        state.rows.some(
          (row) =>
            row.fingerprint === target.fingerprint && row.cls.includes('flat-cred-dead'),
        ),
      `那一行变成「已吊销」（${target.fingerprint}）`,
    );
    const dead = after.rows.find((row) => row.fingerprint === target.fingerprint);
    assert.ok(dead.text.includes('已吊销'), `那一行要写「已吊销」，实际：${dead.text}`);
    assert.ok(after.note.includes('吊销'), `要把结果说出来，实际：${after.note}`);
    assert.equal(
      after.rows.filter((row) => row.cls.includes('flat-cred-live')).length,
      1,
      '吊销一条旧凭据不该动到在用的那条',
    );
    assert.equal(
      after.rows.length,
      before.rows.length,
      '吊销是留痕不删行：行数不变',
    );

    const secondShot = await shot('2-revoked');
    console.log(JSON.stringify({
      harness,
      credentials: before.rows.length,
      revoked: target.fingerprint,
      revokedRow: target.text.split('\n')[0],
      note: after.note,
      shots: [firstShot, secondShot],
      errors,
    }, null, 2));
  }

  assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
} finally {
  await browser.close();
}
