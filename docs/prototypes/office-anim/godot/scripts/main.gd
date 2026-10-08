extends Node2D
## 主场景：左办公室 + 右手机群聊。
##
## 全部 UI 用代码搭（不写 .tscn 的 Control 树）—— 原型阶段改起来快，
## 且好处是这一份文件就能看清整个交互编排。定稿后再考虑搬进 .tscn
## 让非程序员也能在编辑器里摆。

const Story := preload("res://scripts/story.gd")
const WorkerScene := preload("res://scenes/worker.tscn")

# 布局：设计分辨率 1600x900。左侧办公室占 0~0.68，右侧手机占 0.68~1.0。
const OFFICE_W := 1088.0
const PHONE_X := 1088.0
const PHONE_W := 512.0
const VPH := 900.0

const BG := Color("f4f1ea")
const INK := Color("1f2430")
const SUB := Color("6b7280")
const LINE := Color("e5e7eb")
const CARD := Color("ffffff")
const WARN := Color("b45309")
const WARNBG := Color("fffbeb")
const WARNLINE := Color("fde68a")
const OK := Color("166534")
const OKBG := Color("dcfce7")

var rounds := 2
var playing := false
var script_msgs: Array = []
var upto := 0
var table_round := 0
var comments := {}
var holder := ""
var states := {}
var timer := 0.0
var gap := 0.8
var workers := {}
var desks := {}
var rng := RandomNumberGenerator.new()

# UI 控件
var _rounds_slider: HSlider
var _rounds_val: Label
var _chat_box: VBoxContainer
var _phase: Label
var _play_btn: Button
var _reset_btn: Button
var _say: TextEdit
var _tip: Label
var _tip_panel: PanelContainer
var _toasts: Array = []


func _ready() -> void:
	rng.randomize()
	_parse_rounds_arg()
	_build_office()
	_build_phone()
	rebuild()
	_apply_post_args()


## 调试/自动化入口：`-- --autoplay --rounds 3 --jump 20`
## 加这个不是为了产品功能，是为了能在不看窗口的情况下验证
## 「播到中段时场景长什么样」——参考图工具（tools/shot.tscn）靠它推进剧情。
##
## 刻意分两段：`--rounds` 必须在 rebuild() **之前**读（它决定生成多少条消息），
## `--autoplay`/`--jump` 必须在 rebuild() **之后** —— rebuild() 会把
# playing/upto 清零，先设就白设。
func _parse_rounds_arg() -> void:
	var ua := OS.get_cmdline_user_args()
	for i in range(ua.size()):
		if String(ua[i]) == "--rounds" and i + 1 < ua.size():
			rounds = clampi(int(ua[i + 1]), 1, 5)
			return


func _apply_post_args() -> void:
	var ua := OS.get_cmdline_user_args()
	for i in range(ua.size()):
		var a := String(ua[i])
		if a == "--autoplay":
			playing = true
			_play_btn.text = "⏸ 暂停"
			timer = gap  # 下一帧就 tick 一次，别空等
		elif a == "--jump" and i + 1 < ua.size():
			upto = clampi(int(ua[i + 1]), 0, script_msgs.size())
			states = Story.scene_at(script_msgs, upto, rng)
			_apply_states()
			_phase.text = "%d/%d" % [upto, script_msgs.size()]
	if _rounds_slider:
		_rounds_slider.value = float(rounds)
		_rounds_val.text = "%d 轮" % rounds


# ------------------------------------------------------------------ 办公室

func _build_office() -> void:
	# 地板与墙（画在 Node2D 上，置于所有角色之后）
	var bg := Node2D.new()
	bg.name = "Backdrop"
	bg.set_script(preload("res://scripts/backdrop.gd"))
	add_child(bg)

	# 工位 + 角色
	for i in range(Story.CHARACTERS.size()):
		var c: Dictionary = Story.CHARACTERS[i]
		var col := i % 4
		var row := int(i / 4)
		var dp := Vector2(150.0 + float(col) * 250.0, 400.0 + float(row) * 300.0)
		desks[c["key"]] = dp

		var w: Variant = WorkerScene.instantiate()
		w.spec = c
		w.desk_pos = dp
		w.mirror = col >= 2
		w.position = dp
		w.name = String(c["key"])
		add_child(w)
		workers[c["key"]] = w


