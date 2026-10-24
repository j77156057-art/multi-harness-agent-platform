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

/**
 * CLI 的工具名 → 人话。
 *
 * 这些是各家 CLI 自己的词汇（OpenCode 叫 shell/read/write，Claude Code 叫
 * Bash/Read/Write），直接摆出来对第一次用的人没有意义。认不出的照原样显示 ——
 * 猜错比不翻译更糟。
 */
const TOOL_VERB = {
  shell: '跑了命令',
  bash: '跑了命令',
  read: '读了文件',
  write: '写了文件',
  edit: '改了文件',
  multiedit: '改了文件',
  apply_patch: '改了文件',
  grep: '搜了代码',
  glob: '找了文件',
  list: '看了目录',
  ls: '看了目录',
  task: '派了个活',
  agent: '派了个活',
  webfetch: '抓了网页',
  websearch: '搜了网',
  todowrite: '记了待办',
  todoread: '看了待办',
};

/** 工具的输入里，哪个字段最值得说。 */
const PREFERRED_INPUT = ['path', 'file_path', 'filePath', 'command', 'pattern', 'query', 'url', 'description'];

/** 只留文件名：完整路径对读者没有意义，还占地方。 */
const shortPath = (value) => {
  const text = String(value ?? '');
  const parts = text.split(/[\\/]/).filter(Boolean);
  const name = parts.length ? parts[parts.length - 1] : text;
  return clip(name, 48);
};

/**
 * 输出里的绝对路径压成短形式。
 *
 * `D:\study\tools\agent\new agent\wt\69d9\x.md` → `wt\69d9\x.md`：
 * 整条绝对路径是给写代码的人看的 —— 它把卡片撑变形，而且没人靠它找文件。
 * 认不出来的路径原样留着（不猜、不改内容）。
 */
export function shortenPath(value) {
  const text = String(value ?? '');
  // 路径段里可能有空格（`new agent`），所以前缀允许空格；尾巴到空白为止。
  return text.replace(/[A-Za-z]:[\\/][^"'<>|\r\n]*?\\wt\\[^\s"'<>|]*/g, (match) => {
    const parts = match.split(/[\\/]/);
    const index = parts.findIndex((part) => part.toLowerCase() === 'wt');
    return index < 0 ? match : parts.slice(index).join('\\');
  });
}

/**
 * 一条命令怎么说成人话。
 *
 * 命令里带了工作目录的（`cd D:\...\wt\69d9 && git log`），说成
 * 「在 wt\69d9 里跑了 git log」—— 人要的是**在哪儿跑的什么**，不是那条路径。
 */
function commandLabel(verb, value) {
  const text = String(value ?? '').trim();
  const short = shortenPath(text);
  const dir = /(?:^|[\s"'=])(wt\\[^\s"']+)/.exec(short);
  // `cd <目录> && <命令>` 与 `git -C <目录> <命令>` 两种写法都要认。
  const chain = /^(?:cd\s+)?(.+?)\s*&&\s*(.+)$/.exec(short);
  const withC = /^(.*?)\s+-C\s+(\S+)\s+(.+)$/.exec(short);
  if (dir && chain) {
    return `在 ${dir[1]} 里跑了 ${clip(chain[2].trim(), 60)}`;
  }
  if (dir && withC) {
    const command = `${withC[1].trim()} ${withC[3].trim()}`.trim();
    return `在 ${dir[1]} 里跑了 ${clip(command, 60)}`;
  }
  // 没有工作目录就照旧说（`跑了命令：npm test`；codex 那条只给命令本身）。
  return verb ? `${verb}：${clip(short, 60)}` : clip(short, 60);
}

/**
 * 把一次工具调用说成人话：「写了文件 commit-msg.txt」「跑了命令 npm test」。
 */
function describeTool(tool, input) {
  const raw = String(tool ?? '').trim();
  const verb = TOOL_VERB[raw.toLowerCase()] || raw;
  if (!input || typeof input !== 'object') return verb;
  const key = PREFERRED_INPUT.find((candidate) => input[candidate] !== undefined && input[candidate] !== null);
  if (!key) return verb;
  const value = input[key];
  if (key === 'command') return commandLabel(verb, value);
  if (key === 'path' || key === 'file_path' || key === 'filePath') {
    const raw = String(value);
    const short = shortenPath(raw);
    // 工作目录里的路径留「wt\69d9\x.md」这样的短形式；别处的路径只留文件名。
    return `${verb}：${short !== raw ? short : shortPath(raw)}`;
  }
  return `${verb}：${clip(String(value), 48)}`;
}

const clip = (value, limit) => {
  const text = String(value ?? '');
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
};

/** 一条消息文本（模型的"话"）。 */
const pushText = (list, text, kind) => {
  const value = shortenPath(String(text ?? '')).trim();
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
      // 命令里的工作目录说成「在 wt\69d9 里跑了 git log」，别的照旧。
      pushTool(tools, commandLabel('', item.command), item.status);
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
  // OpenCode 的工具事件同样把细节放在 part 里：
  //   {"type":"tool_use","part":{"type":"tool","tool":"read","state":{"input":{...}}}}
  if (type === 'tool' || type === 'tool_use') {
    const part = event.part || {};
    const tool = event.tool || part.tool || 'tool';
    const state = event.state || part.state || {};
    const status = state.status || '';
    // 输入参数的位置三家不一样：Claude Code 放顶层（event.input），
    // OpenCode 放在 part.state.input —— 两个地方都找。
    const input = state.input || event.input || part.input;
    pushTool(tools, describeTool(tool, input), status);
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

/**
 * Markdown 源码 → 能读的正文。
 *
 * 窗口交上来的是一份 **Markdown 报告**：带 `##` 标题、`**粗体**`、表格竖线、
 * 反引号。直接摆出来，人看到的是"源码"而不是"这件事" —— 这一层就是把那层
 * 标记去掉，**内容一个字不改**（原文仍在"看原始记录"里）。
 *
 * 表格行（`| a | b |`）压成一行（`a · b`），不然一屏全是竖线。
 */
export function readableText(markdown) {
  const lines = String(markdown || '').split('\n');
  const out = [];
  for (const raw of lines) {
    let line = raw;
    // 表格：`| 屏 | 内容 |` → `屏 · 内容`
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const cells = line
        .trim()
        .replace(/^\||\|$/g, '')
        .split('|')
        .map((cell) => cell.trim())
        .filter((cell) => cell && !/^:?-{2,}:?$/.test(cell));
      line = cells.length ? cells.join(' · ') : '';
    }
    line = line
      .replace(/^\s{0,3}#{1,6}\s+/, '') // 标题的 `##`
      .replace(/^\s{0,3}>\s?/, '· ') // 引用
      .replace(/^\s*[-*+]\s+/, '· ') // 列表
      .replace(/^\s*\d+\.\s+/, '· ') // 有序列表
      .replace(/\*\*([^*]+)\*\*/g, '$1') // 粗体
      .replace(/(^|\s)\*([^*\n]+)\*/g, '$1$2') // 斜体
      .replace(/`([^`]+)`/g, '$1') // 反引号
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1（$2）') // 链接
      .replace(/[ \t]+$/, '');
    if (line.trim() || (out.length && out[out.length - 1] !== '')) out.push(line);
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}
