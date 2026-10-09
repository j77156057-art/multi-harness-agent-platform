extends Node3D
class_name Office3D
## 2.5D 办公室：**真 3D** 的地板、桌子、光照与阴影，
## 加上**面片化**的角色（2D 画进 SubViewport 当 Sprite3D billboard）。
##
## 为什么这么分而不是纯 3D 或纯 2D：
## - 纯 2D 没有体积感，地板和桌面只是两张画，斜看立刻露怯（用户实测要求 2.5D）；
## - 纯 3D 要建 8 个角色模型 + 9 种状态的动画，工作量大一个数量级；
## - 关键在于**深度测试自动让桌沿挡住角色下半身**，"坐在桌子后面"不用手写；
##   「走去帮忙」也是真在 3D 空间里走，不是贴图位移。
##
## 已知取舍：面片斜看会露怯。要更真就换 VRM 模型 + .vrma 动画 —— 另一件事。

const Story := preload("res://scripts/story.gd")
const WorkerScene := preload("res://scenes/worker.tscn")

const COLS := 4
const ROWS := 2
const DX := 2.7          # 列间距
const DZ := 3.0          # 排间距
const DESK_H := 1.05     # 桌面高
const DESK_W := 2.2      # 桌面宽
const DESK_D := 1.1      # 桌面深
const CHAR_DZ := -0.45   # 角色站在桌的哪一侧（-Z = 远离相机 = 屏幕上方）

#: 8 块屏幕合成一张横向长条纹理，一帧只更新一次。
const SCREEN_CELL_W := 96
const SCREEN_CELL_H := 64

var characters: Dictionary = {}
var _desks: Array = []
var _screen_tex: ImageTexture
var _screen_img: Image
var _shadow_tex: ImageTexture
var _clock: float = 0.0


func _ready() -> void:
	_build_camera()
	_build_light()
	_build_floor()
	_build_shadow_texture()
	_build_screen_texture()
	_build_grid()
	_build_walls()


# ------------------------------------------------------------------ 相机与光

func _build_camera() -> void:
	var cam := Camera3D.new()
	cam.projection = Camera3D.PROJECTION_ORTHOGONAL
	# 正交 + size=9 → 视里竖向 9 个世界单位。屏幕 1600×900，故横向 16。
	cam.size = 9.0
	# 左栏占 68%：横向 [-8,8] 里左 68% 的中心在 -2.56，
	# 把相机目标挪过去，办公室就落在左栏里（右侧留给手机 UI）。
	var target := Vector3(-2.56, 0.7, 1.2)
	cam.position = target + Vector3(0.0, 6.9, 7.4)
	add_child(cam)
	# **必须在入树之后**才能转向：先 add_child 再 look_at 会报
	# "Node not inside tree"。用 look_at_from_position 对该情形安全，
	# 行为与 look_at 完全一致。
	cam.look_at_from_position(cam.position, target, Vector3.UP)
	cam.current = true


func _build_light() -> void:
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-48, -28, 0)
	sun.light_energy = 1.15
	sun.shadow_enabled = true
	sun.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_4_SPLITS
	sun.directional_shadow_max_distance = 40.0
	add_child(sun)

	# 一点环境光，免得背光面死黑
	var env := WorldEnvironment.new()
	var e := Environment.new()
	e.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	e.ambient_light_color = Color("cfd9e6")
	e.ambient_light_energy = 0.45
	env.environment = e
	add_child(env)


# ------------------------------------------------------------------ 地板

func _build_floor() -> void:
	# 程序化生成地砖纹理。
	# 配色刻意走**暖奶油**（参考图/officelik 一类 2.5D 拟人办公室的底色），
	# 不是灰色楼板：暖底 + 浅色家具才撑得起「温馨办公室」，
	# 冷灰底只会像停车场。
	var N := 512
	var img := Image.create(N, N, false, Image.FORMAT_RGBA8)
	img.fill(Color("efe6d4"))
	var cell := N / 8
	for gy in range(8):
		for gx in range(8):
			var c: Color = Color("f6efe0") if (gx + gy) % 2 == 0 else Color("e9dfcb")
			img.fill_rect(Rect2i(gx * cell + 2, gy * cell + 2, cell - 4, cell - 4), c)
	var tex := ImageTexture.create_from_image(img)

	var plane := PlaneMesh.new()
	plane.size = Vector2(26, 20)
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = tex
	# 程序化出来的是朝 +Y 的平面；UV 0..1 铺满，够用
	mat.uv1_scale = Vector3(6, 5, 1)
	var mi := MeshInstance3D.new()
	mi.mesh = plane
	mi.material_override = mat
	mi.position = Vector3(-2.56, 0, 2.5)
	add_child(mi)


