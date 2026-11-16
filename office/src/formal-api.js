// /api/formal 的统一入口：群请求头与错误口径共用一份。
//
// 错误口径：优先平台 error.message / error 字符串，否则带状态码；
// json 解析失败按空对象处理（网关断连时可能回非 json）。
import { withGroup } from './office-scope.js';

export const formal = (url, options = {}) =>
  fetch(url, { ...options, headers: withGroup(options.headers || {}) });

export const formalJson = async (url, options = {}) => {
  const response = await formal(url, { cache: 'no-store', ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = data.error;
    throw new Error((error && error.message) || error || `请求失败（${response.status}）`);
  }
  return data;
};