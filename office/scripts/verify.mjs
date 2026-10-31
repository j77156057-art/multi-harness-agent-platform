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
import { readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const files = readdirSync(here).filter((name) => name.startsWith('verify-') && name.endsWith('.mjs')).sort();
const failed = [];
const skipped = [];
for (const file of files) {
  const code = await new Promise((resolve) => {
    const child = spawn(process.execPath, [join(here, file)], { stdio: 'inherit' });
    child.on('exit', (value) => resolve(value ?? 1));
  });
  if (code === 2) skipped.push(file);
  else if (code !== 0) failed.push(file);
}

const ran = files.length - skipped.length;
console.log(`\n${ran - failed.length}/${ran} 通过${skipped.length ? `，${skipped.length} 个跳过（缺环境）` : ''}，共 ${files.length} 个脚本`);
if (skipped.length) {
  console.log('跳过（先起界面/平台再跑）：' + skipped.join(', '));
}
if (failed.length) {
  console.log('失败：' + failed.join(', '));
  process.exit(1);
}
