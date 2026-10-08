extends Node
## 场景树与布局转储：把每个节点的名字/类/位置打成文本。
##
## 用途：本环境截不了 Godot 窗口，只能靠引擎自己出图或出文本。
## 出图只能看「大概」，定位「哪个控件跑哪去了」必须看数字。
##
##     Godot --path . tools/dump.tscn -- --frames 30

const MainScene := preload("res://scenes/main.tscn")

var _frames := 0


func _ready() -> void:
	var want := 30
	var ua := OS.get_cmdline_user_args()
	for i in range(ua.size()):
		if String(ua[i]) == "--frames" and i + 1 < ua.size():
			want = maxi(1, int(ua[i + 1]))
	var m := MainScene.instantiate()
	add_child(m)
	_want = want


var _want := 30


func _process(_delta: float) -> void:
	_frames += 1
	if _frames < _want:
		return
	await get_tree().process_frame
	await get_tree().process_frame
	_dump(get_tree().root, 0)
	get_tree().quit(0)


func _dump(n: Node, depth: int) -> void:
	var pad := ""
	for _i in range(depth):
		pad += "  "
	var info := "%s%s [%s]" % [pad, n.name, n.get_class()]
	if n is CanvasItem:
		info += " pos=%s visible=%s" % [n.position, n.visible]
	# 角色：把状态与标签一起打出来，否则「场景里飘着一行字」这种问题
	# 从场景树里看不出来（它是 _draw 画出来的，不是节点）
	if n.get_script() != null and (n as Node).has_method("is_worker"):
		info += "  state=%s label='%s'" % [n.state, n.label_text]
	if n is Control:
		var c := n as Control
		info += " rect=%s size=%s min=%s" % [c.get_rect(), c.size, c.custom_minimum_size]
		if not c.visible:
			info += "  <<< HIDDEN"
	print(info)
	for ch in n.get_children():
		_dump(ch, depth + 1)
