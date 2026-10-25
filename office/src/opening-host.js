// 开工目标的长度上限。向导组件与宿主校验共用同一个值：组件默认写死 40 字，
// 真实目标在输入框里就会被静默截断。宿主通过 config.goalMaxLen 把它下发给组件，
// 校验也用同一条规则，摘要与派单标题才能看到完整目标。
export const OPENING_GOAL_MAX = 120;

// 向导里显示的项目名只取项目本名；平台项目号单独放在边界说明里。
// 以前把「（平台项目 N）」拼进项目名，派单标题会变成
// 「重开-112（平台项目 112） · 修开工链路…」，读起来像另一个项目。
export function openingProjectName(name) {
  const value = String(name ?? '').trim();
  return value || '未命名项目';
}
export function openingProjectScope(projectId) {
  return projectId ? `平台项目 ${projectId}` : '';
}

// Mock-only host. Neither a selected instance nor a completed animation starts a Harness.
export function validateOpeningConfig(request, catalog, { connected = false } = {}) {
  if (!request || !catalog || !['mock', 'formal'].includes(catalog.mode)) return '缺少任务配置';
  if (!connected) return '尚未连接 Harness';
  if (typeof request.request_id !== 'string' || !request.request_id) return '缺少请求编号';
  if (!catalog.projects.some(p => p.project_id === request.project_id)) return '项目已不可用，请重新选择';
  const available = id => catalog.instances.some(i => i.instance_id === id && i.available !== false);
  const admin = request.admin;
  // 管理员是用户选择的外部 Harness。本机 Agent 只在模拟数据源里存在，正式平台从不接受它；
  // 拒绝时给出可执行的下一步，而不是含糊的「不可用」。
  if (admin?.kind === 'local_agent') {
    if (catalog.mode === 'formal' || catalog.local_agent !== true) return '正式平台要求管理员是外部 Harness：请在「唯一管理员实例」里选择一个已登记的窗口';
  } else if (admin?.kind !== 'harness' || !admin.instance_id) return '请选择管理员 Harness（本项目由它在开工向导里负责规划与审查）';
  else if (!available(admin.instance_id)) return '管理员已不可用，请重新选择';
  const ids = request.workers;
  if (!Array.isArray(ids) || !ids.length || new Set(ids).size !== ids.length) return '请选择互不重复的普通成员';
  if (ids.some(id => !available(id))) return '有普通成员已不可用';
  if (admin.kind === 'harness' && ids.includes(admin.instance_id)) return '同一注册实例不能同时担任管理员和成员；请选择同 Harness 的另一个 instance_id';
  const cap = catalog.capacity;
  if (!Number.isInteger(cap?.max_workers) || cap.max_workers < 1 || ids.length > cap.max_workers) return '超出当前普通工位容量';
  if (!Number.isInteger(request.review_rounds) || request.review_rounds < cap.review_rounds_min || request.review_rounds > cap.review_rounds_max) return '传阅轮数超出范围';
  const goal = typeof request.goal === 'string' ? request.goal : '';
  if (!goal.trim() || goal.length > OPENING_GOAL_MAX) {
    return `任务目标需为 1–${OPENING_GOAL_MAX} 字（当前 ${goal.length} 字）`;
  }
  return null;
}