func _build_walls() -> void:
	# 远端一道墙 + 三扇窗。**没有墙和窗，场景就像飘在虚空里** ——
	# 参考图里那面浅蓝灰的窗墙是「办公室」读感的主要来源。
	var wall := StandardMaterial3D.new()
	wall.albedo_color = Color("dde5ee")
	var trim := StandardMaterial3D.new()
	trim.albedo_color = Color("c3cede")
	var glass := StandardMaterial3D.new()
	glass.albedo_color = Color("eaf4fb")
	glass.emission_enabled = true
	glass.emission = Color("dfeeF8")
	glass.emission_energy = 0.5

	# 主墙（沿 X 展开，位于 Z = -1.7）
	var wm := MeshInstance3D.new()
	var wb := BoxMesh.new()
	wb.size = Vector3(24.0, 3.0, 0.25)
	wb.material = wall
	wm.mesh = wb
	wm.position = Vector3(-2.56, 1.5, -1.7)
	add_child(wm)
	# 踢脚线
	var km := MeshInstance3D.new()
	var kb := BoxMesh.new()
	kb.size = Vector3(24.0, 0.18, 0.32)
	kb.material = trim
	km.mesh = kb
	km.position = Vector3(-2.56, 0.09, -1.62)
	add_child(km)

	# 三扇窗
	for i in range(3):
		var cx := -7.0 + float(i) * 7.0
		var fw := 2.2
		var fh := 1.5
		var fm := MeshInstance3D.new()
		var fb := BoxMesh.new()
		fb.size = Vector3(fw, fh, 0.1)
		fb.material = glass
		fm.mesh = fb
		fm.position = Vector3(cx, 1.85, -1.62)
		add_child(fm)
		# 窗框（上下两根）
		for k in range(2):
			var fm2 := MeshInstance3D.new()
			var fb2 := BoxMesh.new()
			fb2.size = Vector3(fw + 0.22, 0.12, 0.14)
			fb2.material = trim
			fm2.mesh = fb2
			fm2.position = Vector3(cx, 1.85 + (0.78 if k == 0 else -0.78), -1.58)
			add_child(fm2)


func _build_shadow_texture() -> void:
	# 径向渐变小圆：角色的接触阴影。3D 光照的面片角色不投影，
	# 没有它人就像贴在地上，2.5D 的"落地感"直接掉一半。
	var N := 64
	var img := Image.create(N, N, false, Image.FORMAT_RGBA8)
	for y in range(N):
		for x in range(N):
			var dx: float = (float(x) / float(N - 1)) * 2.0 - 1.0
			var dy: float = (float(y) / float(N - 1)) * 2.0 - 1.0
			var d: float = sqrt(dx * dx + dy * dy)
			var a: float = clampf(1.0 - d, 0.0, 1.0)
			img.set_pixel(x, y, Color(0, 0, 0, a * a * 0.42))
	_shadow_tex = ImageTexture.create_from_image(img)


func _build_screen_texture() -> void:
	var w := SCREEN_CELL_W * COLS * ROWS
	var img := Image.create(w, SCREEN_CELL_H, false, Image.FORMAT_RGBA8)
	img.fill(Color("101623"))
	_screen_img = img
	_screen_tex = ImageTexture.create_from_image(img)


