# 输入与输出接口

运行时为 Python 3.11+ 标准库。脚本不请求网络、读取账户信息或执行交易；调用方 agent 收集并核验官方信息。

## 命令行

```text
python3 scripts/query_events.py --prepare [查询选项]
python3 scripts/query_events.py --input 文件或- [查询选项] [--format both|json|markdown]
```

- `--period today|week|next`：默认 today。week 为美东周一至周日；next 配合 `--days N`，包括今天，共 N 个日历日。
- `--start YYYY-MM-DD --end YYYY-MM-DD`：显式日期，含首尾；只给 start 时查询该日，与 period 互斥。
- `--instruments ES NQ GC`：品种过滤，默认配置中的三个品种。
- `--categories macro earnings expiry`：类别过滤，默认全部。
- `--companies NVDA AMD SPCX`：财报公司过滤；未指定时使用完整配置名单。只查财报时同时设置 categories earnings。
- `--contracts GCZ26`：具体合约过滤，可接受 GCZ2026；同类未匹配合约不显示，宏观和财报仍按品种过滤。只查合约节点时同时设置 categories expiry。
- `--config PATH`：替换默认配置；默认配置相对于脚本定位，与当前工作目录无关。
- `--now ISO8601`：固定查询时刻供测试与复现，必须带偏移，例如 `2026-09-30T08:43:11Z`。实时查询省略此参数。
- `--format both`：中文 Markdown + JSON，默认；json 包含 `summary_zh`，方便调用方显示摘要。

`--prepare` 的输出保存查询范围、过滤条件、`query_started_at`、待核验来源和空事件列表。填写后仅用 `--input` 即可沿用范围和过滤条件；倒计时仍按渲染时的当前时刻计算。显式查询选项可覆盖骨架条件，但日期或合约范围超出已核验范围时会标为不完整。

## 输入

根对象包含 `query_started_at`、`range`、`query`（骨架生成）、`events`、`coverage`。`range` 为已收集数据所覆盖的美东日期范围：`start_date`、`end_date`、`timezone`。`required_sources` 是骨架的提示信息，程序会重新从配置计算，不信任其中的来源定义。

事件必填：

| 字段 | 含义 |
|---|---|
| `type` | config.json 中的事件类型，或 core_cpi / core_pce 别名 |
| `precision` | exact、session 或 date |
| `status` | confirmed 或 unconfirmed；默认 confirmed，表示已确认的计划信息 |
| `at` | exact 时必填：带 UTC 偏移的 ISO8601 时间 |
| `date`、`timezone` | session / date 时必填：官方日历日期及 IANA 时区 |
| `session` | session 时必填：before_open 或 after_close |
| `symbol` | 财报必填：配置里的公司代码 |
| `contract` | 具体期货／黄金期权节点必填：标的期货代码，例如 GCZ26 |
| `source_id`、`url`、`checked_at` | 官方来源标识、实际核验的链接、带偏移的本次核验时刻 |

可选 `note`、`option_contract`。脚本从配置生成标题、类别及各品种级别，输入里的自定义颜色不影响分级。

下面是用于说明接口的固定样例，不是实时查询数据；测试时配合对应的 `--now`：

```json
{
  "query_started_at": "2026-10-02T11:55:00Z",
  "range": {"start_date": "2026-10-02", "end_date": "2026-10-02", "timezone": "America/New_York"},
  "events": [
    {"type": "nonfarm_payrolls", "precision": "exact", "at": "2026-10-02T08:30:00-04:00", "source_id": "bls_jobs", "url": "https://www.bls.gov/schedule/news_release/empsit.htm", "checked_at": "2026-10-02T11:56:00Z"}
  ],
  "coverage": [
    {"source_id": "bls_jobs", "status": "checked", "url": "https://www.bls.gov/schedule/news_release/empsit.htm", "checked_at": "2026-10-02T11:56:00Z", "note": "已核对官方发布日历"}
  ]
}
```

该样例仅提供一个来源，默认全类别查询会正确报告其他来源缺失。未核验来源不需要填造数据。

## 覆盖状态

每条 coverage 包含 `source_id`、`status`、`url`、`checked_at`、可选 `note`。一条来源只有一个状态，查询骨架已列出所需标识。

- `checked`：已核验来源的相关类型、公司／合约及指定日期范围。该来源事件列表可为空。
- `not_announced`：已核对财报来源，但相关日期尚未宣布；不同于已核验无事件。仅用于财报来源。
- `partial`：读取了部分日期、部分合约或只确认了财报发布／会议其中一项，需要在 note 中说明缺失部分。
- `unavailable`：没有读取到可用信息，checked_at 可为 null。

默认要求核验时间不早于 query_started_at，且距离渲染不超过 60 分钟；更旧的资料可留在结果里，但标为 stale，不进入临近提醒，也不证明范围已完整覆盖。实际资料发布时间和核验时间是两个不同字段语义，不能通过更新 checked_at 伪装重新联网核验。输入中的未来核验时间报错。

## 输出

JSON 包含 `schema_version`、`generated_at`、`query_started_at`、`range`、`filters`、`coverage`、`events`、`imminent_event_ids`、`warnings`、`summary_zh`。

每个事件包含稳定 `id`、type/title/category、symbol/contract、`contract_month`（例如 2026-12）、`levels`（各相关品种级别）、`priority`、`precision`、`confirmation_status`、`verification_status`、`at_utc`、双时区文本、日期候选、`minutes_to_event`、`schedule_status`、来源链接及核验时间。

只有 confirmed + 本次核验有效 + exact + 配置的重要级别，且剩余时间满足 **0 ≤ 时间差 ≤ 4 小时**，才进入 imminent_event_ids。过去事件仍保留在日历中。没有精确时间时 at_utc、minutes_to_event 为 null；显示已知的日期／盘前盘后和保守的北京时间日期范围，不生成假的准确时刻。

同次 CPI / 核心 CPI、PCE / 核心 PCE 合并；FOMC 双事件、财报发布／会议、不同合约保留独立 ID。来源对同一事件的精确时间或 session 信息相冲突时降为日期精度、unconfirmed，并显示冲突说明。

coverage.status 为 complete、partial 或 unavailable；not_announced 和 stale 都不算完整覆盖。缺失状态不等于交易安全信号。结构错误退出码为 2，输出 JSON error；来源不完整仍返回可用结果，退出码为 0，调用方根据 coverage 判定完整性。
