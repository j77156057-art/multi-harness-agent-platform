/**
 * Agent Office —— 应用外壳。
 *
 * 以前这一层是「3D 场景 + 手机」的小样外壳：场景导航（办公室 / 走廊）、工位、
 * 动作预览、相机、一个悬浮的手机入口……而真正的办公室数据（任务、方案、额度、
 * 事件、群聊）只是塞在其中一个面板里，入口互相打架（"我都不知道该点哪"）。
 *
 * 现在办公室**就是**这个应用：
 *
 *   顶栏   —— 项目、连接状态、去项目列表；
 *   主区   —— 要么是项目大厅，要么是这个项目的办公室（数据版）；
 *   对话框 —— 开工配置（不再是"手机"）。
 *
 * 3D 场景与手机都撤了：场景的本机产物还在仓库里（`godot-office/`），
 * 将来要再做一版场景，那是另一件事，不该混在办公路径里。
 */

import './app-shell.css';
import '../contributions/office-opening-phone/office-opening-phone.css';
import '../contributions/office-opening-phone/office-opening-phone.js';
import './opening-host.css';
import { mountOpeningHost, OPENING_GOAL_MAX, openingProjectName } from './opening-host.js';
import { mountFlatOffice } from './flat-office.js';
import { mountAdminAgent } from './admin-agent.js';
import { mountProjectWorkspace } from './project-workspace.js';
import { isAdministratorHarness, isPlatformAssistantHarness } from './harness-roles.js';

const $ = (selector) => document.querySelector(selector);

// ---------------------------------------------------------------- 平台状态

const status = await fetch('/api/formal/status', { cache: 'no-store' })
  .then((response) => (response.ok ? response.json() : null))
  .catch(() => null);

const roster = (status?.harnesses || []).filter(
  (item) => item.status === 'online' && item.capabilities?.includes('coding'),
);
// 管理员是**外部 Harness 窗口**（群主在开工向导里选），不是平台 Agent。
const ADMIN_IDENTITY = status?.admin?.identity || null;
// "已登记"不等于"能干活"：没有平台凭据的窗口既不能当管理员（细化会失败），
// 也不能当成员（任务派给它就永远停在 ready 没人领）。
const actionable = roster.filter((item) => Number(item.credential_count ?? 1) > 0);
const adminCandidates = actionable
  .filter((item) => !isPlatformAssistantHarness(item))
  .map((item) => ({ harness_id: item.harness_id, name: item.name || item.harness_id }));
const administratorIds = roster
  .filter((item) => isAdministratorHarness(item, ADMIN_IDENTITY))
  .map((item) => item.harness_id);
const memberCandidates = actionable
  .filter((item) => !isAdministratorHarness(item, ADMIN_IDENTITY) && !isPlatformAssistantHarness(item))
  .map((item) => ({ harness_id: item.harness_id, name: item.name || item.harness_id }));

// ---------------------------------------------------------------- 外壳

const app = $('#app');
app.innerHTML = `
  <header class="ao-topbar">
    <div class="ao-brand">
      <span class="ao-mark">AO</span>
      <div class="ao-brand-text">
        <strong>Agent Office</strong>
        <span id="aoScope">协作控制台</span>
      </div>
    </div>
    <div class="ao-topbar-actions">
      <span class="ao-conn" id="aoConn"><i></i><span>正在连接平台…</span></span>
      <button class="ao-btn" id="aoStartBtn" type="button" hidden>开工配置</button>
      <span id="aoTopActions" class="ao-topbar-actions"></span>
      <button class="ao-btn" id="aoLobbyBtn" type="button">项目列表</button>
      <span class="ao-user" title="群主">你</span>
    </div>
  </header>
  <main class="ao-main">
    <section class="ao-lobby" id="aoLobby"></section>
    <section class="ao-workspace flat-office" id="aoWorkspace" hidden></section>
  </main>
  <div id="aoOpening"></div>
`;