## 每帧更新屏幕纹理：每格画「输入代码」进度条，睡/摸鱼的格子息屏。
func update_screens(states: Dictionary, t: float) -> void:
	if _screen_img == null:
		return
	var phase: float = fmod(t * 3.2, 1.0)
	var idx := 0
	for r in range(ROWS):
		for c in range(COLS):
			var key := _key_of(r, c)
			var st := String(states.get(key, {}).get("state", "idle"))
			var x0 := idx * SCREEN_CELL_W
			var dark: bool = st == "sleep" or st == "snoop"
			for i in range(5):
				var y := 8 + float(i) * 10
				var bw: int = SCREEN_CELL_W - 24 - i * 8
				var col: Color
				if dark:
					col = Color(0.16, 0.18, 0.22, 1.0)
				else:
					var on: bool = i < int(floor(phase * 5.0)) + 1
					col = Color(0.24, 0.78, 0.70, 1.0) if on else Color(0.30, 0.34, 0.42, 1.0)
				_screen_img.fill_rect(Rect2i(x0 + 12, y, maxi(4, bw), 5), col)
			idx += 1
	_screen_tex.update(_screen_img)


# ------------------------------------------------------------------ 桌子与角色

## 3D 席位 (r,c) 对应哪个角色。索引直接来自 Story.CHARACTERS ——
## 刻意不为图省事再抄一份名单：抄一份就会和 story.gd 漂移，
## 而漂移的表现是「角色和桌上的屏幕对不上」，很难查。
func _key_of(r: int, c: int) -> String:
	var spec: Dictionary = Story.CHARACTERS[(r * COLS + c) % Story.CHARACTERS.size()]
	return String(spec["key"])


func _build_grid() -> void:
	for r in range(ROWS):
		for c in range(COLS):
			var spec: Dictionary = Story.CHARACTERS[r * COLS + c]
			var key := String(spec["key"])
			var p := Vector3(-2.56 + float(c - 1.5) * DX, 0.0, float(r) * DZ)
			_build_desk(p, spec, _index_of(r, c))
			_build_character(p, spec, r, c)


func _index_of(r: int, c: int) -> int:
	return r * COLS + c


func _build_desk(at: Vector3, spec: Dictionary, idx: int) -> void:
	var g := Node3D.new()
	g.position = at
	add_child(g)
	_desks.append(g)

	var wood := StandardMaterial3D.new()
	wood.albedo_color = Color("c99a68")
	var leg := StandardMaterial3D.new()
	leg.albedo_color = Color("9c7550")
	var dark := StandardMaterial3D.new()
	dark.albedo_color = Color("2b2f3a")

	# 桌面
	var slab := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = Vector3(DESK_W, 0.1, DESK_D)
	bm.material = wood
	slab.mesh = bm
	slab.position = Vector3(0, DESK_H, 0)
	slab.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	g.add_child(slab)

	# 桌腿
	for lx in [-DESK_W / 2.0 + 0.16, DESK_W / 2.0 - 0.16]:
		var lm := MeshInstance3D.new()
		var lbm := BoxMesh.new()
		lbm.size = Vector3(0.1, DESK_H, DESK_D - 0.1)
		lbm.material = leg
		lm.mesh = lbm
		lm.position = Vector3(lx, DESK_H / 2.0, 0)
		lm.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
		g.add_child(lm)

	# 显示器外壳（立在桌面靠后一侧）
	var mon := MeshInstance3D.new()
	var mm := BoxMesh.new()
	mm.size = Vector3(1.0, 0.62, 0.07)
	mm.material = dark
	mon.mesh = mm
	mon.position = Vector3(-0.42, DESK_H + 0.46, -0.28)
	mon.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	g.add_child(mon)
	# 支架
	var st := MeshInstance3D.new()
	var sm := BoxMesh.new()
	sm.size = Vector3(0.1, 0.24, 0.1)
	sm.material = dark
	st.mesh = sm
	st.position = Vector3(-0.42, DESK_H + 0.12, -0.28)
	g.add_child(st)

	# 屏幕：发光面片，纹理由 update_screens() 每帧写
	var scr := MeshInstance3D.new()
	var qm := QuadMesh.new()
	qm.size = Vector2(0.88, 0.5)
	var smat := StandardMaterial3D.new()
	if _screen_tex:
		smat.albedo_texture = _screen_tex
	smat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	smat.emission_enabled = true
	smat.emission_energy = 1.4
	if _screen_tex:
		smat.emission_texture = _screen_tex
	scr.mesh = qm
	scr.material_override = smat
	scr.position = Vector3(-0.42, DESK_H + 0.46, -0.24)
	scr.rotation_degrees = Vector3(0, 180, 0)
	# 取长条纹理里属于自己那一格。uv1_* 是 Vector3（xyz = uvw）。
	smat.uv1_scale = Vector3(1.0 / float(COLS * ROWS), 1.0, 1.0)
	smat.uv1_offset = Vector3(float(idx) / float(COLS * ROWS), 0.0, 0.0)
	g.add_child(scr)

	# 键盘（占位小盒子）
	var kb := MeshInstance3D.new()
	var km := BoxMesh.new()
	km.size = Vector3(0.6, 0.03, 0.24)
	km.material = dark
	kb.mesh = km
	kb.position = Vector3(0.05, DESK_H + 0.06, -0.05)
	g.add_child(kb)


