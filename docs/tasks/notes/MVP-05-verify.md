### MVP-05-verify：MVP-05 独立验收（✅ done，通过附遗留清单）

~~~text
任务编号：MVP-05-verify
负责人 Harness：TRAE
开始时间：2026-10-05 23:34
依赖任务：MVP-05（代码已合 main；2883302 合入，验收基线取最新 main 719fca6）
工作区：.worktrees/trae/
真相源引用：R10 §13（canonical 拓扑 / Push Proxy / fencing 执行点）、
            §14（worktree 与分支命名、归档、容量）、§17（权限矩阵）、
            §15（side_effect_intents 字段与状态）、§12（命令幂等键）、
            docs/spec/mvp-task-checklist.md MVP-05（交付/完成标准/test_id、
            单账户降级要求）、docs/adr/ADR-DEV-002-single-account-acl.md、
            docs/prep/MVP-08/openapi.yaml（/adapter/publish_task_branch 契约）
允许修改目录：TASKS.md、docs/claims/36-trae--MVP-05-verify.md（验收者不改被验代码）
禁止修改目录：docs/spec/**、backend/**、tools/**、docs/adr/**、docs/prep/**、
            docs/claims/ 下非本任务的文件
交付分支：agent/local/trae/MVP-05-verify/a1
背景：MVP-05 由 Qoder 先合 main 后补卡（1d83799 为满足 merge 门禁的补登记），
      claims/29-qoder 仍停在 doing、无独立验收结论。本任务补机械核查，
      决定 MVP-06 能否以它为基线。
核查计划（逐条双证据：代码行 + 机械断言，不采信卡片自陈）：
  GIT-ACL-001（两个任务不共享可写 worktree）：
    1. 分支命名唯一实现 agent/<group-short>/<harness>/<task-id>/<attempt-id>，
       §14 白名单与 Push Proxy 共用同一函数（R10-09）；
    2. provision 按 attempt 幂等、活动路径互斥、短 ID 目录、容量上限；
    3. alternates 只读指向 canonical objects，gitdir 在 canonical 之外；
    4. 归档 bundle verify 通过才回收（真目录消失），patch + artifacts 落库。
  GIT-ACL-002（直接写集成分支失败；旧 epoch 发布失败）：
    1. canonical pre-receive 真拒绝客户端 push（真跑 git push，含 agent 分支）；
    2. publish 校验顺序：epoch / 分支白名单 / alternates / commit 可达 /
       幂等键；旧 epoch 拒绝且不写 intent、不移动 ref；
    3. 对象导入走 scheduler 侧 fetch + update-ref，不经接收端口；
    4. 幂等重放返回首发结果；head_commit 回写 attempt。
  契约对齐：HTTP 路由 /adapter/publish_task_branch 与 openapi 字段/状态码；
  ADR-DEV-002：insecure_single_account 默认与开关、阶段 2 清单是否写明。
test_id：GIT-ACL-001、GIT-ACL-002
测试命令：cd backend; ..\.venv\Scripts\python.exe -m pytest tests/test_publish.py
          tests/test_git_worktree.py -v；全量 ..\.venv\Scripts\python.exe -m pytest -q
当前状态：✅ done（通过附遗留清单；无指向 MVP-05 交付物的 P1）
完成时间：2026-10-06 00:05
commit：f8f2483（验收证据提交；本行为随附回填提交）
验证结果：
  结论：✅ 通过。GIT-ACL-001/002 逐条双证据全部成立，证据套件 19/19
  退出 0。完整逐条结论、门禁数字与 6 项遗留/观察已回填于本文件 MVP-05
  卡末「验收意见（TRAE，2026-10-06 00:05，基线 719fca6）」，此处只摘门禁：
  - docstate 90/0、discipline 39/0、pytest tools 14 passed；
  - backend：证据套件 test_publish.py + test_git_worktree.py 19/19
    退出 0；除 tests/test_merge_guard.py 外全量 135 项退出 0
    （该文件在本机 pytest 会话确定性硬崩，属 OpenCode merge-interlock，
    详见 MVP-05 卡遗留 5；不影响 GIT-ACL 判定）；
  - ruff check All checks passed、ruff format 50 files already formatted；
  - 环境处置：共享 .venv 原解释器被原地升级 3.13.12→3.13.14 致
    subprocess reader 线程崩溃，已按 backend/requirements(-dev).txt
    锁定版本用系统 Python 3.13.3 重建（旧目录改名 .venv.bak，junction
    与依赖版本均未变）。
~~~
