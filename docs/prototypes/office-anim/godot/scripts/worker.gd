extends Node2D
class_name Worker
## 拟人 harness 的**人物部分**。
##
## 2.5D 架构：本脚本只画「人」，画进一个 SubViewport，
## 再由 office3d.gd 把它当 `Sprite3D` billboard 贴进 3D 场景。
## 桌子/地板/光照/阴影**不在本文件里** —— 它们是 3D 的。
##
## 这样分工的理由：
## - 人物有 9 种状态、大量手绘细节，用 CanvasItem._draw() 最快；
## - 而桌子地板需要的是真体积感与深度遮挡，只有 3D 能给；
## - 关键是**深度测试自动让桌沿挡住角色下半身**，"坐在桌子后面"不用手写。
##
## 代价：角色是面片，斜看时会露怯。要更真就得换 VRM + .vrma 动画（另一件事）。

const TAU: float = PI * 2.0

const STATE_IDLE: String = "idle"
const STATE_TYPING: String = "typing"
const STATE_SNACK: String = "snack"
const STATE_SNOOP: String = "snoop"
const STATE_SLEEP: String = "sleep"
const STATE_DONE: String = "done"
const STATE_HELPING: String = "helping"
const STATE_STUCK: String = "stuck"
const STATE_REVIEWING: String = "reviewing"

## 状态对应的标签文案与颜色。
##
## 刻意做成「名字一行 + 状态一行、状态带色」：这是参考图
## （Agent Office / Star Office UI 一类 2.5D 拟人办公室）的**标志性视觉**。
## 只写状态不写名字，8 个人并排就看不出谁是谁；名字与状态同色，
## 又分不出「她叫什么」和「她在干嘛」—— 所以分行、异色。
const STATE_TEXT := {
	STATE_IDLE: "IDLE",
	STATE_TYPING: "CODING",
	STATE_SNACK: "ON BREAK",
	STATE_SNOOP: "ON BREAK",
	STATE_SLEEP: "ON BREAK",
	STATE_DONE: "DONE",
	STATE_HELPING: "HELPING",
	STATE_STUCK: "STUCK",
	STATE_REVIEWING: "REVIEWING",
}

const STATE_COLOR := {
	STATE_IDLE: Color("9aa5b1"),
	STATE_TYPING: Color("3fbf8f"),
	STATE_SNACK: Color("f0a03c"),
	STATE_SNOOP: Color("f0a03c"),
	STATE_SLEEP: Color("f0a03c"),
	STATE_DONE: Color("3fbf8f"),
	STATE_HELPING: Color("5b9cf5"),
	STATE_STUCK: Color("ef5b6b"),
	STATE_REVIEWING: Color("b07cf0"),
}


## 名牌与气泡相对头的偏移（向上为负）。
const LABEL_DY: float = -74.0
const BUBBLE_DY: float = -104.0

var state: String = STATE_IDLE:
	set(v):
		if v == state:
			return
		state = v
		queue_redraw()

var label_text: String = "":
	set(v):
		label_text = v
		queue_redraw()

var spec: Dictionary = {}
var mirror: bool = false
var seed_off: float = 0.0

var _t: float = 0.0

var _skin: Color = Color("f7d9c4")
var _hair: Color = Color("2f3550")
var _cloth: Color = Color("37c2a8")
var _vibe: String = ""
var _name: String = ""


func _ready() -> void:
	seed_off = randf() * TAU
	_sync_look()


func _sync_look() -> void:
	_skin = spec.get("skin", Color("f7d9c4"))
	_hair = spec.get("hair", Color("2f3550"))
	_cloth = spec.get("accent", Color("37c2a8"))
	_vibe = String(spec.get("vibe", ""))
	_name = String(spec.get("name", ""))


func _process(delta: float) -> void:
	_t += minf(delta, 0.05)
	queue_redraw()


## 供外部设置状态。lbl 是「要额外强调的一句话」，
## 传空表示没有 —— **不等于**不显示状态标签（状态标签由 _draw_plate 常驻画）。
func set_state(s: String, lbl: String) -> void:
	state = s
	label_text = lbl


## 供 dump 工具识别「这是角色」。
func is_worker() -> bool:
	return true


# ------------------------------------------------------------------ 绘制

