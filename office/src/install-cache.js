/**
 * 「这台机器上装了哪些 CLI」的探测结果，存一份在浏览器里。
 *
 * 为什么需要缓存：探测要跑十六条 `--version`，所以它**只由人点**触发。于是探测结果
 * 只能活在页面里 —— 换标签页、刷新、或者页面因为别的原因重载一次，人就得再点一遍，
 * 而屏幕上那句话会退回「这台机器还没探测」。人看到的现象就是「探完了，过一会又变回去
 * 了」，还以为探测没生效。
 *
 * 只缓存**真的探过**的结果：`guidance_only` 的那份（只有官方安装方式）不是探测结果，
 * 存下来会把「还没探测」伪装成探过。
 *
 * 缓存带时间戳，界面要把「什么时候探的」说出来：装了新 CLI 之后这份数据就旧了，
 * 而人只有看见时间才知道该不该再点一次。
 */

const KEY = 'agent-office.installs.v1';

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** 这份清单是不是一次真探测的结果（`guidance_only` = 只有安装方式，没探过）。 */
export function isProbedPayload(payload) {
  if (!payload || !Array.isArray(payload.installers) || !payload.installers.length) return false;
  if (typeof payload.guidance_only === 'boolean') return !payload.guidance_only;
  return !payload.guidanceOnly;
}

/** 读缓存：`{payload, at}` 或 null（没缓存/坏了/不是探测结果）。 */
export function readInstallCache() {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || !isProbedPayload(parsed.payload)) return null;
    return { payload: parsed.payload, at: typeof parsed.at === 'string' ? parsed.at : '' };
  } catch {
    return null;
  }
}

/** 写缓存。只有探测结果才写；写失败（隐私模式、配额）不该影响界面。 */
export function writeInstallCache(payload) {
  const store = storage();
  if (!store || !isProbedPayload(payload)) return false;
  try {
    store.setItem(KEY, JSON.stringify({ at: new Date().toISOString(), payload }));
    return true;
  } catch {
    return false;
  }
}

/** 清缓存：人点了探测但结果作废（例如读失败）时用不到，留给测试与将来。 */
export function clearInstallCache() {
  try {
    storage()?.removeItem(KEY);
  } catch {
    // 清不掉就算了：缓存坏了 readInstallCache 自己会丢掉它。
  }
}

/** 「探测于 22:41」这种说法；时间读不出来就不说，别编一个。 */
export function probedAtLine(at) {
  if (!at) return '';
  const moment = new Date(at);
  if (Number.isNaN(moment.getTime())) return '';
  const time = moment.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
  return `探测于 ${time}`;
}
