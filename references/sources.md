# 官方来源核验

来源入口、允许的官方域名、公司名单存于 [默认配置](../assets/config.json)。每次查询运行 `--prepare` 得到本次所需来源，按查询类别和品种筛选，避免读取无关日历。

每日运行先核验宏观和交易所关键来源，再核验财报；相同官方日历可一次读取后分别记录覆盖状态。优先读取官方 JSON、ICS 或公告正文，动态空壳再用浏览器或官网直接链接的公告。工具支持超时设置时，每次请求默认最多 20 秒。attempts_per_source 默认 2，表示首次核验及故障后的重试合计最多两轮；必要的目录→公告正文／时区说明导航属于同一轮，不必在第二页截断核验。重试可换备用入口或浏览器，不对已失败入口反复请求。总采集预算 480 秒，具体以 daily.collection 为准；工具不支持强制超时时，在工具返回后检查剩余预算，不再追加超时预算外的尝试。

达到预算即保存已确认事件，剩余来源标 unavailable／partial，并在 note 说明超时、拒绝访问或未覆盖的日期／合约。重试不循环整个任务，不因某一来源失败丢弃其他结果。这些采集约束由 agent 执行，两个 Python 脚本仅处理已收集的数据。

历史缓存用于参考，不能替代本次核验。若使用 HTTP 条件请求，只有本次成功返回 304 且本地保留对应响应正文及验证器时，才可视为重新核验；记录实际请求时间与依据。请求失败时保留旧核验时刻，不能只改 checked_at。

## 宏观

