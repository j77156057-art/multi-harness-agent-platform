// \u4e0e\u7ba1\u7406\u5458\u7684\u79c1\u804a\u7a97\u53e3\uff08\u72ec\u7acb\u9875\u9762\uff09\u3002
//
// \u5e73\u53f0\u53ea\u6709\u7fa4\u6d88\u606f\u4e00\u79cd\uff0c\u4f46\u5b83\u53ef\u4ee5\u5e26 `direct` \u7b7e\u540d\uff08R10 \u00a719\uff09\uff1a
// \u8fd9\u6837\u7684\u6d88\u606f\u53ea\u6709\u53d1\u9001\u65b9\u4e0e\u6536\u4ef6\u65b9\u770b\u5f97\u5230\uff0c\u5176\u4ed6\u6210\u5458\u8bfb\u4e0d\u5230\u3002
// \u8fd9\u4e2a\u9875\u9762\u5c55\u793a\u7684\u5c31\u662f\u8fd9\u7c7b\u6d88\u606f\uff1b\u53d1\u51fa\u53bb\u4e5f\u5e26 direct\uff0c\u6240\u4ee5\u5b83\u4e0d\u4f1a\u6cc4\u5230\u7fa4\u91cc\u3002
import './direct-chat.css';

const DIRECT_REF = 'direct';
const $ = (selector) => document.querySelector(selector);
const escape = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);

const api = async (route, body_) => {
  const response = await fetch(`/api/formal${route}`, {
    cache: 'no-store',
    ...(body_
      ? {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Office-Formal': '1' },
          body: JSON.stringify(body_),
        }
      : {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
};

const state = { status: null, messages: [], error: null };

/** \u7ba1\u7406\u5458\u7a97\u53e3\uff08\u5e73\u53f0\u914d\u7f6e\u7684\u90a3\u4e2a\uff09\u3002 */
function adminId() {
  return state.status?.admin?.identity || null;
}

function adminName() {
  const id = adminId();
  if (!id) return null;
  const hit = (state.status?.harnesses || []).find((item) => item.harness_id === id);
  return (hit && hit.name) || id;
}

/** \u53ea\u770b\u5b9a\u5411\u6d88\u606f\uff1a\u5e26 direct \u7b7e\u540d\u7684\u90a3\u4e9b\u3002 */
function directMessages() {
  return state.messages
    .filter((message) => (Array.isArray(message.refs) ? message.refs : []).includes(DIRECT_REF))
    .sort((a, b) => Number(a.created_seq || 0) - Number(b.created_seq || 0));
}

function render() {
  const root = $('#directApp');
  const id = adminId();
  const name = adminName();
  const thread = directMessages();
  root.innerHTML = `
    <div class="direct-shell">
      <header class="direct-head">
        <h1>${name ? `与 ${escape(name)} 的私聊` : '私聊'}</h1>
        <p>${id ? `收件人：<b>${escape(id)}</b>` : '平台还没有配置管理员窗口。'}</p>
        <p class="direct-note">这是<b>定向消息</b>：只有你和管理员看得到，群里其他成员读不到（平台按消息的收件人过滤）。管理员要回你，得用同样的定向消息发回来。</p>
      </header>
      <div class="direct-list" id="directList">
        ${
          thread.length
            ? thread
                .map((message) => {
                  const mine = message.sender_type === 'human';
                  const who = mine ? '你' : message.sender_id || '平台';
                  const at = String(message.created_at || '').replace('T', ' ').slice(5, 16);
                  return `<article class="direct-msg ${mine ? 'is-mine' : 'is-admin'}">
                    <b>${escape(who)}</b><time>${escape(at)}</time>
                    <p>${escape(message.body || '')}</p>
                  </article>`;
                })
                .join('')
            : '<p class="direct-empty">还没有私聊。发一条，只有你和管理员看得到。</p>'
        }
      </div>
      ${state.error ? `<p class="direct-error">${escape(state.error)}</p>` : ''}
      <form class="direct-form" id="directForm">
        <input type="text" id="directInput" placeholder="对管理员说一句话（只有你们俩看得到）" aria-label="私聊消息" />
        <button type="submit" ${id ? '' : 'disabled'}>发送</button>
      </form>
    </div>`;

  const list = $('#directList');
  if (list) list.scrollTop = list.scrollHeight;

  $('#directForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = $('#directInput');
    const body = String(input.value || '').trim();
    if (!body || !id) return;
    input.value = '';
    try {
      await api('/messages', { body, mentions: [id], refs: [DIRECT_REF], direct: true });
      state.error = null;
      await refresh();
    } catch (error) {
      state.error = String(error.message || error);
      render();
    }
  });
}

async function refresh() {
  try {
    const [status, messages] = await Promise.all([
      api('/status').catch(() => null),
      api('/messages?since_seq=0&limit=500').catch(() => ({ messages: [] })),
    ]);
    state.status = status;
    state.messages = messages.messages || [];
    state.error = null;
  } catch (error) {
    state.error = String(error.message || error);
  }
  render();
}

void refresh();
setInterval(() => void refresh(), 15000);
