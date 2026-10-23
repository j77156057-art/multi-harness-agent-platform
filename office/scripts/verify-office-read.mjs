import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createOfficeReadRuntime} from '../src/office-read-runtime.js';
import {createOfficeReadFixture} from '../src/office-read-fixture.js';

const checks = [];
const check = (name, fn) => {fn(); checks.push(name);};
const sample = createOfficeReadFixture();
const fresh = () => {const r = createOfficeReadRuntime({now:()=> '2026-10-12T01:00:00Z'}); r.configure(sample.scope); return r;};
const facts = s => ({tasks:s.tasks,events:s.events,messages:s.messages,cursors:s.cursors,updated_at:s.updated_at});
check('no scope cannot masquerade as empty online', () => {
  const r = createOfficeReadRuntime(); assert.equal(r.ingest(sample).ok,false);
  assert.equal(r.snapshot().state,'unconfigured'); assert.equal(r.snapshot().updated_at,null);
});
check('valid released epoch and row status are preserved without inferred acceptance', () => {
  const r=fresh(); assert.equal(r.ingest(sample).ok,true); const s=r.snapshot();
  assert.equal(s.tasks[0].status,'awaiting_review'); assert.equal(s.tasks[1].current_epoch,2);
  assert.equal(s.tasks[1].current_attempt_id,null); assert.equal(s.tasks[1].acceptance_verified,false);
  assert.equal(s.events[2].event_name,'future_extension'); assert.equal(s.writes_enabled,false);
});
check('caller and consumer mutation cannot corrupt committed snapshot', () => {
  const r=fresh(), input=structuredClone(sample); r.ingest(input); input.tasks_response.tasks[0].status='approved';
  const s=r.snapshot();s.events[0].payload.attempt_id='tampered';
  assert.equal(r.snapshot().tasks[0].status,'awaiting_review'); assert.equal(r.snapshot().events[0].payload.attempt_id,'SYN-A1');
});
check('identical replay does not duplicate events or messages', () => {
  const r=fresh();r.ingest(sample);const before=facts(r.snapshot());r.ingest(sample);assert.deepEqual(facts(r.snapshot()),before);
});
check('disconnect retains last facts and recovery clears stale', () => {
  const r=fresh();r.ingest(sample);const before=facts(r.snapshot());r.disconnect();
  assert.equal(r.snapshot().state,'stale');assert.deepEqual(facts(r.snapshot()),before);
  assert.equal(r.ingest(sample).ok,true);assert.equal(r.snapshot().stale,false);
});
check('failure before first success is error, never zero tasks success', () => {
  const r=fresh();r.disconnect();assert.equal(r.snapshot().state,'error');assert.equal(r.snapshot().updated_at,null);
});
const negative = [
  ['wrong project', b=>{b.groups_response.groups[0].project_id='OTHER';}],
  ['wrong configured scope', b=>{b.scope.group_id='OTHER';}],
  ['wrong task group', b=>{b.tasks_response.tasks[0].group_id='OTHER';}],
  ['duplicate task', b=>{b.tasks_response.tasks.push(b.tasks_response.tasks[0]);}],
  ['boolean epoch', b=>{b.tasks_response.tasks[0].current_epoch=true;}],
  ['non synthetic input', b=>{b.synthetic=false;}],
  ['missing task list', b=>{delete b.tasks_response;}],
  ['non task foreign aggregate still rejected', b=>{b.event_pages[0].response.events[0].aggregate_type='group';b.event_pages[0].response.events[0].aggregate_id='OTHER';}],
  ['foreign message group', b=>{b.message_pages[0].response.messages[0].group_id='OTHER';}],
  ['message references task outside list', b=>{b.message_pages[0].response.messages[0].task_id='OTHER';}],
  ['event sequence reorder', b=>{b.event_pages[0].response.events.reverse();}],
  ['duplicate sequence within page', b=>{b.event_pages[0].response.events[1].seq=1;}],
  ['bool sequence', b=>{b.event_pages[0].response.events[0].seq=true;}],
  ['incorrect next cursor', b=>{b.event_pages[0].response.next_seq=6;}],
  ['short page claiming more', b=>{b.event_pages[0].response.has_more=true;}],
  ['over limit', b=>{b.event_pages[0].limit=1;}],
  ['empty more page', b=>{b.event_pages[0].response.events=[];b.event_pages[0].response.next_seq=0;b.event_pages[0].response.has_more=true;}],
  ['cursor gap', b=>{b.event_pages[0].since_seq=6;}],
  ['duplicate sequence across streams', b=>{b.message_pages[0].response.messages[0].created_seq=1;}],
];
for (const [name, mutate] of negative) check(`${name}: rejects atomically and retains last facts`, () => {
  const r=fresh();r.ingest(sample);const before=facts(r.snapshot()), input=structuredClone(sample);mutate(input);
  assert.equal(r.ingest(input).ok,false);assert.equal(r.snapshot().state,'stale');assert.deepEqual(facts(r.snapshot()),before);
});
check('late invalid page cannot commit earlier task changes', () => {
  const r=fresh();r.ingest(sample);const before=facts(r.snapshot()), input=structuredClone(sample);
  input.tasks_response.tasks[0].status='approved';input.message_pages[0].response.messages[1].group_id='OTHER';
  assert.equal(r.ingest(input).ok,false);assert.deepEqual(facts(r.snapshot()),before);
});
check('same id non task event allowed and cannot approve row', () => {
  const b=structuredClone(sample);b.event_pages[0].response.events[0].aggregate_type='group';b.event_pages[0].response.events[0].event_name='approve';
  const r=fresh();assert.equal(r.ingest(b).ok,true);assert.equal(r.snapshot().tasks[0].status,'awaiting_review');
});
check('two full pages chain with independent event and message cursors', () => {
  const b=structuredClone(sample), page=b.event_pages[0], rows=page.response.events;
  b.event_pages=[{...page,limit:2,response:{events:rows.slice(0,2),next_seq:3,has_more:true}},
    {...page,since_seq:3,limit:2,response:{events:rows.slice(2),next_seq:5,has_more:false}},b.event_pages[1]];
  const r=fresh();assert.equal(r.ingest(b).ok,true);assert.equal(r.snapshot().partial,false);
  assert.deepEqual(r.snapshot().cursors.map(c=>c.next_seq),[5,0,4]);
});
check('incomplete full page is visibly partial; full final page is valid', () => {
  const b=structuredClone(sample);b.event_pages[0].limit=3;b.event_pages[0].response.has_more=true;
  const r=fresh();assert.equal(r.ingest(b).ok,true);assert.equal(r.snapshot().partial,true);
  b.event_pages[0].response.has_more=false;assert.equal(r.ingest(b).ok,true);assert.equal(r.snapshot().partial,false);
});
check('empty terminal page preserves cursor and is successful', () => {
  const r=fresh();r.ingest(sample);const b=structuredClone(sample);
  b.event_pages[0].since_seq=5;b.event_pages[0].response={events:[],next_seq:5,has_more:false};
  b.message_pages=[];assert.equal(r.ingest(b).ok,true);assert.equal(r.snapshot().events.length,3);
});
check('incremental page adds once; conflicting replay rejected', () => {
  const r=fresh();r.ingest(sample);const b=structuredClone(sample), row={...b.event_pages[0].response.events[2],seq:7};
  b.event_pages[0].since_seq=5;b.event_pages[0].response={events:[row],next_seq:7,has_more:false};b.message_pages=[];
  assert.equal(r.ingest(b).ok,true);assert.equal(r.ingest(b).ok,true);assert.equal(r.snapshot().events.length,4);
  row.event_name='approve';assert.equal(r.ingest(b).reason,'conflicting_duplicate');
});
check('project switch and retired result cannot leak prior facts', () => {
  const r=fresh();r.ingest(sample);const old=r.snapshot().generation;
  r.configure({project_id:'OTHER',group_id:'NEW'});assert.equal(r.snapshot().tasks.length,0);assert.equal(r.snapshot().updated_at,null);
  assert.equal(r.ingest(sample,{expected_generation:old}).reason,'retired_generation');assert.equal(r.snapshot().state,'empty');
});
check('retire ignores cancelled pending import without damaging current facts', () => {
  const r=fresh();r.ingest(sample);const generation=r.snapshot().generation;r.retire();const before=facts(r.snapshot());
  assert.equal(r.ingest(sample,{expected_generation:generation}).reason,'retired_generation');assert.deepEqual(facts(r.snapshot()),before);
});
check('missing event or message pages remain explicitly incomplete', () => {
  const r=fresh(), b=structuredClone(sample);b.event_pages=[];b.message_pages=[];
  assert.equal(r.ingest(b).ok,true);assert.equal(r.snapshot().partial,true);
  assert.deepEqual(r.snapshot().missing_streams,['messages:SYN-TEAM','events:SYN-T1','events:SYN-T2']);
});
const out='handoff/office-read-adapter-2026-10-12';await fs.mkdir(out,{recursive:true});
await fs.writeFile(`${out}/runtime-results.json`,JSON.stringify({planned:checks.length,ran:checks.length,passed:checks.length,failed:0,checks},null,2)+'\n');
console.log(`office read runtime: ${checks.length}/${checks.length} PASS`);
