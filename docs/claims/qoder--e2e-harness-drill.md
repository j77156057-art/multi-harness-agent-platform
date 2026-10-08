# 认领：e2e-harness-drill

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `e2e-harness-drill` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 08:16 |
| 分支 | `agent/local/qoder/e2e-harness-drill/a1` |
| 状态 | 🚧 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `e2e-harness-drill`。

## 变更记录

- 2026-10-08 08:16：认领（协调者指令：Codex+OpenCode 真实联调 + 故障演练 +
  前端追溯验收）。环境侦察（如实）：codex CLI 在桌面应用包内
  （`AppData/Local/OpenAI/Codex/bin/codex.exe`，不在 PATH，`exec --json`
  事件流实测可用）；opencode v2.0.22 已装；两者均需 relay
  （`~/.codex/geekspace-proxy.mjs`，8402，relay=dbkj）在线；opencode 默认模型
  区域受限，已按授权为其配置指向 relay 的 provider（
  `~/.config/opencode/opencode.json` 最小新增，未改其它）。冒烟双双通过
  （codex `pong` + 带 usage；opencode `pong`）。
  另注：relay 脚本内含明文密钥（读取时已入会话记录，未复制到任何文件；
  分享会话记录时留意）。**若演练过程发现平台缺陷**：能用测试钉死的小修
  在本卡内落地（含门禁）；大型改动另立卡，如实移交。
