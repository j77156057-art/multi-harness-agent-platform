// 审核的归属：办公室前端**不碰**审核。
//
// `FORMAL-MULTI-HARNESS-02` 的第 4 条完成标准说「前端审核操作通过 API，不能直接
// 修改任务状态」。这里的现状是：办公室这一侧**根本没有审核操作** —— 审核（领取
// review / 提交结论）是**管理员窗口**的动作，走平台的 `/api/v1/human/review/*`。
// 办公室只做只读展示与转述（`admin-agent.js` 里那句话就是这么写的）。
//
// 钉住三件事，将来谁在前端加一个「直接改状态」的按钮，这条会红：
//   1. 办公室前端不调用审核端点；
//   2. 办公室前端不向任务发写请求（只读 /tasks/…）；
//   3. 那句「审核是管理员窗口的动作」的说明还在。
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Locate the office sources **relative to this script** -- verify.mjs runs the
// checks from its own working directory, so `path.resolve('src')` would point
// somewhere else entirely.
const here = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.resolve(here, '..', 'src');
const files = [];
(function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) walk(full);
    else if (entry.endsWith('.js')) files.push(full);
  }
})(srcRoot);

const problems = [];
let sawOwnershipNote = false;

for (const file of files) {
  const text = readFileSync(file, 'utf8');
  const rel = path.relative(process.cwd(), file);

  if (text.includes('human/review')) {
    problems.push(`${rel} 调用了审核端点 human/review —— 审核是管理员窗口的动作`);
  }

  // 写请求里的 /tasks/ 路径：办公室不该改任务状态。
  const writes = [...text.matchAll(/method:\s*['"](POST|PUT|PATCH)['"]/g)];
  for (const match of writes) {
    const around = text.slice(Math.max(0, match.index - 400), match.index + 400);
    if (/\/tasks\/|base-harnesses|harnesses/.test(around) && /status/.test(around)) {
      problems.push(`${rel} 有一个带任务状态的写请求 —— 状态只能由平台改`);
    }
  }

  if (text.includes('审核（领取 / 提交结论）是管理员窗口的动作')) sawOwnershipNote = true;
}

if (!sawOwnershipNote) {
  problems.push('admin-agent.js 里「审核是管理员窗口的动作」这句说明不见了');
}

if (problems.length) {
  console.error(JSON.stringify({ ok: false, problems }, null, 1));
  process.exit(1);
}
console.log(JSON.stringify({ ok: true, files: files.length }, null, 1));
