// 平台助手：工作区域里常驻的入口 + 速览 + 提问。
// 只读检查：不代替用户发问（那会写一条定向私聊），只断言界面与数据摆对了。
import { chromium } from 'playwright';
import { requireOffice } from './lib/office-url.mjs';

const base = await requireOffice('verify-office-assistant.mjs', process.env.OFFICE_UI_URL);

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));

  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#aoLobbyBtn', { timeout: 20000 });
  await page.waitForTimeout(1500);

  // 进一个项目（有活的那种，速览才有东西可算）
  await page.locator('button', { hasText: '打开这个项目' }).first().click();
  // 等第一轮 refresh 回来：窗口名单、任务表、助手轨迹都是异步拉的
  // （探本机 CLI 配置慢），不等就会把"还没拉到"当成"没有"。
  await page.waitForFunction(
    () => {
      const el = document.querySelector('#aoWorkspace');
      return el && el.dataset.flatState === 'ready';
    },
    { timeout: 30000 },
  );
  await page.waitForTimeout(6000);

  // 1. 助手这一行**常驻**：不依赖平台注册表里有没有某个窗口
  const row = page.locator('#aoWorkspace .flat-conv', { hasText: '平台助手' }).first();
  if (!(await row.count())) throw new Error('the assistant row is missing from the workspace');
  await row.click();
  await page.waitForTimeout(1500);

  const pane = await page.evaluate(() => {
    const panel = document.querySelector('#aoWorkspace .flat-assistant');
    if (!panel) return null;
    return {
      title: panel.querySelector('h2')?.textContent.trim() || '',
      sections: [...panel.querySelectorAll('.flat-spec h3, .flat-assistant-trace > summary')]
        .map((h) => h.textContent.trim()),
      facts: [...panel.querySelectorAll('.flat-facts dt')].map((dt) => ({
        key: dt.textContent.trim(),
        value: dt.nextElementSibling?.textContent.trim() || '',
      })),
      presets: [...panel.querySelectorAll('[data-act="assistant-preset"]')].map((b) => b.textContent.trim()),
      hasInput: Boolean(panel.querySelector('#flatAssistantQuestion')),
      hasSend: Boolean(panel.querySelector('[data-act="assistant-send"]')),
      hasDetail: Boolean(panel.querySelector('[data-act="assistant-detail"]')),
      // 对话流：我问的与它答的按时间交替
      turns: [...panel.querySelectorAll('.flat-assistant-turn')].map((t) => ({
        mine: t.classList.contains('is-mine'),
        who: t.querySelector('.flat-assistant-who b')?.textContent.trim() || '',
        text: t.querySelector('.flat-assistant-text')?.textContent.trim() || '',
      })),
      // 它这一轮做了什么：折叠的轨迹块
      trace: panel.querySelector('.flat-assistant-trace > summary')?.textContent.trim() || '',
      // 助手只读：面板里不能有分派/处置/审核这类动作
      actions: [...panel.querySelectorAll('button')].map((b) => b.textContent.trim()),
    };
  });
  if (!pane) throw new Error('the assistant pane did not render');
  if (pane.title !== '平台助手') throw new Error('unexpected assistant title: ' + pane.title);
  if (!pane.sections.some((s) => s.includes('项目速览'))) {
    throw new Error('no 项目速览 section: ' + JSON.stringify(pane.sections));
  }
  if (!pane.sections.some((s) => s.includes('对话'))) {
    throw new Error('no 对话 section: ' + JSON.stringify(pane.sections));
  }
  if (!pane.trace.includes('它')) {
    throw new Error('the assistant does not show what it did: ' + pane.trace);
  }
  if (!pane.hasInput || !pane.hasSend) throw new Error('the assistant has no question box');
  if (!pane.presets.length) throw new Error('the assistant has no preset questions');
  if (!pane.hasDetail) throw new Error('the assistant cannot reach the patrol panel');
  // 问与答要交替出现（对话不是两块分开的列表）
  if (pane.turns.length && !pane.turns.some((turn) => turn.mine)) {
    throw new Error('the thread shows no question of mine: ' + JSON.stringify(pane.turns));
  }
  const forbidden = /分派|派单|领取审核|提交审核|续期|重新打开|释放|取消|改派/;
  const offenders = pane.actions.filter((t) => forbidden.test(t));
  if (offenders.length) throw new Error('the assistant pane offers writes: ' + JSON.stringify(offenders));
  const taskFact = pane.facts.find((f) => f.key === '任务');
  if (!taskFact) throw new Error('the overview does not say how many tasks there are');
  if (!/\d+ 条/.test(taskFact.value)) throw new Error('the task count is not a fact: ' + taskFact.value);

  await page.screenshot({ path: 'handoff/shot-office-assistant.png' });
  console.log(JSON.stringify({ ok: true, pane, errors }, null, 1));
} finally {
  await browser.close();
}
