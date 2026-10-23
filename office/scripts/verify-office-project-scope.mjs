// 办公室的**项目范围**：任务表只属于当前项目。
//
// 平台的「项目」后端其实是一个 group：同一 group 下并列多个前端项目
// （GAME-COLLAB、UI-ISOLATION-…）。办公室的正式读取如果把整群的任务都摆出来，
// 别的项目的派发记录会整段混进来。
//
// 断言：
//   1. 打开一个名字与群里某个项目一致的项目 → 任务表里只有那个项目的任务；
//   2. 打开一个与谁都不匹配的项目 → 0 条任务（不泄漏别处的历史）。
import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const BASE = process.env.OFFICE_URL || 'http://127.0.0.1:4174';

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(BASE + '/');

  const platform = await (await fetch(`${BASE}/api/formal/tasks`)).json();
  const groupTasks = platform.tasks || [];
  assert.ok(groupTasks.length > 1, 'the group has several projects to mix up');

  // 一个真实存在的项目名：取群任务标题里出现最多的那个前缀。
  const prefixes = new Map();
  for (const task of groupTasks) {
    const title = String(task.title || '');
    const cut = title.indexOf(' · ');
    if (cut < 0) continue;
    const prefix = title.slice(0, cut);
    prefixes.set(prefix, (prefixes.get(prefix) || 0) + 1);
  }
  const known = [...prefixes.entries()].sort((a, b) => b[1] - a[1])[0][0];
  const knownTaskIds = groupTasks
    .filter(task => String(task.title || '').startsWith(`${known} · `))
    .map(task => task.task_id);
  assert.ok(knownTaskIds.length > 0, 'the known project owns at least one task');

  const openProject = async (project) => {
    await page.evaluate(([key, activeKey, value]) => {
      localStorage.setItem(key, JSON.stringify([value]));
      localStorage.setItem(activeKey, value.id);
    }, ['agent-office.projects.v1', 'agent-office.active-project.v1', project]);
    await page.reload();
    const card = page.locator('.project-card', { hasText: project.name }).first();
    await card.waitFor({ state: 'visible' });
    await card.locator('.project-card-open').click();
    await page.waitForSelector('#aoWorkspace .flat-task', { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(1200);
  };

  // 1. 项目一致 → 只看到它的任务。
  await openProject({
    id: 'project-scope-known',
    index: '90',
    name: known,
    createdAt: '2026/10/22 00:00:00',
    phase: 'scene',
    source: 'platform',
    members: [],
    taskIds: knownTaskIds,
  });
  const shown = await page.locator('#aoWorkspace .flat-task').count();
  assert.ok(shown > 0, 'the known project shows its tasks');
  assert.ok(shown <= knownTaskIds.length, 'no task from another project leaks in');

  // 2. 名字对不上 → 0 条。
  await openProject({
    id: 'project-scope-none',
    index: '91',
    name: '一个跟谁都不匹配的项目',
    createdAt: '2026/10/22 00:00:00',
    phase: 'scene',
    source: 'platform',
    members: [],
    taskIds: [],
  });
  const empty = await page.locator('#aoWorkspace .flat-task').count();
  assert.equal(empty, 0, 'an unrelated project shows no tasks');
  const emptyNote = await page.locator('#aoWorkspace .flat-empty').first().textContent();
  assert.ok(emptyNote && emptyNote.includes('还没有任务'), 'the empty state says so');

  assert.deepEqual(errors, [], 'the page raised no errors');
  console.log('verify-office-project-scope: ok');
} finally {
  await browser.close();
}
