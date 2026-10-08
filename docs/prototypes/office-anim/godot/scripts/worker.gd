extends Node2D
class_name Worker
## 一个拟人 harness：Q 版小人，全部程序化绘制。
##
## 关键设计：**同一套骨架 + 按状态改参数**，而不是每个状态各画一张。
## 这样同一个角色跨状态长相一致；也预留了贴图插槽 head_texture，
## 后续可直接换成 ComfyUI 生成的立绘而不动骨架。

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
var desk_pos: Vector2 = Vector2.ZERO
var mirror: bool = false
var seed_off: float = 0.0

#: 预留插槽：设了就用生成立绘当头，未设置则画程序化的头。
var head_texture: Texture2D = null:
	set(v):
		head_texture = v
		queue_redraw()

var _t: float = 0.0

var walk_target: Vector2 = Vector2.ZERO
var walk_from: Vector2 = Vector2.ZERO
var walk_t: float = -1.0
var walk_dur: float = 1.2
var _walking: bool = false

var _skin: Color = Color("f7d9c4")
var _hair: Color = Color("2f3550")
var _cloth: Color = Color("37c2a8")
var _vibe: String = ""


func _ready() -> void:
	seed_off = randf() * TAU
	_sync_look()
	position = desk_pos


func _sync_look() -> void:
	_skin = spec.get("skin", Color("f7d9c4"))
	_hair = spec.get("hair", Color("2f3550"))
	_cloth = spec.get("accent", Color("37c2a8"))
	_vibe = String(spec.get("vibe", ""))


func _process(delta: float) -> void:
	_t += minf(delta, 0.05)
	if _walking:
		walk_t += delta / maxf(0.01, walk_dur)
		if walk_t >= 1.0:
			walk_t = 1.0
			_walking = false
		position = walk_from.lerp(walk_target, ease(walk_t, 0.35))
	queue_redraw()


func walk_to(target: Vector2, dur: float) -> void:
	walk_from = position
	walk_target = target
	walk_dur = dur
	walk_t = 0.0
	_walking = true


func return_to_desk() -> void:
	walk_to(desk_pos, 0.9)


# ------------------------------------------------------------------ 绘制

func _draw() -> void:
	# 起身类状态抬高整个人
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

	# 阴影（贴地，不随 lift 抬升）
	draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)
	draw_circle(Vector2(0, 58.0 - lift), 26.0, Color(0, 0, 0, 0.10))

	var mx: float = -1.0 if mirror else 1.0
	draw_set_transform(Vector2(0, lift), 0.0, Vector2(mx, 1.0))

	# 椅背
	draw_colored_polygon(PackedVector2Array([
		Vector2(-26, body_y + 4), Vector2(26, body_y + 4),
		Vector2(26, body_y + 34), Vector2(-26, body_y + 34),
	]), Color(0.35, 0.35, 0.42, 0.35))

	# 躯干 + 衣领
	draw_colored_polygon(PackedVector2Array([
		Vector2(-19, body_y), Vector2(19, body_y),
		Vector2(19, body_y + 40), Vector2(-19, body_y + 40),
	]), _cloth)
	draw_colored_polygon(PackedVector2Array([
		Vector2(-7, body_y + 1), Vector2(7, body_y + 1), Vector2(0, body_y + 11),
	]), Color(1, 1, 1, 0.85))

	_draw_arms(body_y, bob)
	_draw_head(hy(8.0 + bob), lean)

	# 标签与工位道具：回到世界坐标单独画
	draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)
	_draw_bubble(lift)
	_draw_desk_props()


func hy(v: float) -> float:
	return v


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

	# 后发 + 脸 + 刘海
	draw_circle(Vector2.ZERO, 21.0, _hair)
	draw_circle(Vector2.ZERO, 17.5, _skin)
	var fringe := PackedVector2Array()
	for i in range(13):
		var a: float = lerp(PI * 1.06, PI * 1.94, float(i) / 12.0)
		fringe.append(Vector2(cos(a), sin(a)) * 17.5)
	draw_colored_polygon(fringe, _hair)

	# 发饰：按角色区分辨识度
	match _vibe:
		"star":
			# 五角星：10 个顶点（外 5 + 内 5），**共线会三角化失败**
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


func _draw_bubble(lift: float) -> void:
	if label_text.is_empty():
		return
	var font := ThemeDB.fallback_font
	var size: float = 11.0
	var tw: float = font.get_string_size(label_text, HORIZONTAL_ALIGNMENT_LEFT, -1.0, size).x
	var on_top: bool = state in [STATE_DONE, STATE_HELPING, STATE_REVIEWING]
	var by: float = -66.0 + (-6.0 if on_top else 0.0) - lift
	var rect := Rect2(Vector2(-tw / 2.0 - 7.0, by - 11.0), Vector2(tw + 14.0, 20.0))
	var fill: Color
	match state:
		STATE_STUCK: fill = Color("fee2e2")
		STATE_DONE: fill = Color("dcfce7")
		_: fill = Color(0.06, 0.09, 0.16, 0.82)
	# 圆角要用 StyleBoxFlat + draw_style_box。
	# 不要给 draw_rect 传第 4 个参数当圆角：那是 width，且 filled=true 时被忽略，
	# 每帧还会刷一条 WARNING。
	var sb := StyleBoxFlat.new()
	sb.bg_color = fill
	sb.set_corner_radius_all(10)
	sb.content_margin_left = 7.0
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


## 工位上的电脑与「输入代码」小动画。
func _draw_desk_props() -> void:
	var mx: float = -60.0
	var my: float = 40.0
	draw_rect(Rect2(Vector2(mx - 26, my - 18), Vector2(52, 34)), Color("2b2f3a"))
	draw_rect(Rect2(Vector2(mx - 22, my - 14), Vector2(44, 26)), Color("101623"))
	var phase: float = fmod(_t * 3.2, 1.0)
	for i in range(5):
		var on: bool = i < int(floor(phase * 5.0)) + 1
		var wdt: float = maxf(4.0, 30.0 - float(i) * 3.2)
		draw_rect(Rect2(Vector2(mx - 16, my - 9 + float(i) * 4.4), Vector2(wdt, 2.4)), _cloth if on else Color(0.47, 0.55, 0.67, 0.25))
	draw_rect(Rect2(Vector2(mx - 5, my + 16), Vector2(10, 4)), Color("2b2f3a"))
	draw_rect(Rect2(Vector2(mx - 12, my + 20), Vector2(24, 3)), Color("2b2f3a"))


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


## 供外部设置状态的便捷入口。
func set_state(s: String, lbl: String) -> void:
	state = s
	label_text = lbl
