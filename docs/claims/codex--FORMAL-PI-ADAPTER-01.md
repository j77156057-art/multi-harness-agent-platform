# 认领：FORMAL-PI-ADAPTER-01

| 字段 | 值 |
|---|---|
| task_id | `FORMAL-PI-ADAPTER-01` |
| Harness | Codex（协调与适配器接线）；成员验收对象 Pi CLI + 本机 Ollama |
| 认领时间 | 2026-10-15 10:55 +08:00 |
| 分支 | `agent/local/pi/FORMAL-PI-ADAPTER-01/a1` |
| 状态 | doing |

目标是将本机 Pi CLI 接入正式 worker，并用 Ollama 本地模型完成一次真实、受 exclusive_paths 限制的任务。Gemini CLI 仅安装成功，认证探测返回错误码 41（缺少 API key / GCA / Vertex 配置），不计为本次成员。

范围仅含 Pi CLI adapter、注册、worker 映射、定点测试和本任务证据。不得读取或写入用户其他 Harness 的工作树，不得把服务层调用或 scripted runner 结果描述为真实模型执行。