func _apply_states() -> void:
	for key in workers:
		var s: Dictionary = states.get(key, {"state": "idle", "label": ""})
		var w: Variant = workers[key]
		var ns := String(s["state"])
		var lbl := String(s["label"])
		# 走到别人工位去帮忙：只在状态**变成** helping 时走一次
		if ns == "helping" and String(w.state) != "helping":
			var other: String = "trae" if String(key) != "trae" else "opencode"
			var target: Vector2 = desks[other]
			w.walk_to(target + Vector2(70, -10), 1.1)
		elif ns != "helping" and String(w.state) == "helping":
			w.return_to_desk()
		w.set_state(ns, lbl)


# ------------------------------------------------------------------ 右侧手机

func _build_phone() -> void:
	var layer := CanvasLayer.new()
	layer.name = "Phone"
	add_child(layer)

	# 底
	var bg := ColorRect.new()
	bg.color = Color("e9edf2")
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	bg.offset_left = PHONE_X
	bg.offset_right = 0
	layer.add_child(bg)

	# 头部
	var head := PanelContainer.new()
	head.set_anchors_preset(Control.PRESET_TOP_WIDE)
	head.offset_left = PHONE_X
	head.offset_right = 0
	head.offset_top = 0
	head.offset_bottom = 56
	layer.add_child(head)
	var hs := StyleBoxFlat.new()
	hs.bg_color = Color("f7f8fa")
	hs.border_color = LINE
	hs.set_border_width(SIDE_BOTTOM, 1)
	head.add_theme_stylebox_override("panel", hs)

	var hbox := HBoxContainer.new()
	hbox.set_anchors_preset(Control.PRESET_FULL_RECT)
	hbox.offset_left = 14
	hbox.offset_right = -14
	head.add_child(hbox)
	var av := Panel.new()
	av.custom_minimum_size = Vector2(30, 30)
	var avs := StyleBoxFlat.new()
	avs.bg_color = Color("f2b632")
	avs.set_corner_radius_all(8)
	av.add_theme_stylebox_override("panel", avs)
	hbox.add_child(av)
	var vb := VBoxContainer.new()
	vb.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hbox.add_child(vb)
	var t1 := Label.new()
	t1.text = "项目协作群"
	t1.add_theme_font_size_override("font_size", 14)
	vb.add_child(t1)
	var t2 := Label.new()
	t2.text = "8 人 · 群主 / 管理员 / 6 个 harness"
	t2.add_theme_color_override("font_color", SUB)
	t2.add_theme_font_size_override("font_size", 12)
	vb.add_child(t2)

	# 聊天滚动区
	var scroll := ScrollContainer.new()
	scroll.set_anchors_preset(Control.PRESET_FULL_RECT)
	scroll.offset_left = PHONE_X + 12
	scroll.offset_right = PHONE_X - 0
	scroll.offset_top = 56
	scroll.offset_bottom = -96
	layer.add_child(scroll)
	_chat_box = VBoxContainer.new()
	_chat_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_chat_box.add_theme_constant_override("separation", 9)
	scroll.add_child(_chat_box)

	# 轮次提示区（默认隐藏）
	_tip_panel = PanelContainer.new()
	_tip_panel.set_anchors_preset(Control.PRESET_BOTTOM_WIDE)
	_tip_panel.offset_left = PHONE_X + 12
	_tip_panel.offset_right = PHONE_X - 0
	_tip_panel.offset_top = -150
	_tip_panel.offset_bottom = -96
	_tip_panel.visible = false
	layer.add_child(_tip_panel)
	var ts := StyleBoxFlat.new()
	ts.bg_color = WARNBG
	ts.border_color = WARNLINE
	ts.set_border_width(SIDE_LEFT, 1)
	ts.set_border_width(SIDE_RIGHT, 1)
	ts.set_border_width(SIDE_TOP, 1)
	ts.set_border_width(SIDE_BOTTOM, 1)
	ts.set_corner_radius_all(9)
	ts.content_margin_left = 11
	ts.content_margin_right = 11
	ts.content_margin_top = 9
	ts.content_margin_bottom = 9
	_tip_panel.add_theme_stylebox_override("panel", ts)
	_tip = Label.new()
	_tip.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_tip.add_theme_color_override("font_color", WARN)
	_tip.add_theme_font_size_override("font_size", 13)
	_tip_panel.add_child(_tip)

	# 输入区
	var bar := PanelContainer.new()
	bar.set_anchors_preset(Control.PRESET_BOTTOM_WIDE)
	bar.offset_left = PHONE_X
	bar.offset_right = 0
	bar.offset_top = -96
	bar.offset_bottom = 0
	layer.add_child(bar)
	var bs := StyleBoxFlat.new()
	bs.bg_color = Color("f7f8fa")
	bs.border_color = LINE
	bs.set_border_width(SIDE_TOP, 1)
	bs.content_margin_left = 11
	bs.content_margin_right = 11
	bs.content_margin_top = 9
	bs.content_margin_bottom = 9
	bar.add_theme_stylebox_override("panel", bs)
	var bh := HBoxContainer.new()
	bh.add_theme_constant_override("separation", 8)
	bar.add_child(bh)
	_say = TextEdit.new()
	_say.custom_minimum_size = Vector2(0, 38)
	_say.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_say.placeholder_text = "以群主身份说一句…（会出现在群里并触发对应动作）"
	_say.add_theme_font_size_override("font_size", 13)
	bh.add_child(_say)
	var sb := Button.new()
	sb.text = "发送"
	sb.pressed.connect(_on_send)
	bh.add_child(sb)

	# 顶栏：轮次与播放控制
	_build_topbar(layer)