const connection = $('#aoConn');
if (status?.connected) {
  connection.classList.add('is-live');
  connection.querySelector('span').textContent = `正式平台 · ${status.project_id || ''}`;
} else {
  connection.classList.add('is-offline');
  connection.querySelector('span').textContent = '未连接平台';
}

let activeProject = null;
let openingHost = null;
let office = null;

// ---------------------------------------------------------------- 开工配置

const openingCatalogFor = (project) => ({
  mode: 'formal',
  capacity: {
    max_workers: Math.max(1, memberCandidates.length),
    review_rounds_min: 1,
    review_rounds_max: 8,
  },
  local_agent: false, // 管理员只能是用户选的外部 Harness；本机 Agent 不是管理员
  // 实例表在**顶层**：手机向导要的是 `projects[]` 与 `instances[]` 两个平级数组。
  instances: memberCandidates.map((h) => ({
    instance_id: h.harness_id,
    display_name: h.name,
    brand: h.harness_id,
    available: true,
  })),
  projects: [
    {
      project_id: status?.project_id || '111',
      name: project ? openingProjectName(project.name) : '正式项目',
    },
  ],
});

async function submitFormalGoal(request, catalog) {
  const project = catalog.projects.find((item) => item.project_id === request.project_id);
  const projectName = openingProjectName(activeProject?.name || project?.name || '正式项目');
  // 目标按宿主与向导共同声明的上限裁剪；两者同源，正常输入不会被截断。
  const goal = String(request.goal ?? '').slice(0, OPENING_GOAL_MAX);
  const workers = (request.workers || []).filter(
    (harnessId) => !isAdministratorHarness({ harness_id: harnessId }, ADMIN_IDENTITY),
  );
  if (!workers.length) {
    throw new Error('没有可参与项目的成员窗口：管理员窗口不能作为执行者。');
  }
  // 群主只说一件事：项目目标。怎么拆成任务规格是管理员 Agent 的活，浏览器不再
  // 按成员数复制多份。机器可读的信封挂在 refs 上，群里那段话仍然是人话。
  const envelope = {
    goal_id: crypto.randomUUID(),
    project_name: projectName,
    goal,
    members: workers,
    max_tasks: Math.min(8, Math.max(3, workers.length)),
    admin: request.admin?.instance_id || ADMIN_IDENTITY || null,
  };
  const response = await fetch('/api/formal/messages', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Office-Formal': '1' },
    body: JSON.stringify({
      body: `项目目标（${projectName}）：${goal}\n管理员 Agent 将据此拆解任务；成员：${workers.join('、')}。`,
      refs: ['project-goal', `project-goal-json:${JSON.stringify(envelope)}`],
    }),
    cache: 'no-store',
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'project_goal_post_failed');
  return {
    run_id: `formal-opening-${request.request_id}`,
    epoch: Date.now(),
    confirmed_config: { ...request, goal_id: envelope.goal_id, task_ids: [] },
    tasks: [],
  };
}

openingHost = mountOpeningHost({
  root: $('#aoOpening'),
  reducedMotion: false,
  catalog: openingCatalogFor(null),
  members: [],
  harnesses: adminCandidates.map((h) => ({ harness_id: h.harness_id, name: h.name })),
  administratorIds,
  onOpen() {},
  onPreview() {},
  onDispatch: submitFormalGoal,
  onReady(run) {
    // 把本次开工回执里的 task_id 记到项目上：办公室默认按项目过滤，
    // 不同项目的任务不能串在一起。
    if (activeProject && run.tasks?.length) {
      const taskIds = [
        ...new Set([
          ...(activeProject.taskIds || []),
          ...run.tasks.map((task) => task.task_id).filter(Boolean),
        ]),
      ];
      activeProject.taskIds = taskIds;
      projectWorkspace.updateProject(activeProject.id, { taskIds });
    }
    const receiptAdmin =
      run?.confirmed_config?.admin?.kind === 'harness'
        ? run.confirmed_config.admin.instance_id
        : null;
    openingHost.setAdminHarness?.(receiptAdmin || ADMIN_IDENTITY);
    // 目标已经交出去了：这个项目从此"已开工"（旧壳里这一步叫进入场景）。
    if (activeProject && activeProject.phase !== 'scene') {
      activeProject.phase = 'scene';
      document.body.dataset.projectPhase = 'scene';
      projectWorkspace.updateProject(activeProject.id, { phase: 'scene' });
    }
  },
});

