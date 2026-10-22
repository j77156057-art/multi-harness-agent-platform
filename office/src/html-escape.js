// HTML 转义：给**平台带来的文本**用。
//
// 为什么单独一个模块：这一段是照 `flat-office.js` 里那份抄的，抄的时候最怕
// 手抖 —— 转义表写错一个字符，页面上就会把 CLI 输出里的 `<div>` 当成标签
// 渲染（不是显示难看，是能被人塞进内容里）。所以办公室与「各窗口说了什么」
// 共用这一份，别再手抄第三份。
//
// 用法：`escapeHtml(userText)`，然后拼进 innerHTML。
const escape = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (ch) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);

export const escapeHtml = escape;
