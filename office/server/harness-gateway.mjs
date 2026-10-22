import {execFile} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {promisify} from 'node:util';

const execFileAsync = promisify(execFile);
const PREFIX = '/api/harnesses';
const LOOPBACK = new Set(['127.0.0.1', 'localhost', '::1']);

const catalog = [
  {
    id: 'codex', name: 'Codex CLI', kind: 'coding', executable: ['codex'],
    officialModels: ['gpt-6.1-sol', 'gpt-5.5', 'gpt-5.3-codex'],
    localModels: ['ollama/qwen3.6:35b-a3b-agent256k', 'ollama/qwen3:14b'],
    docs: 'https://developers.openai.com/codex/cli/',
  },
  {
    id: 'claude', name: 'Claude Code', kind: 'coding', executable: ['claude'],
    officialModels: ['claude-sonnet-5-5', 'claude-opus-5-5', 'claude-haiku-5-5'],
    localModels: [], docs: 'https://code.claude.com/docs/en/overview',
  },
  {
    id: 'opencode', name: 'OpenCode', kind: 'coding', executable: ['opencode'],
    officialModels: ['opencode/claude-sonnet-5-5', 'opencode/step-5-preview-free', 'opencode/space-bunny-free'],
    localModels: ['ollama/qwen3:4b', 'ollama/qwen3:14b', 'ollama/qwen3.6:35b-a3b'],
    docs: 'https://opencode.ai/docs/cli/',
  },
  {
    id: 'qoder', name: 'Qoder CLI', kind: 'coding', executable: ['qoderclicn', 'qodercn', 'qoder', 'qoder-cli'],
    officialModels: ['Qoder 官方模型（登录后读取）'], localModels: [], docs: 'https://docs.qoder.com/cli/overview',
  },
  {
    id: 'workbuddy', name: 'WorkBuddy', kind: 'coding', executable: ['workbuddy', 'workbuddy-cli'],
    officialModels: ['WorkBuddy 官方模型（登录后读取）'], localModels: [], docs: 'https://www.workbuddy.ai/',
  },
  {
    id: 'codebuddy', name: 'CodeBuddy CLI', kind: 'coding', executable: ['codebuddy', 'cbc', 'codebuddy-code'],
    officialModels: ['HY 3', '阶跃星辰 5'], localModels: [], docs: 'https://www.codebuddy.ai/',
  },
  {
    id: 'trae', name: 'TRAE', kind: 'coding', executable: ['trae', 'trae-cli'],
    officialModels: ['TRAE 官方模型（登录后读取）'], localModels: [], docs: 'https://www.trae.ai/',
  },
  {
    id: 'gemini', name: 'Gemini CLI', kind: 'coding', executable: ['gemini'],
    officialModels: ['gemini-3.5-flash', 'gemini-3.1-pro'], localModels: [], docs: 'https://github.com/google-gemini/gemini-cli',
  },
  {
    id: 'deepseek', name: 'DeepSeek Harness', kind: 'coding', executable: ['deepseek', 'deepseek-cli', 'ds'],
    officialModels: ['deepseek-chat', 'deepseek-reasoner'], localModels: [], docs: 'https://platform.deepseek.com/api-docs/',
  },
];