| 来源标识 | 核验内容 | 入口 |
|---|---|---|
| `fed` | 当次 FOMC 决议、记者会；会议第一天不等于决议日 | [会议日历](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)、[活动日历](https://www.federalreserve.gov/newsevents/calendar.htm) |
| `bls_cpi` | CPI / 核心 CPI 的同次发布时间 | [CPI 日历](https://www.bls.gov/schedule/news_release/cpi.htm)、[ICS](https://www.bls.gov/schedule/news_release/bls.ics) |
| `bls_jobs` | Employment Situation，包括非农 | [就业报告日历](https://www.bls.gov/schedule/news_release/empsit.htm)、[ICS](https://www.bls.gov/schedule/news_release/bls.ics) |
| `bea` | Personal Income and Outlays，其中包含 PCE 及核心 PCE | [发布日历](https://www.bea.gov/news/schedule)、[ICS / JSON 入口](https://www.bea.gov/news/schedule/icalendar) |

读取来源的时区说明。BLS 的参考月份是统计所属月份，事件日期使用 Release Date。BEA 的季度 GDP 与 PCE 月度发布区分开。[官方 JSON](https://apps.bea.gov/API/signup/release_dates.json) 中仅选 `Personal Income and Outlays`，保留其带偏移时间；州级 Real Personal Consumption Expenditures 不是本技能的核心 PCE 数据。FOMC 常规发布时间仅供检索，填入精确时间须有适用于当次会议的官方依据；若只能确认日期，保留日期精度。

同一个 ICS 可以核对两类 BLS 来源，但分别填写覆盖状态。订阅文件要读取本次内容，不能把文件最后修改日期当作本次核验时刻。数据发布时间发生调整时使用当前公告。

## 财报

| 标识 | 公司 | 官方入口 |
|---|---|---|
| `earnings:NVDA` | NVIDIA | [活动](https://investor.nvidia.com/events-and-presentations/events-and-presentations/default.aspx)、[新闻](https://investor.nvidia.com/news/press-release/default.aspx) |
| `earnings:AAPL` | Apple | [投资者关系](https://investor.apple.com/)、[新闻](https://www.apple.com/newsroom/)、[电话会议](https://www.apple.com/investor/earnings-call/) |
| `earnings:MSFT` | Microsoft | [投资者关系](https://www.microsoft.com/en-us/investor/)、[活动](https://www.microsoft.com/en-us/investor/events)、[新闻](https://news.microsoft.com/source/) |
| `earnings:AMZN` | Amazon | [活动](https://ir.aboutamazon.com/events/default.aspx)、[新闻](https://press.aboutamazon.com/) |
| `earnings:GOOGL` | Alphabet | [投资者关系](https://abc.xyz/investor/)、[活动](https://abc.xyz/investor/events/default.aspx) |
| `earnings:META` | Meta | [活动](https://investor.atmeta.com/investor-events/default.aspx)、[新闻](https://investor.atmeta.com/investor-news/default.aspx) |
| `earnings:TSLA` | Tesla | [投资者关系](https://ir.tesla.com/) |
| `earnings:AMD` | AMD | [活动](https://ir.amd.com/news-events/ir-calendar)、[新闻](https://ir.amd.com/news-events/press-releases) |
| `earnings:SPCX` | SpaceX | [活动](https://ir.spacex.com/events/)、[新闻](https://ir.spacex.com/updates/) |

先看 Upcoming Events 和最新 earnings-date 公告，再打开相关正文。财务季度不是自然季度，不按往年财报日期推算今年日期。“after market close”只能记录为 `session / after_close`；有精确时间的 webcast 应记录为 `earnings_call`。

动态页面加载出的导航、订阅表单或空白容器不证明无事件。可用浏览器加载、读取官网直接链接的公告或搜索官方域名里的公告；搜索结果只帮助定位，仍需正文核验。只读到部分内容用 `partial`；完全未读到可用内容用 `unavailable`。完整核对后仍没有下一次财报日期用 `not_announced`；完整覆盖指定历史／已公布范围且没有落在范围内的事件用 `checked`。

## 到期和合约节点

| 标识 | 核验内容 | 官方入口 |
|---|---|---|
| `cboe` | 标准月度、季度集中到期及节假日调整 | [交易时间和年度期权日历入口](https://www.cboe.com/about/hours/us-options/) |
| `cme_es` | 指定 ES 季度期货最后交易／到期日期 | [ES 日历](https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.calendar.html) |
| `cme_nq` | 指定 NQ 季度期货最后交易／到期日期 | [NQ 日历](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.calendar.html) |
| `cme_gc_options` | GC 标准月度期权到期及对应标的月份 | [黄金期权日历](https://www.cmegroup.com/markets/metals/precious/gold.calendar.options.html) |
| `cme_gc_futures` | GC 各合约第一通知日、最后交易日 | [黄金期货日历](https://www.cmegroup.com/markets/metals/precious/gold.calendar.html)、[清算公告](https://www.cmegroup.com/notices/clearing.html) |

打开查询年度的官方日历／PDF，核对表格列头、月份、日期说明和交易所时区。季度集中到期由当季的标准月度期权到期和 ES/NQ 季度期货节点共同呈现；Cboe 标注的 `End-of-Quarter` 季末季度期权另用 `us_quarterly_opex`，不能把季末日期当作“三巫日”。各节点不归并成一个共同结算时刻。

CME 页面可能动态加载。未获得日期表不能声明已核验。可读取同一交易所发布的合约日历、年度金属日历或当前交割月清算公告。公告若只覆盖某交割月，其他合约仍属于缺失范围；不能用旧年度 PDF 填今年的日期。

黄金表中 `FIRST POSITION` 与 `FIRST NOTICE` 是不同列，必须对照列头。标准月度黄金期权的标的月份核对现行 [COMEX 第 115 章](https://www.cmegroup.com/rulebook/COMEX/1a/115.pdf)；日期仍取当前合约日历。精确终止时刻可核对 [黄金期权规格](https://www.cmegroup.com/markets/metals/precious/gold.contractSpecs.options.html#optionProductId=192)，不能把规格中的时刻应用到通知日。

无精确时间的交易所日期使用 `date` 精度，`timezone` 保留来源时区。没有说明时区时先核对说明，无法确认则不补造事件。第一通知日是日期节点，不能虚构为当天零点的最后平仓时刻。
