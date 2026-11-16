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
    // 只取**本次 fetch 调用**（括号配平切片）：±N 字符窗口会借到相邻调用的
    // URL 与 status（LF 行尾下窗口覆盖更多行，曾把 harness 探测 POST 与隔壁
    // harnesses/status 的 GET 拼成假违规）。
    const callStart = text.lastIndexOf('fetch(', match.index);
    if (callStart < 0) continue;
    let depth = 1;
    let end = callStart + 6;
    for (let i = callStart + 6; i < text.length; i++) {
      if (text[i] === '(') depth++;
      else if (text[i] === ')') {
        depth--;
        if (depth === 0) {
          end = i;
          break;
        }
      }
    }
    const call = text.slice(callStart, end + 1);
    if (/\/tasks\//.test(call) && /status/.test(call)) {
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
