// 人话从**平台**来。
//
// 这些文本的出处就在平台：失败原因是平台写的、事件名是平台的事件、窗口的登记名
// 在平台的注册表里。所以翻译这件事该由平台做（`POST /wording/translate`），
// 前端只负责显示 —— 平台够不着时用本地兜底，界面照常显示原文。
//
// 规则：
//   * 翻过的**记住**（原文 → 人话），同一句话不问第二次；
//   * 平台说"翻不出来"就**原样留着** —— 宁可原样，也不要编一个错的说法；
//   * 一次问一批（界面一屏有十几条），别让人等十几次。

const cache = new Map();

const post = async (route, body) => {
  const response = await fetch(`/api/formal${route}`, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-Office-Formal': '1' },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = data.error;
    throw new Error((error && error.message) || error || `请求失败（${response.status}）`);
  }
  return data;
};

/**
 * 一批机器文本 → 人话表（原文 → 人话）。
 *
 * @param {string[]} texts 原文们
 * @param {string} [kind] 这批是什么类型（auto 让平台自己认）
 * @returns {Promise<Map<string,string>>}
 */
export async function humanize(texts, kind = 'auto') {
  const wanted = [...new Set((texts || []).map((text) => String(text || '').trim()))]
    .filter(Boolean)
    .filter((text) => !cache.has(`${kind}:${text}`));
  if (!wanted.length) return new Map(cache);
  try {
    const data = await post('/wording/translate', { texts: wanted, kind });
    for (const row of data.texts || []) {
      if (row && row.translated && row.original) {
        cache.set(`${kind}:${row.original}`, row.text);
      }
    }
  } catch {
    // 平台够不着（没连平台、没权限、平台在忙）：本地兜底照旧，界面显示原文。
  }
  return new Map(cache);
}

/**
 * 自由文本（方案原文、规格）→ 一句人话。
 *
 * 走平台的管理员模型通道；翻不出来就返回空串，界面把原文摆出来。
 *
 * @param {string} text 原文
 * @param {number} [maxChars] 最多几个字
 * @returns {Promise<string>}
 */
export async function summarize(text, maxChars = 60) {
  const raw = String(text || '').trim();
  if (!raw) return '';
  const key = `summary:${raw}`;
  if (cache.has(key)) return cache.get(key);
  try {
    const data = await post('/wording/summarize', { text: raw, max_chars: maxChars });
    const summary = String(data.summary || '').trim();
    if (summary) cache.set(key, summary);
    return summary;
  } catch {
    return '';
  }
}

/** 已经翻过的（同步查表；没翻过就是空串）。 */
export function translated(text) {
  const raw = String(text || '').trim();
  if (!raw) return '';
  for (const kind of ['auto', 'reason', 'event', 'status', 'harness', 'path', 'id']) {
    const hit = cache.get(`${kind}:${raw}`);
    if (hit) return hit;
  }
  return '';
}
