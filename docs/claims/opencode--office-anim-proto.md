# 认领：office-anim-proto

<!-- OpenCode 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `office-anim-proto` |
| Harness | OpenCode |
| 认领时间 | 2026-10-09 02:10 |
| 分支 | `agent/local/opencode/office-anim-proto/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `office-anim-proto`。

## 要做什么

给项目做**前端动画原型**：把「群主派任务 → 管理员细化 → 任务表轮传给群员 →
群员提意见 → 管理员优化 → 群员认领 → 干活 → 管理员审核」这条流程，表现成

- **左侧（主区）**：拟人化办公室场景，每个 harness 是一个角色，坐在工位上，
  按状态播放对应动画（敲代码、摸鱼、吃饭、睡觉、伸懒腰、帮忙、审核）。
- **右侧**：手机群聊页面，显示真实内容（任务表、消息、状态）。

用户要求的**任务表轮传机制**：默认轮 2 次，可调；
轮数 < 1 时提示「不好好细化一下任务表吗？」；
轮数 > 3 时提示「这会不会太多了，可能要多花点钱哦」。

## 交付范围（本期只做原型，不接后端）

- 一个**可直接双击打开的 HTML 原型**（零构建、零依赖），先把效果与交互定下来；
- 拟人角色立绘：用本机 ComfyUI 生图模型生成；
- 资产管线说明（什么模型、什么步骤、怎么复现），供后续换真资产。

**明确不做**：接后端 WebSocket、写进 Vue 路由、改 `backend/**`。
原型定稿后再谈集成。

## 本机 ComfyUI 实况（决定了资产方案）

启动探查结果，先说结论：**本机的视频模型不可用，生图模型可用**。

    Total VRAM 12227 MB / RAM 31935 MB（RTX 5070 Ti Laptop）

- 唯一的视频模型是 MiniMax H3（`diffusion_models/` 下两个 19.5GB int8），
  装不进 12GB 显存。`comfy_launch.log` 里上一次运行的实证：
  `Prompt executed in 368.55 seconds` 之后紧跟
  `Windows fatal exception: access violation` —— **单条 prompt 6 分钟且把
  ComfyUI 进程搞崩了**。
- `checkpoints/` 里只有占位文件（没有 SD1.5/SDXL/FLUX checkpoint）。
- 可用的生图链路是 **Z-Image Turbo**：
  `UnetLoaderGGUF(z_image_turbo-Q8_0.gguf)` 6.7GB +
  `CLIPLoaderGGUF(Qwen3-4B-Q8_0.gguf)` 4GB +
  `VAELoader(ae.safetensors)` 320MB ≈ 11GB，能塞进 12GB。
  另有 `upscale_models/4x-AnimeSharp.pth`（动漫放大，正好对口）。

## 因此采用的方案（重要决策）

**生图模型出角色立绘，动作用前端程序化动画，不用生视频模型出动作。**

理由：

1. 视频模型在本机**跑不动**（上述 6 分钟 + 崩溃的实证）；
2. 就算跑得动，每个动作每个角色各出一段视频 = 几十段 6 分钟的渲染，
   且**跨片段保持同一个角色长相**是视频生成的经典难题；
3. 用户要的动作（敲键盘、吃零食、偷看屏幕、睡觉、伸懒腰、走去帮忙、
   「任务完成」牌子、抓耳挠腮、审核代码）全是**小循环**，
   程序化动画可以精确控制、零运行成本、可重复、可随时改；
4. 参考先例也是这么做的：`Star Office UI`（像素办公室，角色按状态移动）、
   `Agentshire`（3D 小镇）。后者一条架构教训直接抄：
   **用「意图 + 前端回传」推进编排，不要用 setTimeout 猜动画时长**
   —— 它踩过「Bridge 在逻辑层猜 Three.js 动画播了多久」导致
   幽灵 NPC / 工位撞车的坑。

动画形态：Canvas 2D 骨骼式程序化动画（比 CSS 关键帧更利于
「同一角色多状态切换」「走路到别人工位」这类复合动作），
立绘以图层叠加进 Canvas，保证角色长相与立绘一致。

## 变更记录

- 2026-10-09 02:10：认领。