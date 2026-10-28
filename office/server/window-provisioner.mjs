// 替人在本机开一个 Harness 窗口 —— 就是把 start-formal.ps1 给某一个窗口做的那三件
// 事按需做一遍：发一份平台凭据、把凭据写到 data/<窗口>.key、起一个它自己的 worker。
//
// 为什么要有这个：开屏那个清单只报"几家 Harness、各几个窗口"，具体再开几个窗口是
// 进了项目才决定的事。人右键那一家，期望的是"这个窗口现在能用"——不是"已注册，请
// 自己去终端起进程"。工作树仍然由平台在它接到活时按次开（每次尝试一棵），这里不碰。
//
// 三条纪律：
//   1. **只认平台上已经注册的窗口**：id 形状先校验，再问平台注没注册。浏览器给什么
//      都不能让这里执行任意命令（参数全由服务端拼，人只给一个 harness_id）。
//   2. **幂等**：这个窗口已经有活着的 worker 就不再起第二个（一个 group 上两个 worker
//      会互相抢活，start-formal.ps1 专门拦过这件事）。
//   3. **失败要说清**：发凭据失败、写文件失败、进程没起来，都原样报出来，不假装成功。
import {execFile, spawn} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {promisify} from 'node:util';

const execFileAsync = promisify(execFile);

// 一个窗口 id：与平台注册表同一种形状，且不许带路径分隔符。
const WINDOW_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;

const fail = (status, code) => Object.assign(new Error(code), {status, code});

