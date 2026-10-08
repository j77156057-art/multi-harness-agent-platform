# 项目办公室 · 拟人协作动画原型

把这条流程做成动画：

```
群主派任务 → 管理员细化成任务表 → 任务表轮传给群员 → 群员提意见
   → 管理员优化 → （可调轮数，默认 2）→ 群员认领 → 各自干活
   → 卡住的求救、别人赶去帮忙 → 管理员代码审核 → 通过
```

**左侧**是拟人办公室：每个 harness 是一个 Q 版小人，坐在工位上，按群里真实
消息切换状态。**右侧**是手机群聊，显示真实内容（任务表、消息、认领、完成）。

## 现在怎么看

```bash
# 方式一：浏览器里看（推荐，零安装）
#   web/ 目录已导出好，起个静态服务器即可
cd docs/prototypes/office-anim/web
python -m http.server 8137
#   然后开 http://127.0.0.1:8137/index.html

# 方式二：跑 Godot 工程（可交互、可改）
D:\Tools\Godot\Godot_v4.7.2-stable_win64.exe ^
  --path docs/prototypes/office-anim/godot
```

## 目录

```
docs/prototypes/office-anim/
├── godot/                    # Godot 4.7 工程（主交付）
│   ├── project.godot
│   ├── export_presets.cfg    # Web 预设
│   ├── scenes/main.tscn      # 主场景
│   ├── scenes/worker.tscn    # 单个角色（复用 8 次）
│   ├── scripts/
│   │   ├── story.gd          # 剧情 + 状态机（不持有时间轴）
│   │   ├── worker.gd         # 角色绘制与动作
│   │   ├── backdrop.gd       # 办公室背景
│   │   └── main.gd           # 编排 + 右侧手机 UI
│   └── tools/shot.tscn       # 参考图工具（见下）
├── web/                      # 导出的 Web 版（37MB wasm）
├── shots/                    # 参考图
└── canvas-fallback/          # 早期手搓 Canvas 版，已被 Godot 版取代
```

## 参考图怎么来的（重要）

这个仓库里我只能用命令行验证 Godot 工程 —— 起不了带窗口的交互，也截不了桌面。
所以加了一个 `tools/shot.tscn`：让引擎**自己**把某一帧存成 PNG。

```bash
GODOT="D:\Tools\Godot\Godot_v4.7.2-stable_win64.exe"
PROJ=docs/prototypes/office-anim/godot

# 默认 90 帧后抓一张
"$GODOT" --path "$PROJ" tools/shot.tscn

# 播到中段：自动播放 + 等 520 帧
"$GODOT" --path "$PROJ" tools/shot.tscn -- --autoplay --frames 520

# 跳到第 25 条消息（审核阶段）再抓
"$GODOT" --path "$PROJ" tools/shot.tscn -- --jump 25 --frames 90

# 4 轮任务表
"$GODOT" --path "$PROJ" tools/shot.tscn -- --rounds 4 --jump 6 --frames 90
```

`--autoplay` / `--rounds` / `--jump` 会透传给 `main.gd`。这些参数**不是产品功能**，
只为验证；定稿时应删掉或改成正规的测试钩子。

产出落在 `%APPDATA%\Godot\app_userdata\<项目名>\shot.png`。
（项目名是中文，那个目录名在某些终端里显示为乱码，但文件本身没问题。）

## ComfyUI 生图：本机实况与建议

**结论先说：本机的视频模型不可用，生图模型可用；动作不要用生视频做。**

探查结果（`D:\ComfyUI`）：

```
Total VRAM 12227 MB / RAM 31935 MB（RTX 5070 Ti Laptop）
```

| 用途 | 模型 | 体积 | 能不能用 |
|---|---|---|---|
| 生图 | `unet/z_image_turbo-Q8_0.gguf` | 6.7 GB | ✅ 可用 |
| 文本编码 | `clip/Qwen3-4B-Q8_0.gguf` | 4.0 GB | ✅ 可用 |
| VAE | `vae/ae.safetensors` | 0.3 GB | ✅ 可用 |
| 动漫放大 | `upscale_models/4x-AnimeSharp.pth` | 64 MB | ✅ 可用 |
| 生视频 | `diffusion_models/minimax_h3_*_int8_convrot.safetensors` ×2 | 19.5 GB ×2 | ❌ 装不进 12GB 显存 |

