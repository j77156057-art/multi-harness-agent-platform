// 替人把**管理员 worker** 起起来 —— 它在项目自己的群里接目标、细化方案、传阅、派单。
//
// 为什么要有这个：办公室会为每个新项目开自己的群，而一个 worker 一次只服务**一个
// 群**（见 window-provisioner 的纪律）。目标提交到哪个群，就得有一个**为那个群**跑着
// 的管理员 worker；没有它，目标就静静躺在群里没人管。
//
// 2026-11-17 真机走查实测到的就是这个：目标落群、界面显示"已开工"，管理员那边一
// 个字都没有。原因是**顺序**：管理员 worker 首轮"锚定最新消息、不重放历史目标"
// （见 app/admin/worker.py 的 poll_messages），所以"先提交目标、后起 worker"会把那条
// 目标当成历史目标跳过 —— 没人处理，也没人报错。
//
// 所以这里的顺序是硬要求：**先让管理员 worker 就位，再把目标交出去**。
// 就位的判据不是"进程起来了"，而是"它已经锚定过一次、正在按秒轮询"（状态文件里
// initialized 为真）—— 否则目标仍可能在它锚定之前就落下来，又被当成历史跳过。

import {execFile, spawn} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {promisify} from 'node:util';

const execFileAsync = promisify(execFile);

const WINDOW_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const GROUP_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

export function createAdminWorkerProvisioner({
  repoRoot,
  python,
  dataDir,
  upstream,
  reviewRounds = 2,
}) {
  const root = path.resolve(repoRoot);
  const pythonExe = python || path.join(root, '.venv', 'Scripts', 'python.exe');
  const data = path.resolve(dataDir || path.join(root, 'data'));

  const keyFileFor = (harnessId) => path.join(data, `${harnessId}.key`);
  const stateFileFor = (harnessId, group) =>
    path.join(data, `admin-worker-${harnessId}-${group}.json`);

  function readKey(harnessId) {
    try {
      const text = fs.readFileSync(keyFileFor(harnessId), 'utf8').trim();
      return /^apt_[a-f0-9]{64}$/.test(text) ? text : '';
    } catch {
      return '';
    }
  }

  /** 给这个窗口发一份平台凭据（它要拿管理员身份调平台的接口）。 */
  async function issueCredential(harnessId) {
    let stdout;
    try {
      ({stdout} = await execFileAsync(
        pythonExe,
        ['-m', 'app.auth', 'issue', '--type', 'harness', '--id', harnessId, '--label', '办公室管理员'],
        {cwd: path.join(root, 'backend'), timeout: 60000, windowsHide: true},
      ));
    } catch (error) {
      const detail = String(error?.stderr || error?.message || error).trim().slice(0, 400);
      throw new Error(`签发凭据失败：${detail}`);
    }
    let payload;
    try {
      payload = JSON.parse(String(stdout).trim().split('\n').pop());
    } catch {
      throw new Error('签发凭据的返回读不懂（不是 JSON）');
    }
    const token = payload?.issued?.token;
    if (typeof token !== 'string' || !/^apt_[a-f0-9]{64}$/.test(token)) {
      throw new Error(`签发凭据被拒：${payload?.refused || '没有拿到明文'}`);
    }
    fs.mkdirSync(data, {recursive: true});
    fs.writeFileSync(keyFileFor(harnessId), `${token}\n`, {mode: 0o600});
    return token;
  }

  /** 这个管理员 worker 现在是不是在为**这个群**跑着？ */
  async function runningFor(harnessId, group) {
    if (process.platform !== 'win32') {
      try {
        const {stdout} = await execFileAsync(
          'pgrep',
          ['-af', `--admin-harness-id ${harnessId}`],
          {timeout: 5000},
        );
        return String(stdout)
          .trim()
          .split('\n')
          .filter(Boolean)
          .some((line) => line.includes(`--group ${group}`));
      } catch {
        return false;
      }
    }
    const script =
      "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | " +
      `Where-Object { $_.CommandLine -like '*--admin-harness-id ${harnessId}*' } | ` +
      'ForEach-Object { $_.CommandLine }';
    try {
      const {stdout} = await execFileAsync(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-Command', script],
        {timeout: 20000, windowsHide: true},
      );
      return String(stdout)
        .trim()
        .split(/\r?\n/)
        .filter(Boolean)
        .some((line) => line.includes(`--group ${group}`));
    } catch {
      return false;
    }
  }

  /** 它锚定过一次没有（状态文件里的 initialized）。 */
  function anchored(harnessId, group) {
    try {
      const text = fs.readFileSync(stateFileFor(harnessId, group), 'utf8');
      const state = JSON.parse(text);
      return state && state.initialized === true;
    } catch {
      return false;
    }
  }

  function spawnAdminWorker(harnessId, group) {
    const args = [
      '-m',
      'app.admin.worker',
      '--url',
      upstream,
      '--group',
      group,
      '--admin-harness-id',
      harnessId,
      '--token-file',
      keyFileFor(harnessId),
      '--review-rounds',
      String(reviewRounds),
    ];
    const child = spawn(pythonExe, args, {
      cwd: path.join(root, 'backend'),
      // 平台在 enforce 下才认证；worker 自己会拒绝在 off 下跑。
      env: {...process.env, AP_AUTH_MODE: 'enforce'},
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    });
    child.unref();
    if (!child.pid) throw new Error('管理员 worker 没能启动（没有 pid）');
    return child.pid;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  return {
    /**
     * 让这个管理员为这个群**就位**。已经就位就什么都不做；起不来就说清为什么。
     *
     * 返回 `{ready, started, reason}`：`ready` 为假时，调用方**不该**把目标交出去
     * —— 交出去就是一条没人处理的目标。
     */
    async ensure({harnessId, group}) {
      const id = String(harnessId || '').trim();
      const wanted = String(group || '').trim();
      if (!WINDOW_ID.test(id)) return {ready: false, started: false, reason: '管理员窗口 id 不合法'};
      if (!GROUP_ID.test(wanted)) return {ready: false, started: false, reason: '群 id 不合法'};

      if ((await runningFor(id, wanted)) && anchored(id, wanted)) {
        return {ready: true, started: false, reason: '这个管理员已经在这个群里就位'};
      }
      const notes = [];
      if (!readKey(id)) {
        await issueCredential(id);
        notes.push('已签发平台凭据');
      }
      let pid;
      try {
        pid = spawnAdminWorker(id, wanted);
      } catch (error) {
        return {ready: false, started: false, reason: `${notes.join('；')}；${error.message}`};
      }
      notes.push(`已启动管理员 worker（pid ${pid}）`);
      // 等它锚定过一次：锚定之前落下来的目标会被当成历史目标跳过。
      for (let attempt = 0; attempt < 40; attempt += 1) {
        await sleep(500);
        if (anchored(id, wanted)) {
          return {ready: true, started: true, pid, reason: notes.join('；')};
        }
      }
      return {
        ready: false,
        started: true,
        pid,
        reason: `${notes.join('；')}；但它 20 秒内没有就位，目标先不交出去`,
      };
    },
  };
}
