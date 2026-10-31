// 私聊与平台助手：真发一句、真等回话，把回话原文记下来。
//
// 用法：$env:OFFICE_URL='http://127.0.0.1:4174'; node scripts/verify-collab-talk.mjs
import fs from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';
import { requireOffice, requireTasks } from './lib/office-url.mjs';

const base = await requireOffice('verify-collab-talk.mjs');
const outFile = process.env.OFFICE_TALK_OUT || path.join(process.env.TEMP || '.', 'collab-talk.json');
const assistantQuestion =
  process.env.TALK_ASSISTANT_QUESTION ||
  '这个项目现在一共多少条任务？分别是什么状态？有没有哪条任务是同一个窗口自己做完又自己审的？';
const adminQuestion =
  process.env.TALK_ADMIN_QUESTION ||
  '第 3 批那五条复盘任务现在到哪一步了？已经合入了几条、还在等审的几条是谁的？';
const directQuestion =
  process.env.TALK_DIRECT_QUESTION ||
  '用一句话说说这个项目现在最大的问题是什么？';

const browser = await chromium.launch();
const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));

const result = {at: new Date().toISOString(), office: base, results: {}, errors};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function openProject() {
  await page.goto(base, {waitUntil: 'domcontentloaded'});
  await requireTasks(base, 'verify-collab-talk.mjs');
  await page.waitForSelector('.project-platform-open', { timeout: 30000 });
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
  if (!(await openProject())) throw new Error('进不去项目工作区');

  // ---------------------------------------------------------- 平台助手
  {
    const entry = {question: assistantQuestion, sent: false, reply: null, waitedSeconds: 0};
    const conv = page.locator('#aoWorkspace .flat-conv', {hasText: '平台助手'}).first();
    await conv.click();
    await page.waitForSelector('#flatAssistantQuestion', {timeout: 15000});
    // 等**对方**的新回话：我自己那条不算（上一轮只等列表变长，结果抓到了旧消息）。
    const before = await page.locator('.flat-assistant-turn.is-assistant').count();
    await page.fill('#flatAssistantQuestion', assistantQuestion);
    await page.click('[data-act="assistant-send"]');
    entry.sent = true;
    const started = Date.now();
    // 助手的回话要过一次模型：轮询等，别写死 sleep。
    while (Date.now() - started < 240000) {
      await sleep(3000);
      const replies = await page.evaluate(() =>
        [...document.querySelectorAll('.flat-assistant-turn.is-assistant')].map((n) =>
          n.textContent.replace(/\s+/g, ' ').trim(),
        ),
      );
      if (replies.length > before) {
        entry.reply = replies[replies.length - 1];
        entry.turns = await page.locator('.flat-assistant-turn').count();
        break;
      }
    }
    entry.waitedSeconds = Math.round((Date.now() - started) / 1000);
    result.results.assistant = entry;
  }

  // ---------------------------------------------------------- 与管理员对话（定向私聊）
  {
    const entry = {question: adminQuestion, sent: false, reply: null, waitedSeconds: 0};
    await page.click('[data-act="open-admin-chat"]');
    await page.waitForSelector('#flatAdminChat[open]', {timeout: 10000});
    const before = await page.locator('#flatAdminChatList .flat-thread-msg:not(.is-mine)').count();
    await page.fill('#flatAdminChatInput', adminQuestion);
    await page.click('#flatAdminChatForm button[type="submit"]');
    entry.sent = true;
    const started = Date.now();
    while (Date.now() - started < 240000) {
      await sleep(3000);
      const replies = await page.evaluate(() =>
        [...document.querySelectorAll('#flatAdminChatList .flat-thread-msg:not(.is-mine)')].map((n) => ({
          who: (n.querySelector('b') || {}).textContent || '',
          text: n.textContent.replace(/\s+/g, ' ').trim(),
        })),
      );
      if (replies.length > before) {
        const last = replies[replies.length - 1];
        entry.reply = last.text;
        entry.answeredBy = last.who;
        entry.messages = await page.locator('#flatAdminChatList .flat-thread-msg').count();
        break;
      }
    }
    entry.waitedSeconds = Math.round((Date.now() - started) / 1000);
    await page.evaluate(() => document.querySelector('#flatAdminChat')?.close());
    result.results.adminChat = entry;
  }

  // ---------------------------------------------------------- 私聊窗口页
  {
    const entry = {question: directQuestion, sent: false, reply: null, waitedSeconds: 0};
    const direct = await browser.newPage({viewport: {width: 1200, height: 860}});
    try {
      await direct.goto(`${base}/direct.html`, {waitUntil: 'domcontentloaded'});
      await direct.waitForSelector('#directInput', {timeout: 15000});
      const before = await direct.locator('.direct-msg:not(.is-mine)').count();
      await direct.fill('#directInput', directQuestion);
      await direct.click('#directForm button[type="submit"]');
      entry.sent = true;
      const started = Date.now();
      while (Date.now() - started < 240000) {
        await sleep(3000);
        const replies = await direct.evaluate(() =>
          [...document.querySelectorAll('.direct-msg:not(.is-mine)')].map((n) => ({
            who: (n.querySelector('b') || {}).textContent || '',
            text: n.textContent.replace(/\s+/g, ' ').trim(),
          })),
        );
        if (replies.length > before) {
          const last = replies[replies.length - 1];
          entry.reply = last.text;
          entry.answeredBy = last.who;
          entry.messages = await direct.locator('.direct-msg').count();
          break;
        }
      }
      entry.waitedSeconds = Math.round((Date.now() - started) / 1000);
    } finally {
      await direct.close();
    }
    result.results.directPage = entry;
  }
} catch (error) {
  result.fatal = String(error.message || error);
} finally {
  fs.writeFileSync(outFile, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
}