func _build_topbar(_layer: CanvasLayer) -> void:
	# 独立 layer，避免和手机底撞
	var top := CanvasLayer.new()
	top.name = "Topbar"
	add_child(top)

	var bs := StyleBoxFlat.new()
	bs.bg_color = CARD
	bs.border_color = LINE
	bs.set_border_width(SIDE_BOTTOM, 1)
	bs.content_margin_left = 16
	bs.content_margin_right = 16
	bs.content_margin_top = 8
	bs.content_margin_bottom = 8
	var p := PanelContainer.new()
	p.set_anchors_preset(Control.PRESET_TOP_WIDE)
	p.offset_top = 0
	p.offset_bottom = 48
	p.add_theme_stylebox_override("panel", bs)
	top.add_child(p)

	var hb := HBoxContainer.new()
	hb.add_theme_constant_override("separation", 12)
	p.add_child(hb)

	var brand := Label.new()
	brand.text = "项目办公室"
	brand.add_theme_font_size_override("font_size", 16)
	hb.add_child(brand)
	var sub := Label.new()
	sub.text = "拟人协作动画原型 · Godot 4.7 + 程序化角色（预留立绘插槽）"
	sub.add_theme_color_override("font_color", SUB)
	sub.add_theme_font_size_override("font_size", 12)
	hb.add_child(sub)

	var l1 := Label.new()
	l1.text = "任务表轮数"
	l1.add_theme_color_override("font_color", SUB)
	l1.add_theme_font_size_override("font_size", 13)
	hb.add_child(l1)

	_rounds_slider = HSlider.new()
	_rounds_slider.min_value = 1
	_rounds_slider.max_value = 5
	_rounds_slider.step = 1
	_rounds_slider.value = 2
	_rounds_slider.custom_minimum_size = Vector2(120, 20)
	_rounds_slider.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_rounds_slider.value_changed.connect(_on_rounds)
	hb.add_child(_rounds_slider)

	_rounds_val = Label.new()
	_rounds_val.text = "2 轮"
	_rounds_val.add_theme_font_size_override("font_size", 13)
	hb.add_child(_rounds_val)

	_play_btn = Button.new()
	_play_btn.text = "▶ 开始"
	_play_btn.pressed.connect(_on_play)
	hb.add_child(_play_btn)

	_reset_btn = Button.new()
	_reset_btn.text = "重置"
	_reset_btn.pressed.connect(_on_reset)
	hb.add_child(_reset_btn)

	var sp := Control.new()
	sp.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hb.add_child(sp)

	_phase = Label.new()
	_phase.text = "待开始"
	_phase.add_theme_color_override("font_color", SUB)
	_phase.add_theme_font_size_override("font_size", 13)
	hb.add_child(_phase)


# ------------------------------------------------------------------ 逻辑

func rebuild() -> void:
	script_msgs = Story.make_script(rounds)
	upto = 0
	holder = ""
	comments = {}
	table_round = 0
	playing = false
	timer = 0.0
	for c in _chat_box.get_children():
		c.queue_free()
	states = Story.scene_at(script_msgs, 0, rng)
	_apply_states()
	_play_btn.text = "▶ 开始"
	_phase.text = "待开始"


func tick() -> void:
	if upto >= script_msgs.size():
		playing = false
		_play_btn.text = "↻ 重播"
		_phase.text = "已结束"
		return
	var m: Dictionary = script_msgs[upto]
	var kind := String(m.get("kind", ""))
	var from := String(m["from"])

	if kind == "table" or kind == "publish":
		holder = from
		table_round += 1
	elif kind == "comment":
		for it in Story.make_task_table(table_round, comments)["items"]:
			if String(m["text"]).contains(String(it["id"])):
				comments[it["id"]] = "%s（据 %s 第 %d 轮意见补充）" % [it["text"], from, table_round]
				break

	_push_msg(m)
	upto += 1
	states = Story.scene_at(script_msgs, upto, rng)
	_apply_states()
	_phase.text = "%d/%d" % [upto, script_msgs.size()]
	gap = 1.5 if kind in ["table", "publish"] else 0.8


