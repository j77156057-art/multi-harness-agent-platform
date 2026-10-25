# 部署免费档目录变更哨（free-set-watch）

> 这一页写的是**怎么把它装上去**。哨本身的功能与取舍在
> [`docs/tasks/notes/pool-free-set-watch.md`](tasks/notes/pool-free-set-watch.md)。

## 它是什么、不是什么

免费额度会失效、免费模型会下架、新的会出现 —— 目录会变，人需要被喊。池子的目录
本身已经是动态的（`PoolService.models()` 带 tier 标签），缺的是「变了有人说」。

这是一件**两段**的事，别混成一件：

| 段 | 进程 | 干什么 |
|---|---|---|
| **哨** | `python -m app.pool.free_set_watch` | 比对免费档名单与上次快照；变了就把一条告警**入队**（`alerts_enqueued`），自己不投递 |
| **投递** | `python -m app.alerts` | 把队里的告警投给收件人（HMAC 签名的 webhook），失败重试 |

哨**不主动探测**烧 CLI 槽：一次探测就是一次真实调用。活性由消费端的冷却来喂探针。

## 部署方式（定案）

**两个独立进程，与平台进程并列。** 不是 Windows 计划任务、也不是 cron —— 那是某台机器
的事，不是仓库的事；仓库只给「跑什么、跑多久、怎么停」。

```
# 哨：每 15 分钟看一次名单（免费档变化不频繁，15 分钟足够）
python -m app.pool.free_set_watch --loop --interval 900

# 投递：每分钟把队里的告警送出去
python -m app.alerts --loop --interval 60
```

两个进程都要带平台的同一个数据目录（`AP_DATA_DIR`），否则哨写的快照与投递读的
队列不在一处：

```
AP_DATA_DIR=<平台的 data 目录> python -m app.pool.free_set_watch --loop --interval 900
AP_DATA_DIR=<平台的 data 目录> python -m app.alerts --loop --interval 60
```

### 为什么不能挂进平台进程

平台有一条不变量：**sweeps 永不做 outbound**（连告警排队都不做）——
`sweeps.py` 的 `sweep_budget` 注释里写着。哨会**发告警**（outbound 性质），所以它
不挂 `run_all_sweeps`，而是**独立入口**才合平台规矩。这条别绕过：把哨塞进 sweeper
会让「sweeps 不发消息」这条保证失效。

## 安装门槛（没有收件人就别装）

投递端要一个收件人：

| 环境变量 | 作用 | 缺了会怎样 |
|---|---|---|
| `AP_ALERT_WEBHOOK_URL` | 收件人（**HTTPS**） | `app.alerts` 报 `{"configured": false, ...}`：告警**留在账里**，没人收 |
| `AP_ALERT_WEBHOOK_SECRET` | HMAC 签名密钥 | 收件人验签不过，投递会被拒 |

所以顺序是：**先有收件人，再装哨**。没收件人就装了哨，等于「变了记录在本地账里、
没人知道」—— 那还不如不装。

本仓库目前**没有告警读面**（`routes.py` 里没有 alerts 的读端点），所以没配收件人时
队列里的告警只能直接查库（见下面「账在哪看」）。

## 怎么起、怎么停

```bash
# 起（两个进程，各自独立）
python -m app.pool.free_set_watch --loop --interval 900 &
python -m app.alerts --loop --interval 60 &

# 停：停掉进程即可，没有落盘的常驻状态要清
```

只想手动查一次（不常驻）：

```bash
python -m app.pool.free_set_watch --once
python -m app.alerts --once
```

## 账在哪看

告警队列在平台库的 `alert_deliveries` 表（一行一条告警，含重试记账）：

```sql
SELECT kind, source_key, status, attempts, created_at
FROM alert_deliveries
ORDER BY rowid DESC LIMIT 10;
```

`status` 三态：`pending`（还没投出去）、`delivered`（投出去了）、`failed`（达尝试上限
后停靠，不再自动重试 —— 这一态在操作面可见）。

入队幂等键是 `UNIQUE(kind, source_key)`，`source_key` 是当轮名单的**内容指纹**：
同一个指纹不会投两条。

## 排错

| 现象 | 原因 |
|---|---|
| 首跑什么都没发生 | **正常**：首跑只建基线（`first_snapshot: true`），不喊人 |
| 名单没变也不出声 | **正常**：同名单静默，账本不重写 |
| 快照坏了 | 算「基线丢失」，不当成变化误报 |
| `app.alerts` 报 `configured: false` | 没配 `AP_ALERT_WEBHOOK_URL` → 告警留在 `pending` |
| 投递反复失败 | 看 `attempts` 与收件人日志；达上限后停靠（`failed`），不会无限重试 |

## 一次整链验证（2026-10-26，本机）

在快照里加一个不存在的名字，模拟「供应商新增了一个免费模型」：

```
[哨]     {"sweep": "free_set_watch", "changed": true, "first_snapshot": false, ...}
[账]     kind=pool.free_set_changed  source_key=a9b68f20…  status=pending  attempts=0
[投递]   {"configured": false, "detected": 0, "delivered": 0, "failed": 0, "pending": 1}
```

哨认出了变化 → 账里多了一条 `pending` → 投递端因为本机没有收件人而**明说**没投
（`configured: false`，不假装投出去了）→ 快照已还原。

## 相关

- 实现与取舍：`docs/tasks/notes/pool-free-set-watch.md`
- 池子目录本身：`backend/app/pool/sources.py`（`declared_cli_slots()` / `CLI_TIERS`）
- 投递端：`backend/app/alerts/service.py`（HMAC 签名、失败重试直至停靠）