func _draw() -> void:
	var lift: float = 0.0
	var bob: float = sin(_t * 2.1 + seed_off) * 2.4
	var lean: float = 0.0
	match state:
		STATE_DONE:
			lift = -18.0
			bob = sin(_t * 5.0) * 2.0
		STATE_HELPING:
			lift = -10.0
			bob = sin(_t * 8.0) * 3.0
		STATE_REVIEWING:
			lift = -8.0
		STATE_TYPING, STATE_SNACK, STATE_SNOOP:
			bob *= 0.35
			lean = 0.18 if state == STATE_SNOOP else 0.10

	var body_y: float = 22.0 + bob * 0.4

	# 椅子
	draw_colored_polygon(PackedVector2Array([
		Vector2(-26, body_y + 4), Vector2(26, body_y + 4),
		Vector2(26, body_y + 34), Vector2(-26, body_y + 34),
	]), Color(0.35, 0.35, 0.42, 0.35))

	var mx: float = -1.0 if mirror else 1.0
	draw_set_transform(Vector2(0, lift), 0.0, Vector2(mx, 1.0))

	# 躯干 + 衣领
	draw_colored_polygon(PackedVector2Array([
		Vector2(-19, body_y), Vector2(19, body_y),
		Vector2(19, body_y + 40), Vector2(-19, body_y + 40),
	]), _cloth)
	draw_colored_polygon(PackedVector2Array([
		Vector2(-7, body_y + 1), Vector2(7, body_y + 1), Vector2(0, body_y + 11),
	]), Color(1, 1, 1, 0.85))

	_draw_arms(body_y, bob)
	_draw_head(8.0 + bob, lean)

	# 名牌与气泡：回到本地 identity 单独画（字不能随镜像翻转）
	draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)
	_draw_plate()
	_draw_bubble(lift)


func _draw_arms(body_y: float, bob: float) -> void:
	var swing: float = sin(_t * (13.0 if state == STATE_TYPING else 3.0)) * (4.5 if state == STATE_TYPING else 2.0)
	var ends: Array = []
	match state:
		STATE_TYPING, STATE_REVIEWING:
			ends = [Vector2(-31, body_y + 44 + swing * 0.4), Vector2(31, body_y + 44 - swing * 0.4)]
		STATE_DONE:
			ends = [Vector2(-28, body_y + 16), Vector2(28, body_y + 16)]
		STATE_STUCK:
			ends = [Vector2(-25, body_y + 30), Vector2(25, body_y + 30)]
		STATE_SNACK:
			ends = [Vector2(-20, body_y + 34), Vector2(30, body_y + 24)]
		STATE_SNOOP:
			ends = [Vector2(-36, body_y + 32), Vector2(10, body_y + 34 + swing * 0.3)]
		_:
			ends = [Vector2(-25, body_y + 12 + swing), Vector2(25, body_y + 12 - swing)]

	var e0: Vector2 = ends[0]
	var e1: Vector2 = ends[1]
	draw_line(Vector2(-16, body_y + 2), e0, _cloth, 7.0, true)
	draw_line(Vector2(16, body_y + 2), e1, _cloth, 7.0, true)
	draw_circle(e0, 3.4, _skin)
	draw_circle(e1, 3.4, _skin)


