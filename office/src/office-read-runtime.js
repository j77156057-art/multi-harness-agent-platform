// Offline wire adapter. No HTTP client, credentials, mutations or approval inference.
export const OFFICE_READ_VERSION = 'office-read/1';
const copy = value => structuredClone(value);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const id = value => typeof value === 'string' && value.trim().length > 0;
const seq = value => Number.isSafeInteger(value) && value >= 0;
const same = (a, b) => {
  const stable = value => Array.isArray(value) ? value.map(stable) : object(value)
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])])) : value;
  return JSON.stringify(stable(a)) === JSON.stringify(stable(b));
};
const require = (ok, reason) => { if (!ok) throw new Error(reason); };
const keyOf = (type, identity) => `${type}:${identity}`;

function readPage(page, rowsKey, sequenceKey, stream, validateRow) {
  require(object(page) && seq(page.since_seq) && Number.isSafeInteger(page.limit)
    && page.limit >= 1 && page.limit <= (rowsKey === 'events' ? 1000 : 500), 'invalid_page_request');
  require(page.since_seq <= stream.cursor, 'cursor_gap');
  const wire = page.response;
  require(object(wire) && Array.isArray(wire[rowsKey]) && typeof wire.has_more === 'boolean'
    && seq(wire.next_seq), 'invalid_page');
  const rows = wire[rowsKey];
  require(rows.length <= page.limit && (!wire.has_more || rows.length === page.limit), 'invalid_page_length');
  let previous = page.since_seq;
  const oldCursor = stream.cursor;
  for (const row of rows) {
    require(object(row) && seq(row[sequenceKey]) && row[sequenceKey] > previous, 'invalid_sequence');
    validateRow(row);
    previous = row[sequenceKey];
    const old = stream.rows.get(previous);
    if (old) require(same(old, row), 'conflicting_duplicate');
    else {
      require(previous > oldCursor, 'unseen_old_sequence');
      stream.rows.set(previous, copy(row));
    }
  }
  require(wire.next_seq === previous, 'invalid_next_seq');
  // A replay of an earlier page cannot roll the cursor or completeness backwards.
  if (wire.next_seq >= stream.cursor) {
    stream.cursor = wire.next_seq;
    stream.has_more = wire.has_more;
  }
}

