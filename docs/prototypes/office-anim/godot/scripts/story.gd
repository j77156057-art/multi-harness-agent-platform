class_name Story
extends RefCounted
## 剧情与状态机：把群聊脚本推进成「某一刻每个人在做什么」。
##
## 刻意**不持有时间轴**：只提供 scene_at(script, upto)，由调用方每帧问一次。
## 这样「动画播多久」完全由渲染层决定，逻辑层不用猜 —— 这是 Agentshire
## 用 setTimeout 串动画、结果长出幽灵 NPC / 工位撞车之后的结论。

const CHARACTERS := [
	{"key": "owner", "name": "群主", "role": "owner", "accent": Color("f2b632"), "skin": Color("f7d9c4"), "hair": Color("4a4033"), "vibe": "leader"},
	{"key": "coordinator", "name": "管理员", "role": "coordinator", "accent": Color("f2b632"), "skin": Color("f7d9c4"), "hair": Color("2f3550"), "vibe": "star"},
	{"key": "opencode", "name": "OpenCode", "accent": Color("37c2a8"), "skin": Color("fbdcc8"), "hair": Color("1f4d47"), "vibe": "hoodie"},
	{"key": "workbuddy", "name": "WorkBuddy", "accent": Color("e8a33d"), "skin": Color("f7d9c4"), "hair": Color("4a3a1e"), "vibe": "glasses"},
	{"key": "qoder", "name": "Qoder", "accent": Color("a78bfa"), "skin": Color("fbdcc8"), "hair": Color("3b2f5c"), "vibe": "hoodie"},
	{"key": "trae", "name": "TRAE", "accent": Color("fb7185"), "skin": Color("f9dfcb"), "hair": Color("5c2130"), "vibe": "cool"},
	{"key": "codex", "name": "Codex", "accent": Color("a3e635"), "skin": Color("f7d9c4"), "hair": Color("3f4d16"), "vibe": "sleepy"},
	{"key": "claudecode", "name": "Claude Code", "accent": Color("818cf8"), "skin": Color("fbdcc8"), "hair": Color("2c2f52"), "vibe": "bun"},
]

## 任务表初始条目。comments 是群员意见，会改写对应条目文本。
const BASE_ITEMS := [
	{"id": "T1", "text": "_classify_pending_merge 增加第二条证据：MERGE_HEAD == rev-parse(请求分支)"},
	{"id": "T2", "text": "为「别人的待提交现场」补三条拒绝用例"},
	{"id": "T3", "text": "_run_gate 摘要取门禁自己说的「结果：」行"},
	{"id": "T4", "text": "docstate --verbose 的 UTF-8 崩溃固化进代码"},
	{"id": "T5", "text": "仓库根 ruff 全量口径的 2 条既有问题"},
]

const SLACK_STATES := ["snack", "snoop", "sleep"]


static func make_script(rounds: int) -> Array:
	var m: Array = []
	_add(m, "群主", "新任务：mergeguard 续跑会认错人，把别人的合并当成自己的提交掉。优先级最高。", "dispatch")
	_add(m, "管理员", "收到。我先细化成任务表，然后轮传给各位看。", "text")
	for r in range(1, rounds + 1):
		_add(m, "管理员", "第 %d 轮：任务表如下，请大家对照群主的原始需求提意见。" % r, "table")
		_add(m, "OpenCode", "T2 的用例要覆盖「分支 tip 已前进」这个形态，否则过期现场漏掉。" if r == 1 else "这轮没问题。", "comment")
		_add(m, "Qoder", "T5 是我之前踩的 ruff 口径问题，能并进来就一起做。" if r == 1 else "同意。", "comment")
		_add(m, "TRAE", "T3 的摘要是绿灯的证据，建议单独一条，别混在 T2 里。" if r == 1 else "过。", "comment")
		if r < rounds:
			_add(m, "管理员", "第 %d 轮意见已收到，我改一版再传。" % r, "text")
	_add(m, "管理员", "轮完 %d 轮，任务表定稿。各位可以认领。" % rounds, "publish")
	_add(m, "OpenCode", "我领 T3、T4。", "claim")
	_add(m, "Qoder", "我领 T5。", "claim")
	_add(m, "TRAE", "T2 给我。", "claim")
	_add(m, "OpenCode", "T4 完成：UTF-8 固化进代码，四道门禁零环境变量全绿。", "done")
	_add(m, "Qoder", "T5 完成：ruff 全量口径 2 条既有问题清掉了。", "done")
	_add(m, "OpenCode", "T3 有点卡——摘要在结果行之后被吃掉的场景比想的多。", "stuck")
	_add(m, "TRAE", "我来。", "help")
	_add(m, "TRAE", "搞定，根因是门禁可能在结果后打印任何东西。", "done")
	_add(m, "管理员", "全部完成，我来做代码审核。", "review")
	_add(m, "管理员", "审核通过。本次合并落地。", "pass")
	return m


static func _add(m: Array, from: String, text: String, kind: String) -> void:
	m.append({"from": from, "text": text, "kind": kind})


static func make_task_table(round: int, comments: Dictionary) -> Dictionary:
	var items: Array = []
	for it in BASE_ITEMS:
		var txt: String = it["text"]
		var owner: String = ""
		var state: String = ""
		if comments.has(it["id"]):
			txt = String(comments[it["id"]])
		items.append({"id": it["id"], "text": txt, "owner": owner, "state": state})
	return {"round": round, "title": "把 mergeguard 的续跑判据从「标签」换成「事实」", "items": items}


## 给定已播到第几条消息，返回每个角色当前状态。
## 与 story.js 的 sceneAt() 语义一致，两边应保持同步。
static func scene_at(script: Array, upto: int, rng: RandomNumberGenerator) -> Dictionary:
	var st := {}
	for c in CHARACTERS:
		st[c["key"]] = {"state": "idle", "label": ""}

	var shown := script.slice(0, maxi(0, upto))
	# 从后往前找每个角色最后一条相关消息 —— 那一条决定它当前状态
	var last_of := func(who: String) -> Dictionary:
		for i in range(shown.size() - 1, -1, -1):
			if shown[i]["from"] == who:
				return shown[i]
		return {}

	for c in CHARACTERS:
		var mine: Dictionary = last_of.call(c["name"])
		if mine.is_empty():
			continue
		match String(mine.get("kind", "")):
			"claim": st[c["key"]] = {"state": "typing", "label": "干活中"}
			"done": st[c["key"]] = {"state": "done", "label": "任务完成"}
			"stuck": st[c["key"]] = {"state": "stuck", "label": "卡住了"}
			"help": st[c["key"]] = {"state": "helping", "label": "赶去帮忙"}
			"comment": st[c["key"]] = {"state": "idle", "label": "看任务表"}

	var coord: Dictionary = last_of.call("管理员")
	if not coord.is_empty():
		match String(coord.get("kind", "")):
			"review": st["coordinator"] = {"state": "reviewing", "label": "代码审核"}
			"table", "publish": st["coordinator"] = {"state": "typing", "label": "细化任务表"}

	# 摸鱼是随机点缀：只在没正事时偶尔出现
	for c in CHARACTERS:
		var cur: Dictionary = st[c["key"]]
		if cur["state"] == "idle" and rng.randf() < 0.22:
			var roll: float = rng.randf()
			var kind := "snack" if roll < 0.34 else ("snoop" if roll < 0.67 else "sleep")
			st[c["key"]] = {
				"state": kind,
				"label": "偷吃零食" if kind == "snack" else ("偷看别的屏幕" if kind == "snoop" else "偷偷睡觉"),
			}
	return st
