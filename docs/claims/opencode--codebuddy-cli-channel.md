# 认领：codebuddy-cli-channel（CodeBuddy 正式通道两处真缺陷）

<!-- OpenCode 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `codebuddy-cli-channel` |
| Harness | OpenCode |
| 认领时间 | 2026-10-17 16:40 |
| 分支 | `agent/local/codex/FORMAL-MULTI-HARNESS-01/a1`（与 R10-fix-13 同一工作区，见其认领文件的「分支说明」） |
| 状态 | doing：三处缺陷已修并各有回归测试；真实 CLI 复跑与线上端到端取证通过；待 Merge Gate |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `codebuddy-cli-channel`。

## 背景：这条通道是被上证实的真缺陷打断的

`codebuddy-member-1` 曾真实领取 `FD-d5c10f46dae09e42` 并以 `CliFailed` 结束，
失败现场 `data/formal-runs/FD-d5c10f46dae09e42.failure.json`。当时只记录了这个现象，
**本轮由群主明确授权修**。修的过程里发现是**两个独立缺陷**，不是同一个。

## 缺陷 1：变参 `--add-dir` 把工作单吞了

原 argv：

    codebuddy.CMD -p --output-format json --permission-mode bypassPermissions \
        --add-dir <worktree> <work_order>

`--add-dir` 是**变参**（`--add-dir <directories...>`），后面那个位置参数被当成第二个目录
吃掉了。真实 CLI 于是拿到一个空提示词，回 `"result": ""`，适配器把"空"读成"这个 harness
什么都没产出"。第一步修法：把位置参数（工作单）放到变参之前，变参收尾 —— 与 `qoder_command`
已有的形状一致（`-w <path>` 再跟工作单）。同时抽成模块级 `codebuddy_command()`，
让第二个调用方复用同一份 argv 而不是留一份会漂移的副本。

**这一步并不够。** 它只解决了"提示词被当成目录"，没有解决"多行提示词怎么送进去"，
见缺陷 3。最终形状是把工作单**移出 argv**、改走 stdin，所以现在 `codebuddy_command()`
只接受 `worktree`，argv 里没有任何位置参数。

## 缺陷 2（修完第 1 个才暴露）：解析器把一份 JSON 数组当 NDJSON 读

按上面改完，真实 CLI `rc=0`、`"result": "OK"`，**但** `parse_output` 返回的
`final_message` 是一段 JSON 语法碎片，`session_id` 完全没取到。

原因：这个版本的 CodeBuddy 在 `--output-format json` 下打印的是**一整份美化过的 JSON
数组**（整场会话一条文档），而 `parse_output` 逐行 `json.loads`。逐行解析全部失败 →
落到 `stdout.strip()[-4000:]` 兜底 → 把尾巴当成 harness 的消息。既有测试用的是
单行 NDJSON 假 stdout，所以这个缺陷在测试里永远看不到。

修法：先尝试把**整份** stdout 当 JSON 文档解析（dict 或 list），失败再退回逐行 NDJSON，
最后才用原始尾巴兜底。两种形状都保留，因为哪种形状出现取决于调用方式/版本。

## 缺陷 3（修完第 1 个才暴露）：多行位置参数被截到第一行

把工作单挪到变参之前后，真实任务 `FD-8438d596ff9225e6` 仍然失败：CLI `rc=1`，
stderr 是 `Refused: no committed change against base ...`。失败现场
`data/formal-runs/FD-8438d596ff9225e6.failure.json` 里的转写给出了原因 —— CLI 自己的
`<user_query>` 里只有工作单的**第一行**：

    <user_query>You are harness codebuddy-member-1, working task FD-8438d596ff9225e6 …</user_query>

模型因此看不到任务规格与验收条件，只能回"我不知道要做什么"，一个 attempt 被烧掉。
隔离复现（四行提示词，与任务无关）：

    位置参数： <user_query>Line1 alpha</user_query>     ← 只有第一行
    stdin  ： <user_query>Line1 alpha\nLine2 beta\nLine3 gamma\n\nReply with …</user_query>
              模型回复 "Line1, Line2, Line3"

所以位置参数这条通道本身就不成立：**多行内容会被截断**。修法：工作单改走 stdin
（`-p` 的文档口径就是 "useful for pipes"），argv 只留 flag 与 `--add-dir`。
`CliHarness.prompt_via_stdin` 由适配器声明，`SubprocessRunner` 在需要时才建管道，
其余 CLI 仍拿 `DEVNULL`（不能因为一个新适配器就让所有 CLI 等 stdin）。

