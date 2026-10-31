// 办公室页面的**版式回归**：三列各安其位，谁也不许压到谁身上。
//
// 踩过的坑：群聊对话台里的消息被拉高（flex 默认 stretch），整条对话台溢出到
// 上面，把任务表和详情盖住了 —— 看上去像"都叠在一起"。这个脚本盯住：
//   1. 任务表 / 详情 / 右栏 / 对话台四块互不重叠；
//   2. 对话台里的内容不越出对话台；
//   3. 详情里的文字不横向溢出自己的盒子（路径这种长串不许撑破）；
//   4. 页面不出现横向滚动条。
//
// 只读脚本：不建任务、不发消息。
import { chromium } from 'playwright';
import { requireOffice } from './lib/office-url.mjs';
import assert from 'node:assert/strict';

const BASE = await requireOffice('verify-office-layout.mjs', process.env.OFFICE_URL);
const WIDTHS = [1600, 1200, 900];

const browser = await chromium.launch({ headless: true });
try {
  for (const width of WIDTHS) {
    const page = await browser.newPage({ viewport: { width, height: 950 } });
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));

    // 新上下文没有本机项目历史，注入一个（跟群里某个项目同名，才会看到它的任务）
    const platform = await (await fetch(`${BASE}/api/formal/tasks`)).json();
    const groupTasks = platform.tasks || [];
    const prefixes = new Map();
    for (const t of groupTasks) {
      const title = String(t.title || '');
      const cut = title.indexOf(' · ');
      if (cut < 0) continue;
      const prefix = title.slice(0, cut);
      prefixes.set(prefix, (prefixes.get(prefix) || 0) + 1);
    }
    const known = [...prefixes.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || '本地项目';
    const taskIds = groupTasks
      .filter((t) => String(t.title || '').startsWith(`${known} · `))
      .map((t) => t.task_id);
    await page.addInitScript(([knownName, ids]) => {
      const project = {
        id: 'verify-layout',
        index: '71',
        name: knownName,
        createdAt: '2026/10/22 00:00:00',
        phase: 'scene',
        source: 'platform',
        members: [],
        taskIds: ids,
      };
      localStorage.setItem('agent-office.projects.v1', JSON.stringify([project]));
      localStorage.setItem('agent-office.active-project.v1', project.id);
    }, [known, taskIds]);

    await page.goto(BASE);
    await page.waitForSelector('.project-card-open', { timeout: 30000 });
    await page.locator('.project-card-open').first().click();
    await page.waitForSelector('#aoWorkspace .flat-task', { timeout: 30000 });
    await page.waitForTimeout(1500);

    const report = await page.evaluate(() => {
      const ws = document.querySelector('#aoWorkspace');
      const rect = (sel) => {
        const el = ws.querySelector(sel);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, w: r.width, h: r.height };
      };
      const boxes = {
        tasks: rect('.flat-tasks'),
        detail: rect('.flat-detail'),
        rail: rect('.flat-rail'),
        chat: rect('.flat-chat'),
      };
      // 对话台里的内容不许越出对话台
      const chat = boxes.chat;
      const chatList = ws.querySelector('.flat-chat-list');
      const listRect = chatList ? chatList.getBoundingClientRect() : null;
      // 对话台折叠时消息列表是隐藏的（display:none），没有实形就不能报溢出。
      const listVisible = Boolean(listRect && listRect.width > 0 && listRect.height > 0);
      const listBleed = chat && listRect
        ? {
            visible: listVisible,
            above: Math.round(chat.y - listRect.y),
            below: Math.round(listRect.bottom - chat.bottom),
            left: Math.round(chat.x - listRect.x),
            right: Math.round(listRect.right - chat.right),
          }
        : null;
      // 详情里的文字横向溢出（路径、长 JSON）
      const spill = [];
      for (const el of ws.querySelectorAll('.flat-detail *')) {
        if (!el.textContent || !el.textContent.trim()) continue;
        const cs = getComputedStyle(el);
        if (cs.overflowX !== 'visible') continue;
        if (el.scrollWidth > el.clientWidth + 2) {
          spill.push({
            cls: (el.className || el.tagName).toString().slice(0, 40),
            sw: el.scrollWidth,
            cw: el.clientWidth,
          });
        }
      }
      return {
        boxes,
        listBleed,
        spill: spill.slice(0, 6),
        pageScrollW: document.documentElement.scrollWidth,
        viewW: window.innerWidth,
      };
    });

    // 1. 四块互不重叠
    const names = Object.keys(report.boxes).filter((k) => report.boxes[k] && report.boxes[k].w > 1 && report.boxes[k].h > 1);
    for (let i = 0; i < names.length; i++) {
      for (let j = i + 1; j < names.length; j++) {
        const a = report.boxes[names[i]];
        const b = report.boxes[names[j]];
        const ox = Math.min(a.right, b.right) - Math.max(a.x, b.x);
        const oy = Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y);
        assert.ok(
          !(ox > 2 && oy > 2),
          `${width}px: ${names[i]} 与 ${names[j]} 压在一起（${Math.round(ox)}x${Math.round(oy)}）`,
        );
      }
    }

    // 2. 对话台的内容不越界。
    //    群聊现在是左栏里的一个会话，要切过去才看得到 —— 这一段在
    //    切到群聊之后单独测（见下面的第二遍）。
    if (report.listBleed && report.listBleed.visible) {
      const b = report.listBleed;
      assert.ok(b.above <= 2, `${width}px: 对话台的内容往上溢出了 ${b.above}px`);
      assert.ok(b.below <= 2, `${width}px: 对话台的内容往下溢出了 ${b.below}px`);
    }

    // 2b. 切到群聊会话：对话台在中间那栏，内容同样不许越界。
    const chatConv = page.locator('#aoWorkspace .flat-conv', { hasText: '项目群聊' }).first();
    if (await chatConv.count()) {
      await chatConv.click();
      await page.waitForSelector('#aoWorkspace .flat-chat', { timeout: 20000 });
      await page.waitForTimeout(800);
      const bleed = await page.evaluate(() => {
        const ws = document.querySelector('#aoWorkspace');
        const chat = ws.querySelector('.flat-chat');
        const list = ws.querySelector('.flat-chat-list');
        if (!chat || !list) return null;
        const c = chat.getBoundingClientRect();
        const l = list.getBoundingClientRect();
        return {
          visible: l.width > 0 && l.height > 0,
          above: Math.round(c.y - l.y),
          below: Math.round(l.bottom - c.bottom),
        };
      });
      if (bleed && bleed.visible) {
        assert.ok(bleed.above <= 2, `${width}px: 群聊内容往上溢出了 ${bleed.above}px`);
        assert.ok(bleed.below <= 2, `${width}px: 群聊内容往下溢出了 ${bleed.below}px`);
      }
    }

    // 3. 详情里的文字不横向撑破盒子
    assert.deepEqual(report.spill, [], `${width}px: 详情里有文字横向溢出`);

    // 4. 页面不出横向滚动条
    assert.ok(
      report.pageScrollW <= report.viewW + 1,
      `${width}px: 页面出现横向滚动条（${report.pageScrollW} > ${report.viewW}）`,
    );

    assert.deepEqual(errors, [], `${width}px: 页面报错`);
    await page.close();
  }
  console.log('verify-office-layout: ok');
} finally {
  await browser.close();
}
