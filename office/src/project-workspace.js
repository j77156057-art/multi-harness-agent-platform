import './project-workspace.css';
import { isAdministratorHarness, isPlatformAssistantHarness } from './harness-roles.js';

const clone = value => structuredClone(value);

// 项目历史必须跨刷新保留。此前它只活在这个模块的数组里：刷新、换标签页或
// 返回首页后项目就消失，看起来像平台把项目弄丢了。现在把项目元数据写进
// 同源 localStorage；真正的任务与场景仍以平台回执为准，这里只保存入口上下文。
const PROJECTS_KEY = 'agent-office.projects.v1';
const ACTIVE_PROJECT_KEY = 'agent-office.active-project.v1';

function readStoredProjects() {
  try {
    const raw = localStorage.getItem(PROJECTS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(project => project && typeof project.id === 'string' && typeof project.name === 'string')
      .map(project => ({
        ...project,
        index: String(project.index || '').padStart(2, '0'),
        createdAt: typeof project.createdAt === 'string' && project.createdAt
          ? project.createdAt
          : new Date().toLocaleString('zh-CN', {hour12: false}),
        phase: project.phase === 'scene' ? 'scene' : 'phone',
        source: project.source === 'mock' ? 'mock' : 'platform',
        members: Array.isArray(project.members) ? project.members : [],
        taskIds: Array.isArray(project.taskIds) ? project.taskIds : [],
      }));
  } catch {
    return [];
  }
}

function readStoredActiveId() {
  try { return localStorage.getItem(ACTIVE_PROJECT_KEY); } catch { return null; }
}

function nextSequence(projects) {
  return projects.reduce((max, project) => {
    const match = /^project-(\d+)$/.exec(project.id || '');
    return Math.max(max, match ? Number(match[1]) : 0);
  }, 0);
}

export function mountProjectWorkspace({ root, onOpenProject, members = [] }) {
  let projects = readStoredProjects();
  let sequence = nextSequence(projects);
  let activeId = readStoredActiveId();
  if (!projects.some(project => project.id === activeId)) activeId = null;
  const persistProjects = () => {
    try { localStorage.setItem(PROJECTS_KEY, JSON.stringify(projects)); } catch {}
  };
  const persistActive = () => {
    try {
      if (activeId) localStorage.setItem(ACTIVE_PROJECT_KEY, activeId);
      else localStorage.removeItem(ACTIVE_PROJECT_KEY);
    } catch {}
  };

  root.innerHTML = `
    <section class="project-lobby" aria-labelledby="projectLobbyTitle">
      <div class="project-lobby-body">
        <div class="project-lobby-copy"><p class="project-instance-note" data-instance-note hidden></p><h1 id="projectLobbyTitle">从一个项目开始</h1><p>每个项目都有自己的任务、方案与额度。点「新建项目」填上名字和成员，就能进这个项目的办公室。</p><div class="project-lobby-actions"><button type="button" class="project-new primary" data-new-project><span>新建项目</span></button><button type="button" class="project-connect" data-open-connections>连接工作窗口</button></div>
          <p class="project-lobby-hint" data-worker-hint hidden></p>
          <ol class="project-lobby-steps" aria-label="新建项目流程"><li><span>1</span>点「新建项目」，填上名字和要一起干活的人</li><li><span>2</span>创建后直接进「开工配置」，写一句要做的事</li><li><span>3</span>在向导里挑管理员和成员，管理员会把它拆成任务分下去</li></ol>
        </div>
        <div class="project-lobby-side">
        <section class="project-history" aria-labelledby="projectHistoryTitle"><div class="project-section-head"><h2 id="projectHistoryTitle">项目历史</h2><span data-count>0 个项目</span></div><div class="project-history-list" data-history><div class="project-empty" data-empty><span class="project-empty-icon">+</span><strong>还没有项目</strong><span>新建后会在这里留下历史记录</span><button type="button" class="project-recover" data-recover-project hidden></button></div></div></section>
        <section class="project-platform" data-platform hidden aria-labelledby="projectPlatformTitle">
          <div class="project-section-head"><h2 id="projectPlatformTitle">平台上已有的项目</h2><span data-platform-count></span></div>
          <p class="project-platform-note">这些是正式平台上已经在做的事。点「打开」就能进那个项目的办公室 —— 不用重新建一个。</p>
          <div class="project-platform-list" data-platform-list></div>
        </section>
        </div>
      </div>
      <footer class="project-lobby-foot"><span>这份项目历史存在这台电脑的浏览器里</span><span>换一个入口（比如评审端口）就看不到它</span></footer>
    </section>`;

  // 4176 是跨提供方评审实例，和 4174 不同源，项目历史本来就不共享。这里把
  // 事实写明，避免用户看到「0 个项目」以为项目被平台弄丢了。
  const instanceNote = root.querySelector('[data-instance-note]');
  const reviewInstance = location.port === '4176' || new URLSearchParams(location.search).get('mode') === 'review';
  if (reviewInstance) {
    instanceNote.hidden = false;
    instanceNote.append('这是独立评审实例，不共享 4174 的项目历史。项目创建与日常协作请回 ');
    const link = document.createElement('a');
    link.href = `${location.protocol}//${location.hostname}:4174/`;
    link.textContent = '4174';
    instanceNote.append(link, '。');
  }

  const dialog = document.createElement('dialog');
  dialog.className = 'harness-dialog';
  dialog.setAttribute('aria-labelledby', 'harnessDialogTitle');
  dialog.innerHTML = `
    <header class="harness-dialog-head"><div><span class="eyebrow">CONNECTIONS</span><h2 id="harnessDialogTitle">连接工作窗口</h2><p>这里检查本机装了哪些 AI 工具、能不能连上模型，并把它们登记成能干活的窗口。</p></div><button type="button" data-close-connections aria-label="关闭">关闭</button></header>
    <div class="harness-tools"><button type="button" data-refresh-connections>重新检测</button><span data-connection-summary role="status">正在扫描本机…</span></div>
    <section class="harness-list" data-harness-list aria-label="Harness 列表"></section>
    <section class="harness-base" aria-labelledby="harnessBaseTitle"><div><span class="eyebrow">BASE HARNESS</span><h3 id="harnessBaseTitle">登记基座 Harness</h3><p>把平台内置适配器登记为正式基座；只有基座登记后，才能为它注册独立成员窗口。适配器与能力由服务端模板决定，浏览器只选择名称。</p></div><div class="harness-base-form"><label>基座 Harness<select data-base-source><option value="">读取可登记基座…</option></select></label><button type="button" data-register-base>登记基座</button></div><p class="harness-help" data-base-result role="status">登记写入正式注册表，同一版本可重复提交。</p></section>
    <section class="harness-register" aria-labelledby="harnessRegisterTitle"><div><span class="eyebrow">FORMAL WINDOWS</span><h3 id="harnessRegisterTitle">注册同一 Harness 的新窗口</h3><p>为 Codex、Qoder 或其他已注册 Harness 创建独立成员身份。注册后需在该窗口启动对应 worker。</p></div><div class="harness-register-form"><label>基础 Harness<select data-register-source><option value="">读取正式注册表…</option></select></label><label>窗口实例 ID<input data-register-instance placeholder="codex-member-1" maxlength="100" spellcheck="false"></label><label>显示名称<input data-register-name placeholder="Codex CLI · 成员1" maxlength="120"></label><button type="button" data-register-harness>注册窗口</button></div><p class="harness-help" data-register-result role="status">注册只复制后端已登记的能力与适配器，不在浏览器保存 token。</p></section>
    <section class="model-connection"><div><span class="eyebrow">MODEL ENDPOINT</span><h3>模型端点</h3></div><div class="model-connection-form"><label>连接类型<select data-transport><option value="local_ollama">本地 Ollama</option><option value="openai_compatible">OpenAI 兼容端口</option></select></label><label>Harness<select data-model-harness></select></label><label>端点<input data-endpoint value="http://127.0.0.1:11434" spellcheck="false"></label><label>模型<select data-model><option value="">先检测可用模型</option></select></label><button type="button" data-test-endpoint>测试连接</button></div><p class="harness-help" data-model-result>不会把 API 密钥发送到浏览器。本机端口仅允许 loopback；云端模型登录由对应 CLI 自己管理。</p></section>
    <p class="harness-boundary">CLI 安装不等于账号已登录；模型可调用不等于已注册正式派发。正式任务只会路由到后端真实注册并在线的 Harness。</p>`;
  root.append(dialog);

  // Creating a project used to happen inline: the lobby showed a permanent
  // "新建项目配置" form *next to* the "新建项目" button, the button only read
  // those fields, and the user then had to find "进入开工配置" a second time.
  // The form is now a real step behind the button, and confirming it opens the
  // opening wizard directly.
  const createDialog = document.createElement('dialog');
  createDialog.className = 'project-create-dialog';
  createDialog.setAttribute('aria-labelledby', 'projectCreateTitle');
  createDialog.innerHTML = `
    <header class="project-create-head"><div><span class="eyebrow">PROJECT SETUP</span><h2 id="projectCreateTitle">项目配置</h2><p>填完即进入开工配置，无需再点一次。</p></div><button type="button" class="project-create-close" data-close-create aria-label="关闭">×</button></header>
    <div class="project-create-body"><label>项目名称<input data-project-name value="GAME-COLLAB" maxlength="80" autocomplete="off"></label><label>运行数据源<select data-project-source><option value="platform">正式平台</option><option value="mock">演示沙盘</option></select></label><fieldset><legend>干活的人（成员窗口）—— 不含管理员，管理员在开工向导里选</legend><div data-project-members></div></fieldset><p class="project-create-scope" data-project-scope>正在读取正式项目范围…</p></div>
    <footer class="project-create-foot"><span data-create-hint>创建后会直接打开该项目的开工配置。</span><button type="button" class="project-new primary" data-confirm-create>创建并进入开工配置</button></footer>`;
  root.append(createDialog);


  const history = root.querySelector('[data-history]');
  const empty = root.querySelector('[data-empty]');
  const recoverButton = root.querySelector('[data-recover-project]');
  const count = root.querySelector('[data-count]');
  const connectionList = dialog.querySelector('[data-harness-list]');
  const summary = dialog.querySelector('[data-connection-summary]');
  const modelHarness = dialog.querySelector('[data-model-harness]');
  const transport = dialog.querySelector('[data-transport]');
  const endpoint = dialog.querySelector('[data-endpoint]');
  const modelSelect = dialog.querySelector('[data-model]');
  const modelResult = dialog.querySelector('[data-model-result]');
  const registerSource = dialog.querySelector('[data-register-source]');
  const registerInstance = dialog.querySelector('[data-register-instance]');
  const registerName = dialog.querySelector('[data-register-name]');
  const registerButton = dialog.querySelector('[data-register-harness]');
  const registerResult = dialog.querySelector('[data-register-result]');
  const baseSource = dialog.querySelector('[data-base-source]');
  const baseButton = dialog.querySelector('[data-register-base]');
  const baseResult = dialog.querySelector('[data-base-result]');
  const projectName = createDialog.querySelector('[data-project-name]');
  const projectSource = createDialog.querySelector('[data-project-source]');
  const projectMembers = createDialog.querySelector('[data-project-members]');
  // 管理员与普通成员都由 Harness 担任：新建项目不再问「进入身份」，管理员在开工
  // 向导里从已注册窗口里挑。
  const projectScope = createDialog.querySelector('[data-project-scope]');
  let connectionState = null;
  let formalState = null;
  // 旧版本的项目只活在内存里；升级后第一次打开大厅时，可以从正式任务按项目名
  // 恢复最近一个项目。按钮只在没有本地历史时出现，且不写平台 project_id。
  let recoverable = null;
  // 平台上已有的项目（按任务标题的「项目名 · 」前缀归堆）。
  let platformProjects = [];

  /**
   * 任务标题是「项目名 · 任务名」，所以项目名就是前缀。
   *
   * 这不是平台给的项目列表（平台那边项目名常常就是编号），但它是**真实存在**
   * 的活——按前缀归堆，人才知道自己有几个项目在跑。
   */
  function platformProjectsFrom(tasks) {
    const groups = new Map();
    for (const task of tasks) {
      const title = typeof task.title === 'string' ? task.title : '';
      const cut = title.indexOf(' · ');
      if (cut <= 0) continue;
      const name = title.slice(0, cut).trim();
      if (!name) continue;
      if (!groups.has(name)) groups.set(name, { name, taskIds: [], count: 0, latest: 0 });
      const group = groups.get(name);
      group.count += 1;
      if (task.task_id) group.taskIds.push(String(task.task_id));
      group.latest = Math.max(group.latest, Number(task.state_changed_seq || 0));
    }
    return [...groups.values()].sort((a, b) => b.latest - a.latest);
  }

  function renderPlatformProjects() {
    const section = root.querySelector('[data-platform]');
    const list = root.querySelector('[data-platform-list]');
    const count = root.querySelector('[data-platform-count]');
    if (!section || !list) return;
    if (!platformProjects.length) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    count.textContent = `${platformProjects.length} 个`;
    list.replaceChildren();
    for (const project of platformProjects) {
      const row = document.createElement('article');
      row.className = 'project-platform-item';
      const title = document.createElement('div');
      title.className = 'project-platform-main';
      const name = document.createElement('strong');
      name.textContent = project.name;
      const meta = document.createElement('span');
      meta.textContent = `${project.count} 件事`;
      title.append(name, meta);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'project-platform-open';
      button.textContent = '打开';
      button.addEventListener('click', () => openPlatformProject(project));
      row.append(title, button);
      list.append(row);
    }
  }

  /** 平台上在线、且不是管理员/助手的窗口 —— 它们才是能干活的人。 */
  function onlineMemberIds() {
    const admin = actingAdministratorId(formalState);
    const online = (formalState?.harnesses || [])
      .filter(item => item.status === 'online' &&
        !isAdministratorHarness(item, admin) && !isPlatformAssistantHarness(item))
      .map(item => item.harness_id);
    return online.length ? online : members.map(member => member.id);
  }

  /**
   * 打开平台上的一个项目：把它变成一个本机项目（这样下次还在历史里），然后进办公室。
   */
  function openPlatformProject(project) {
    const existing = projects.find((item) => item.name === project.name);
    if (existing) {
      activeId = existing.id;
      persistActive();
      render();
      onOpenProject(clone(existing));
      return;
    }
    sequence += 1;
    const local = {
      id: `platform-${String(sequence).padStart(3, '0')}`,
      index: String(sequence).padStart(2, '0'),
      name: project.name,
      createdAt: new Date().toLocaleString('zh-CN', { hour12: false }),
      // 平台上已经在做的事，不是"待开工"。
      phase: 'scene',
      source: 'platform',
      members: onlineMemberIds(),
      taskIds: project.taskIds,
    };
    projects.push(local);
    activeId = local.id;
    persistProjects();
    persistActive();
    render();
    onOpenProject(clone(local));
  }

  async function loadRecoverableProject() {
    try {
      const response = await fetch('/api/formal/tasks', {cache: 'no-store'});
      if (!response.ok) return;
      const data = await response.json();
      const tasks = Array.isArray(data?.tasks) ? data.tasks : [];
      platformProjects = platformProjectsFrom(tasks);
      renderPlatformProjects();
      if (projects.length) return;
      const candidates = tasks.filter(task => typeof task.title === 'string' && task.title.includes(' · '));
      if (!candidates.length) return;
      const latest = candidates.reduce((best, task) =>
        Number(task.state_changed_seq || 0) > Number(best.state_changed_seq || 0) ? task : best, candidates[0]);
      const name = latest.title.split(' · ')[0].trim();
      if (!name) return;
      const taskIds = candidates
        .filter(task => String(task.title).startsWith(`${name} · `))
        .map(task => task.task_id).filter(Boolean);
      recoverable = {name, taskIds};
      // 说人话：以前写「恢复最近项目：xxx」，读的人不知道"恢复"是什么。
      recoverButton.textContent = `打开这个项目（已有 ${taskIds.length} 件事）`;
      recoverButton.hidden = false;
    } catch {}
  }
  void loadRecoverableProject();
  // 名册要在大厅打开时就读一次：「已经有几个能干活的了」这句话依赖它。
  // 以前只有注册之后才刷新，所以大厅里从来没人看到过这句提示。
  void loadFormalStatus();

  function renderFormalRegistration(state) {
    formalState = state;
    // 「注册同一 Harness 的新窗口」的基础 Harness 下拉原本只在这里填，而这里只由大厅打开时
    // 的一次异步请求触发；那次请求若还没回来（或失败走 catch），下拉会一直停在
    // 「读取正式注册表…」——本机明明有 10 个已注册窗口，"注册窗口"看起来像不可用。
    registerSources((state?.harnesses || []).map(item => ({id: item.harness_id, name: item.name, status: item.status})));
    if (baseSource) {
      baseSource.replaceChildren();
      const bases = state?.base_adapters || [];
      for (const base of bases) {
        const option = document.createElement('option');
        option.value = base.harness_id;
        option.textContent = `${base.name || base.harness_id} · ${base.adapter}`;
        baseSource.append(option);
      }
      if (!bases.length) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = state?.configured ? '所有基座均已登记' : '正式接口未配置';
        baseSource.append(option);
      }
      if (baseButton) baseButton.disabled = !bases.length;
    }
  }

  /**
   * 「连接工作窗口」这扇门对第一次用的人没用 ——
   * 它是"本机还没登记机器人"时的补救动作。
   *
   * 所以平台上已经有能干活的人时，这句话直接说明白，让人跳过它；
   * 真的一个都没有时，才提醒他可能得先登记。
   */
  function renderWorkerHint() {
    const hint = root.querySelector('[data-worker-hint]');
    if (!hint) return;
    const roster = formalState?.harnesses || [];
    const admin = actingAdministratorId(formalState);
    const workers = roster.filter(
      (item) =>
        item.status === 'online' &&
        !isAdministratorHarness(item, admin) &&
        !isPlatformAssistantHarness(item) &&
        Number(item.credential_count ?? 1) > 0,
    );
    if (workers.length) {
      // 名单不要全铺出来 —— 一屏七个名字读起来像日志。写"有几个"，
      // 名字让「连接工作窗口」那扇门自己去列。
      const names = workers
        .map((item) => item.name || item.harness_id)
        .slice(0, 3)
        .join('、');
      const more = workers.length > 3 ? ` 等 ${workers.length} 个` : '';
      hint.textContent = `已经有 ${workers.length} 个窗口能干活了（${names}${more}）。要加新的，点「连接工作窗口」。`;
    } else if (roster.length) {
      hint.textContent = '本机登记了窗口，但它们现在都不能接活（没登录、没凭据或掉线了）。点「连接工作窗口」看看。';
    } else {
      hint.textContent = '本机还没有登记任何工作窗口。点「连接工作窗口」可以检查本机装了哪些 AI 工具、把它们登记进来。';
    }
    hint.hidden = false;
  }

  async function loadFormalStatus() {
    try {
      const response = await fetch('/api/formal/status', {cache: 'no-store'});
      const state = response.ok ? await response.json() : null;
      renderFormalRegistration(state);
      renderWorkerHint();
      return state;
    } catch {
      renderFormalRegistration(null);
      renderWorkerHint();
      return null;
    }
  }

  // 在任管理员是外部 Harness 窗口，不是成员：同一个窗口不能既是管理员又是普通成员。
  // 判定只认平台给的 `admin.identity`，不在这里写死 id —— 写死的那份会随管理员换人漂移。
  function actingAdministratorId(state) { return state?.admin?.identity || null; }
  // 同一个基座开出来的窗口显示名会重名（例如两个「CodeBuddy」）：下拉里已经带了
  // instance_id，成员列表也要带上，否则看不出勾的是哪一个。
  function dedupByName(rows) {
    const seen = new Map();
    for (const row of rows) seen.set(row.name, (seen.get(row.name) || 0) + 1);
    return rows.map(row => seen.get(row.name) > 1 ? {...row, name: `${row.name}（${row.id}）`} : row);
  }
  function renderMembers(availableMembers) {
    projectMembers.replaceChildren();
    for (const member of availableMembers) {
    // 防御：即使调用方传进来的是旧项目成员（历史里可能勾过管理员窗口），
    // 成员区也不再渲染管理员。管理员只由开工向导的管理员选择器提供。
    if (isAdministratorHarness({harness_id: member.id}) || isPlatformAssistantHarness({harness_id: member.id})) continue;
    const label = document.createElement('label'); label.className = 'project-member-option';
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = member.id; input.checked = member.checked !== false;
    const text = document.createElement('span'); text.textContent = member.name;
    const role = document.createElement('small'); role.textContent = member.status ? `${member.role || '成员'} · ${member.status}` : (member.role || '成员');
    label.append(input, text, role); projectMembers.append(label);
    }
  }
  renderMembers(members);
  fetch('/api/formal/status', {cache: 'no-store'}).then(response => response.ok ? response.json() : null).then(state => {
    renderFormalRegistration(state);
    if (!state?.project_id) { projectScope.textContent = '正式项目范围暂不可用；本窗口仍可创建本地项目。'; return; }
    // 在任管理员和其它管理员窗口都不进成员名单；管理员由开工向导从全部外部窗口里挑（不预选）。
    const admin = actingAdministratorId(state);
    // 「注册同一 Harness 的新窗口」的基础 Harness 下拉原本只由 renderFormalRegistration 填，
    // 而它只在大厅打开时跑一次异步请求；这次请求若先于填充返回（或失败后走 catch），下拉就
    // 永远停在「读取正式注册表…/暂无正式注册 Harness」——本机明明有 10 个已注册窗口。
    registerSources(state.harnesses || []);
    const online = (state.harnesses || []).filter(item => item.status === 'online').map(item => ({
      id: item.harness_id, name: item.name || item.harness_id, role: '成员窗口', status: item.status,
      // 没有平台凭据的窗口领不了任务：派给它的活会永远停在 ready。
      credential_count: Number(item.credential_count ?? 1),
    }));
    const memberCandidates = online.filter(item =>
      !isAdministratorHarness(item, admin) && !isPlatformAssistantHarness(item));
    const actionable = memberCandidates.filter(item => item.credential_count > 0);
    const idle = memberCandidates.filter(item => item.credential_count === 0);
    // 不摆项目号/群号：那两个串是给机器看的，第一次用的人看到只会以为界面漏了东西。
    projectScope.textContent = `正式平台上有 ${actionable.length} 个窗口能接活`
      + (idle.length ? `（另有 ${idle.length} 个窗口登记在册但没有凭据，接不了活）` : '') + '。';
    renderMembers(dedupByName(actionable));
  }).catch(() => { projectScope.textContent = '正式项目范围暂不可用；本窗口仍可创建本地项目。'; });

  // 注册窗口只能挂在「已登记基座」下面：后端按 source_harness_id 复制能力与适配器。
  // 本窗口自己也算一份来源（同一适配器再开一个 instance_id），否则只有一份已注册窗口时
  // 用户会以为必须先去登记基座。
  function registerSources(harnesses) {
    if (!registerSource) return;
    const previous = registerSource.value;
    registerSource.replaceChildren();
    for (const harness of harnesses) {
      const option = document.createElement('option');
      option.value = harness.id || harness.harness_id;
      option.textContent = harness.name ? `${harness.name} · ${harness.id || harness.harness_id}` : String(harness.id || harness.harness_id);
      registerSource.append(option);
    }
    if (!harnesses.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = '暂无已登记窗口：请先登记基座 Harness';
      registerSource.append(option);
    } else if (harnesses.some(item => (item.id || item.harness_id) === previous)) {
      registerSource.value = previous;
    }
    if (registerButton) registerButton.disabled = !registerSource.value;
  }

  function renderConnections(state) {
    connectionState = state;
    connectionList.replaceChildren();
    modelHarness.replaceChildren();
    const onlineNames = [];
    for (const harness of state.catalog) {
      const card = document.createElement('article');
      card.className = 'harness-card';
      const heading = document.createElement('div');
      heading.className = 'harness-card-title';
      const title = document.createElement('strong'); title.textContent = harness.name;
      const stateTag = document.createElement('span');
      stateTag.className = `harness-state ${harness.installed ? 'installed' : 'missing'}`;
      stateTag.textContent = harness.installed ? 'CLI 已安装' : '未检测到 CLI';
      heading.append(title, stateTag);
      const meta = document.createElement('p');
      meta.textContent = harness.installed ? harness.executable : '可安装 CLI，或通过本地 OpenAI 兼容端点接入';
      const modelLine = document.createElement('p');
      modelLine.textContent = `官方模型：${harness.official_models.join('、')} · 本地：${harness.local_models.join('、') || '支持配置兼容端点'}`;
      const actions = document.createElement('div'); actions.className = 'harness-card-actions';
      const probe = document.createElement('button'); probe.type = 'button'; probe.textContent = harness.installed ? '检测 CLI' : '安装说明';
      probe.addEventListener('click', async () => {
        if (!harness.installed) { window.open(harness.docs, '_blank', 'noopener,noreferrer'); return; }
        probe.disabled = true; probe.textContent = '检测中…';
        try {
          const result = await post('/test', {harness_id: harness.id, transport: 'cli'});
          probe.textContent = result.status === 'installed' ? 'CLI 可运行 · 待检查登录' : '未安装';
        } catch { probe.textContent = '检测失败'; }
        finally { probe.disabled = false; }
      });
      const docs = document.createElement('a'); docs.href = harness.docs; docs.target = '_blank'; docs.rel = 'noreferrer'; docs.textContent = '官方文档';
      actions.append(probe, docs); card.append(heading, meta, modelLine, actions); connectionList.append(card);
      if (harness.installed) onlineNames.push(harness.name);
      const option = document.createElement('option'); option.value = harness.id; option.textContent = harness.name; modelHarness.append(option);
    }
    for (const local of state.local_endpoints) {
      const item = document.createElement('article'); item.className = 'endpoint-row';
      item.innerHTML = `<strong></strong><span></span><code></code>`;
      item.querySelector('strong').textContent = local.name;
      item.querySelector('span').textContent = local.status === 'connected' ? '在线' : '未连接';
      item.querySelector('code').textContent = local.base_url;
      connectionList.append(item);
    }
    summary.textContent = `${onlineNames.length} 个 CLI 已安装：${onlineNames.join('、') || '无'}；模型连接需单独测试`;
    refreshModels();
  }

  function refreshModels() {
    const item = connectionState?.catalog.find(harness => harness.id === modelHarness.value);
    const local = transport.value === 'local_ollama';
    endpoint.value = local ? 'http://127.0.0.1:11434' : 'http://127.0.0.1:8000';
    modelSelect.replaceChildren();
    const models = local ? (item?.local_models || []) : (item?.official_models || []);
    for (const name of models) { const option = document.createElement('option'); option.value = name; option.textContent = name; modelSelect.append(option); }
    if (!models.length) { const option = document.createElement('option'); option.value = ''; option.textContent = '连接成功后读取模型'; modelSelect.append(option); }
  }

  async function post(route, body) {
    const response = await fetch(`/api/harnesses${route}`, {method:'POST', headers:{'Content-Type':'application/json','X-Office-Harness':'1'}, body:JSON.stringify(body), cache:'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '连接失败');
    return data;
  }

  async function loadConnections() {
    summary.textContent = '正在扫描本机…';
    try {
      const response = await fetch('/api/harnesses/status', {cache:'no-store'});
      if (!response.ok) throw new Error('连接服务不可用');
      renderConnections(await response.json());
    } catch (error) { summary.textContent = error.message; }
  }

  root.querySelector('[data-open-connections]').addEventListener('click', () => { dialog.showModal(); void loadConnections(); });
  dialog.querySelector('[data-close-connections]').addEventListener('click', () => dialog.close());
  dialog.querySelector('[data-refresh-connections]').addEventListener('click', () => void loadConnections());
  baseButton.addEventListener('click', async () => {
    const harness_id = baseSource.value;
    if (!harness_id) { baseResult.textContent = '暂无可登记的基座 Harness。'; return; }
    baseButton.disabled = true;
    baseResult.textContent = '正在写入正式注册表…';
    try {
      const response = await fetch('/api/formal/base-harnesses', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Office-Formal': '1'},
        body: JSON.stringify({harness_id}),
        cache: 'no-store',
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'base_registration_failed');
      baseResult.textContent = `已登记基座 ${result.harness_id}（${result.registration_version}${result.replayed ? ' · 重放' : ''}）。现在可在下方为它注册成员窗口。`;
      await loadFormalStatus();
    } catch (error) {
      baseResult.textContent = `登记失败：${error.message}`;
    } finally {
      // loadFormalStatus() already disabled the button when no base is left to
      // register; re-enabling unconditionally would resurrect a dead control.
      baseButton.disabled = !baseSource.value;
    }
  });
  registerButton.addEventListener('click', async () => {
    const source_harness_id = registerSource.value;
    const instance_id = registerInstance.value.trim();
    const name = registerName.value.trim();
    if (!source_harness_id || !instance_id || !name) {
      registerResult.textContent = '请填写基础 Harness、窗口实例 ID 和显示名称。';
      return;
    }
    registerButton.disabled = true;
    registerResult.textContent = '正在写入正式注册表…';
    try {
      const response = await fetch('/api/formal/harnesses', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Office-Formal': '1'},
        body: JSON.stringify({source_harness_id, instance_id, name}),
        cache: 'no-store',
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'formal_registration_failed');
      registerResult.textContent = `已注册 ${result.instance_id}。请在该实例窗口启动 worker，然后重新检测正式状态。`;
      await loadFormalStatus();
      // The main application builds its roster before mounting the lobby. A
      // reload is deliberate here: it replaces that immutable roster with the
      // freshly registered formal instances instead of showing a stale picker.
      window.setTimeout(() => window.location.reload(), 700);
    } catch (error) {
      registerResult.textContent = `注册失败：${error.message}`;
    } finally {
      registerButton.disabled = false;
    }
  });
  modelHarness.addEventListener('change', refreshModels);
  transport.addEventListener('change', refreshModels);
  dialog.querySelector('[data-test-endpoint]').addEventListener('click', async event => {
    const button = event.currentTarget; button.disabled = true; modelResult.textContent = '正在检查端点…';
    try {
      const result = await post('/test', {harness_id:modelHarness.value, transport:transport.value, base_url:endpoint.value});
      const models = result.models || [];
      modelSelect.replaceChildren();
      for (const name of models) { const option = document.createElement('option'); option.value = name; option.textContent = name; modelSelect.append(option); }
      if (!models.length) { const option = document.createElement('option'); option.value = ''; option.textContent = '端点在线，但未返回模型清单'; modelSelect.append(option); }
      modelResult.textContent = result.status === 'connected' ? `端点已连接，发现 ${models.length} 个模型。` : `暂不可用：${result.reason || result.status}`;
    } catch (error) { modelResult.textContent = `测试失败：${error.message}`; }
    finally { button.disabled = false; }
  });

  function render() {
    history.replaceChildren();
    empty.hidden = projects.length > 0;
    if (!projects.length) history.append(empty);
    for (const project of projects) {
      const card = document.createElement('article');
      card.className = 'project-card';
      card.dataset.projectId = project.id;
      card.dataset.active = project.id === activeId ? '1' : '0';
      const status = project.phase === 'scene' ? '已开工' : '待开工';
      const action = project.phase === 'scene' ? '进入办公室' : '去配置';
      card.innerHTML = `<div class="project-card-icon">${project.index}</div><div class="project-card-main"><strong></strong><span></span></div><span class="project-card-status ${project.phase}">${status}</span><button type="button" class="project-card-open">${action}</button>`;
      card.querySelector('strong').textContent = project.name;
      // 卡的副行只说"什么时候建的"：项目编号是给查问题的人看的，不该占着这一行
      // （完整的编号留在 title 里，鼠标悬上去能看到）。
      card.querySelector('.project-card-main span').textContent = `${project.createdAt.split(' ')[0]} 建`;
      card.querySelector('.project-card-main span').title = project.id;
      card.querySelector('button').addEventListener('click', () => {
        activeId = project.id;
        persistActive();
        onOpenProject(clone(project));
      });
      history.append(card);
    }
    count.textContent = `${projects.length} 个项目`;
  }

  function createProject() {
    sequence += 1;
    const project = {
      id: `project-${String(sequence).padStart(3, '0')}`,
      index: String(sequence).padStart(2, '0'),
      name: projectName.value.trim() || `新项目 ${String(sequence).padStart(2, '0')}`,
      createdAt: new Date().toLocaleString('zh-CN', { hour12: false }),
      phase: 'phone',
      source: projectSource.value,
      members: [...projectMembers.querySelectorAll('input:checked')].map(input => input.value),
    };
    projects.push(project);
    activeId = project.id;
    persistProjects();
    persistActive();
    createDialog.close();
    render();
    // `openAfterCreate` tells the host to jump straight into the opening
    // wizard; the lobby no longer round-trips through the project phone.
    onOpenProject(clone(project), { openAfterCreate: true });
  }

  root.querySelector('[data-new-project]').addEventListener('click', () => {
    createDialog.showModal();
    projectName.focus();
  });
  createDialog.querySelector('[data-close-create]').addEventListener('click', () => createDialog.close());
  createDialog.querySelector('[data-confirm-create]').addEventListener('click', createProject);
  createDialog.addEventListener('cancel', event => { event.preventDefault(); createDialog.close(); });

  recoverButton.addEventListener('click', () => {
    if (!recoverable || projects.length) return;
    sequence += 1;
    const project = {
      id: `project-${String(sequence).padStart(3, '0')}`,
      index: String(sequence).padStart(2, '0'),
      name: recoverable.name,
      createdAt: new Date().toLocaleString('zh-CN', { hour12: false }),
      // 恢复出来的项目在平台上**已经有活了**（taskIds 是从平台读回来的），
      // 所以它不是"待开工"，而是"已经在干"：卡片上要写「已开工 / 进入办公室」，
      // 而不是"先配置一下"——点下去直接就是办公室，写错了会让人以为还得再配一次。
      phase: 'scene',
      source: 'platform',
      members: onlineMemberIds(),
      taskIds: recoverable.taskIds,
    };
    projects.push(project);
    activeId = project.id;
    persistProjects();
    persistActive();
    render();
    onOpenProject(clone(project));
  });

  // 同一 origin 的另一个标签页增删项目时，直接刷新大厅列表；否则两个标签页会各自
  // 显示不同的历史，用户又会以为项目丢了。
  window.addEventListener('storage', event => {
    if (event.key === ACTIVE_PROJECT_KEY) {
      activeId = readStoredActiveId();
      if (!projects.some(project => project.id === activeId)) activeId = null;
      render();
      return;
    }
    if (event.key !== PROJECTS_KEY) return;
    projects = readStoredProjects();
    sequence = nextSequence(projects);
    if (!projects.some(project => project.id === activeId)) activeId = null;
    render();
  });

  render();

  return {
    showLobby(project = null) {
      // 回到大厅时不能把上一轮开工/连接的模态框留在打开状态：它们会在
      // 大厅或场景上留下一个看不见的 backdrop，页面看起来就像卡死。
      for (const dialog of root.querySelectorAll('dialog[open]')) dialog.close();
      root.hidden = false;
      if (project) activeId = project.id;
      persistActive();
      render();
    },
    hideLobby() {
      for (const dialog of root.querySelectorAll('dialog[open]')) dialog.close();
      root.hidden = true;
    },
    markStarted(id) {
      const target = projects.find(project => project.id === id);
      if (target) target.phase = 'scene';
      activeId = id;
      persistProjects();
      persistActive();
      render();
    },
    // 开工回执里的 task_ids 要回写到项目，正式任务面板才能只显示本项目。
    updateProject(id, patch = {}) {
      const target = projects.find(project => project.id === id);
      if (!target) return null;
      Object.assign(target, patch);
      persistProjects();
      render();
      return clone(target);
    },
    active() { return clone(projects.find(project => project.id === activeId) || null); },
    projects() { return clone(projects); },
  };
}