func _build_character(at: Vector3, spec: Dictionary, r: int, c: int) -> void:
	var vp := SubViewport.new()
	# 220×300：两行名牌（38 高，位于 y=-74）+ 之上的气泡，
	# 加上身体，260 高会把标签顶出视口被裁掉 —— 实测过一次，标签凭空消失。
	vp.size = Vector2i(220, 300)
	vp.transparent_bg = true
	vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	vp.handle_input_locally = false
	add_child(vp)

	var w: Worker = WorkerScene.instantiate()
	w.spec = spec
	w.mirror = c >= 2
	# 角色画在视口偏下处：头顶留足两行名牌 + 气泡的空间
	w.position = Vector2(110, 185)
	w.name = String(spec.get("key", "w"))
	vp.add_child(w)

	var spr := Sprite3D.new()
	spr.texture = vp.get_texture()
	spr.pixel_size = 0.014
	spr.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	spr.position = at + Vector3(0, 0.0, CHAR_DZ)
	spr.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var sm := StandardMaterial3D.new()
	sm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	sm.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	sm.no_depth_test = false
	spr.material_override = sm
	spr.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	add_child(spr)
	characters[String(spec.get("key", ""))] = {"sprite": spr, "worker": w, "home": spr.position, "vp": vp}

	# 接触阴影（贴地小圆）
	var sh := MeshInstance3D.new()
	var sq := QuadMesh.new()
	sq.size = Vector2(1.05, 0.85)
	var shm := StandardMaterial3D.new()
	shm.albedo_texture = _shadow_tex
	shm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	shm.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	shm.no_depth_test = true
	shm.render_priority = -1
	sh.mesh = sq
	sh.material_override = shm
	sh.position = at + Vector3(0, 0.012, CHAR_DZ)
	sh.rotation_degrees = Vector3(-90, 0, 0)
	add_child(sh)
	characters[String(spec.get("key", ""))]["shadow"] = sh


# ------------------------------------------------------------------ 每帧

func apply_states(states: Dictionary) -> void:
	for key in characters:
		var e: Dictionary = characters[key]
		var s: Dictionary = states.get(key, {"state": "idle", "label": ""})
		e["worker"].set_state(String(s["state"]), String(s["label"]))


func get_home(key: String) -> Vector3:
	return characters[key]["home"]


func set_pos(key: String, p: Vector3) -> void:
	characters[key]["sprite"].position = p
	characters[key]["shadow"].position = Vector3(p.x, 0.012, p.z)


# ------------------------------------------------------------------ 每帧

func _process(delta: float) -> void:
	_clock += delta
	# 走路只在这里推进插值，**不用计时器猜时长**。
	# Agentshire 用 setTimeout 串动画、在逻辑层猜渲染层播了多久，
	# 长出幽灵 NPC / 工位撞车；这里位移归位移，动画归动画。
	for key in characters:
		var e: Dictionary = characters[key]
		var w: Variant = e.get("walk")
		if w == null:
			continue
		var t: float = float(w["t"]) + delta / maxf(0.05, float(w["dur"]))
		if t >= 1.0:
			t = 1.0
		set_pos(String(key), (w["from"] as Vector3).lerp(w["to"], ease(t, 0.35)))
		if t >= 1.0:
			e.erase("walk")


## 供外层拿当前时间驱动屏幕动画。
func get_clock() -> float:
	return _clock


## 走到某个位置（走去帮忙 / 回工位都走这里）。
func walk_to(key: String, target: Vector3, dur: float) -> void:
	if not characters.has(key):
		return
	characters[key]["walk"] = {
		"from": characters[key]["sprite"].position,
		"to": target,
		"t": 0.0,
		"dur": maxf(0.05, dur),
	}