// ---------------------------------------------------------------- 项目大厅

const projectWorkspace = mountProjectWorkspace({
  root: $('#aoLobby'),
  members: memberCandidates.map((h) => ({ id: h.harness_id, name: h.name, role: 'Harness 成员', status: 'online' })),
  onOpenProject(project) {
    activeProject = project;
    openingHost?.setProjectContext(project.name);
    openingHost?.setProjectMembers(project.members || []);
    $('#aoScope').textContent = project.name || '协作控制台';
    $('#aoLobby').hidden = true;
    $('#aoWorkspace').hidden = false;
    $('#aoStartBtn').hidden = false;
    // 页面所处的阶段（大厅 / 进场）：样式与冒烟脚本都看这个标记。
    document.body.dataset.projectPhase = project.phase || 'scene';
    adminAgent.setAdminHarness?.(ADMIN_IDENTITY);
    // 开工向导的**实例表**：先管理员（向导把第一个实例当管理员），再是这个项目的
    // 成员，最后是平台在册的窗口 —— 少了最后一段，成员那一栏会空着没法选人。
    const projectAdmins = (project.members || []).filter((id) => administratorIds.includes(id));
    openingHost.setProjectAdminCandidates?.([
      ...new Set(
        [
          ...projectAdmins,
          ADMIN_IDENTITY,
          ...(project.members || []),
          ...memberCandidates.map((h) => h.harness_id),
        ].filter(Boolean),
      ),
    ]);
    if (!office) {
      office = mountFlatOffice({ root: $('#aoWorkspace'), project: () => activeProject });
    } else {
      void office.refresh();
    }
  },
});

// 旧的"手机开机向导"入口：欢迎块里那两句（开工 / 先看看场景）属于小样，
// 现在由顶栏的「开工配置」进入对话框。
openingHost.hideWelcome();
$('#aoStartBtn').addEventListener('click', () => openingHost.open());
$('#aoLobbyBtn').addEventListener('click', () => {
  $('#aoLobby').hidden = false;
  $('#aoWorkspace').hidden = true;
  $('#aoStartBtn').hidden = true;
  $('#aoScope').textContent = '协作控制台';
  document.body.dataset.projectPhase = 'lobby';
  projectWorkspace.showLobby();
});

// 给冒烟脚本看的一个只读窗口：现在开了什么配置、ready 了几个人。
// （以前这套外壳是 3D 场景 + 手机，调试信息挂在 window 上；重构后仍然留着，
//   免得脚本只能靠截图猜。）
Object.defineProperty(window, 'officePreviewState', {
  configurable: true,
  get() {
    return {
      opening: openingHost?.debug?.() ?? null,
      project: activeProject
        ? { id: activeProject.id, name: activeProject.name, phase: activeProject.phase }
        : null,
      // 办公室的刷新入口：测试可以主动迫一次、看任务书会不会自己长出来。
      office: office ? { refresh: () => office.refresh() } : null,
    };
  },
});

// 平台助手（巡检 + 上报）不是管理员：它只读。入口放在顶栏，不跟项目入口混。
const adminAgent = mountAdminAgent({
  launchArea: $('#aoTopActions'),
  phoneArea: null,
  members: memberCandidates.map((h) => ({ harness_id: h.harness_id, name: h.name })),
  bootstrap: true,
  adminHarness: ADMIN_IDENTITY,
});

// 平台没连上时，大厅里说清楚，别让人对着空界面猜。
if (!status?.connected) {
  const note = document.createElement('p');
  note.className = 'ao-lobby-note';
  note.textContent = '未连接正式平台：这里显示的是本机界面，任务、方案与额度都读不到。';
  $('#aoLobby').prepend(note);
}
