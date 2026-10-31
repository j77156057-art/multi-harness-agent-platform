// 界面的地址：一处说清"这些脚本要连哪个办公室"。
//
// 为什么要有这个文件：八份脚本各自把地址写死成 `http://127.0.0.1:5173`，
// 而界面端口早就改成 4174（`vite.config.js` 取 `OFFICE_PORT`，默认 4174）。
// 于是 `npm run verify` 里这八份**必然失败** —— 失败原因跟被测代码一点关系都没有，
// 久了就没人看这个红灯了。地址只留一份，端口跟着 `OFFICE_PORT` 走。
//
// 优先级：`OFFICE_URL`（显式给整条地址）> `OFFICE_PORT` > 4174。
export function officeUrl(explicit) {
  const given = String(explicit || '').trim() || String(process.env.OFFICE_URL || '').trim();
  if (given) return given.replace(/\/+$/, '');
  const port = String(process.env.OFFICE_PORT || '4174').trim() || '4174';
  return `http://127.0.0.1:${port}`;
}

/**
 * 这个地址上有没有真的界面在跑。
 *
 * 这些脚本要的是**一个已经起好的办公室**（它们点真实界面、读真实平台数据）。
 * 没起的时候不该报"校验失败"，而该说清"先起界面" —— 所以调用方拿到 false
 * 就跳过，退出码 2（`scripts/verify.mjs` 把 2 记成"跳过"而不是"失败"）。
 */
export async function officeReachable(url = officeUrl(), timeoutMs = 2500) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * 需要界面却连不上时统一走这里：一行说清怎么办，然后退出码 2。
 *
 * `explicit` 是各脚本自己的覆盖变量（`OFFICE_UI_URL` 之类）—— 保留它们，
 * 免得把已经写好的用法作废。
 */
export async function requireOffice(scriptName, explicit) {
  const url = officeUrl(explicit);
  if (await officeReachable(url)) return url;
  console.log(
    `${scriptName}: 跳过 —— ${url} 上没有界面在跑。先起一个：` +
      '.\\start-demo.bat（或 office 目录下 npm run dev，端口用 OFFICE_PORT 改）',
  );
  process.exit(2);
}

/**
 * 跳过：环境/数据不够，不是代码坏了。
 *
 * 为什么单独一个函数：跳过要被**说出来**（一行，带原因），不是静悄悄 return 0。
 * 一个看起来"全绿"的校验比红灯更危险 —— 它让人以为验过了。
 *
 * 实现上为什么抛异常而不是 process.exit()：刚发过 fetch 的进程里还有没关的
 * socket / 定时器，Windows 上 `process.exit()` 抢跑会撞进 libuv 的退出期断言
 * （`!(handle->flags & UV_HANDLE_CLOSING)`，src\win\async.c:76），退出码变成
 * 0xC0000409 —— 于是"跳过 2"被伪装成"崩溃"。抛一个哨兵让本轮模块求值中止，
 * 退出码交给下面的 uncaughtException 处理，事件循环自然收尾。
 */
class Skipped extends Error {}

process.on("uncaughtException", (error) => {
  if (error instanceof Skipped) {
    process.exitCode = 2;
    return;
  }
  console.error(error);
  process.exitCode = 1;
});

export function skip(scriptName, reason) {
  console.log(`${scriptName}: 跳过 —— ${reason}`);
  throw new Skipped(reason);
}

/** 通过办公室读平台上的任务（脚本要"真有一条活"时才用得着）。 */
export async function tasksOf(base, timeoutMs = 15000) {
  try {
    const response = await fetch(`${base}/api/formal/tasks`, {
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) return null;
    const document = await response.json();
    return Array.isArray(document.tasks) ? document.tasks : null;
  } catch {
    return null;
  }
}

/**
 * 这几屏要的是「这个项目里**真有活**」。
 *
 * 空项目上它们等不到任务行，超时以失败告终 —— 那不是代码坏了，是数据没到。
 * 所以先问一声平台：没任务就跳过，并说明跳过的原因是"没有任务"。
 */
export async function requireTasks(base, scriptName) {
  const tasks = await tasksOf(base);
  if (tasks === null) {
    skip(scriptName, '读不到平台的任务（后端没连上，或这个界面没配项目/分组）');
  }
  if (!tasks.length) {
    skip(scriptName, '这个项目里一条任务都没有（先批准一份方案，让它下发出任务）');
  }
  return tasks;
}

/**
 * 大厅那一列「平台上已有的项目」要的是**能归堆的任务**。
 *
 * 那一列是按任务标题的前缀（`项目名 · 任务名`）归出来的 —— 平台上有一百条活，
 * 但标题里都不带「 · 」，这一列仍然是空的。判据就得是这条，而不是"有没有任务"。
 */
export async function requireProjectGroups(base, scriptName) {
  const tasks = await requireTasks(base, scriptName);
  const grouped = tasks.filter((task) => String(task.title || '').includes(' · '));
  if (!grouped.length) {
    skip(
      scriptName,
      '平台上的任务标题都不带「项目名 · 」前缀，大厅里归不出项目（这是数据口径，不是代码）',
    );
  }
  return grouped;
}

/**
 * 这一屏等的是「本地这个项目里真有任务」。
 *
 * 为什么不能只查平台：办公室在浏览器里按**项目范围**过滤任务，平台上有一百条活、
 * 当前项目名字对不上也还是一条都看不到。所以判据落在这一屏自己会不会出现任务行 ——
 * 等不到就跳过（说清是"这个项目里没有任务"），而不是让整轮判死。
 */
export async function waitForTasksOrSkip(page, scriptName, { selector = '#aoWorkspace .flat-task', timeout = 20000 } = {}) {
  try {
    await page.waitForSelector(selector, { timeout });
  } catch {
    skip(scriptName, '这个项目里没有任务（换一个有活的项目，或先批准一份方案）');
  }
}
