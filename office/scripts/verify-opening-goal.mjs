// 开工向导的「任务目标」输入框必须**第一屏就看得见**，且照旧参与校验与摘要。
//
// 为什么要有这个检查：目标框曾被组件排在表单最后，落在滚动区下方一百多像素处
// （实测 top=912，而可视区只到 bottom=798），第一次用的人第一眼看不到自己要写
// 什么。宿主把组件自己的输入框搬到表单最前（见 opening-host.js 的
// promoteGoalField）—— 这条检查钉住两件事：位置，以及搬动没有把它弄坏。
import { chromium } from 'playwright';

const base = process.env.OFFICE_UI_URL || 'http://127.0.0.1:4174/';
const GOAL = '为本期活动写一页说明';

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 300)));

  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#aoLobbyBtn', { timeout: 20000 });
  await page.waitForTimeout(1500);

  // 新建项目 → 进入开工配置
  await page.locator('button', { hasText: '新建项目' }).first().click();
  await page.waitForTimeout(900);
  await page.locator('dialog[open] input:not([type="checkbox"])').first().fill('目标框位置检查');
  await page.locator('button', { hasText: '创建并进入开工配置' }).first().click();
  await page.waitForTimeout(2500);

  // 向导可能没自动打开（创建后流程已切换）：显式叫起来
  await page.evaluate(() => {
    const d = document.querySelector('#openingDialog');
    if (d && !d.open) {
      const btn = [...document.querySelectorAll('button')].find((b) => /开工配置/.test(b.textContent || ''));
      btn?.click();
    }
  });
  await page.waitForTimeout(1200);

  // 推进到配置阶段（idle → connect → config）
  for (let step = 0; step < 6; step += 1) {
    if (await page.locator('#openingPhoneSlot textarea[data-field="goal"]').count()) break;
    const clicked = await page.evaluate(() => {
      const slot = document.querySelector('#openingPhoneSlot');
      const btns = [...slot.querySelectorAll('button')].filter((b) => !b.disabled);
      const preferred = btns.find((b) => /开始任务|连接正式平台|连接演示|下一步|继续|配置|选择/.test(b.textContent || ''));
      const target = preferred || btns[btns.length - 1];
      if (!target) return null;
      const text = (target.textContent || '').trim();
      target.click();
      return text;
    });
    if (!clicked) break;
    await page.waitForTimeout(1600);
  }

  const goal = page.locator('#openingPhoneSlot textarea[data-field="goal"]');
  if (!(await goal.count())) throw new Error('the goal field never appeared');
  await goal.fill(GOAL);
  await page.waitForTimeout(400);

  // 位置：目标框必须落在滚动容器的可视区内（不用滚就能看到）
  const geometry = await page.evaluate(() => {
    const ta = document.querySelector('#openingPhoneSlot textarea[data-field="goal"]');
    const r = ta.getBoundingClientRect();
    let scroller = null;
    for (let p = ta.parentElement; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (/(auto|scroll)/.test(s.overflowY)) { scroller = p; break; }
    }
    const sr = scroller ? scroller.getBoundingClientRect() : null;
    return {
      goalTop: Math.round(r.top),
      goalBottom: Math.round(r.bottom),
      visibleTop: sr ? Math.round(sr.top) : null,
      visibleBottom: sr ? Math.round(sr.bottom) : null,
      firstGroupIsGoal: ta.closest('.oop-group') === scroller?.querySelector('.oop-form')?.firstElementChild,
    };
  });
  const visible = geometry.visibleTop === null
    || (geometry.goalTop >= geometry.visibleTop && geometry.goalBottom <= geometry.visibleBottom);
  if (!visible) {
    throw new Error(`the goal field is still below the fold: ${JSON.stringify(geometry)}`);
  }
  if (!geometry.firstGroupIsGoal) {
    throw new Error(`the goal field is not the first item of the form: ${JSON.stringify(geometry)}`);
  }

  // 组件的校验要求：选一个管理员窗口 + 至少一个成员窗口。先选上，
  // 否则「查看配置摘要」会被机械门拦下，摘要里自然看不到目标。
  const picked = await page.evaluate(() => {
    const groups = [...document.querySelectorAll('#openingPhoneSlot .oop-group')];
    const pick = (keyword) => {
      const group = groups.find((g) => {
        const label = g.querySelector('label.oop-label');
        return label && (label.textContent || '').includes(keyword);
      });
      const chip = group ? group.querySelector('.oop-chip:not(.on)') : null;
      if (!chip) return false;
      chip.click();
      return true;
    };
    return { admin: pick('管理员窗口'), member: pick('成员窗口') };
  });
  await page.waitForTimeout(800);

  // 搬动没有把它弄坏：目标照旧进摘要
  await page.locator('#openingPhoneSlot button', { hasText: '查看配置摘要' }).first().click();
  await page.waitForTimeout(1200);
  const summary = await page.locator('#openingPhoneSlot').innerText();
  if (!summary.includes(GOAL)) {
    throw new Error(`the goal did not reach the configuration summary (picked=${JSON.stringify(picked)})`);
  }

  await page.screenshot({ path: 'handoff/shot-opening-goal.png' });
  console.log(JSON.stringify({ ok: true, geometry, picked, goalInSummary: true, errors }, null, 1));
} finally {
  await browser.close();
}
