// 跑官方办公室的全部冒烟脚本，把失败的都列出来。
//
// 三类结果，必须分开算，否则这个红灯永远没人看：
//   · 通过（退出码 0）
//   · **跳过**（退出码 2）—— 脚本自己说"我这里需要一个已经起好的界面 / 一个真平台"，
//     而本机现在没有。这不是代码的问题，不能算失败；
//   · 失败（其它非零）—— 这才是要看的东西。
//
// 起界面的办法写在 scripts/lib/office-url.mjs 里（跟着 OFFICE_URL / OFFICE_PORT 走）；
// 需要平台的那几份会在自己的输出里说明缺什么。
import { spawn } from 'node:child_process';
import { appendFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
// 调试时只跑一部分：`CI_VERIFY_FILTER=office-model,anchors node scripts/verify.mjs`
// （逗号分隔，按文件名包含匹配）。正式跑（CI）不设它，跑的就是全部。
const filters = String(process.env.CI_VERIFY_FILTER || '')
  .split(',')
  .map((part) => part.trim())
  .filter(Boolean);
const files = readdirSync(here)
  .filter((name) => name.startsWith('verify-') && name.endsWith('.mjs'))
  .filter((name) => !filters.length || filters.some((part) => name.includes(part)))
  .sort();
const failed = [];
const skipped = [];
for (const file of files) {
  // 退出码要拿，跳过的**原因**也要拿（它打印在子进程输出里）。所以这里不再
  // 直接继承 stdio，而是接住输出原样转发，同时留一份用于提取原因行。
  const outcome = await new Promise((resolve) => {
    const child = spawn(process.execPath, [join(here, file)], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '';
    const pump = (stream, target) => {
      stream.setEncoding('utf8');
      stream.on('data', (chunk) => {
        output += chunk;
        target.write(chunk);
      });
    };
    pump(child.stdout, process.stdout);
    pump(child.stderr, process.stderr);
    // 单个脚本挂死不能把整个 job 拖到超时：5 分钟熔断，记失败并点名。
    const fuse = setTimeout(() => {
      console.error(`${file}: 超过 5 分钟无退出，熔断记失败`);
      child.kill('SIGKILL');
    }, 300000);
    child.on('exit', (value) => {
      clearTimeout(fuse);
      resolve({code: value ?? 1, output});
    });
  });
  if (outcome.code === 2) {
    // 跳过必须自己说出缺什么：`<脚本>: 跳过 —— <原因>`（见 lib/office-url.mjs）。
    const match = /:\s*跳过 ——\s*(.+)/.exec(outcome.output);
    skipped.push({file, reason: match ? match[1].trim() : '（脚本没有说明原因）'});
  } else if (outcome.code !== 0) failed.push(file);
}

const executed = files.length - skipped.length;
const passed = executed - failed.length;
console.log(`\n${passed}/${executed} 通过${skipped.length ? `，${skipped.length} 个跳过（未执行，不计为通过）` : ''}，共 ${files.length} 个脚本`);
console.log(`SUMMARY passed=${passed} failed=${failed.length} skipped=${skipped.length} executed=${executed} total=${files.length}`);
if (skipped.length) {
  console.log('跳过（未执行）：');
  for (const {file, reason} of skipped) console.log(`  - ${file} —— ${reason}`);
}
if (failed.length) {
  console.log('失败：' + failed.join(', '));
}

// CI 的 step summary：绿勾不能掩盖「有脚本根本没跑」。只在 GitHub Actions
// 设置了这个路径时写，本地跑不受影响；写失败也不改变校验结论。
const summaryPath = process.env.GITHUB_STEP_SUMMARY;
if (summaryPath) {
  const lines = [
    '### Agent Office 冒烟校验',
    '',
    `- 通过：${passed} / 执行 ${executed}`,
    `- 失败：${failed.length}`,
    `- 跳过：${skipped.length} —— 本次跳过 ${skipped.length} 项、未执行`,
    '',
    `> 本次共 ${files.length} 个脚本，其中 ${skipped.length} 项因缺环境未执行；跳过的脚本退出码为 2，不计为通过。`,
  ];
  if (skipped.length) {
    lines.push('', '跳过清单（未执行）：');
    for (const {file, reason} of skipped) lines.push(`- \`${file}\` —— ${reason}`);
  }
  if (failed.length) {
    lines.push('', '失败清单：' + failed.join(', '));
  }
  try {
    appendFileSync(summaryPath, lines.join('\n') + '\n', 'utf8');
  } catch (error) {
    console.log(`写 GITHUB_STEP_SUMMARY 失败（不影响校验结论）：${error.message}`);
  }
}

if (failed.length) {
  process.exit(1);
}
