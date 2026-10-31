// 注册窗口表单的可达性：用户必须能真的看到并点到它，而不是「下拉永远为空」。
// 这里只做只读检查：不提交注册，不写正式注册表。
import { chromium } from 'playwright';
import { requireOffice } from './lib/office-url.mjs';

const base = await requireOffice('verify-window-registration.mjs', process.env.OFFICE_UI_URL);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 300)));
await page.goto(base, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('[data-open-connections]', { timeout: 20000 });
await page.locator('[data-open-connections]').click();
await page.locator('[data-register-source] option[value="codex"]').waitFor({ state: 'attached', timeout: 15000 });
// 真实滚动：该区块在连接对话框的滚动区里，用户要靠滚动才看得到。
await page.locator('.harness-register').scrollIntoViewIfNeeded();
const visible = await page.locator('.harness-register').isVisible();
const box = await page.locator('[data-register-harness]').boundingBox();
if (!visible || !box || box.width < 40) throw new Error('register window form is not reachable');
await page.locator('[data-register-source]').selectOption('codex');
await page.locator('[data-register-instance]').fill('codex-member-1');
await page.locator('[data-register-name]').fill('code CLI · 成员1');
const options = await page.locator('[data-register-source] option').count();
const disabled = await page.locator('[data-register-harness]').isDisabled();
console.log(JSON.stringify({ ok: true, options, button: [Math.round(box.width), Math.round(box.height)], disabled, errors }, null, 1));
await page.screenshot({ path: 'handoff/shot-register-window.png' });
await browser.close();
