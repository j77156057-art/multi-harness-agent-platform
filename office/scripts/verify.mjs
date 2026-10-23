// 跑官方办公室的全部冒烟脚本，把失败的都列出来。
//
// 脚本依赖本机的正式平台（vite preview 在 4174）与一个可查询的任务群；
// 没有平台时这些脚本会失败，不是代码的问题。
import { spawn } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const files = readdirSync(here).filter((name) => name.startsWith('verify-') && name.endsWith('.mjs')).sort();
const failed = [];
for (const file of files) {
  const code = await new Promise((resolve) => {
    const child = spawn(process.execPath, [join(here, file)], { stdio: 'inherit' });
    child.on('exit', (value) => resolve(value ?? 1));
  });
  if (code !== 0) failed.push(file);
}
console.log(`\n${files.length - failed.length}/${files.length} 通过`);
if (failed.length) {
  console.log('失败：' + failed.join(', '));
  process.exit(1);
}
