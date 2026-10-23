// 从 CLI 的输出里抽出**人话**。
//
// 为什么需要它：CLI 在 `--json` / `--output-format stream-json` 之后吐的是
// 机器日志（JSONL），一屏全是 `{"type":"tool","state":{...}}`。人想看的
// 其实就两样：**它说了什么**（结论），以及**它做了什么**（跑了哪些命令、
// 改了哪些文件）。其余是协议。
//
// 各家 CLI 的格式（查过，别猜）：
// - Claude Code `--output-format stream-json`：每行一个对象，
//   `{"type":"assistant","message":{"content":[{"type":"text","text":...}]}}`，
//   收尾有 `{"type":"result","result":"<最终结论>","is_error":false}`；
// - Codex `exec --json`：`{"type":"item.completed","item":{"type":"agent_message","text":...}}`
//   （老版本叫 assistant_message）；命令是 `item.type === "command_execution"`；
// - OpenCode：`{"type":"text","text":...}`、`{"type":"tool","tool":"shell",...}`。
//
// 所以这里按**字段形状**认，而不是按某个 CLI 的名字认 —— 认不出来就原样返回，
// 绝不猜。

const MAX_TEXT = 4000;
const MAX_TOOL_LABEL = 160;

const clip = (value, limit) => {
  const text = String(value ?? '');
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
};

/** 一条消息文本（模型的"话"）。 */
const pushText = (list, text, kind) => {
  const value = String(text ?? '').trim();
  if (!value) return;
  list.push({ kind, text: clip(value, MAX_TEXT) });
};

/** 一次工具调用（模型"做的事"）。 */
const pushTool = (list, label, status) => {
  const value = String(label ?? '').trim();
  if (!value) return;
  list.push({ label: clip(value, MAX_TOOL_LABEL), status: status || '' });
};

/** 从一个事件对象里认出人话与工具调用。 */
function collect(event, texts, tools) {
  if (!event || typeof event !== 'object') return;
  const type = String(event.type || '');

  // Claude Code：收尾的 result 是**最终结论**，最该给人看。
  if (type === 'result') {
    pushText(texts, event.result, '结论');
    return;
  }

  // Claude Code：assistant 消息里的 text 块。
  if (type === 'assistant') {
    const content = event.message?.content ?? event.content;
    if (typeof content === 'string') pushText(texts, content, '话');
    else if (Array.isArray(content)) {
      for (const block of content) {
        if (block && block.type === 'text') pushText(texts, block.text, '话');
      }
    }
    return;
  }

  // Codex：item.completed / item.started 里带 item。
  if (type === 'item.completed' || type === 'item.started' || type === 'item.updated') {
    const item = event.item;
    if (!item || typeof item !== 'object') return;
    const itemType = String(item.type || '');
    if (itemType === 'agent_message' || itemType === 'assistant_message') {
      pushText(texts, item.text, '话');
      return;
    }
    if (itemType === 'command_execution') {
      pushTool(tools, item.command, item.status);
      return;
    }
    if (itemType === 'file_change') {
      const changes = Array.isArray(item.changes) ? item.changes.length : 0;
      pushTool(tools, `改了 ${changes || 1} 个文件`, item.status);
      return;
    }
    if (itemType === 'mcp_tool_call' || itemType === 'web_search') {
      pushTool(tools, itemType === 'web_search' ? '搜了下网' : '调了个外部工具', item.status);
    }
    return;
  }

  // OpenCode：直接的 text / tool 事件。
  // 注意它的文字**在 part 里**（{"type":"text","part":{"type":"text","text":...}}），
  // 不在顶层 —— 照着顶层读会读到 undefined，然后什么也抽不出来。
  if (type === 'text') {
    pushText(texts, event.text ?? event.part?.text, '话');
    return;
  }
  if (type === 'tool') {
    const tool = event.tool || 'tool';
    const status = event.state?.status || '';
    const input = event.state?.input;
    const hint = input && typeof input === 'object'
      ? Object.entries(input).slice(0, 2).map(([k, v]) => `${k}=${clip(v, 40)}`).join(' ')
      : '';
    pushTool(tools, hint ? `${tool}(${hint})` : tool, status);
    return;
  }
  if (type === 'tool_use') {
    pushTool(tools, event.tool, '');
    return;
  }
  if (type === 'command_execution') {
    pushTool(tools, event.command, event.status);
  }
}

/**
 * 解析一段 CLI 输出。
 *
 * @param {string} raw stdout/stderr 原文
 * @returns {{isJson: boolean, texts: {kind: string, text: string}[], tools: {label: string, status: string}[], plain: string}}
 */
export function parseTranscript(raw) {
  const source = String(raw ?? '');
  const texts = [];
  const tools = [];
  let parsed = 0;
  for (const line of source.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || (trimmed[0] !== '{' && trimmed[0] !== '[')) continue;
    let value = null;
    try {
      value = JSON.parse(trimmed);
    } catch {
      continue;
    }
    parsed += 1;
    if (Array.isArray(value)) {
      for (const item of value) collect(item, texts, tools);
    } else {
      collect(value, texts, tools);
    }
  }
  return { isJson: parsed > 0, texts, tools, plain: source };
}

/**
 * 人话摘要：最后一段话（结论）优先，没有就最后一段。
 * @param {{texts: {kind: string, text: string}[]}} transcript
 */
export function headlineText(transcript) {
  const list = transcript?.texts || [];
  if (!list.length) return '';
  const last = list[list.length - 1];
  return last.kind === '结论' ? last.text : last.text;
}
