// 时间的统一解析与格式化。兜底文案由调用方传（'—' / '' / '时间未记' 各有语义），
// util 只保证「解析失败→兜底」与格式串不再各处手抄。
const parse = (value) => {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

/** 完整本地时间：2026/10/18 20:20:08（hour12: false）。 */
export const fullTime = (value, fallback) => {
  const date = parse(value);
  return date ? date.toLocaleString('zh-CN', { hour12: false }) : fallback;
};

/** 到分钟：10/18 20:20（月/日不补零，与时分两位对齐原实现）。 */
export const minuteTime = (value, fallback) => {
  const date = parse(value);
  return date
    ? date.toLocaleString('zh-CN', {
        hour12: false,
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : fallback;
};

/** 事件窄列短时间：10/18 20:20（手工拼，月/日不补零）。 */
export const shortTime = (value, fallback) => {
  const date = parse(value);
  if (!date) return fallback;
  const month = String(date.getMonth() + 1);
  const day = String(date.getDate());
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${month}/${day} ${hours}:${minutes}`;
};

/** 当前时刻的完整本地时间（写 createdAt 用）。 */
export const nowTime = () => new Date().toLocaleString('zh-CN', { hour12: false });