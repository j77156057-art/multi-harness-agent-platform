// 注册窗口表单的可达性：用户必须能真的看到并点到它，而不是「下拉永远为空」。
// 这里只做只读检查：不提交注册，不写正式注册表。
//
// 下拉里列的是**这台机器上已经登记过的窗口 id**，属于环境而不是界面契约。
// 默认找 `codex`；换台机器用 OFFICE_REGISTER_SOURCE=<窗口 id> 指一个。
// 一个都没有、或指定的不在列表里时，说清缺什么再跳过。
import { chromium } from 'playwright';
import { requireOffice, skip } from './lib/office-url.mjs';

const base = await requireOffice('verify-window-registration.mjs', process.env.OFFICE_UI_URL);
const source = process.env.OFFICE_REGISTER_SOURCE || 'codex';
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 300)));
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-open-connections]', { timeout: 20000 });
  await page.locator('[data-open-connections]').click();
  await page.waitForSelector('[data-register-source]', { timeout: 15000 });

  const values = await page
    .locator('[data-register-source] option')
    .evaluateAll((nodes) => nodes.map((node) => node.value));
  if (!values.length) {
    skip(
      'verify-window-registration.mjs',
      '连接对话框里的「登记窗口」表单一个来源都没列（下拉是空的）。先用 start-fresh.ps1 起界面或注册一个窗口，再跑这条校验',
    );
  }
  if (!values.includes(source)) {
    skip(
      'verify-window-registration.mjs',
      `下拉里没有窗口「${source}」（现在可选：${values.join(', ')}）。用 OFFICE_REGISTER_SOURCE=<窗口 id> 指一个已有的，或先用 start-fresh.ps1 把本机 CLI 登记成窗口`,
    );
  }

  // 真实滚动：该区块在连接对话框的滚动区里，用户要靠滚动才看得到。
  await page.locator('.harness-register').scrollIntoViewIfNeeded();
  const visible = await page.locator('.harness-register').isVisible();
  const box = await page.locator('[data-register-harness]').boundingBox();
  if (!visible || !box || box.width < 40) throw new Error('register window form is not reachable');
  await page.locator('[data-register-source]').selectOption(source);
  await page.locator('[data-register-instance]').fill('codex-member-1');
  await page.locator('[data-register-name]').fill('code CLI · 成员1');
  const disabled = await page.locator('[data-register-harness]').isDisabled();
  console.log(
    JSON.stringify(
      { ok: true, options: values.length, button: [Math.round(box.width), Math.round(box.height)], disabled, errors },
      null,
      1,
    ),
  );
  await page.screenshot({ path: 'handoff/shot-register-window.png' });
} finally {
  // 失败时也必须关掉浏览器，否则 npm run verify 会卡在这一条上等下去。
  await browser.close();
}
