extends Node
## 3D 场景转储：专盯「面片角色为什么不渲染」。
##
## 踩过的两个自己的坑：
## 1) SubViewport 没有 `visible` 属性，访问即崩 —— 于是什么都没看到；
## 2) get_tree().quit() 当帧末才生效，_process 会再跑几次，
##    把同一批节点重复打印好几遍。

const MainScene := preload("res://scenes/main.tscn")

var _frames := 0
var _want := 40
var _main: Node = null
var _done := false


func _ready() -> void:
	var ua := OS.get_cmdline_user_args()
	for i in range(ua.size()):
		if String(ua[i]) == "--frames" and i + 1 < ua.size():
			_want = maxi(1, int(ua[i + 1]))
	_main = MainScene.instantiate()
	add_child(_main)


func _process(_delta: float) -> void:
	if _done:
		return
	_frames += 1
	if _frames < _want:
		return
	_done = true
	await get_tree().process_frame
	await get_tree().process_frame
	_report(_main, 0)
	get_tree().quit(0)


func _report(n: Node, depth: int) -> void:
	var pad := ""
	for _i in range(depth):
		pad += "  "
	if n is SubViewport:
		var vp := n as SubViewport
		var tex: Texture2D = vp.get_texture()
		var info := "%sSubViewport size=%s update=%s" % [pad, vp.size, vp.render_target_update_mode]
		var img: Image = tex.get_image() if tex != null else null
		if img == null:
			info += "  TEX_IMAGE_NULL"
		else:
			var nz := 0
			var total := 0
			for y in range(0, img.get_height(), 6):
				for x in range(0, img.get_width(), 6):
					total += 1
					if img.get_pixel(x, y).a > 0.02:
						nz += 1
			info += "  tex=%s  非透明采样点 %d/%d" % [tex.get_size(), nz, total]
		print(info)
		return
	if n is Sprite3D:
		var s := n as Sprite3D
		print("%sSprite3D tex=%s pixel_size=%s pos=%s" % [pad, s.texture != null, s.pixel_size, s.position])
		return
	if n is MeshInstance3D:
		print("%sMesh %s pos=%s" % [pad, (n as MeshInstance3D).mesh, n.position])
		return
	if n is Camera3D:
		print("%sCamera pos=%s ortho_size=%s rot=%s" % [pad, n.position, n.size, n.rotation_degrees])
		return

	for ch in n.get_children():
		_report(ch, depth + 1)