func _draw_head(head_y: float, lean: float) -> void:
	draw_set_transform(Vector2(0, head_y), lean * 0.35, Vector2.ONE)

	draw_circle(Vector2.ZERO, 21.0, _hair)
	draw_circle(Vector2.ZERO, 17.5, _skin)
	var fringe := PackedVector2Array()
	for i in range(13):
		var a: float = lerp(PI * 1.06, PI * 1.94, float(i) / 12.0)
		fringe.append(Vector2(cos(a), sin(a)) * 17.5)
	draw_colored_polygon(fringe, _hair)

	match _vibe:
		"star":
			var s := PackedVector2Array()
			for i in range(10):
				var rr: float = 8.0 if i % 2 == 0 else 3.4
				var a: float = -PI / 2.0 + float(i) * PI / 5.0
				s.append(Vector2(cos(a), sin(a)) * rr + Vector2(0, -19))
			draw_colored_polygon(s, _cloth)
		"bun":
			draw_circle(Vector2(0, -21), 6.0, _hair)
		"glasses":
			var gcol := Color(0.18, 0.18, 0.24, 0.75)
			_arc(Vector2(-6, 1), 5.0, gcol, 1.6)
			_arc(Vector2(6, 1), 5.0, gcol, 1.6)
			draw_line(Vector2(-1, 1), Vector2(1, 1), gcol, 1.6)
		"cool":
			var s2 := PackedVector2Array()
			for i in range(13):
				var a2: float = lerp(PI * 1.1, PI * 1.9, float(i) / 12.0)
				s2.append(Vector2(cos(a2), sin(a2)) * 18.0)
			draw_colored_polygon(s2, _hair)
		_:
			draw_circle(Vector2(-13, -8), 4.5, _cloth)
			draw_circle(Vector2(13, -8), 4.5, _cloth)

	_draw_eyes()
	_draw_mouth()

	if state == STATE_SLEEP:
		var f := ThemeDB.fallback_font
		draw_string(f, Vector2(15, -8 + sin(_t * 2.0) * 2), "z", HORIZONTAL_ALIGNMENT_LEFT, -1.0, 10, Color("1f2430"))
		draw_string(f, Vector2(20, -14 + sin(_t * 2.0 + 1.0) * 2), "z", HORIZONTAL_ALIGNMENT_LEFT, -1.0, 10, Color("1f2430"))
	if state == STATE_HELPING:
		draw_rect(Rect2(Vector2(-1.6, -34), Vector2(3.2, 9)), Color("f59e0b"))
		draw_circle(Vector2(0, -22.4), 1.9, Color("f59e0b"))


func _draw_eyes() -> void:
	var dark := Color("1f2430")
	var blink: bool = sin(_t * 1.7) > 0.985
	var sleepy: bool = state == STATE_SLEEP
	var eye := func(ex: float) -> void:
		var p := Vector2(ex, 1.0)
		if sleepy:
			var s := PackedVector2Array()
			for i in range(9):
				var a: float = lerp(0.0, PI, float(i) / 8.0)
				s.append(p + Vector2(-4.0 + 8.0 * (a / PI), sin(a) * 2.4))
			draw_polyline(s, dark, 1.7)
		elif blink:
			draw_line(p + Vector2(-4, 0), p + Vector2(4, 0), dark, 1.7)
		elif state == STATE_STUCK:
			draw_line(p + Vector2(-4.4, -1.6), p + Vector2(4.4, 2.4), dark, 1.7)
			draw_line(p + Vector2(-4.4, 2.6), p + Vector2(4.4, -0.4), dark, 1.7)
		elif state == STATE_DONE:
			var s2 := PackedVector2Array()
			for i in range(9):
				var a2: float = lerp(0.0, PI, float(i) / 8.0)
				s2.append(p + Vector2(-4.0 + 8.0 * (a2 / PI), 2.0 - sin(a2) * 3.4))
			draw_polyline(s2, dark, 1.9)
		elif state == STATE_TYPING or state == STATE_REVIEWING:
			draw_circle(p, 3.0, dark)
		else:
			var pts := PackedVector2Array()
			for i in range(24):
				var a3: float = TAU * float(i) / 24.0
				pts.append(p + Vector2(cos(a3) * 2.9, sin(a3) * 3.6))
			draw_colored_polygon(pts, dark)
			draw_circle(p + Vector2(1, -0.6), 1.1, Color(1, 1, 1, 0.9))
	eye.call(-6.4)
	eye.call(6.4)


func _draw_mouth() -> void:
	var c := Color("b4553f")
	match state:
		STATE_SNACK, STATE_SNOOP:
			_arc(Vector2(0, 5.4), 2.6, c, 1.5, 0.0, PI)
		STATE_STUCK:
			_arc(Vector2(0, 8.6), 2.6, c, 1.5, PI, TAU)
		STATE_SLEEP:
			_blob(Vector2(0, 6), Vector2(2.2, 2.8), c)
		_:
			_arc(Vector2(0, 3.4), 3.0, c, 1.5, 0.18 * PI, 0.82 * PI)


