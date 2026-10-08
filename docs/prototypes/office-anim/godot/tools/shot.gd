extends Node
## 参考图工具：不开编辑器、不点按钮，直接把某一刻的画面存成 PNG。
##
## 用法：
##     Godot --path . tools/shot.tscn                      # 默认 90 帧后抓一张
##     Godot --path . tools/shot.tscn -- --frames 150      # 等 150 帧
##     Godot --path . tools/shot.tscn -- --rounds 3 --autoplay --frames 400
##     Godot --path . tools/shot.tscn -- --rounds 4 --jump 24 --frames 120
##
## 为什么需要它：这个仓库里我只能用命令行验证 Godot 工程（起不了带窗口的交互），
## 而「看起来对不对」是动画原型的核心验收项。所以让引擎自己出图，
## 再交给浏览器/用户去看——比我在代码里空口断言「应该没问题」可靠。
##
## `--autoplay` / `--rounds` / `--jump` 会原样透传给 main.gd。

const MainScene := preload("res://scenes/main.tscn")

var _frames := 0
var _want := 0
var _main: Node = null


func _ready() -> void:
	_want = 90
	var ua := OS.get_cmdline_user_args()
	for i in range(ua.size()):
		if String(ua[i]) == "--frames" and i + 1 < ua.size():
			_want = maxi(1, int(ua[i + 1]))
	_main = MainScene.instantiate()
	add_child(_main)


func _process(_delta: float) -> void:
	_frames += 1
	if _frames < _want:
		return
	# 多等两帧，让 _process 里的位移动画与 UI 布局都落地
	await get_tree().process_frame
	await get_tree().process_frame
	var img := get_viewport().get_texture().get_image()
	if img == null:
		push_error("shot: 拿不到视口纹理（headless 下没有渲染，需要真窗口运行）")
		get_tree().quit(1)
		return
	var dir := OS.get_user_data_dir()
	var path := dir.path_join("shot.png")
	var err := img.save_png(path)
	if err != OK:
		push_error("shot: 保存失败 err=%d" % err)
		get_tree().quit(1)
		return
	print("SHOT_SAVED ", path, " size=", img.get_width(), "x", img.get_height())
	get_tree().quit(0)
