// 「探测结果别在重载后变回『还没探测』」——这一条的纯规则校验（不需要浏览器）。
//
// 起因是实测反馈：点「探测这台机器」出了结果，过一会界面又回到「这台机器还没探测」。
// 查下来应用自身没有定时刷新；真正的原因是探测结果只活在 DOM 里 —— 页面一重载
// （刷新、换标签页、开发时整页热重载）就没了，而自动探测已经关掉，没人再探一遍。
//
// 修法是把探测结果存进 localStorage。这一支钉住两条规则：
//   · 只有**真的探过**的结果才值得存（`guidance_only` 那份存下来会把「还没探测」
//     伪装成探过 —— 那是在替这台机器撒谎）；
//   · 读回来要是坏的/缺的，就当没缓存，不许抛异常把整屏带崩。
import assert from 'node:assert/strict';

// 这个模块摸的是浏览器的 localStorage；给它一个内存版替身再 import。
const store = new Map();
globalThis.window = {
  localStorage: {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  },
};

const { clearInstallCache, isProbedPayload, probedAtLine, readInstallCache, writeInstallCache } =
  await import('../src/install-cache.js');

const probed = {
  guidance_only: false,
  installers: [{ harness_id: 'codex', status: 'ready', version: '0.134.0' }],
  summary: { ready: 1, missing: 0, unknown: 0 },
};
const guidance = {
  guidance_only: true,
  installers: [{ harness_id: 'codex', status: 'unknown' }],
  summary: { ready: 0, missing: 0, unknown: 1 },
};

const checks = [];
const check = (name, fn) => {
  fn();
  checks.push(name);
};

check('a real probe result is cacheable', () => {
  assert.equal(isProbedPayload(probed), true);
});

check('a guidance-only payload is NOT a probe result', () => {
  // 存下来就等于把「还没探测」说成「探过了」。
  assert.equal(isProbedPayload(guidance), false);
  assert.equal(isProbedPayload(null), false);
  assert.equal(isProbedPayload({ installers: [] }), false);
});

check('writing then reading gives the same payload and a timestamp', () => {
  clearInstallCache();
  assert.equal(writeInstallCache(probed), true);
  const cached = readInstallCache();
  assert.deepEqual(cached.payload, probed);
  assert.ok(cached.at && !Number.isNaN(new Date(cached.at).getTime()), cached.at);
});

check('a guidance-only payload never overwrites a real result', () => {
  clearInstallCache();
  assert.equal(writeInstallCache(probed), true);
  assert.equal(writeInstallCache(guidance), false);
  assert.deepEqual(readInstallCache().payload, probed);
});

check('broken cache is treated as no cache, not as a crash', () => {
  store.set('agent-office.installs.v1', '{not json');
  assert.equal(readInstallCache(), null);
  store.set('agent-office.installs.v1', JSON.stringify({ at: 'x' })); // 没有 payload
  assert.equal(readInstallCache(), null);
});

check('the probe time reads as a time, and a bad one reads as nothing', () => {
  assert.match(probedAtLine('2026-10-31T15:11:00.000Z'), /探测于 \d{2}:\d{2}/);
  assert.equal(probedAtLine(''), '');
  assert.equal(probedAtLine('not-a-date'), '');
});

console.log(`install cache: ${checks.length}/${checks.length} PASS`);