## 名牌：**名字一行 + 状态一行**，状态带色。
## 这是参考图（Agent Office 一类 2.5D 拟人办公室）的标志性视觉。
func _draw_plate() -> void:
	if _name.is_empty():
		return
	var f := ThemeDB.fallback_font
	var size: float = 12.0
	var st_txt: String = String(STATE_TEXT.get(state, "IDLE"))
	var st_col: Color = STATE_COLOR.get(state, STATE_COLOR[STATE_IDLE])
	var nw: float = f.get_string_size(_name, HORIZONTAL_ALIGNMENT_LEFT, -1.0, size).x
	var sw: float = f.get_string_size(st_txt, HORIZONTAL_ALIGNMENT_LEFT, -1.0, 10.0).x
	var w: float = maxf(nw, sw) + 16.0
	var h: float = 38.0
	var r := Rect2(Vector2(-w / 2.0, LABEL_DY - h), Vector2(w, h))
	var bg := StyleBoxFlat.new()
	bg.bg_color = Color(1, 1, 1, 0.90)
	bg.set_corner_radius_all(7)
	bg.shadow_color = Color(0, 0, 0, 0.16)
	bg.shadow_size = 4
	draw_style_box(bg, r)
	# 左缘一道状态色竖条：远看也能扫出谁在干嘛
	draw_rect(Rect2(r.position + Vector2(0, 5), Vector2(3.0, h - 10)), st_col)
	draw_string(f, r.position + Vector2(9.0, 16.0), _name, HORIZONTAL_ALIGNMENT_LEFT, -1.0, size, Color("1f2430"))
	draw_string(f, r.position + Vector2(9.0, 31.0), st_txt, HORIZONTAL_ALIGNMENT_LEFT, -1.0, 10.0, st_col)


func _draw_bubble(lift: float) -> void:
	if label_text.is_empty():
		return
	var font := ThemeDB.fallback_font
	var size: float = 11.0
	var tw: float = font.get_string_size(label_text, HORIZONTAL_ALIGNMENT_LEFT, -1.0, size).x
	var by: float = BUBBLE_DY - lift
	var rect := Rect2(Vector2(-tw / 2.0 - 7.0, by - 11.0), Vector2(tw + 14.0, 20.0))
	var fill: Color
	match state:
		STATE_STUCK: fill = Color("fee2e2")
		STATE_DONE: fill = Color("dcfce7")
		_: fill = Color(0.06, 0.09, 0.16, 0.82)
	var sb := StyleBoxFlat.new()
	sb.bg_color = fill
	sb.set_corner_radius_all(10)
	draw_style_box(sb, rect)
	draw_string(font, rect.position + Vector2(0, 15.0), label_text, HORIZONTAL_ALIGNMENT_LEFT, -1.0, size, Color.WHITE)

	if state == STATE_STUCK:
		for i in range(3):
			var a: float = _t * 6.0 + float(i) * TAU / 3.0
			var base := Vector2(tw / 2.0 + 9.0, by - 4.0)
			var d: Vector2 = base + Vector2(cos(a) * 5.0, sin(a) * 5.0)
			draw_line(d, d + Vector2(6, 0), Color("ef4444"), 2.0)
	if state == STATE_DONE:
		for i in range(4):
			var a2: float = _t * 2.0 + float(i)
			draw_circle(Vector2(cos(a2) * 26.0, by - 14.0 + sin(a2) * 10.0), 3.2 - float(i) * 0.5, Color(0.98, 0.8, 0.08, 0.9 - float(i) * 0.18))


# ------------------------------------------------------------------ 小工具

func _arc(center: Vector2, radius: float, color: Color, width: float, from_angle: float = 0.0, to_angle: float = TAU) -> void:
	var pts := PackedVector2Array()
	var steps := 16
	for i in range(steps + 1):
		var a: float = lerp(from_angle, to_angle, float(i) / float(steps))
		pts.append(center + Vector2(cos(a), sin(a)) * radius)
	draw_polyline(pts, color, width)


func _blob(center: Vector2, radii: Vector2, color: Color) -> void:
	var pts := PackedVector2Array()
	for i in range(24):
		var a: float = TAU * float(i) / 24.0
		pts.append(center + Vector2(cos(a) * radii.x, sin(a)) * radii.y)
	draw_colored_polygon(pts, color)