**视频不可用的实证**：`comfy_launch.log` 里上一次运行是

```
Prompt executed in 368.55 seconds
Windows fatal exception: access violation
```

单条 prompt 6 分钟，还把 ComfyUI 进程搞崩了。

### 所以动作怎么做

**前端程序化动画，不用生视频。** 理由：

1. 视频模型在本机跑不动（上面 6 分钟 + 崩溃）；
2. 就算跑得动，「每个动作 × 每个角色」各出一段 = 几十段 6 分钟渲染；
3. 更致命的是**跨片段保持同一个角色长相**——这是视频生成的经典难题，
   编不出来就是「第 3 个镜头里她换了张脸」；
4. 要的动作（敲键盘、吃零食、偷看屏幕、睡觉、伸懒腰、走去帮忙、
   抓耳挠腮、代码审核）全是**小循环**：程序化动画能精确控制、零运行成本、
   可重复、随时能改。你现在看到的就是这么做的。

生图模型的正确定位：**出角色立绘**（一张），然后贴进 `worker.gd`。

### 立绘怎么接进去

`worker.gd` 已经留好插槽：

```gdscript
var head_texture: Texture2D = null:   # 设了就用立绘当头，没设置就画程序化的头
```

换两步：

1. 在 Godot 里把立绘导成 `res://assets/portraits/opencode.png`；
   （`--import` 一次让 Godot 生成 `.import` 文件）
2. 在 `main.gd` 的 `_build_office()` 里给对应 worker 赋值：

```gdscript
w.head_texture = load("res://assets/portraits/opencode.png")
```

骨架、动作、状态机全部不变。

### 工作流（ComfyUI 里要连哪些节点）

可用的生图链路（已用 `object_info` 逐个确认过节点与文件名）：

```
UnetLoaderGGUF(z_image_turbo-Q8_0.gguf)
CLIPLoaderGGUF(Qwen3-4B-Q8_0.gguf, type=sd3)
VAELoader(ae.safetensors)
CLIPTextEncode(prompt) ─┐
CLIPTextEncode("")     ─┤  负 conditioning 接空文本
EmptyLatentImage(768×1024)
KSampler(euler, sgm_uniform, steps=8, cfg=1.0)   ← Turbo 系列，别给高步数
VAEDecode
SaveImage
```

合计约 11 GB，塞得进 12 GB 显存。约 1–3 分钟/张。

**注意**：本次发现 `D:\ComfyUI` 里**本来就有一个 ComfyUI 进程在跑**
（已占用 11.1 GB 显存，还有人在跑 `gen.py jobs_actions.json`）。
我又起了一个，制造了 `Database is locked` 还在抢 GPU。已停掉我误起的那个，
没碰别人那个。**下次用它之前先看一眼进程列表。**

## 已知不做（本期范围外）

- **不接后端**。状态机是写死的演示脚本（`story.gd` 的 `make_script()`），
  不是从真实 harness 事件来的。要接的话，`Agentshire` 那篇的教训直接可用：
  **翻译层做成纯函数**（hook 事件 → 视觉状态），**编排用「意图 + 前端回传」**，
  不要用 `setTimeout` 猜动画播了多久——它因此长出过幽灵 NPC 和工位撞车。
- 不写进 Vue 路由。定稿后可以用 `frontend/` 里一个 iframe 嵌 `web/`。
- 立绘全部是程序化的方块/圆，没有 AI 生图资产（ComfyUI 那条要单独占 GPU，
  且别人正在用）。

## 一个值得记的实现细节

`story.gd` 的 `scene_at(script, upto, rng)` 是**唯一出口**：给定已播到第几条
消息，返回每个角色当前状态。渲染层每帧问一次，**不持有时间轴**。

这样「动画播多久」完全由渲染层决定，逻辑层永远不用猜。
这是我抄 `Agentshire` 的结论：它把编排塞进一个文件、用 `setTimeout(3000)` 串
每一步，结果 Bridge 在 JS 逻辑层猜 Three.js 动画播了多久，两个时间模型对不上，
长出「场景错乱 / 幽灵 NPC / 工位撞车」三类 bug。改法是**不管动画怎么播，
只发意图，前端播完回传**。
