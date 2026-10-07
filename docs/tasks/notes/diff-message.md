### diff-message：diff 消息进任务线程（🚧 doing）

~~~text
任务编号：diff-message
负责人 Harness：Qoder
开始时间：2026-10-08 04:48
依赖任务：frontend-thread（✅ fb30ae9；任务线程视图=本卡的落点）；
          paperclip-buzz-source（✅ 553ee0f；机制学习来源）；
          jsonl-export（✅ 967dc7f；ADR-DEV-004 只读集成面的先例）
工作区：.worktrees/qoder/
真相源引用：R10 §22（「右：任务/负责人/Lease/审查」——线程承载任务活动；
            前端只读、操作走 API）；§15（异步后置：diff 是阅读面，绝不回写
            权威状态）；ADR-DEV-003（唯一真相源：diff 从 canonical 仓的
            commit 事实计算，不落库、不发明数据）；学习机制出处：Buzz
            kind:40008——`buzz-sdk/src/builders.rs` `build_diff_message`
            （DiffMeta：repo/commit/parent-commit/branch/file/language/
            description/truncated/alt + 正文=patch + 60KB 上限）与
            `buzz-relay/src/handlers/ingest.rs` `validate_diff_event`
            （同口径校验）——已逐行核读，代码原生重写
范围声明：
  - **本切片**（两件）：
    ①**后端 diff 端点**（只读、零迁移、零新事件）：`GET /tasks/{task_id}/diff`
      （`?attempt_id=&include_patch=`）。attempt 解析=指定者或 current attempt
      （无则取最新有 `head_commit` 的 attempt）；base/head=attempt 的
      `base_commit`/`head_commit`（claim 冻结 + publish 写入的权威事实）；
      stat=`git diff --numstat`（文件级 +增/−删、二进制识别）；patch=
      `git diff` 输出，**服务端固定上限 64 KiB**（Buzz 60KB 的同族），超限
      `truncated:true`；对象不在 canonical（未发布/已回收）→
      `available:false` + reason，**不发明空 diff**。未知任务 404。
      Buzz DiffMeta → 我们的字段映射（如实记录差异）：repo→canonical 仓
      （本地对象库，无 URL——我们没有远端，声明不同不硬造）；commit→
      head_commit；parent-commit→base_commit；branch→workspaces 记录
      （尽力而为）；truncated→同名；file/language/description/alt→**出范围**
      （不做逐文件 patch 与语法标注）。
    ②**前端 diff 卡**（TaskPane 线程头部下）：文件表（路径 + `+n`/`−m`，
      二进制标注）、总计行（N 文件 +X/−Y + base..head 短哈希）、「展开/
      收起 patch」（按需 `include_patch=1`，截断提示）、`available:false`
      的 muted 说明、加载态；数据编排进 session store（`openTask` 同取、
      `expandDiff()`），TaskPane 保持 props/emit 纯渲染。全部用既有对比度
      断言对（ok/bad/muted/ink），不新增 token。
  - **显式出范围**（记录不静默）：merge/review 维度的 diff（本卡=attempt
    维度）；逐行/词级高亮与并排视图；diff 落库或进 `task_messages`（我们的
    线程是事件投影 + 按需计算；落库=第二份真相源，不做）；WebSocket 推送
    （沿用轮询/手动刷新先例）；外部仓库 URL 语义（Buzz 的 repo tag 要
    http(s) URL——我们无远端，映射为 canonical 本地对象库，不硬造 URL）。
  - **零迁移、零新事件、零新表**（`git diff` 的输入是已有的
    `task_attempts.base_commit/head_commit` 与 canonical 对象）。
允许修改目录：backend/app/git/**（新增 diff 模块）、backend/app/tasks/**
            （仅 diff 查询方法）、backend/app/api/**（路由+schema）、
            backend/tests/**、frontend/**（api/store/组件/样式/测试）、
            docs/traceability.md、TASKS.md、docs/tasks/notes/diff-message.md、
            docs/claims/qoder--diff-message.md
禁止修改目录：backend/migrations/**（零迁移）、backend/app/state/**、
            backend/app/claim/**、docs/spec/**、tools/**
交付分支：agent/local/qoder/diff-message/a1
完成标准：
  1. **后端统计正确**（真 git 仓 pytest）：publish 后按 attempt 取 diff——
     文件数/增删行与真实 commit 一致；未发布的 attempt → `available:false`
     + reason（无 attempt 同理）；`attempt_id` 指定与默认解析各自命中；
     未知任务 404；二进制文件按 numstat 的 `-` 识别（不假装 0）
  2. **patch 边界**：正常 patch 原样；`patch_max_bytes` 小于输出时
     `truncated:true` 且内容不超上限（字节级断言）；默认响应不含 patch
     （按需拉取）
  3. **前端四态**（vitest）：文件表渲染（含二进制标注）、展开 patch
     （emit 后 store 再取）与截断提示、`available:false` muted 行、
     loading 态；api 客户端 URL 拼接（attempt_id/include_patch 编码）
  4. `vue-tsc`、`vitest`、`vite build`、`check:contrast` 四件全绿（对比度
     不新增对——只用已断言组合）
  5. **真浏览器实测留档**：真后端（临时 data + 种子）+ vite dev，浏览器里
     选中任务→看到 diff 卡（文件/计数）→展开 patch→截断或正常显示；
     console/network 零错留档
  6. `PYTHONUTF8=1 ./scripts.sh check` 全绿；traceability 新增 `MVP-UI-002`
test_id：MVP-UI-002（新登记，形态同 MVP-UI-001）
测试命令：PYTHONUTF8=1 ./scripts.sh check；cd frontend && npm run typecheck &&
          npm test && npm run build && npm run check:contrast
当前状态：doing
~~~
