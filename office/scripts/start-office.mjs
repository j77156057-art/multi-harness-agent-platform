// 本地开发：起 vite dev server（网关插件在 vite.config.js 里接上）。
// 以前这个脚本还会顺手跑 detect-gpu 与 cyber-eggs（3D 小样那套），场景撤了以后
// 那些东西没有了，这里只留开发服务器本身。
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const vite = spawn(
  process.execPath,
  [path.join(root, 'node_modules', 'vite', 'bin', 'vite.js'), '--host', '127.0.0.1', ...process.argv.slice(2)],
  { cwd: root, stdio: 'inherit', windowsHide: true },
);

const stop = (signal) => {
  if (!vite.killed) vite.kill(signal);
  process.exit(0);
};
process.on('SIGINT', () => stop('SIGINT'));
process.on('SIGTERM', () => stop('SIGTERM'));
vite.on('exit', (code) => process.exit(code ?? 0));
