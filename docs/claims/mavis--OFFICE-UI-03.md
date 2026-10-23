# 认领：OFFICE-UI-03

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-03` |
| Harness | Mavis |
| 认领时间 | 2026-10-23 11:05 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-02/a1` |
| 状态 | doing |

目标：让用户能**自己加一个 provider 的 API key**，并在免费额度池拿不出额度时，
由平台**来问**用户要不要用自己的 key 继续做 —— 点了头才花。

这条要动后端（额度池的第四档 `user`、用户 key 的登记与授权、池子拒绝时的
状态码），所以另立一张卡：OFFICE-UI-02 的范围只到 `office/**`，
把后端改动塞进去会让那张卡的「禁止修改 backend/**」名存实亡。

范围：

- 后端：`backend/app/pool/**`（用户 key 的登记、第四档 upstream、路由与拒绝）、
  `backend/app/auth/policy.py`（加一行 `pool:user_keys`，只给 human）、
  `backend/tests/test_pool_user_keys.py`；
- 前端：`office/**`（额度池里的「自己的 API key」区块、网关转发、冒烟）；
- 登记：`TASKS.md`、`docs/claims/mavis--OFFICE-UI-03.md`、
  `docs/tasks/notes/OFFICE-UI-03.md`、`docs/traceability.md`。

不碰 `docs/spec/**`（R10 正文只读）：**不建表、不改 schema、不改状态机、不改事件名**。
用户自己的 key 是**运行期凭据**，不进会被 diff 的 schema；它落在数据目录下的
`data/user-keys/`（一个 key 一个文件 + 一个索引），与平台令牌 `data/*.key` 同一片地方。

不碰 `backend/migrations/**`、不改 `schema_version`。