## 证据（真实 CLI，不是 mock）

### 缺陷 1 + 2 的取证

    ARGV = [".CMD 路径", "-p", "--output-format", "json", "--permission-mode",
            "bypassPermissions", "Reply with exactly OK and do not modify any file.",
            "--add-dir", "D:\\Temp\\cbargv3-..."]
    rc                = 0
    转写字节          = 17736
    ADAPTER parsed    = {"session_id": "01a148f6-ab51-...", "final_message": "OK"}
    final_message ok? = True
    session_id ok?    = True

### 缺陷 3 的线上端到端取证（真任务、真成员、真审查）

    FD-dd3a044074777d1d.a1  commit 0e1dada1  verify rc-24b3efc1  → awaiting_review
    管理员 harness（office-admin / qoder）跨提供方 request_changes + 4 条意见
    FD-dd3a044074777d1d.a2  commit 5acdda3d  verify rc-b07b9c94  → awaiting_review
    该轮 goal 里 findings 原文 4 条全部命中（F1/F2/F3/F4），交付物 round-2.md 逐条处置

也就是说：修复后不止"rc=0"，而是**整条正式通道跑完了一轮审查返修闭环**。

转写里能直接看到提示词确实作为用户轮次到达 CLI：

    <user_query>Reply with exactly OK and do not modify any file.</user_query>
    ..."type": "result", "subtype": "success", "result": "OK"...

## 测试（都是"能失败"的测试）

- `test_codebuddy_argv_carries_no_prompt_and_the_order_goes_on_stdin`：断言 argv 里
  **没有任何位置参数**（位置参数这条通道被真实 CLI 截断，见缺陷 3），并断言适配器声明
  `prompt_via_stdin = True`。
- `test_a_multiline_positional_would_lose_everything_after_line_one`：把"多行位置参数"
  这个失败形状原样重建，断言工作单确实是带换行的位置参数 —— 否则上一条测试等于
  "跟什么都同意"。
- `test_each_adapter_delivers_the_work_order_on_the_channel_its_cli_reads`：按适配器
  断言传输边界 —— 声明了 `prompt_via_stdin` 的必须收到完整多行 stdin，其余必须
  `stdin_text is None`（不许因为一个新适配器把所有 CLI 都改成读管道）。
- `test_the_subprocess_runner_pipes_the_whole_multi_line_prompt`：真实子进程读 stdin
  并写回文件，逐字节比对（含中文与空行）——证明 runner 层没有二次截断。
- `test_the_subprocess_runner_still_gives_devnull_without_a_prompt`：不传 stdin 时子进程
  读到 EOF 而不是挂住。
- `test_the_variadic_replay_fails_on_the_old_argv`：把**改坏前**的 argv 原样喂给同一个
  复现器，断言它确实丢掉提示词 —— 否则上一条测试等于"跟什么都同意"。
- `test_codebuddy_reads_the_pretty_printed_array_transcript`：用**真实转写裁出来的**
  形状（整份数组、user 轮次 content 是 list、result 对象带 session_id）断言
  `final_message == "OK"`、`session_id` 取到。
- `test_the_array_transcript_really_is_not_line_delimited`：断言这份样本**真的**不是逐行
  可解的（没有任何一行单独带 `result`）—— 否则这条测试钉不住缺陷。
- `test_codebuddy_still_reads_one_object_per_line`：另一种形状不许被改坏。

`tests/test_real_adapters.py` 原有的 argv 契约断言同步更新为"CodeBuddy 的 argv 只有
flag 与 `--add-dir`，工作单走 stdin"。

## 验证方式

~~~bash
cd backend
../.venv/Scripts/python.exe -B -m pytest tests/test_real_adapters.py -q
../.venv/Scripts/ruff.exe check . && ../.venv/Scripts/ruff.exe format --check .
~~~

## 变更记录

- 2026-10-17：认领并修复两处缺陷；真实 CLI 复跑取证。
- 2026-10-17：线上真任务 `FD-8438d596ff9225e6` 暴露第三处缺陷（多行位置参数被截到
  第一行）；改为 stdin 投递工作单，新增 `prompt_via_stdin` 与 4 条传输层测试；
  以 `FD-dd3a044074777d1d` 跑通「提交 → 跨提供方审查 → 返修」完整闭环。