const json = (res, status, data) => {
  res.writeHead(status, {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'});
  res.end(JSON.stringify(data));
};
const fail = (status, message) => Object.assign(new Error(message), {status});
const safeId = value => /^[a-z0-9_-]+$/.test(value || '');

async function findExecutable(candidates) {
  const directCandidates = process.platform === 'win32' ? windowsExecutableCandidates(candidates) : [];
  for (const candidate of [...directCandidates, ...candidates]) {
    if (path.isAbsolute(candidate) && fs.existsSync(candidate)) return {available: true, path: candidate};
    try {
      const result = await execFileAsync(process.platform === 'win32' ? 'where.exe' : 'which', [candidate], {timeout: 1500, windowsHide: true});
      const path = String(result.stdout).split(/\r?\n/).map(line => line.trim()).find(Boolean);
      if (path) return {available: true, path};
    } catch {}
  }
  return {available: false, path: null};
}

function windowsExecutableCandidates(candidates) {
  const home = os.homedir();
  const localAppData = process.env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
  const appData = process.env.APPDATA || path.join(home, 'AppData', 'Roaming');
  const direct = [];
  if (candidates.includes('codex')) {
    const root = path.join(localAppData, 'OpenAI', 'Codex', 'bin');
    try {
      for (const entry of fs.readdirSync(root, {withFileTypes: true})) {
        if (entry.isDirectory()) direct.push(path.join(root, entry.name, 'codex.exe'));
      }
    } catch {}
    direct.push(path.join(home, '.local', 'bin', 'codex.exe'));
  }
  if (candidates.some(candidate => ['qoderclicn', 'qodercn', 'qoder'].includes(candidate))) {
    direct.push(
      path.join(home, '.qoder-cn', 'bin', 'qoderclicn', 'qoderclicn.exe'),
      path.join(home, '.qoder-cn', 'entry', 'qodercn.cmd'),
      path.join(home, '.qoder', 'bin', 'qoder.exe'),
    );
  }
  if (candidates.includes('workbuddy')) direct.push(path.join(home, '.workbuddy', 'bin', 'workbuddy.exe'));
  if (candidates.some(candidate => ['codebuddy', 'cbc', 'codebuddy-code'].includes(candidate))) {
    direct.push(
      path.join(appData, 'npm', 'codebuddy.cmd'),
      path.join(appData, 'npm', 'cbc.cmd'),
      path.join(appData, 'npm', 'codebuddy-code.cmd'),
    );
  }
  if (candidates.includes('gemini')) direct.push(path.join(appData, 'npm', 'gemini.cmd'));
  return direct;
}

async function probeHttp(baseUrl) {
  try {
    const url = new URL(baseUrl);
    if (!LOOPBACK.has(url.hostname)) return {status: 'needs_auth', reason: 'external_endpoint_requires_explicit_account'};
    const target = new URL('/v1/models', url);
    const response = await fetch(target, {signal: AbortSignal.timeout(1800)});
    if (!response.ok) return {status: 'unreachable', reason: `http_${response.status}`};
    const data = await response.json();
    return {status: 'connected', models: Array.isArray(data.data) ? data.data.map(item => item.id).filter(Boolean).slice(0, 30) : []};
  } catch (error) {
    return {status: 'unreachable', reason: error?.name === 'TimeoutError' ? 'timeout' : 'connection_refused'};
  }
}

async function probeOllama(baseUrl) {
  try {
    const url = new URL(baseUrl);
    if (!LOOPBACK.has(url.hostname)) return {status: 'needs_auth', reason: 'external_endpoint_requires_explicit_account'};
    const response = await fetch(new URL('/api/tags', url), {signal: AbortSignal.timeout(1800)});
    if (!response.ok) return {status: 'unreachable', reason: `http_${response.status}`};
    const data = await response.json();
    return {status: 'connected', models: Array.isArray(data.models) ? data.models.map(item => item.name).filter(Boolean).slice(0, 30) : []};
  } catch (error) {
    return {status: 'unreachable', reason: error?.name === 'TimeoutError' ? 'timeout' : 'connection_refused'};
  }
}

async function readBody(req) {
  let text = '';
  for await (const chunk of req) { text += chunk; if (Buffer.byteLength(text) > 8000) throw fail(413, 'body_too_large'); }
  try { const value = JSON.parse(text); if (!value || Array.isArray(value)) throw new Error(); return value; } catch { throw fail(400, 'invalid_json'); }
}

export function createHarnessGateway() {
  async function status() {
    const rows = await Promise.all(catalog.map(async entry => {
      const executable = await findExecutable(entry.executable);
      return {
        id: entry.id, name: entry.name, kind: entry.kind, docs: entry.docs,
        executable: executable.path, installed: executable.available,
        account: executable.available ? 'unknown_login' : 'not_installed',
        official_models: entry.officialModels, local_models: entry.localModels,
        transports: ['openai_compatible', 'local_ollama', ...(executable.available ? ['cli'] : [])],
      };
    }));
    return {catalog: rows, local_endpoints: [
      {id: 'ollama', name: 'Ollama', base_url: 'http://127.0.0.1:11434', status: (await probeOllama('http://127.0.0.1:11434')).status},
      {id: 'openai-compatible', name: 'OpenAI 兼容本地端口', base_url: 'http://127.0.0.1:8000', status: (await probeHttp('http://127.0.0.1:8000')).status},
    ]};
  }

  async function handle(req, res) {
    if (!(req.url === PREFIX || req.url.startsWith(`${PREFIX}/`))) return false;
    try {
      const host = req.headers.host;
      if (!host || ![`127.0.0.1:${req.socket.localPort}`, `localhost:${req.socket.localPort}`].includes(host)) throw fail(421, 'invalid_host');
      if (!LOOPBACK.has(req.socket.remoteAddress?.replace('::ffff:', ''))) throw fail(403, 'loopback_only');
      if (req.headers.origin && req.headers.origin !== `http://${host}`) throw fail(403, 'invalid_origin');
      const url = new URL(req.url, `http://${host}`), route = url.pathname.slice(PREFIX.length);
      if (url.search) throw fail(400, 'unexpected_query');
      if (req.method === 'GET' && route === '/status') { json(res, 200, await status()); return true; }
      if (req.method === 'POST' && route === '/test') {
        if (req.headers['x-office-harness'] !== '1' || req.headers.origin !== `http://${host}`) throw fail(403, 'same_origin_write_required');
        const body = await readBody(req);
        if (!safeId(body.harness_id) || !['openai_compatible', 'local_ollama', 'cli'].includes(body.transport)) throw fail(400, 'invalid_probe');
        const entry = catalog.find(item => item.id === body.harness_id); if (!entry) throw fail(404, 'unknown_harness');
        if (body.transport === 'local_ollama') { json(res, 200, await probeOllama(body.base_url || 'http://127.0.0.1:11434')); return true; }
        if (body.transport === 'openai_compatible') { json(res, 200, await probeHttp(body.base_url || 'http://127.0.0.1:8000')); return true; }
        const executable = await findExecutable(entry.executable);
        json(res, 200, executable.available ? {status: 'installed', executable: executable.path, reason: 'cli_detected_login_checked_by_provider'} : {status: 'not_installed'}); return true;
      }
      throw fail(404, 'harness_route_not_allowed');
    } catch (error) { json(res, error.status || 503, {error: error.status ? error.message : 'harness_gateway_unavailable'}); return true; }
  }
  return {handle};
}

export function harnessGatewayPlugin() {
  const configure = server => { const gateway = createHarnessGateway(); server.middlewares.use((req, res, next) => void gateway.handle(req, res).then(handled => { if (!handled) next(); })); };
  return {name: 'local-harness-gateway', configureServer: configure, configurePreviewServer: configure};
}