export function createOfficeReadRuntime({ now = () => new Date().toISOString() } = {}) {
  let scope = null, tasks = [], streams = new Map(), state = 'unconfigured', reason = 'scope_required';
  let updatedAt = null, generation = 0;
  const snapshot = () => {
    const events = [], messages = [], cursors = [];
    for (const [key, stream] of streams) {
      cursors.push({stream: key, next_seq: stream.cursor, has_more: stream.has_more});
      (key.startsWith('events:') ? events : messages).push(...stream.rows.values());
    }
    events.sort((a, b) => a.seq - b.seq);
    messages.sort((a, b) => a.created_seq - b.created_seq);
    const missingStreams = scope ? [keyOf('messages', scope.group_id), ...tasks.map(t => keyOf('events', t.task_id))]
      .filter(key => !streams.has(key)) : [];
    return copy({version: OFFICE_READ_VERSION, source: 'offline', synthetic: true, state, reason,
      scope, generation, updated_at: updatedAt, stale: state === 'stale',
      partial: cursors.some(c => c.has_more) || missingStreams.length > 0,
      missing_streams: missingStreams, tasks, events, messages, cursors,
      authority: 'platform_report_only', acceptance_verified: false, writes_enabled: false});
  };
  function configure(next) {
    const valid = object(next) && id(next.project_id) && id(next.group_id);
    const normalized = valid ? {project_id: next.project_id, group_id: next.group_id} : null;
    if (!same(scope, normalized)) {
      generation++; tasks = []; streams = new Map(); updatedAt = null;
      scope = normalized; state = valid ? 'empty' : 'unconfigured'; reason = valid ? 'input_required' : 'scope_required';
    } else if (!valid) { state = 'unconfigured'; reason = 'scope_required'; }
    return snapshot();
  }
  function disconnect(code = 'disconnected') {
    state = !scope ? 'unconfigured' : updatedAt === null ? 'error' : 'stale';
    reason = typeof code === 'string' ? code : 'read_failed';
    return snapshot();
  }
  function ingest(input, {expected_generation = generation} = {}) {
    // Results from a prior scope/source run are ignored without corrupting current state.
    if (expected_generation !== generation) return {ok: false, reason: 'retired_generation', snapshot: snapshot()};
    if (!scope) return {ok: false, reason: 'scope_required', snapshot: snapshot()};
    try {
      const bundle = copy(input);
      require(object(bundle) && bundle.version === OFFICE_READ_VERSION && bundle.synthetic === true, 'synthetic_bundle_required');
      require(object(bundle.scope) && same(scope, bundle.scope), 'scope_mismatch');
      require(object(bundle.groups_response) && Array.isArray(bundle.groups_response.groups), 'groups_required');
      const groups = bundle.groups_response.groups;
      require(groups.every(g => object(g) && id(g.group_id) && g.project_id === scope.project_id), 'project_mismatch');
      require(new Set(groups.map(g => g.group_id)).size === groups.length
        && groups.some(g => g.group_id === scope.group_id), 'group_not_in_project');
      require(object(bundle.tasks_response) && Array.isArray(bundle.tasks_response.tasks), 'tasks_required');
      const nextTasks = bundle.tasks_response.tasks;
      require(nextTasks.every(t => object(t) && id(t.task_id) && t.group_id === scope.group_id
        && id(t.status) && (t.current_epoch === undefined || seq(t.current_epoch))), 'invalid_task_scope_or_fields');
      require(new Set(nextTasks.map(t => t.task_id)).size === nextTasks.length, 'duplicate_task');
      const knownTasks = new Set(nextTasks.map(t => t.task_id));
      require(Array.isArray(bundle.event_pages) && Array.isArray(bundle.message_pages), 'pages_required');
      const nextStreams = new Map([...streams].filter(([key]) => !key.startsWith('events:') || knownTasks.has(key.slice(7)))
        .map(([key, s]) => [key, {cursor: s.cursor, has_more: s.has_more, rows: new Map(s.rows)}]));
      const getStream = key => {
        if (!nextStreams.has(key)) nextStreams.set(key, {cursor: 0, has_more: false, rows: new Map()});
        return nextStreams.get(key);
      };
      for (const page of bundle.event_pages) {
        require(object(page) && knownTasks.has(page.task_id), 'unknown_task_stream');
        readPage(page, 'events', 'seq', getStream(keyOf('events', page.task_id)), row => {
          require(row.aggregate_id === page.task_id && id(row.aggregate_type) && id(row.event_name)
            && object(row.payload), 'event_scope_or_fields');
        });
      }
      for (const page of bundle.message_pages) {
        require(object(page) && page.group_id === scope.group_id, 'message_scope_mismatch');
        readPage(page, 'messages', 'created_seq', getStream(keyOf('messages', page.group_id)), row => {
          require(row.group_id === scope.group_id && id(row.message_id) && typeof row.body === 'string'
            && (row.task_id == null || knownTasks.has(row.task_id)), 'message_scope_or_fields');
        });
      }
      const messages = [...nextStreams.values()].flatMap(s => [...s.rows.values()].filter(r => r.message_id));
      require(new Set(messages.map(m => m.message_id)).size === messages.length, 'duplicate_message_id');
      const allSequences = [...nextStreams].flatMap(([key, stream]) => [...stream.rows.values()]
        .map(row => key.startsWith('events:') ? row.seq : row.created_seq));
      require(new Set(allSequences).size === allSequences.length, 'global_sequence_conflict');
      // Validate the entire input before committing; a rejected page cannot partly update a snapshot.
      tasks = nextTasks.map(t => ({task_id: t.task_id, group_id: t.group_id,
        title: typeof t.title === 'string' ? t.title : t.task_id, status: t.status,
        owner: typeof t.owner === 'string' ? t.owner : null,
        current_attempt_id: typeof t.current_attempt_id === 'string' ? t.current_attempt_id : null,
        current_epoch: t.current_epoch ?? null, last_receipt_id: t.last_receipt_id ?? null,
        last_review_id: t.last_review_id ?? null, acceptance_verified: false}));
      streams = nextStreams; state = 'ready'; reason = null; updatedAt = now();
      return {ok: true, snapshot: snapshot()};
    } catch (error) {
      disconnect(error.message || 'invalid_input');
      return {ok: false, reason, snapshot: snapshot()};
    }
  }
  return {configure, ingest, disconnect, snapshot,
    retire() { generation++; return snapshot(); }};
}