export function createWindowProvisioner({repoRoot, python, dataDir, canonicalRepo, worktreeRoot, group, upstream, tokenFile}) {
  const root = path.resolve(repoRoot);
  const pythonExe = python || path.join(root, '.venv', 'Scripts', 'python.exe');
  const data = path.resolve(dataDir || path.join(root, 'data'));
  const repo = path.resolve(canonicalRepo || path.join(root, 'canonical.git'));
  const worktrees = path.resolve(worktreeRoot || path.join(root, 'wt'));
  // 办公室里这一轮起了哪些（pid 留着，方便停）。跨重启不保留：进程活不活由平台
  // 心跳判断，这个表只用来"停我自己起的"。
  const started = new Map();

  const keyFileFor = (harnessId) => path.join(data, `${harnessId}.key`);

  function readKey(harnessId) {
    try {
      const text = fs.readFileSync(keyFileFor(harnessId), 'utf8').trim();
      return /^apt_[a-f0-9]{64}$/.test(text) ? text : '';
    } catch {
      return '';
    }
  }

  /// 这个窗口现在还活着吗？
  ///
  /// **直接看进程**，不看平台心跳：心跳 5 分钟才写一次，拿它判"还活着"会漏，
  /// 漏了就会给同一个 group 起第二个 worker —— 两个 worker 抢一条活，比没有更糟
  /// （`start-formal.ps1` 专门拦过这件事）。判据就是命令行里有没有
  /// `--harness-id <这个窗口>`，和启动脚本用的是同一种看法。
  async function workerRunning(harnessId) {
    if (process.platform !== 'win32') {
      try {
        const {stdout} = await execFileAsync('pgrep', ['-f', `--harness-id ${harnessId}`], {
          timeout: 5000,
        });
        return String(stdout).trim().length > 0;
      } catch {
        return false; // pgrep 没找到会以 1 退出
      }
    }
    const script =
      "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | " +
      `Where-Object { $_.CommandLine -like '*--harness-id ${harnessId}*' } | ` +
      'Select-Object -ExpandProperty ProcessId';
    try {
      const {stdout} = await execFileAsync(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-Command', script],
        {timeout: 20000, windowsHide: true},
      );
      return String(stdout).trim().length > 0;
    } catch {
      // 问不出来就当作"没有在跑"：宁可多起一个也要让人能开工，但结果里会写明。
      return false;
    }
  }

  async function issueCredential(harnessId) {
    // 现成的入口：`python -m app.auth issue`。明文只打印这一次。
    let stdout;
    try {
      ({stdout} = await execFileAsync(
        pythonExe,
        ['-m', 'app.auth', 'issue', '--type', 'harness', '--id', harnessId, '--label', '办公室新增'],
        {cwd: path.join(root, 'backend'), timeout: 60000, windowsHide: true},
      ));
    } catch (error) {
      const detail = String(error?.stderr || error?.message || error).trim().slice(0, 400);
      throw fail(503, `签发凭据失败：${detail}`);
    }
    let payload;
    try {
      payload = JSON.parse(String(stdout).trim().split('\n').pop());
    } catch {
      throw fail(503, '签发凭据的返回读不懂（不是 JSON）');
    }
    const token = payload?.issued?.token;
    if (typeof token !== 'string' || !/^apt_[a-f0-9]{64}$/.test(token)) {
      throw fail(503, `签发凭据被拒：${payload?.refused || '没有拿到明文'}`);
    }
    fs.mkdirSync(data, {recursive: true});
    // 只给本人读：这是平台凭据，落在本机磁盘上。
    fs.writeFileSync(keyFileFor(harnessId), `${token}\n`, {mode: 0o600});
    return keyFileFor(harnessId);
  }

  function spawnWorker(harnessId) {
    const args = [
      '-m', 'app.adapter.formal_worker',
      '--url', upstream,
      '--group', group,
      '--canonical-repo', repo,
      '--worktree-root', worktrees,
      '--harness-id', harnessId,
      '--token-file', keyFileFor(harnessId),
    ];
    const child = spawn(pythonExe, args, {
      cwd: path.join(root, 'backend'),
      // 平台在 enforce 下才认证；worker 自己会拒绝在 off 下跑（先说清，别让它起来又死）。
      env: {...process.env, AP_AUTH_MODE: 'enforce'},
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    });
    child.unref();
    if (!child.pid) throw fail(503, 'worker 没能启动（没有 pid）');
    started.set(harnessId, child.pid);
    return child.pid;
  }

  return {
    /** 让这个窗口能干活：没凭据就发、没 worker 就起。已经在跑就什么都不做。 */
    async provision(harnessId) {
      const id = String(harnessId || '');
      if (!WINDOW_ID.test(id)) throw fail(400, 'invalid_harness_id');
      // 已经在跑：不重复起第二个（同一个 group 上两个 worker 会抢活）。
      if (await workerRunning(id)) {
        return {harness_id: id, started: false, reason: '这个窗口已经有一个 worker 在跑'};
      }
      const notes = [];
      let credential = 'reused';
      if (!readKey(id)) {
        await issueCredential(id);
        credential = 'issued';
        notes.push('已签发平台凭据');
      }
      let pid;
      try {
        pid = spawnWorker(id);
      } catch (error) {
        throw fail(503, `${notes.join('；')}${notes.length ? '；' : ''}${error.message}`);
      }
      notes.push(`已启动 worker（pid ${pid}）`);
      return {
        harness_id: id,
        started: true,
        pid,
        credential,
        note: notes.join('；') + '。它接到活时平台会给它开自己的工作树。',
      };
    },

    /** 停掉"我自己起的"那个 worker。别人起的（比如 start-formal.ps1）不碰。 */
    stop(harnessId) {
      const id = String(harnessId || '');
      if (!WINDOW_ID.test(id)) throw fail(400, 'invalid_harness_id');
      const pid = started.get(id);
      if (!pid) return {harness_id: id, stopped: false, reason: '这个窗口不是办公室起的，不在这里停'};
      try {
        process.kill(pid);
      } catch (error) {
        started.delete(id);
        return {harness_id: id, stopped: false, reason: `停不掉（${error.code || 'error'}）`};
      }
      started.delete(id);
      return {harness_id: id, stopped: true, pid};
    },
  };
}
