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
