# 2026-09-30 验收记录

技能包保存在项目 `market-event-radar/`。脚本运行要求 Python 3.11+，仅使用标准库；本机实际验证环境为 macOS、Python 3.14.5。

这是本次联网核验的固定快照，不是实时事件订阅。核验批次开始于 `2026-09-30T08:43:11Z`，完成记录及快照时刻为 `2026-09-30T09:28:10Z`（美东 05:28:10、北京 17:28:10）。网站资料的发布／更新时间与本次核验时间分开处理。

## 格式及脚本验证

- 使用 skill-creator 的 `quick_validate.py` 验证：`Skill is valid!`。
- 执行 `python3 -B -m unittest discover -s tests -q`：36 项测试全部通过。
- 三组真实输入通过 CLI 生成 JSON，并核对事件数、过滤条件、覆盖状态、临近提醒及双时区结果。

| 验证范围 | 结果 |
|---|---|
| 冬夏时间、夏令时切换周及重复小时 | 通过；周的实际长度可为 167 / 169 小时 |
| 北京跨午夜、美东“今天”及周一至周日 | 通过 |
| 4 小时含边界、排除过去事件、日期查询保留过去事件 | 通过 |
| FOMC 双事件、CPI/PCE 总体及核心合并、发布及会议分开 | 通过 |
| 日期／盘前盘后精度、未知时间保留 null 倒计时 | 通过 |
| 合约筛选、两位／四位年份、显式合约年月及期权系列保留 | 通过；更换未核验合约会降为部分覆盖 |
| 交易所日期及节假日调整后的输入原样保留 | 通过 |
| 来源不可用、未宣布、已核验无事件、时效过期、部分范围 | 通过 |
| 冲突信息、非官方域名、错误 JSON、未来核验时刻 | 通过 |
| 从其他工作目录运行、stdin 输入、配置颜色及提前量 | 通过 |

## 三类真实查询

| 查询 | 美东日期范围 | 已列事件 | 来源覆盖 | 文件 |
|---|---|---:|---|---|
| 今日盘前摘要：ES / NQ / GC | 2026-09-30 | 3，置顶 1 | 部分 | [输入](today.input.json)、[中文摘要](today.summary.md)、[JSON](today.report.json) |
| 本周重点财报：完整配置的 9 家公司 | 2026-09-28 至 2026-10-04 | 0 | 部分，不能断言无财报 | [输入](weekly-earnings.input.json)、[中文摘要](weekly-earnings.summary.md)、[JSON](weekly-earnings.report.json) |
| GCZ26 未来节点：期权、第一通知、最后交易 | 2026-09-30 至 2026-12-29 | 4 | 完整，针对本次合约及范围 | [输入](gcz26-nodes.input.json)、[中文摘要](gcz26-nodes.summary.md)、[JSON](gcz26-nodes.report.json) |

今日 PCE 按 [BEA 官方 JSON](https://apps.bea.gov/API/signup/release_dates.json) 的 `Personal Income and Outlays` 项记录为 12:30 UTC，即美东 08:30、北京 20:30，在快照时刻距计划时间约 182 分钟，符合 4 小时置顶条件。总体与核心数据合并为一个事件。

[Cboe 2026 官方期权日历](https://cdn.cboe.com/resources/options/Cboe2026OPTIONSCalendar.pdf) 的颜色图例及日期已通过 PDF 渲染核对：9 月 30 日为季末季度期权，保留日期精度；与当季标准月度期权及 ES/NQ 季度期货集中到期分别处理。同一日历列出 6 月 19 日假日、标准月度到期移至 6 月 18 日，脚本测试采用这个调整后的日期，未按第三个星期五重新计算。

[CME 黄金期货日历](https://www.cmegroup.com/markets/metals/precious/gold.calendar.html) 已通过浏览器加载并对照列头：今日列出 GCV26 第一通知日；GCZ26 列出 11 月 30 日第一通知日和 12 月 29 日最后交易日。两者仅核对日期，保持 Chicago 来源时区及未知分钟时刻。

GCZ26 对应的两个月度期权来自 [CME 黄金期权日历](https://www.cmegroup.com/markets/metals/precious/gold.calendar.options.html)：OGX26 为 10 月 27 日，OGZ26 为 11 月 24 日。标的月份通过 [现行 COMEX 115](https://www.cmegroup.com/rulebook/COMEX/1a/115.pdf) 核对；13:30 美东终止时刻来自 [当前合约规格](https://www.cmegroup.com/markets/metals/precious/gold.contractSpecs.options.html#optionProductId=192)。转换后分别为北京次日 01:30、02:30，验证跨午夜及夏令时变化。期权月份没有被当作标的期货月份。

财报核验保留了实际限制：NVDA、MSFT、TSLA、AMD、SPCX 的已读取页面未列下一次财报日期，使用 `not_announced`；AAPL、AMZN、GOOGL 未取得可用动态日历正文，使用 `unavailable`；META 只取得部分活动信息，使用 `partial`。全部附实际链接及原因。本周的空事件列表没有被表述为“本周无财报”。

## 复现与实时使用

在技能目录中复现今日快照：

```bash
python3 -B scripts/query_events.py \
  --input tests/acceptance/2026-09-30/today.input.json \
  --now 2026-09-30T09:28:10Z
```

其他两组替换输入文件即可。省略固定 `--now` 后，旧核验记录会按时效标为过期；更新 `checked_at` 不能代替重新联网。

实时调用先运行 `--prepare`，由 agent 阅读并核验官方来源、填入事件及覆盖状态，再用 `--input` 生成中文摘要和 JSON。将整个技能目录交给支持技能的 agent，即可按 [SKILL.md](../../../SKILL.md) 调用；交付位置仍在当前项目。