func _push_msg(m: Dictionary) -> void:
	var kind := String(m.get("kind", ""))
	var from := String(m["from"])

	var row := VBoxContainer.new()
	row.add_theme_constant_override("separation", 3)
	var who := Label.new()
	who.text = from
	who.add_theme_color_override("font_color", SUB)
	who.add_theme_font_size_override("font_size", 11)
	row.add_child(who)

	var bub := PanelContainer.new()
	var st := StyleBoxFlat.new()
	st.set_corner_radius_all(10)
	st.content_margin_left = 10
	st.content_margin_right = 10
	st.content_margin_top = 7
	st.content_margin_bottom = 7
	match kind:
		"dispatch": st.bg_color = Color("fff7ed"); st.border_color = Color("fed7aa"); st.set_border_width(SIDE_LEFT, 1); st.set_border_width(SIDE_RIGHT, 1); st.set_border_width(SIDE_TOP, 1); st.set_border_width(SIDE_BOTTOM, 1)
		"table": st.bg_color = Color("eef2ff"); st.border_color = Color("c7d2fe"); st.set_border_width(SIDE_LEFT, 1); st.set_border_width(SIDE_RIGHT, 1); st.set_border_width(SIDE_TOP, 1); st.set_border_width(SIDE_BOTTOM, 1)
		"publish": st.bg_color = Color("eef2ff"); st.border_color = Color("c7d2fe"); st.set_border_width(SIDE_LEFT, 1); st.set_border_width(SIDE_RIGHT, 1); st.set_border_width(SIDE_TOP, 1); st.set_border_width(SIDE_BOTTOM, 1)
		"pass": st.bg_color = OKBG; st.border_color = Color("bbf7d0"); st.set_border_width(SIDE_LEFT, 1); st.set_border_width(SIDE_RIGHT, 1); st.set_border_width(SIDE_TOP, 1); st.set_border_width(SIDE_BOTTOM, 1)
		"review": st.bg_color = Color("fef9c3"); st.border_color = Color("fde68a"); st.set_border_width(SIDE_LEFT, 1); st.set_border_width(SIDE_RIGHT, 1); st.set_border_width(SIDE_TOP, 1); st.set_border_width(SIDE_BOTTOM, 1)
		_: st.bg_color = CARD; st.border_color = LINE; st.set_border_width(SIDE_LEFT, 1); st.set_border_width(SIDE_RIGHT, 1); st.set_border_width(SIDE_TOP, 1); st.set_border_width(SIDE_BOTTOM, 1)
	# 手里的表：金框高亮，表示它正在谁那儿
	if kind in ["table", "publish"] and holder == from:
		st.border_color = Color("f59e0b")
		st.set_border_width(SIDE_LEFT, 2)
		st.set_border_width(SIDE_RIGHT, 2)
		st.set_border_width(SIDE_TOP, 2)
		st.set_border_width(SIDE_BOTTOM, 2)
	bub.add_theme_stylebox_override("panel", st)
	bub.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	row.add_child(bub)

	var inner := VBoxContainer.new()
	inner.add_theme_constant_override("separation", 6)
	bub.add_child(inner)

	var txt := Label.new()
	txt.text = String(m["text"])
	txt.add_theme_font_size_override("font_size", 13)
	txt.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	txt.custom_minimum_size = Vector2(220, 0)
	if kind == "pass":
		txt.add_theme_color_override("font_color", OK)
	if kind == "dispatch":
		txt.add_theme_color_override("font_color", Color("9a3412"))
	inner.add_child(txt)

	if kind in ["table", "publish"]:
		var tb := _task_table_ui()
		inner.add_child(tb)
		var rl := Label.new()
		rl.text = "第 %d 轮细化版" % table_round
		rl.add_theme_color_override("font_color", SUB)
		rl.add_theme_font_size_override("font_size", 11)
		inner.add_child(rl)

	_chat_box.add_child(row)
	# 滚到底
	await get_tree().process_frame
	var sc := _chat_box.get_parent() as ScrollContainer
	if sc:
		sc.scroll_vertical = int(sc.get_v_scroll_bar().max_value)

	# 管理员收到信息的提示
	if kind in ["done", "stuck", "claim"]:
		_toast("管理员收到一条来自 %s 的信息" % from)


