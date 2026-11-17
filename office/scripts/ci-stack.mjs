// CI 里把真后端 + 真办公室起起来，再跑 verify。
//
// 以前 CI 只 `npm run build`，所有需要界面的脚本都退出码 2（跳过），于是
// 「通过的 37 个」掩盖了「27 个根本没跑」。这个脚本按 start-demo 的顺序把
// 栈真正拉起来：
//   1. 临时 data 目录、canonical 仓库、独立 HOME（模型配置不碰 runner 的真实配置）
//   2. python -m app.main（enforce 鉴权）
//   3. python -m app.tools.seed_ci（项目/分组/方案/任务/成员窗口/模型 stub）
//   4. vite dev server
//   5. node scripts/verify.mjs
// 失败或中断都会杀掉两个子进程并删掉临时目录。
import { spawn } from 'node:child_process';
import { chmodSync, copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const officeRoot = resolve(here, '..');
const repoRoot = resolve(officeRoot, '..');
const backendDir = join(repoRoot, 'backend');

const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const backendPort = process.env.CI_BACKEND_PORT || '8848';
const officePort = process.env.CI_OFFICE_PORT || '4174';
const base = `http://127.0.0.1:${backendPort}`;
const officeUrl = `http://127.0.0.1:${officePort}`;

// 端口预检：被占时直接报「谁占的、怎么办」，不起栈。否则后端 bind 失败而
// waitFor 会摸到占端口者的响应，seed 再报 no database 这类二手症状
// （2026-11-17 常驻走查栈占 8848 真撞过一次）。
function portBusy(port) {
  return new Promise((resolveBusy) => {
    const sock = net.connect({ port: Number(port), host: '127.0.0.1' });
    const done = (busy) => {
      sock.destroy();
      resolveBusy(busy);
    };
    sock.once('connect', () => done(true));
    sock.once('error', () => done(false));
    setTimeout(() => done(false), 1500);
  });
}
for (const [label, port, envName] of [
  ['backend', backendPort, 'CI_BACKEND_PORT'],
  ['office', officePort, 'CI_OFFICE_PORT'],
]) {
  if (await portBusy(port)) {
    console.error(
      `[ci] ${label} 端口 ${port} 已被占用：先停掉占它的进程` +
        `（netstat -ano | findstr :${port}），或设 ${envName} 换端口。` +
        '不在被占端口上起栈。',
    );
    process.exit(1);
  }
}

const dataDir = mkdtempSync(join(tmpdir(), 'ap-ci-'));
const modelHome = join(dataDir, 'home');
const tokens = join(dataDir, 'tokens');
mkdirSync(modelHome, { recursive: true });
mkdirSync(tokens, { recursive: true });

// CLI stub：内置额度槽（opencode/codebuddy/qoder）要求「二进制可执行 + store 可读」，
// CI runner 两样都没有，额度池相关的冒烟会等一份永远不来的模型清单。补 stub 并置于
// PATH 头部以 shadow 开发机真 CLI，让本地与 CI 走同一条发现路径。store 由 seed_ci 播种。
const binDir = join(dataDir, 'bin');
const xdgDir = join(dataDir, 'xdg');
mkdirSync(binDir, { recursive: true });
mkdirSync(xdgDir, { recursive: true });
const cliStubs = {
  opencode: {
    sh: '#!/bin/sh\nif [ "$1" = "models" ]; then printf \'opencode/minimax-m3.1-flash-free\\nopencode/beam-free\\nopencode/gpt-free\\nopencode/paid-pro\\n\'; fi\nexit 0\n',
    cmd: '@echo off\r\nif "%~1"=="models" (\r\n  echo opencode/minimax-m3.1-flash-free\r\n  echo opencode/beam-free\r\n  echo opencode/gpt-free\r\n  echo opencode/paid-pro\r\n)\r\nexit /b 0\r\n',
  },
  codebuddy: {
    sh: '#!/bin/sh\necho "Currently supported: (hy3, minimax-m3.1-flash)"\nexit 0\n',
    cmd: '@echo off\r\necho Currently supported: (hy3, minimax-m3.1-flash)\r\nexit /b 0\r\n',
  },
  qoderclicn: {
    sh: '#!/bin/sh\nif [ "$1" = "--list-models" ]; then printf \'Qwen3.8-Flash\\nDeepSeek-Flash\\n\'; fi\nexit 0\n',
    cmd: '@echo off\r\nif "%~1"=="--list-models" (\r\n  echo Qwen3.8-Flash\r\n  echo DeepSeek-Flash\r\n)\r\nexit /b 0\r\n',
  },
};
for (const [name, stub] of Object.entries(cliStubs)) {
  if (process.platform === 'win32') {
    writeFileSync(join(binDir, `${name}.cmd`), stub.cmd);
  } else {
    const file = join(binDir, name);
    writeFileSync(file, stub.sh);
    chmodSync(file, 0o755);
  }
}

const env = {
  ...process.env,
  PATH: binDir + delimiter + (process.env.PATH || ''),
  XDG_DATA_HOME: xdgDir,
  // 发现上游读 Path.home() 下的各家 CLI 配置；不隔离 HOME，开发机真配置会
  // 掩盖 CI 的缺口（upstream-tier/review-gate 要的 opencode 组就来自这里）。
  HOME: modelHome,
  USERPROFILE: modelHome,
  // HOME 隔离后 playwright 默认缓存路径会落空；钉回安装时的真实缓存。
  PLAYWRIGHT_BROWSERS_PATH:
    process.env.PLAYWRIGHT_BROWSERS_PATH ||
    (process.platform === 'win32'
      ? join(process.env.LOCALAPPDATA || join(modelHome, 'AppData', 'Local'), 'ms-playwright')
      : join(process.env.HOME || '~', '.cache', 'ms-playwright')),
  AP_PORT: backendPort,
  AP_AUTH_MODE: 'enforce',
  AP_ADMIN_SUBJECTS: 'codex-window-admin',
  AP_DATA_DIR: dataDir,
  AP_CANONICAL_REPO: join(dataDir, 'canonical.git'),
  AP_WORKTREE_ROOT: join(dataDir, 'wt'),
  AP_EXPORT_DIR: join(dataDir, 'exports'),
  AP_MODEL_HOME: modelHome,
  AP_SWEEPER_ENABLED: '0',
  AP_RUNTIME_AUTO_MERGE: '0',
};

const children = [];
function start(command, args, options) {
  const child = spawn(command, args, { stdio: 'inherit', windowsHide: true, ...options });
  children.push(child);
  return child;
}

function waitForExit(child) {
  return new Promise((resolveExit) => child.on('exit', (code) => resolveExit(code ?? 1)));
}

async function waitFor(url, label, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // not up yet
    }
    await new Promise((done) => setTimeout(done, 500));
  }
  throw new Error(`${label} did not answer at ${url}`);
}

