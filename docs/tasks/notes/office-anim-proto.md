### office-anim-proto：拟人协作动画原型（🟢 doing）

~~~text
任务编号：office-anim-proto
负责人 Harness：OpenCode
开始时间：2026-10-09 02:10
工作区：.worktrees/opencode/
依赖任务：无
真相源引用：D:\Tools\Godot（引擎 4.7.2 + 导出模板齐全）、
            D:\ComfyUI（模型探查）、docs/prototypes/office-anim/README.md
允许修改目录：docs/prototypes/**、docs/claims/**、docs/traceability.md、TASKS.md
禁止修改目录：backend/**、frontend/**、docs/spec/**
交付分支：agent/local/opencode/office-anim-proto/a1
test_id：MVP-UI-006
当前状态：🟢 doing（原型已交付并自验；未接后端、未入 Vue 路由）
commit：a1 = `2f7c555`

### 交付物

- `docs/prototypes/office-anim/godot/`：Godot 4.7 工程，8 个拟人角色
  = 8 个 `worker.tscn` 实例，**全部程序化绘制，无外部资产依赖**。
- `docs/prototypes/office-anim/web/`：导出 Web 版，`index.wasm` 37.7MB。
  headless 导出零错误；浏览器控制台实测有 Godot 引擎横幅 +
  `WebGL 2.0` 上下文 + **0 条 GDScript 运行时错误**。
- `docs/prototypes/office-anim/shots/`：4 张参考图。
- `README.md`：怎么跑、怎么换资产、ComfyUI 实况与建议。

### 交互：任务表轮传

拖动「任务表轮数」（1–5，默认 2）。用户要求的两条提示按面值实现：

    轮数 < 1 → 「不好好细化一下任务表吗？」
    轮数 > 3 → 「这会不会太多了，可能要多花点钱哦」

角色由群聊真实消息驱动：领任务→敲代码；摸鱼→偷吃/偷看屏幕/偷偷睡觉
（随机点缀，只在没正事时）；完成→起身伸懒腰+星星+「任务完成」；
卡住→抓耳挠腮+怒纹；求救→别人**走过去**帮忙；管理员→代码审核。

### 本环境的硬约束与验证手段

**我只能用命令行验 Godot** —— 起不了带窗口的交互，也截不了桌面。
于是加了 `tools/shot.tscn`：让引擎**自己**把某一帧存成 PNG。

    Godot --path . tools/shot.tscn -- --autoplay --frames 520
    Godot --path . tools/shot.tscn -- --jump 25 --frames 90

`--autoplay/--rounds/--jump` 透传给 main.gd。**刻意标注这不是产品功能**，
只为不开窗口验证中段画面，定稿时应删或改成正规测试钩子。
实测 Godot 在真实 NVIDIA GPU（OpenGL 3.3.0 / RTX 5070 Ti）上跑通出图。

### 两个 Godot 4.7 的坑（都是硬报错，值得后人少踩）

1. **退化多边形**：给「star」发饰传了 5 个**共线**点，
   `draw_colored_polygon` 报 `Invalid polygon data, triangulation failed`，
   每帧 18 条刷屏。改真五角星（外 5 + 内 5 共 10 点）。
2. **`draw_rect` 第 4 参不是圆角**：我写 `draw_rect(rect, color, true, 10.0)`
   想做圆角，那是 `width`，且 `filled=true` 时被忽略 —— 圆角没生效，
   还每帧刷 WARNING。改用 `StyleBoxFlat` + `draw_style_box`。
   另：Godot 4.7 原生有 `draw_ellipse(center,w,h,color)`，
   自己写同名 helper 会覆盖原生方法并触发 Warning-as-error。

### ComfyUI 结论：不用生视频做动作

本机 VRAM 12227MB / RAM 31935MB：

| 用途 | 模型 | 体积 | 可用 |
|---|---|---|---|
| 生图 | `unet/z_image_turbo-Q8_0.gguf` | 6.7GB | ✓ |
| 文本编码 | `clip/Qwen3-4B-Q8_0.gguf` | 4.0GB | ✓ |
| VAE | `vae/ae.safetensors` | 0.3GB | ✓ |
| 动漫放大 | `upscale_models/4x-AnimeSharp.pth` | 64MB | ✓ |
| 生视频 | `diffusion_models/minimax_h3_*` ×2 | 19.5GB ×2 | ✗ |

**视频不可用的实证**（comfy_launch.log）：上次运行
`Prompt executed in 368.55 seconds` 后紧跟
`Windows fatal exception: access violation` —— 单条 6 分钟且进程崩。

故：生图模型只出立绘，动作用前端程序化动画。除跑不动外，
更致命的是**跨片段保持同一角色长相**——视频生成经典难题，
编不好就是「第 3 个镜头里她换了张脸」。
`worker.gd` 已留 `head_texture` 插槽，贴立绘时骨架/动作/状态机都不用改。

### 架构：不猜动画时长

`story.gd` 的 `scene_at(script, upto, rng)` 是唯一出口，渲染层每帧问一次、
**不持有时间轴**。抄 `Agentshire` 的结论：它用 `setTimeout(3000)` 串动画，
在逻辑层猜 Three.js 播了多久，两个时间模型对不上，长出
「场景错乱/幽灵 NPC/工位撞车」。解法是「只发意图，前端播完回传」。

### 一次真实的误操作（如实记录）

做生图验证时**没先看进程列表**就直接起了第二个 ComfyUI 实例。
日志随后暴露：机器上本来就有 ComfyUI 在跑（占 11.1GB 显存，
还有人在跑 `gen.py jobs_actions.json`）。我的实例因此报
`Database is locked`，还在抢别人 GPU。已停掉我误起的 PID 30260，
确认他人 PID 19000 未受触碰，显存随即从 11163 → 3262 MiB。

**动共享 GPU 资源前先看进程列表。**

### 验证结果

    docstate    exit=0   90 项检查，0 项失败
    discipline  exit=0   44 项检查，0 项失败
    ruff check  All checks passed!
    pytest      本轮未改 backend/**，未跑（下轮补）
    Godot       headless --quit 0 错误；headless --quit-after 200 0 错误；
                windowed 在真 GPU 上出 4 张 PNG；Web 导出浏览器 0 运行时错误

### 尚未做

1. **未接后端**：状态机是写死的演示脚本，不是真实 harness 事件。
2. **未入 Vue 路由**：定稿后用 `frontend/` 一个 iframe 嵌 `web/` 即可。
3. **无 AI 立绘**：角色全是程序化方块/圆。ComfyUI 那条要单独占 GPU，
   且他人正在用 —— 需要协调后再做。
4. **`canvas-fallback/` 是早期手搓 Canvas 版**，已被 Godot 版取代，
   留作对照，未删。
~~~
