# 认领：OFFICE-UI-32

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-32` |
| Harness | Mavis |
| 认领时间 | 2026-10-25 17:20 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-32/a1` |
| 状态 | doing |

目标：**开工向导里「任务目标」输入框第一眼就看得见**（现在它排在滚动区最下方）。

## 现状（真跑量过）

向导 config 阶段的实测几何：目标框 `top=912`，而滚动容器 `.oop-body` 的可视区
是 `top=208 ~ bottom=798`（`scrollHeight=888 > clientHeight=591`）。也就是说目标框
落在可视区**下面** 100 多像素处 —— 用户得先选项目、管理员、成员、传阅轮数，再往下
滚，才看得到「你要做的是什么事」。这不是 `<details>` 折叠，是组件表单的**顺序**：
目标框被排在最后。

## 范围

- `office/src/opening-host.js`（宿主）：组件 `office/contributions/office-opening-phone`
  是**锁定贡献**（文件不能改），宿主用 MutationObserver 修渲染结果已有先例
  （`patchSummaryNote` / `patchAdminLabel`）。这一张沿用同一手法：每次组件重渲染后，
  把**组件自己的**目标输入框搬到表单最前面。

## 怎么做到不出错

1. **不新增第二个输入框**：搬的是组件自己那个 `textarea[data-field="goal"]`，
   组件的状态、校验、摘要全都照旧（不引入两处不同步）。
2. 组件每次 `renderConfig` 都会重建表单 → 搬移必须每次重渲染后都做（MutationObserver）。
3. 不改组件文件、不改组件的取值与校验逻辑；派单摘要里的目标仍来自组件的 `state.sel.goal`。

## 完成标准

1. 打开向导的 config 阶段，「任务目标」输入框在**第一屏可见**（不用滚）。
2. 组件行为不变：目标照旧进摘要、照旧参与校验与派单。
3. 办公室冒烟与既有向导走查全绿。
