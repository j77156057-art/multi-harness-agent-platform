// Contract check for the project-scope fix, run against the real gateway.
//
//   /status.harnesses          -> the global registry (all groups)
//   /status.project_members    -> the windows THIS group's tasks mention
//   the member roster          -> project_members minus administrator windows
//   the admin panel's roster   -> that member roster + the acting administrator
//
// Administrator windows can legitimately appear in project_members when an old
// task mentioned them. They must never be ordinary members. If someone later
// "simplifies" the panel back to iterating status.harnesses, the load table
// silently regains other projects again. This script fails then.
import { isAdministratorHarness } from '../src/harness-roles.js';

const base = process.env.OFFICE_UI_URL || 'http://127.0.0.1:4174';
const res = await fetch(`${base}/api/formal/status`, { cache: 'no-store' });
if (!res.ok) throw new Error(`status ${res.status}`);
const status = await res.json();

const harnesses = status.harnesses || [];
const registry = harnesses.map((h) => h.harness_id).sort();
const admin = status.admin?.identity || '';
const administrators = [...new Set(harnesses
  .filter((h) => isAdministratorHarness(h, admin))
  .map((h) => h.harness_id))].sort();
const scope = [...new Set((status.project_members || []).concat(admin ? [admin] : []))].sort();
const ordinaryMembers = scope.filter((id) => !administrators.includes(id)).sort();
const outsiders = registry.filter((id) => !scope.includes(id));

const tasks = await (await fetch(`${base}/api/formal/tasks`, { cache: 'no-store' })).json();
const foreign = (tasks.tasks || []).filter((t) => t.group_id && t.group_id !== status.group_id);

const checks = {
  'status is bound to one group': Boolean(status.group_id),
  'ordinary member roster is not the global registry': ordinaryMembers.length < registry.length,
  'every ordinary member is a registered window': ordinaryMembers.every((id) => registry.includes(id)),
  'administrator windows are excluded from ordinary members': administrators.every((id) => !ordinaryMembers.includes(id)),
  'the admin is always in scope (it manages this project)': registry.includes(admin),
  'no task belongs to another group': foreign.length === 0,
  'outsiders are reported, not silently dropped': outsiders.length === harnesses.length - scope.length,
};

const failed = Object.entries(checks).filter(([, ok]) => !ok);
console.log(JSON.stringify({
  group: status.group_id,
  registry: registry.length,
  scope: scope.length,
  ordinaryMembers: ordinaryMembers.length,
  administrators,
  inScope: scope,
  outsiders,
  checks,
  ok: failed.length === 0,
}, null, 1));
if (failed.length) process.exit(1);