export function mountOpeningHost({ root, catalog, reducedMotion, onReady, onPreview, onOpen, onDispatch, members = [], harnesses = [], administratorIds = [] }) {
  root.innerHTML = `<div class="opening-welcome" id="openingWelcome"><span>本轮协作，从这里开始</span><button class="primary" id="openingStartBtn">开工</button><button class="opening-preview" id="openingPreviewBtn">先看看场景</button></div>
    <dialog id="openingDialog" aria-label="开工向导"><div class="opening-bar"><span>开工配置</span><button id="openingCloseBtn" aria-label="关闭开工配置">×</button></div><div id="openingPhoneSlot"></div><p class="opening-boundary" id="openingBoundary">${catalog.mode === 'formal' ? '正式平台 · 目标交给管理员 Agent 细化并分派' : '模拟配置与群聊 · 未启动真实 Harness'}</p></dialog>`;
  const welcome = root.querySelector('#openingWelcome');
  const dialog = root.querySelector('#openingDialog');
  const slot = root.querySelector('#openingPhoneSlot');
  const boundary = root.querySelector('#openingBoundary');
  // The opening-phone component renders one `instances` list for both the
  // administrator and worker selectors. Administrator windows must remain
  // selectable as the administrator, but never as ordinary workers. Remove
  // their worker chips after each component render instead of changing the
  // hash-locked contribution.
  const administratorSet = new Set(administratorIds.filter(Boolean));
  const pruneAdministratorWorkers = () => {
    for (const chip of slot.querySelectorAll('[data-worker]')) {
      if (administratorSet.has(chip.dataset.worker)) chip.remove();
    }
  };
  const administratorWorkerObserver = new MutationObserver(pruneAdministratorWorkers);
  administratorWorkerObserver.observe(slot, {childList: true, subtree: true});
  // The hash-locked phone component still carries the old per-member dispatch
  // sentence in its formal summary. The host now submits one project-goal
  // message, so leaving that copy in place would tell the owner the browser is
  // still the dispatcher. Patch only the rendered text node; the locked file
  // itself stays untouched.
  const patchSummaryNote = () => {
    if (activeCatalog.mode !== 'formal') return;
    for (const note of slot.querySelectorAll('.oop-sum-note')) {
      const text = '确认后仅提交一条项目目标；管理员窗口负责细化与分派，成员窗口领取后执行。';
      if (note.textContent !== text) note.textContent = text;
    }
  };
  const summaryNoteObserver = new MutationObserver(patchSummaryNote);
  summaryNoteObserver.observe(slot, {childList: true, subtree: true});
  // 锁定组件里那一栏的标题写的是"唯一管理员实例"，实际列的是**所有**可选实例
  // （下面那一栏才是选成员的地方）。照原样会让人以为只能选一个。
  const patchAdminLabel = () => {
    for (const label of slot.querySelectorAll('label.oop-label')) {
      if (!label.textContent.includes('唯一管理员实例')) continue;
      label.textContent = '管理员窗口（必选，只能选一个；其余窗口在下方选为成员）';
    }
  };
  const adminLabelObserver = new MutationObserver(patchAdminLabel);
  adminLabelObserver.observe(slot, {childList: true, subtree: true});
  // 同理：手机组件里那个"返回首页"是**手机**的首页。这套外壳没有手机首页，
  // 那个按钮实际只是把对话框关掉，所以把字改回"关闭"（只动渲染出来的文字，
  // 锁定的文件本身不改）。
  const patchHomeLabel = () => {
    for (const btn of slot.querySelectorAll('.oop-btn')) {
      if (btn.dataset.action) continue;
      if (btn.textContent.trim() === '返回首页') btn.textContent = '关闭';
    }
  };
  const homeLabelObserver = new MutationObserver(patchHomeLabel);
  homeLabelObserver.observe(slot, {childList: true, subtree: true});
  // 组件把「任务目标」排在表单的最后一项：实测它在滚动区下方一百多像素处
  // （目标框 top=912，而可视区只到 bottom=798），第一次用的人第一眼看不到自己要
  // 写什么，得先选项目、管理员、成员、传阅轮数再往下滚。
  //
  // 组件是锁定贡献（文件不能改），这里沿用同一手法：每次重渲染后把**组件自己的**
  // 目标输入框那一组搬到表单最前面。搬的是同一个节点 —— 组件的状态、校验、摘要
  // 全都照旧，也不引入第二个输入框（两处输入框必然不同步）。
  const promoteGoalField = () => {
    const goal = slot.querySelector('textarea[data-field="goal"]');
    if (!goal) return;
    const group = goal.closest('.oop-group');
    const form = group ? group.parentElement : null;
    if (!group || !form || form.firstElementChild === group) return;
    form.prepend(group);
  };
  const goalFieldObserver = new MutationObserver(promoteGoalField);
  goalFieldObserver.observe(slot, {childList: true, subtree: true});
  // 锁定组件的措辞是给机器看的（实例 / 打工人 / Harness / instance_id / 宿主 /
  // 唯一管理员 / (harness)），而这条向导是**第一次用的人**走的第一条路。
  // 这里统一改渲染出来的文字：只改显示，不改组件的校验与行为（锁定文件本身不动）。
  //
  // 顺序有意义：具体句式排在通用词之前，否则「普通打工人」会先被拆成
  // 「普通成员窗口」这种半截话。
  const JARGON_FIXES = [
    [/未选择唯一管理员/g, '请先选择管理员窗口'],
    [/至少选择 1 名普通打工人/g, '请至少选择 1 个成员窗口'],
    [/请至少勾选 1 个普通打工人/g, '请至少勾选 1 个成员窗口'],
    [/同一实例不得兼任管理员与打工人/g, '同一窗口不能同时担任管理员与成员'],
    [/实例已不在当前目录/g, '该该窗口已不在当前名单中中'],
    [/实例不可用/g, '窗口不可用'],
    [/打工人数量/g, '成员窗口数量'],
    [/普通打工人/g, '参与成员'],
    [/打工人/g, '成员窗口'],
    [/管理员实例/g, '管理员窗口'],
    [/成员实例/g, '成员窗口'],
    [/唯一管理员/g, '管理员窗口'],
    [/容量\(宿主\)/g, '可用'],
    [/宿主容量/g, '可用数量'],
    [/已注册窗口/g, '已登记窗口'],
    [/同 Harness 的不同 instance_id 可同时加入/g, '同一程序的不同窗口可同时加入'],
    [/管理员 Agent/g, '管理员窗口'],
    [/成员 worker/g, '成员窗口'],
    [/已创建开工聊聊/g, '已创建开工群聊'],
    [/重播演出/g, '再看一遍'],
    [/（请宿主确认）/g, ''],
    [/请宿主确认/g, ''],
    [/（harness）/g, ''],
  ];
  const patchJargon = () => {
    for (const node of slot.querySelectorAll('*')) {
      for (const child of node.childNodes) {
        if (child.nodeType !== 3) continue;
        const text = child.nodeValue || '';
        if (!text) continue;
        let next = text;
        for (const [pattern, to] of JARGON_FIXES) next = next.replace(pattern, to);
        // 「可用 7」「已选 7」后面补个单位，读起来才像句话。
        next = next.replace(/可用 (\d+)(?! 个)/g, '可用 $1 个');
        next = next.replace(/已选 (\d+)(?! 个)/g, '已选 $1 个');
        // 摘要里管理员那一行带着实例 id（[codex-window-admin]）：界面上给人看的是名字。
        next = next.replace(/^([^\[\n]+)\[[^\]\n]+\]/, '$1');
        if (next !== text) child.nodeValue = next;
      }
    }
  };
  const jargonObserver = new MutationObserver(patchJargon);
  jargonObserver.observe(slot, {childList: true, subtree: true});
  // 正式平台下这一屏是组件的**彩排**（演示对话），不是真实回复 —— 说清楚，
  // 否则第一次用的人会把演示里那句「在的，马上开工」当成成员真的回话了。
  const patchPerformLabel = () => {
    if (activeCatalog.mode !== 'formal') return;
    for (const note of slot.querySelectorAll('.oop-perform-demo')) {
      const text = '正式平台 · 任务回执已收到，目标已发到项目群聊；下方为界面演示，不是真实回复。';
      if (note.textContent !== text) note.textContent = text;
    }
  };
  const performLabelObserver = new MutationObserver(patchPerformLabel);
  performLabelObserver.observe(slot, {childList: true, subtree: true});
  // The project phone phase hides the office shell. Keep the configuration
  // dialog outside that hidden subtree so the role/member selector remains
  // reachable after clicking "新建任务".
  document.body.appendChild(dialog);
  let generation = 0, epoch = 0, active = null, readyCount = 0, pending = null;
  let activeCatalog = catalog;
  let returnFocus = null;
  let projectMembers = Array.isArray(members) ? [...members] : [];
  let projectScope = '';

  // 边界说明同时承担「这条配置属于哪个平台项目」的说明职责；项目号不再拼进项目名。
  function renderBoundary() {
    const base = activeCatalog.mode === 'formal'
      ? '正式平台 · 派单会写入真实任务状态'
      : '模拟配置与群聊 · 未启动真实 Harness';
    boundary.textContent = projectScope ? `${projectScope} · ${base}` : base;
  }

  // 本项目在「新建项目」里勾选的成员。开工向导每次打开都会 phone.start() 清空选择，
  // 于是已经挑好的人还要在下一次打开时再点一遍。这里把那份选择作为默认值播种回向导。
  //
  // 管理员**不播种**：管理员是用户在向导里显式选择的外部 Harness，不是平台助手，
  // 也不该由界面按「谁是 office-admin / 谁排第一」替人挑。没有选择时 validateOpeningConfig
  // 会要求用户先选，而不是替他选中一个。
  //
  // 勾选发生在该窗口首次渲染之后（每次 chip.click() 都会 renderConfig() 重建一部分
  // DOM），所以按 catalog 的最小编号顺序点，并重新取节点，绝不缓存初始的 chip 引用。
  function seedSelection() {
    if (phone.getStage() !== 'config') return;
    for (const id of projectMembers) {
      const chip = slot.querySelector(`[data-worker="${CSS.escape(id)}"]`);
      if (chip && !chip.disabled && !chip.classList.contains('on')) chip.click();
    }
  }
  const delivered = new Set();
  const phone = window.OfficeOpeningPhone.mount(slot, {
    catalog, reducedMotion,
    // 组件默认 40 字会在输入阶段静默截断；host 与校验共用 OPENING_GOAL_MAX。
    config: {goalMaxLen: OPENING_GOAL_MAX},
    callbacks: {
      onConfigConfirm(request) {
        const ticket = generation;
        clearTimeout(pending);
        pending = setTimeout(async () => {
          pending = null;
          if (ticket !== generation || !dialog.open || phone.debug().inflightRequestId !== request.request_id || phone.getStage() !== 'summary') return;
          const reason = validateOpeningConfig(request, activeCatalog, { connected: phone.debug().connected });
          if (reason) {
            phone.resolveConfirm({ request_id: request.request_id, status: 'rejected', reason });
            return;
          }
          const confirmed = structuredClone(request);
          delete confirmed.request_id;
          // Set before resolve: reduced motion can synchronously emit onOpeningReady.
          try {
            const dispatched = onDispatch ? await onDispatch(request, activeCatalog) : {
              run_id: `mock-opening-${++epoch}`,
              epoch,
              confirmed_config: confirmed,
              tasks: [],
            };
            if (ticket !== generation || !dialog.open) return;
            active = { run_id: dispatched.run_id, epoch: dispatched.epoch ?? ++epoch, config: dispatched.confirmed_config || confirmed, tasks: dispatched.tasks || [], completed: false };
            phone.resolveConfirm({ request_id: request.request_id, status: 'accepted', ...active, confirmed_config: active.config, tasks: active.tasks });
          } catch (error) {
            if (ticket !== generation || !dialog.open) return;
            phone.resolveConfirm({ request_id: request.request_id, status: 'rejected', reason: error?.message || '正式派单失败' });
          }
        }, 150);
      },
      onOpeningReady(result) {
        if (!active || result.run_id !== active.run_id || result.epoch !== active.epoch) return;
        const key = `${result.run_id}#${result.epoch}`;
        if (delivered.has(key)) return;
        delivered.add(key); active.completed = true; readyCount++;
        welcome.hidden = true;
        dialog.close();
        onReady(structuredClone(active));
      },
    },
  });
  function cancelPending() {
    generation++;
    clearTimeout(pending); pending = null;
    if (active && !active.completed) active = null;
    phone.cancel();
  }
  function open() {
    returnFocus = document.activeElement;
    onOpen();
    if (!dialog.open) dialog.showModal();
    // Re-seeding on every open guarantees the single catalog project chip is
    // already the selected one: the phone starts from `projectId: null`, and a
    // lone justified option the user must still click is a redundant step.
    // `start()` clears selections, so this must come *after* it.
    // Always start a fresh configuration. The phone keeps its previous
    // collapsed/record stage after a completed run; the old guard only started
    // from idle/config, so opening the wizard for a *second* project showed
    // "开工流程已交宿主" instead of the role/member form. Closing the dialog
    // already cancels any pending request, so resetting here is safe.
    phone.start();
    phone.setCatalog(activeCatalog);
    // The phone-only project flow already represents a connected task space.
    // Skip the mock-only welcome/connect screens so "新建任务" lands directly
    // on the administrator and worker selectors.
    if (phone.getStage() === 'connect') {
      slot.querySelector('[data-action="connect-demo"]')?.click();
    }
    pruneAdministratorWorkers();
    seedSelection();
    pruneAdministratorWorkers();
    phone.setViewport({ width: slot.clientWidth });
    // Start from a real form control, not the close button, on keyboard entry.
    slot.querySelector('button')?.focus();
  }
  dialog.addEventListener('close', () => {
    cancelPending();
    if (returnFocus?.isConnected) returnFocus.focus();
  });
  dialog.addEventListener('cancel', e => { e.preventDefault(); dialog.close(); });
  dialog.querySelector('#openingCloseBtn').addEventListener('click', () => dialog.close());
  welcome.querySelector('#openingStartBtn').addEventListener('click', open);
  welcome.querySelector('#openingPreviewBtn').addEventListener('click', () => { welcome.hidden = true; onPreview(); });
  const resize = () => phone.setViewport({ width: slot.clientWidth });
  window.addEventListener('resize', resize, { passive: true });
  return {
    open,
    setProjectName(name) {
      const projectId = activeCatalog.projects[0]?.project_id;
      if (!projectId || !name) return;
      activeCatalog = {...activeCatalog, projects: [{project_id: projectId, name: openingProjectName(name)}]};
      phone.setCatalog(activeCatalog);
    },
    // 「新建项目」建立的只是本窗口里的项目上下文，不会向平台写新的 project_id。
    // 开工向导原本固定显示平台项目号 111 的名字（「正式协作项目」），于是刚填完的
    // 项目名在这里变成了另一个名字 —— 看起来像换了一个项目。这里让向导显示真实上下文：
    // 项目名保持本名，平台项目号放在边界说明里，避免派单标题带上「（平台项目 N）」。
    setProjectContext(name) {
      const projectId = activeCatalog.projects[0]?.project_id;
      if (!projectId) return;
      activeCatalog = {...activeCatalog, projects: [{project_id: projectId, name: openingProjectName(name)}]};
      projectScope = openingProjectScope(projectId);
      renderBoundary();
      phone.setCatalog(activeCatalog);
    },
    // 「新建项目」勾选的成员就是本项目的开工人选；换项目时整体替换。
    setProjectMembers(ids) {
      projectMembers = Array.isArray(ids) ? [...ids] : [];
    },
    // 每个项目的管理员**候选**：只列外部 Harness 窗口。平台 Agent 不在里面 ——
    // 它注册的是模型身份，不是可以替项目做规划的外部 harness。
    setProjectAdminCandidates(ids) {
      const list = Array.isArray(ids) ? [...ids] : [];
      activeCatalog = {...activeCatalog, instances: list.map(id => {
        const h = harnesses.find(item => item.harness_id === id);
        return {instance_id: id, display_name: h?.name || id, brand: id, available: true};
      })}; 
      phone.setCatalog(activeCatalog);
      pruneAdministratorWorkers();
    },
    hideWelcome() { welcome.hidden = true; },
    setReducedMotion(value) { phone.setReducedMotion(value); },
    debug() { return { mode: catalog.mode, capacity: catalog.capacity.max_workers, readyCount,
      run: active ? structuredClone(active) : null, phone: phone.debug(), entryStatus: active?.completed ? 'awaiting-entry-integration' : 'not-started' }; },
  };
}
