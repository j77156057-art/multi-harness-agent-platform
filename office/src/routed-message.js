// Mock-only message envelope for explicit Harness forwarding.
// This is presentation/routing metadata, not a transport or authorization layer.
export const ROUTED_MESSAGE_VERSION = 'agent-message-route/0.1';

const nonempty = value => typeof value === 'string' && value.trim().length > 0;

function normalizeRecipient(value) {
  if (typeof value === 'string') return { instance_id: value, display_name: value, role: 'member' };
  if (!value || !nonempty(value.instance_id) || !nonempty(value.display_name)) {
    throw new Error('invalid_recipient');
  }
  return {
    instance_id: value.instance_id,
    display_name: value.display_name,
    role: value.role === 'admin' ? 'admin' : value.role || 'member',
  };
}

function recipientPrefix(recipient) {
  return recipient.role === 'admin'
    ? `给管理员${recipient.display_name}的：`
    : `给${recipient.display_name}的：`;
}

export function createRoutedMessage({
  sender,
  recipients,
  kind = 'plain',
  taskId = null,
  requestId = null,
  subject,
  body,
  action = null,
}) {
  if (!nonempty(sender)) throw new Error('invalid_sender');
  if (!Array.isArray(recipients) || recipients.length === 0) throw new Error('missing_recipients');
  if (!nonempty(subject) || !nonempty(body)) throw new Error('invalid_message');
  const normalized = recipients.map(normalizeRecipient);
  const ids = normalized.map(recipient => recipient.instance_id);
  if (new Set(ids).size !== ids.length) throw new Error('duplicate_recipient');
  const prefix = normalized.map(recipientPrefix).join('、');
  return Object.freeze({
    version: ROUTED_MESSAGE_VERSION,
    sender,
    recipient_instance_ids: Object.freeze(ids),
    recipients: Object.freeze(normalized.map(Object.freeze)),
    kind,
    task_id: taskId,
    request_id: requestId,
    action,
    subject,
    body,
    prompt: `${prefix}\n${subject}\n${body}`,
  });
}

export function routedRecipient(instanceId, displayName, role = 'member') {
  return normalizeRecipient({ instance_id: instanceId, display_name: displayName, role });
}