func _task_table_ui() -> Control:
	var tbl := GridContainer.new()
	tbl.columns = 3
	tbl.add_theme_constant_override("h_separation", 6)
	tbl.add_theme_constant_override("v_separation", 4)
	var tab := Story.make_task_table(table_round, comments)
	var head := ["#", "任务", "负责人"]
	var widths := [26.0, 220.0, 62.0]
	for i in range(3):
		var h := Label.new()
		h.text = head[i]
		h.add_theme_font_size_override("font_size", 11)
		h.add_theme_color_override("font_color", Color("475569"))
		h.custom_minimum_size = Vector2(widths[i], 0)
		tbl.add_child(h)
	for it in tab["items"]:
		var c1 := Label.new()
		c1.text = String(it["id"])
		c1.add_theme_font_size_override("font_size", 11)
		c1.custom_minimum_size = Vector2(widths[0], 0)
		tbl.add_child(c1)
		var c2 := Label.new()
		c2.text = String(it["text"])
		c2.add_theme_font_size_override("font_size", 11)
		c2.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		c2.custom_minimum_size = Vector2(widths[1], 0)
		tbl.add_child(c2)
		var c3 := Label.new()
		var own := String(it.get("owner", ""))
		c3.text = "待认领" if own.is_empty() else own
		c3.add_theme_font_size_override("font_size", 11)
		c3.add_theme_color_override("font_color", OK if not own.is_empty() else SUB)
		c3.custom_minimum_size = Vector2(widths[2], 0)
		tbl.add_child(c3)
	return tbl


func _toast(text: String) -> void:
	var top := get_node_or_null("Topbar")
	var layer := CanvasLayer.new()
	layer.name = "ToastLayer"
	add_child(layer)
	var p := PanelContainer.new()
	p.set_anchors_preset(Control.PRESET_TOP_LEFT)
	p.offset_left = 16
	p.offset_top = 60
	var ps := StyleBoxFlat.new()
	ps.bg_color = Color(0.06, 0.09, 0.16, 0.9)
	ps.set_corner_radius_all(10)
	ps.content_margin_left = 13
	ps.content_margin_right = 13
	ps.content_margin_top = 8
	ps.content_margin_bottom = 8
	p.add_theme_stylebox_override("panel", ps)
	layer.add_child(p)
	var l := Label.new()
	l.text = text
	l.add_theme_color_override("font_color", Color.WHITE)
	l.add_theme_font_size_override("font_size", 13)
	p.add_child(l)
	var tw := create_tween()
	tw.tween_interval(2.6)
	tw.tween_property(p, "modulate:a", 0.0, 0.4)
	tw.tween_callback(func():
		if is_instance_valid(layer):
			layer.queue_free())


# ------------------------------------------------------------------ 事件

func _on_rounds(v: float) -> void:
	rounds = int(v)
	_rounds_val.text = "%d 轮" % rounds
	if rounds < 1:
		_show_tip("不好好细化一下任务表吗？")
	elif rounds > 3:
		_show_tip("这会不会太多了，可能要多花点钱哦")
	else:
		_hide_tip()
	rebuild()


func _show_tip(t: String) -> void:
	_tip.text = t
	_tip_panel.visible = true


func _hide_tip() -> void:
	_tip_panel.visible = false


func _on_play() -> void:
	if playing:
		playing = false
		_play_btn.text = "▶ 继续"
		_phase.text = "已暂停"
		return
	if upto >= script_msgs.size():
		rebuild()
	playing = true
	_play_btn.text = "⏸ 暂停"
	timer = 0.0


func _on_reset() -> void:
	rebuild()


func _on_send() -> void:
	var txt := _say.text.strip_edges()
	if txt.is_empty():
		return
	playing = false
	script_msgs.append({"from": "群主", "text": txt, "kind": "dispatch"})
	upto = script_msgs.size()
	_push_msg(script_msgs[upto - 1])
	states = Story.scene_at(script_msgs, upto, rng)
	_apply_states()
	_say.text = ""
	_toast("管理员收到一条来自 群主 的信息")


func _process(_delta: float) -> void:
	if not playing:
		return
	timer += _delta
	if timer >= gap:
		timer = 0.0
		tick()


func _draw() -> void:
	# 办公室区域底色：左侧一块，右侧留给手机 UI
	draw_rect(Rect2(Vector2.ZERO, Vector2(OFFICE_W, VPH)), BG, true)
