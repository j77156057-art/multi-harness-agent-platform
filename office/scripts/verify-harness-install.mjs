// 「装 CLI」那一屏的取值规则：状态词、分组顺序、汇总句、以及**不替人撒谎**。
//
// 这一支检查的是纯函数，不需要平台也不需要浏览器：给一份平台形状的数据，
// 看它被读成人话之后有没有变形。
import assert from 'node:assert/strict';
import {
  groupByStatus,
  hostLine,
  leftoverHint,
  rowView,
  rowsView,
  statusOf,
  summaryLine,
} from '../src/harness-install.js';

const row = (over) => ({
  harness_id: 'codex',
  display_name: 'Codex CLI',
  provider: 'OpenAI',
  command: 'codex',
  official_url: 'https://developers.openai.com/codex/cli/',
  install_command: 'npm install -g @openai/codex',
  verify_command: 'codex --version',
  login_note: '',
  notes: [],
  status: 'ready',
  found_command: 'codex',
  path: 'C:\\Users\\x\\AppData\\Local\\OpenAI\\Codex\\bin\\codex.exe',
  version: '0.134.0',
  detail: '',
  supported: true,
  ...over,
});

// 1. 五个状态词都有人话；认不出的词照实写出来，不折成「正常」。
for (const key of ['ready', 'missing', 'version_failed', 'unsupported', 'unknown']) {
  assert(statusOf({ status: key }).label, `状态 ${key} 没有标签`);
}
assert.equal(statusOf({ status: 'something_new' }).label, 'something_new');

// 2. 分组把「要人动手的」排在前面：缺的、坏的，然后才是好的。
const grouped = groupByStatus([
  row({ harness_id: 'a', status: 'ready' }),
  row({ harness_id: 'b', status: 'missing' }),
  row({ harness_id: 'c', status: 'version_failed' }),
]);
assert.deepEqual(grouped.map((g) => g.status), ['missing', 'version_failed', 'ready']);
// 组内排序稳定（不然每 15 秒刷新一次，顺序会跳）。
assert.deepEqual(groupByStatus([row({ harness_id: 'z', status: 'ready' }), row({ harness_id: 'a', status: 'ready' })])[0]
  .rows.map((r) => r.harness_id), ['a', 'z']);

// 3. 「还没探测」不能说成「全都缺」—— 那是替这台机器撒谎。
// 平台的字段名是 `guidance_only`（snake_case）：这里必须用**平台真的发的那个名字**
// 来断言，否则测试自己写了个平台不发的字段名，线上照样把「还没探测」说成「0 个已找到」。
const guidance = summaryLine({ guidance_only: true, installers: [row({}), row({})] });
assert(guidance.includes('还没探测'), guidance);
assert(!guidance.includes('未安装'), guidance);
assert(!guidance.includes('0 个已找到'), guidance);
// 兼容旧写法（早先的夹具用驼峰），但平台的写法优先。
assert(summaryLine({ guidanceOnly: true, installers: [row({})] }).includes('还没探测'));
assert(
  !summaryLine({ guidance_only: false, guidanceOnly: true, installers: [row({})] }).includes('还没探测'),
  '平台的字段名优先：guidance_only=false 时不能被旧的驼峰字段盖过去',
);
// 探测过才给数，且数出来的字要和摘要一致。
assert(summaryLine({ summary: { ready: 5, missing: 1 }, installers: [row({})] }).includes('5 个已找到'));
assert(summaryLine({ summary: { ready: 0, missing: 2, unsupported: 1 }, installers: [row({})] }).includes('2 个未安装'));

// 4. 探测的是哪台机器必须说出来（后端那台，不是人面前的这台）。
assert(hostLine({ platform: { os: 'windows', arch: 'x86_64' } }).includes('Windows'));
assert(hostLine({ platform: { os: 'linux' } }).includes('Linux'));
assert.equal(hostLine({}), '');

// 5. verified 只认 ready：unknown 不能被读成「应该没问题」。
assert.equal(rowView(row({ status: 'ready' })).verified, true);
for (const status of ['unknown', 'missing', 'version_failed', 'unsupported']) {
  assert.equal(rowView(row({ status })).verified, false, `${status} 被当成了可用`);
}

// 6. 「装好了」与「已登录」分开说：平台不读登录状态，只能提醒。
const loggedHint = leftoverHint(rowView(row({ login_note: '运行 codex 登录' })));
assert(loggedHint.includes('查不到'), loggedHint);
assert(loggedHint.includes('运行 codex 登录'));
assert.equal(leftoverHint(rowView(row({ status: 'missing' }))), '', '未安装时不该说登录');

// 7. 空文档不炸：平台刚起来、清单还没到时界面要能画。
assert.deepEqual(rowsView(null), []);
assert.deepEqual(groupByStatus([]), []);
assert(summaryLine({}).includes('没有登记'));

// 8. 地址只搬不造：没有官方地址就留空，界面那边会说「平台不猜」。
assert.equal(rowView(row({ official_url: '' })).officialUrl, '');

const result = { checks: 8, result: 'PASS', scope: 'pure rendering rules for harness CLI install status' };
console.log(JSON.stringify(result));