function shutdown(code) {
  for (const child of children) {
    if (!child.killed) {
      try {
        child.kill('SIGTERM');
      } catch {
        // already gone
      }
    }
  }
  try {
    rmSync(dataDir, { recursive: true, force: true });
  } catch {
    // best effort
  }
  process.exit(code);
}

process.on('SIGINT', () => shutdown(130));
process.on('SIGTERM', () => shutdown(143));

try {
  console.log(`[ci] data=${dataDir}`);
  // canonical 仓库：claim 路径要 worktree，但冒烟脚本本身只需要它存在。
  const ensure = spawn(
    python,
    ['-c', 'from app.git.repo import CanonicalRepo; CanonicalRepo.from_settings().ensure(); print("canonical ready")'],
    { cwd: backendDir, env, stdio: 'inherit', windowsHide: true },
  );
  const ensureCode = await waitForExit(ensure);
  if (ensureCode !== 0) throw new Error('canonical repo setup failed');

  // 档位声明要**在起后端之前**写：池子发现上游后有 TTL 缓存，seed 在后端起来
  // 之后再写，界面仍显示代码默认档（opencode:1 显示「按量计费」而不是免费）。
  // 声明的是 stub 链：CI 里它不花钱，但助手只走免费档的实况目录。
  const poolConfigDir = join(dataDir, 'pool');
  mkdirSync(poolConfigDir, { recursive: true });
  writeFileSync(
    join(poolConfigDir, 'config.json'),
    JSON.stringify({ upstream_tiers: [{ name: 'opencode:1', tier: 'free' }] }, null, 2) + '\n',
    'utf8',
  );

  const backend = start(python, ['-m', 'app.main'], { cwd: backendDir, env });
  await waitFor(`${base}/health`, 'backend');

  const seedCode = await waitForExit(
    start(
      python,
      [
        '-m',
        'app.tools.seed_ci',
        '--base',
        base,
        '--data-dir',
        tokens,
        '--project',
        'demo-project',
        '--group',
        'demo-group',
        '--members',
        '2',
        '--model',
        'stub-model',
      ],
      { cwd: backendDir, env },
    ),
  );
  if (seedCode !== 0) throw new Error(`seed failed with ${seedCode}`);

  // 「这个窗口的凭据」那一屏按**本机 data 目录里的 .key 文件**判断"哪条在用"
  // （backend 的 `_credential_files` 只扫 `settings.data_dir/*.key`）。seed 把
  // 所有窗口的凭据都写进了 tokens/ 子目录，于是界面上一条"在用"都没有、
  // 每条都被摆上吊销按钮 —— 那不是代码坏了，是夹具没落在它读的地方。
  // 只把**这一个**管理员窗口（以及操作员）的 key 放进去：其余窗口的凭据保持
  // "仍然有效、本机没有它的 key 文件"，正好是这条校验要区分的两种状态。
  copyFileSync(join(tokens, 'codex.key'), join(dataDir, 'codex.key'));
  copyFileSync(join(tokens, 'office-operator.key'), join(dataDir, 'office-operator.key'));

  const officeEnv = {
    ...env,
    OFFICE_PORT: officePort,
    OFFICE_FORMAL_UPSTREAM: base,
    OFFICE_FORMAL_TOKEN_FILE: join(tokens, 'office-operator.key'),
    OFFICE_FORMAL_ADMIN_TOKEN_FILE: join(tokens, 'codex.key'),
    OFFICE_FORMAL_MEMBER_TOKEN_FILE: join(tokens, 'codex.key'),
    OFFICE_FORMAL_MEMBER_ID: 'codex',
    OFFICE_FORMAL_PROJECT: 'demo-project',
    OFFICE_FORMAL_GROUP: 'demo-group',
  };
  start(process.execPath, [join(officeRoot, 'scripts', 'start-office.mjs')], {
    cwd: officeRoot,
    env: officeEnv,
  });
  await waitFor(`${officeUrl}/api/formal/status`, 'office');

  // verify-harness-connections 要本机 Ollama 有 qwen3:4b。CI 没有 GPU/模型，
  // 起一个只回名册的 stub，让「端点连通」这条真的被执行，而不是跳过。
  const ollama = createServer((request, response) => {
    const body =
      request.url?.startsWith('/api/tags')
        ? { models: [{ name: 'qwen3:4b', model: 'qwen3:4b' }] }
        : { response: 'ok', done: true, message: { content: 'ok' } };
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify(body));
  });
  // 本机开着真 Ollama 时 11434 是占着的：那不是错误，直接用现成的（CI runner 上
  // 端口是空的，才需要这个 stub）。别让 EADDRINUSE 把整个栈带崩。
  let ollamaServing = false;
  ollama.on('error', (error) => {
    if (error.code === 'EADDRINUSE') {
      console.log('[ci] 11434 已被本机 Ollama 占用，直接用现成的');
      return;
    }
    throw error;
  });
  ollama.listen(11434, '127.0.0.1', () => {
    ollamaServing = true;
  });

  // 助手回话走 pool 免费档 → opencode:1 上游（seed 的 baseURL 8402）。CI 没有
  // 真链：补一个 OpenAI 兼容 stub，让 collab-talk「真等回话」真的被执行；
  // 8402 被本机真链占着时直接用现成的（与 ollama 同款容忍）。
  //
  // stub 必须说**助手循环的协议**，不是人话：引擎每轮要一个 JSON 对象
  // （取证用 tool / 作答用 final），终局还要过机械引证门（每条引用必须解析到
  // 真实的步或上下文行）与自检轮。回普通句子会被判「没有可引证的结论」，
  // 助手只能回诚实 IOU —— 那验的是「没编答案」，不是「答对了」。
  // 阶段靠 prompt 里的「终局自检」区分（app/assistant/loop.py::_transcript）。
  const STUB_MARKER = 'CI-STUB-REPLY';
  const chain = createServer((request, response) => {
    const url = request.url || '';
    if (url.endsWith('/models')) {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(
        JSON.stringify({ object: 'list', data: [{ id: 'minimax-m3.1-flash-free', object: 'model' }] }),
      );
      return;
    }
    let raw = '';
    request.on('data', (chunk) => {
      raw += chunk;
    });
    request.on('end', () => {
      const reflect = raw.includes('终局自检');
      const content = reflect
        ? JSON.stringify({ verdict: 'pass', thought: 'CI stub 自检轮：草稿符合清单。' })
        : JSON.stringify({
            thought: 'CI stub：按平台已落档的事实作答。',
            final: {
              text: `${STUB_MARKER} 已收到问题；这是冒烟验证用的可引证固定答复。`,
              cites: ['[c1]'],
            },
          });
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(
        JSON.stringify({
          id: 'chatcmpl-ci-stub',
          object: 'chat.completion',
          choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }],
          usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
        }),
      );
    });
  });
  chain.on('error', (error) => {
    if (error.code === 'EADDRINUSE') {
      console.log('[ci] 8402 已被本机真链占用，直接用现成的');
      return;
    }
    throw error;
  });
  chain.listen(8402, '127.0.0.1');

  // collab-talk 三段回话各来自「被问窗口的助手 worker」：office-assistant 回
  // 助手栏，codex 回管理员私聊与私聊页（默认收件人）。模型走上面的 stub。
  for (const assistantId of ['office-assistant', 'codex']) {
    start(
      python,
      [
        '-m',
        'app.assistant.worker',
        '--url',
        base,
        '--token-file',
        join(tokens, `${assistantId}.key`),
        '--group',
        'demo-group',
        '--assistant-id',
        assistantId,
        '--interval',
        '2',
      ],
      { cwd: backendDir, env },
    );
  }

  const verifyEnv = {
    ...officeEnv,
    OFFICE_URL: officeUrl,
    // 这条读的是 dev server 的插件 API，不能指向静态页面。
    OFFICE_PREVIEW_BASE: officeUrl,
    OFFICE_ALERTS_STUB: '1',
    // 「需要你处理」的注入消息要署**这个平台的管理员窗口**（status.admin.identity，
    // 这里就是 seed 登记的 codex）；署成别的 id 界面不会把它当成管理员报的失败。
    OFFICE_ALERTS_STUB_SENDER: 'codex',
    // 对话类脚本要真实回话；CI 没有会回话的窗口，20 秒确认后明确跳过。
    TALK_REPLY_TIMEOUT_MS: '30000',
    // 模型是本栈的 stub（8402）：回话必须是过得了引证门的答案，不接受诚实 IOU。
    TALK_EXPECT_STUB: '1',
    GITHUB_STEP_SUMMARY: process.env.GITHUB_STEP_SUMMARY,
  };
  // 探索走查用：CI_KEEP=1 起栈后常驻（不跑 verify、不清理），供浏览器逐功能
  // 走查与 API 探针；Ctrl+C 退出时照常杀子进程删临时目录。
  if (process.env.CI_KEEP) {
    console.log(`[ci] keep-alive: backend=${base} office=${officeUrl}`);
    console.log(`[ci] data=${dataDir} tokens=${tokens}`);
    await new Promise(() => {});
  }
  const verify = start(process.execPath, [join(officeRoot, 'scripts', 'verify.mjs')], {
    cwd: officeRoot,
    env: verifyEnv,
  });
  const verifyCode = await waitForExit(verify);
  if (ollamaServing) ollama.close();
  shutdown(verifyCode);
} catch (error) {
  console.error(`[ci] ${error.message}`);
  shutdown(1);
}
