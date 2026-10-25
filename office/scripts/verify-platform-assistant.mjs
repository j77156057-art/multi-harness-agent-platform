// 平台助手 = 巡检 + 上报，不是管理员：面板里不能出现任何写入动作（分派 / 处置 / 审核）。
// 断言：
//   1. 按钮与标题都叫「平台助手」；
//   2. 面板里没有任何分派 / 处置 / 审核按钮（分派、领取审核、续期重跑、重新打开、
//      释放升级、取消、去重取消、打开正式分派）；
//   3. 上报按钮在没有指定管理员 Harness 时是禁用的（不自动挑人顶替）。
// 只读：不写正式平台。真写过请用 OFFICE_FORMAL_LIVE=1 的脚本。
import { chromium } from 'playwright';

const url = process.env.OFFICE_UI_URL || 'http://127.0.0.1:4174/';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
await page.goto(url, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('[data-new-project]', { timeout: 20000 });

await page.locator('[data-new-project]').click();
await page.locator('[data-project-name]').fill('平台助手校验');
await page.locator('[data-confirm-create]').click();
// 开工配置现在是顶栏的按钮（旧的手机入口已撤）。
await page.waitForSelector('#aoStartBtn', { state: 'visible', timeout: 20000 });
await page.locator('#aoStartBtn').click();
await page.waitForSelector('#openingDialog', { state: 'visible', timeout: 20000 });
await page.locator('#openingCloseBtn').click();

const button = await page.locator('#adminAgentBtn').textContent();
if (button.trim() !== '平台助手') throw new Error(`button label is not 平台助手: ${button}`);

// 按钮在 phone-only 阶段不在屏幕上：直接在页面里打开面板，只读巡检摘要。
await page.evaluate(() => document.querySelector('#adminAgentBtn').click());
await page.waitForSelector('#adminAgentDialog[open]', { timeout: 10000 });
await page.waitForFunction(() => {
  const el = document.querySelector('#adminAgentDialog [data-body]');
  return el && !el.hidden;
}, { timeout: 20000 }).catch(() => {});

const audit = await page.evaluate(() => {
  const dialog = document.querySelector('#adminAgentDialog');
  const texts = [...dialog.querySelectorAll('button')].map(b => b.textContent.trim());
  const forbidden = /分派|领取审核|提交审核|续期|重开|重新打开|释放|取消|去重|打开管理员/;
  const offenders = texts.filter(t => forbidden.test(t));
  const reports = [...dialog.querySelectorAll('button')].filter(b => /^上报管理员/.test(b.textContent.trim()));
  // 「可执行成员」是只读观察面：它可以列窗口、模型和不可用的原因，但不能带任何按钮
  // （换模型、改派、接手都是管理员/人的决定，助手不代劳）。
  // 段落的标题现在是给人看的说法。
  const sections = [...dialog.querySelectorAll('.agent-section')];
  const availability = sections.find(s => (s.querySelector('h3')?.textContent || '').includes('可执行成员')) || null;
  // 「进度总览」：排队/在办/谁在跑都在这一节里。**管理员窗口不能出现在这里**
  // 的成员表里 —— 它不是干活的人，它是收件人。
  const patrol = sections.find(s => (s.querySelector('h3')?.textContent || '').includes('进度总览')) || null;
  // 「额度」也一样：它只说池子里有什么、谁的额度被谁花了，不能有动作。
  const pool = sections.find(s => (s.querySelector('h3')?.textContent || '').includes('额度')) || null;
  // 「未完成的任务」：只列事实 + 递请求。按钮只能是「请管理员换个人做」，
  // 而且没有指定管理员时必须是禁用的（请求递不出去）。
  const takeover = sections.find(s => (s.querySelector('h3')?.textContent || '').includes('未完成的任务')) || null;
  // 「等你批准的方案」：给人最终审核的那一版 + 人的批准按钮。
  const plan = sections.find(s => (s.querySelector('h3')?.textContent || '').includes('等你批准的方案')) || null;
  return {
    title: dialog.querySelector('h2')?.textContent.trim(),
    buttons: texts,
    offenders,
    reportCount: reports.length,
    disabledReports: reports.filter(b => b.disabled).length,
    hasAdminFields: Boolean(dialog.querySelector('form, [name="harness_id"], [name="goal"], [name="exclusive_paths"], [name="acceptance"]')),
    summary: dialog.querySelector('[data-body]')?.textContent || '',
    patrol: patrol ? { text: patrol.textContent || '' } : null,
    availability: availability ? {
      buttons: [...availability.querySelectorAll('button')].map(b => b.textContent.trim()),
      text: availability.textContent || '',
    } : null,
    pool: pool ? {
      buttons: [...pool.querySelectorAll('button')].map(b => b.textContent.trim()),
      text: pool.textContent || '',
    } : null,
    plan: plan ? {
      buttons: [...plan.querySelectorAll('button')].map(b => b.textContent.trim()),
      text: plan.textContent || '',
    } : null,
    takeover: takeover ? {
      buttons: [...takeover.querySelectorAll('button')].map(b => b.textContent.trim()),
      disabled: [...takeover.querySelectorAll('button')].filter(b => b.disabled).length,
      text: takeover.textContent || '',
    } : null,
  };
});
if (audit.offenders.length) throw new Error('platform assistant still offers administrator actions: ' + JSON.stringify(audit.offenders));
if (!audit.availability) throw new Error('platform assistant has no 「可执行成员」 section');
if (audit.availability.buttons.length) throw new Error('the availability section must stay read-only: ' + JSON.stringify(audit.availability.buttons));
if (!/本项目范围内没有登记在册的窗口|本项目 \d+ 个窗口/.test(audit.availability.text)) {
  throw new Error('the availability section does not scope windows to the active project: ' + audit.availability.text.slice(0, 200));
}
if (!audit.plan) throw new Error('platform assistant has no 「等你批准的方案」 section');
// 批准方案是**人**的动作：这一节只该有这一个按钮。
// （按钮文案是给人看的说法，平台那边的动作名是"批准并派单"。）
const planButtons = audit.plan.buttons.filter(t => t && t !== '批准，开始做');
if (planButtons.length) throw new Error('the plan section offers more than the human approval: ' + JSON.stringify(planButtons));
if (!audit.takeover) throw new Error('platform assistant has no 「未完成的任务」 section');
// 这一节只能有「请管理员换个人做」——不能有直接派单/改派的按钮。
const takeoverButtons = audit.takeover.buttons.filter(t => t && t !== '请管理员改派' && !t.startsWith('请管理员改派'));
if (takeoverButtons.length) throw new Error('the takeover section offers more than a request: ' + JSON.stringify(takeoverButtons));
if (audit.takeover.buttons.some(t => t.startsWith('请管理员改派')) && audit.takeover.disabled < audit.takeover.buttons.length) {
  // 没有指定管理员 Harness 时，请求按钮必须是禁用的。
  const state = await page.evaluate(() => window.adminAgentState || null);
  if (!state || !state.adminRecipient) {
    throw new Error('the reassign request button is enabled although no administrator harness is selected');
  }
}
if (!audit.pool) throw new Error('platform assistant has no 「额度」 section');
if (audit.pool.buttons.length) throw new Error('the pool section must stay read-only: ' + JSON.stringify(audit.pool.buttons));
// The pool section says which quota is whose: 免费额度 / 订阅额度 / 按量计费。
if (!/额度源|免费额度|订阅额度|按量计费/.test(audit.pool.text)) {
  throw new Error('the pool section does not name the quota tiers: ' + audit.pool.text.slice(0, 200));
}
if (audit.hasAdminFields) throw new Error('platform assistant renders administrator dispatch fields');
// 群范围不是项目范围：同一 group 下并列着别的项目。新建的这个项目还没有任务，
// 面板必须说「本项目 0 条」，并把整群数量单独标出来 —— 否则就是把 44 条别的
// 项目的任务当成自己的，连排队和在办都会串。
if (!/本项目 0 条任务/.test(audit.summary)) {
  throw new Error('platform assistant does not scope tasks to the active project: ' + audit.summary.slice(0, 200));
}
if (!/本群共 \d+ 条/.test(audit.summary)) {
  throw new Error('platform assistant hides the group total instead of labelling it: ' + audit.summary.slice(0, 200));
}
// 管理员窗口不能被当成"在排队的干活的人"。这条只查**进度总览**那一节 ——
// 排队表、在办表都在那里。方案正文里出现管理员窗口是另一回事（方案就是它
// 写的，"发起：Codex · 管理员"是事实），所以不再拿整块面板来判。
//
// 那一节里还有一句「上报收件人：管理员窗口 Codex · 管理员」—— 说的是**收件人**，
// 不是排队的人。匹配前先把这句去掉，剩下的才是"谁在排队/在办"。
if (!audit.patrol) throw new Error('platform assistant has no 「进度总览」 section');
const queueText = audit.patrol.text.replace(/上报收件人：[^\n]*/g, '');
if (/管理员 harness|Codex · 管理员|Qoder · 管理员/.test(queueText)) {
  throw new Error('platform assistant lists administrator windows as queueing members');
}
if (audit.reportCount && audit.disabledReports !== audit.reportCount) {
  // 禁用条件是唯一的：没有指定管理员 Harness。指定了就应当可点。
  // `adminAgentState` 是 getter，直接带 `adminRecipient`；这里不调用 `debug()`
  // ——那个方法在 getter 上从来没有过，写它是给这条断言留了一个永远为真的后门。
  const state = await page.evaluate(() => window.adminAgentState || null);
  if (!state || !state.adminRecipient) {
    throw new Error('report buttons are enabled although no administrator harness is selected');
  }
}
console.log(JSON.stringify({ ok: true, button: button.trim(), audit, errors }, null, 1));
await browser.close();
