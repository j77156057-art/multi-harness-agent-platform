import {chromium} from 'playwright';
import { requireOffice, skip } from './lib/office-url.mjs';
import assert from 'node:assert/strict';

const base = await requireOffice('verify-harness-connections.mjs', process.env.OFFICE_PREVIEW_BASE);
const status = await (await fetch(`${base}/api/harnesses/status`)).json();
assert.equal(Array.isArray(status.catalog), true);
assert(status.catalog.some(item => item.id === 'codex'));
assert(status.catalog.some(item => item.id === 'opencode'));
assert(status.local_endpoints.some(item => item.id === 'ollama'));

const ollama = await (await fetch(`${base}/api/harnesses/test`, {
  method: 'POST', headers: {'Origin': base, 'X-Office-Harness': '1', 'Content-Type': 'application/json'},
  body: JSON.stringify({harness_id: 'opencode', transport: 'local_ollama', base_url: 'http://127.0.0.1:11434'}),
})).json();
const models = Array.isArray(ollama.models) ? ollama.models : [];
if (ollama.status !== 'connected' || !models.includes('qwen3:4b')) {
  skip(
    'verify-harness-connections.mjs',
    `本机 Ollama 没有可用的 qwen3:4b（返回 status=${ollama.status || 'unknown'}，模型=${models.join(',') || '无'}）。` +
      '先运行 `ollama pull qwen3:4b`，并确认 `ollama serve` 在 127.0.0.1:11434 上跑着，再跑这条校验',
  );
}
assert.equal(ollama.status, 'connected');
assert(models.includes('qwen3:4b'));

const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 960}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.locator('[data-open-connections]').click();
  await page.waitForFunction(() => document.querySelector('[data-connection-summary]')?.textContent.includes('CLI 已安装'));
  assert.equal(await page.locator('.harness-card').count(), 9);
  assert.equal(await page.locator('.endpoint-row').count(), 2);
  await page.locator('[data-test-endpoint]').click();
  await page.waitForFunction(() => document.querySelector('[data-model-result]')?.textContent.includes('端点已连接'));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({result: 'PASS', checks: 9, installed: status.catalog.filter(item => item.installed).map(item => item.id), ollama_models: ollama.models.slice(0, 8)}));
} finally {
  await browser.close();
}
