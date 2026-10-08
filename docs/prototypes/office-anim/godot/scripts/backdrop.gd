extends Node2D
## 办公室背景：地板、墙、窗、地毯。
## 单独一个文件，改场景装饰不用碰角色逻辑。

const OFFICE_W: float = 1088.0
const VPH: float = 900.0

var _t: float = 0.0


func _process(delta: float) -> void:
	_t += delta
	queue_redraw()


func _draw() -> void:
	var w: float = OFFICE_W
	var h: float = VPH

	# 墙
	var g := Gradient.new()
	g.set_color(0, Color("cfd9e6"))
	g.set_color(1, Color("e6ebf2"))
	var wall := GradientTexture2D.new()
	wall.gradient = g
	wall.width = int(w)
	wall.height = int(h * 0.3)
	wall.fill = GradientTexture2D.FILL_LINEAR
	wall.fill_from = Vector2(0, 0)
	wall.fill_to = Vector2(0, 1)
	draw_texture_rect(wall, Rect2(Vector2.ZERO, Vector2(w, h * 0.3)), false)

	# 地板
	var gf := Gradient.new()
	gf.set_color(0, Color("efe6d8"))
	gf.set_color(1, Color("d9cdb8"))
	var floor_tex := GradientTexture2D.new()
	floor_tex.gradient = gf
	floor_tex.width = int(w)
	floor_tex.height = int(h * 0.72)
	floor_tex.fill = GradientTexture2D.FILL_LINEAR
	floor_tex.fill_from = Vector2(0, 0)
	floor_tex.fill_to = Vector2(0, 1)
	draw_texture_rect(floor_tex, Rect2(Vector2(0, h * 0.28), Vector2(w, h * 0.72)), false)

	# 地砖缝
	var seam := Color(0.47, 0.39, 0.27, 0.12)
	for i in range(1, 7):
		var y: float = h * 0.28 + (h * 0.72 * float(i)) / 7.0
		draw_line(Vector2(0, y), Vector2(w, y), seam, 1.0)
	for i in range(1, 12):
		var x: float = w * float(i) / 12.0
		draw_line(Vector2(x, h * 0.28), Vector2(x, h * 1.02), seam, 1.0)

	# 窗户：三扇，窗外缓慢移动的云（让场景不死板）
	for i in range(3):
		var x: float = w * (0.08 + float(i) * 0.30)
		var y: float = h * 0.06
		var ww: float = w * 0.20
		var wh: float = h * 0.16
		draw_rect(Rect2(Vector2(x, y), Vector2(ww, wh)), Color(1, 1, 1, 0.55))
		draw_rect(Rect2(Vector2(x, y), Vector2(ww, wh)), Color(0.35, 0.43, 0.55, 0.35), false, 1.0)
		for k in range(2):
			var cx: float = x + fmod(_t * (6.0 + float(k) * 3.0), ww + 40.0) - 20.0
			var cy: float = y + 24.0 + float(k) * 26.0
			draw_circle(Vector2(cx, cy), 9.0, Color(1, 1, 1, 0.75))
			draw_circle(Vector2(cx + 9, cy - 3), 7.0, Color(1, 1, 1, 0.75))
			draw_circle(Vector2(cx - 9, cy - 2), 6.0, Color(1, 1, 1, 0.75))

	# 墙上挂一块「项目进度」板
	var bx: float = w * 0.40
	var by: float = h * 0.10
	draw_rect(Rect2(Vector2(bx, by), Vector2(w * 0.20, h * 0.13)), Color(0.16, 0.20, 0.28, 0.85))
	draw_rect(Rect2(Vector2(bx, by), Vector2(w * 0.20, h * 0.13)), Color(0.35, 0.43, 0.55, 0.6), false, 1.0)
	var font := ThemeDB.fallback_font
	draw_string(font, Vector2(bx + 10, by + 18), "项目进度", HORIZONTAL_ALIGNMENT_LEFT, -1.0, 12, Color(1, 1, 1, 0.9))
	for i in range(4):
		var filled: bool = i < 2
		var cell := Rect2(Vector2(bx + 10.0 + float(i) * 34.0, by + 28.0), Vector2(26, 10))
		draw_rect(cell, Color(0.15, 0.18, 0.24))
		if filled:
			draw_rect(cell, Color("37c2a8"))

	# 地毯（用 Godot 原生 draw_ellipse：center, width, height, color）
	draw_ellipse(Vector2(w * 0.5, h * 0.88), w * 0.16, h * 0.045, Color(0.62, 0.45, 0.35, 0.18))
