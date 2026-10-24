# 提案：池子候选自发现（pool-self-provision）

状态：**已批准并实施**（2026-10-23，仓库主人指示“批”）。

新 harness 注册后，平台从已有注册表、池槽位声明和 CLI 适配器声明中发现
尚未覆盖、值得人工确认的候选，并通过既有 alerts 提醒。发现是只读的：不调用
未知 CLI、不把未知 tier 当作 free、不自动写入池配置。

规则：未覆盖 harness 生成 `uncovered_harness` 候选；缺 tier 槽位生成
`tier_missing` 候选；两者均为 `tier=unknown`，附来源证据。候选集合按内容
指纹入队，首轮只建立基线，重复指纹由 alerts 的 `source_key` 去重。人工确认
后，维护者才补写 `data/pool/config.json` 的显式声明。

实现为 `backend/app/pool/provision.py` 与 scripted 测试，入口为
`python -m app.pool.provision`；不新增路由、表、事件或权限键。
