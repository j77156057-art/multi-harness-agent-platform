// 协作演练的逐项核验 + 截图留证。
//
// 每一张截图都等到**真有内容**再拍 —— 之前拍到空屏，是因为界面第二次重绘
// （消息、窗口名单是第二批才到的）还没落地就按了快门。
//
// 用法：$env:OFFICE_URL='http://127.0.0.1:5173'; node scripts/verify-collab-live.mjs
import fs from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const base = (process.env.OFFICE_URL || 'http://127.0.0.1:5173').replace(/\/+$/, '');
const dir = process.env.OFFICE_SHOTS || path.join(process.env.TEMP || '.', 'collab-shots');
const outFile = process.env.OFFICE_LIVE_OUT || path.join(process.env.TEMP || '.', 'collab-live.json');
fs.mkdirSync(dir, {recursive: true});

const browser = await chromium.launch();
const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));

const report = {at: new Date().toISOString(), office: base, shots: [], checks: {}, errors};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const shot = async (name) => {
  const file = path.join(dir, `${name}.png`);
  await page.screenshot({path: file, fullPage: false});
  report.shots.push(file);
  return file;
};

/** 等一个条件成立（轮询，不用固定 sleep）。 */
async function until(fn, {timeout = 60000, step = 1000} = {}) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const value = await fn();
    if (value) return value;
    await sleep(step);
  }
  return null;
}

async function openProject() {
  await page.goto(base, {waitUntil: 'domcontentloaded'});
  await page.waitForSelector('.project-platform-open', {timeout: 30000});
  const total = await page.locator('.project-platform-open').count();
  for (let i = 0; i < total; i += 1) {
    await page.locator('.project-platform-open').nth(i).click();
    const ok = await page
      .waitForSelector('#aoWorkspace .flat-conv', {timeout: 6000})
      .then(() => true)
      .catch(() => false);
    if (ok) return true;
    await page.goto(base, {waitUntil: 'domcontentloaded'});
    await page.waitForSelector('.project-platform-open', {timeout: 30000});
  }
  return false;
}

try {
  await page.goto(base, {waitUntil: 'domcontentloaded'});
  await sleep(1500);
  await shot('01-lobby');

  if (!(await openProject())) throw new Error('进不去项目工作区');

  // 窗口名单是第二批才到的（要读每个 CLI 的配置，慢）。
  await until(async () => (await page.locator('#aoWorkspace .flat-conv').count()) > 5, {timeout: 90000});
  await sleep(2000);

  report.checks.header = await page.evaluate(() => {
    const node = document.querySelector('#aoConn span');
    return node ? node.textContent.trim() : null;
  });
  report.checks.roster = await page.evaluate(() =>
    [...document.querySelectorAll('#aoWorkspace .flat-conv')].map((n) =>
      n.textContent.replace(/\s+/g, ' ').trim(),
    ),
  );
  await shot('02-project-overview');

  // 窗口名单：点第一个成员窗口，看它的状态页
  const member = page.locator('#aoWorkspace .flat-conv', {hasText: 'Claude Code'}).first();
  if (await member.isVisible().catch(() => false)) {
    await member.click();
    await sleep(2000);
    report.checks.windowPane = await page.evaluate(() => {
      const pane = document.querySelector('.flat-window');
      if (!pane) return null;
      const facts = [...pane.querySelectorAll('.flat-facts dt, .flat-facts dd')].map((n) =>
        n.textContent.replace(/\s+/g, ' ').trim(),
      );
      return facts.slice(0, 24);
    });
    await shot('03-window-detail');
  }

  // 一条真实的活
  const task = page.locator('.flat-tasks .flat-task, .flat-task-row, [data-task-id]').first();
  if (await task.isVisible().catch(() => false)) {
    await task.click();
    await sleep(2500);
    report.checks.taskDetail = await page.evaluate(() => {
      const pane = document.querySelector('.flat-detail');
      return pane ? pane.textContent.replace(/\s+/g, ' ').trim().slice(0, 600) : null;
    });
    await shot('04-task-detail');
  }

  // 项目群聊：等到真的有消息再拍
  const group = page.locator('#aoWorkspace .flat-conv', {hasText: '项目群聊'}).first();
  if (await group.isVisible().catch(() => false)) {
    await group.click();
    const ready = await until(
      async () => {
        const count = await page.evaluate(() => {
          const node = document.querySelector('.flat-chat-count');
          return node ? node.textContent.trim() : '';
        });
        return count && count !== '暂无消息' ? count : null;
      },
      {timeout: 90000},
    );
    report.checks.groupChat = await page.evaluate(() => {
      const count = document.querySelector('.flat-chat-count');
      const messages = [...document.querySelectorAll('.flat-msg')].map((n) =>
        n.textContent.replace(/\s+/g, ' ').trim().slice(0, 220),
      );
      return {
        count: count ? count.textContent.trim() : null,
        rendered: messages.length,
        first: messages[0] || null,
        last: messages[messages.length - 1] || null,
      };
    });
    report.checks.groupChat.ready = ready;
    await sleep(1200);
    await shot('05-group-chat');
  }

  // 平台助手
  const assistant = page.locator('#aoWorkspace .flat-conv', {hasText: '平台助手'}).first();
  if (await assistant.isVisible().catch(() => false)) {
    await assistant.click();
    await until(async () => (await page.locator('.flat-assistant-thread').count()) > 0, {timeout: 30000});
    await sleep(1500);
    report.checks.assistant = await page.evaluate(() => {
      const pane = document.querySelector('.flat-assistant');
      if (!pane) return null;
      const facts = [...pane.querySelectorAll('.flat-facts dt, .flat-facts dd')].map((n) =>
        n.textContent.replace(/\s+/g, ' ').trim(),
      );
      const turns = [...pane.querySelectorAll('.flat-assistant-turn')].map((n) =>
        n.textContent.replace(/\s+/g, ' ').trim().slice(0, 260),
      );
      return {facts: facts.slice(0, 16), turns: turns.slice(-4)};
    });
    await shot('06-assistant');
  }

  // 私聊窗口（独立页面）
  const direct = await browser.newPage({viewport: {width: 1200, height: 860}});
  try {
    await direct.goto(`${base}/direct.html`, {waitUntil: 'domcontentloaded'});
    await direct.waitForSelector('#directInput', {timeout: 20000});
    await sleep(2500);
    report.checks.direct = await direct.evaluate(() => ({
      head: document.querySelector('.direct-head')?.textContent.replace(/\s+/g, ' ').trim().slice(0, 300) || null,
      messages: [...document.querySelectorAll('.direct-msg')].map((n) =>
        n.textContent.replace(/\s+/g, ' ').trim().slice(0, 220),
      ),
    }));
    await direct.screenshot({path: path.join(dir, '07-direct.png')});
    report.shots.push(path.join(dir, '07-direct.png'));
  } finally {
    await direct.close();
  }
} catch (error) {
  report.fatal = String(error.message || error);
} finally {
  fs.writeFileSync(outFile, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
}
