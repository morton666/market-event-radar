# 每日入口验收：2026-10-02

这是当次官方查询的历史记录，不可用作以后日期的已核验日历。

## 本次范围与结果

- 在本机执行 daily_brief.py --prepare，未传 --now；仅筛选 macro，关注 ES/NQ/GC。
- 查询范围：美东 2026-10-02 至 2026-10-09，包含今天及之后 7 天。
- 结果：run_status=ok、coverage.status=complete、退出码 0，4 个相关来源完整核验，1 个事件进入四小时置顶。
- 事件的双时区时间、已核验范围及来源链接保存在 daily-macro.input.json、daily-macro.report.json 和 daily-macro.summary.md。
- 本次仅核验宏观类别，不包含财报和交易所到期节点。FOMC 会议纪要不在当前配置的事件类型中。

## 来源依据

1. [美联储十月日历](https://www.federalreserve.gov/newsevents/2026-october.htm)：核对当月决议、记者会安排与查询范围。
2. [BLS CPI 发布表](https://www.bls.gov/schedule/news_release/cpi.htm)：核对当月 CPI 日期，区分统计参考月份和发布日期。
3. [BLS Employment Situation 发布表](https://www.bls.gov/schedule/news_release/empsit.htm)及[十月月历](https://www.bls.gov/schedule/2026/10_sched.htm)：核对非农日期、08:30 及 Eastern Time 说明。
4. [BEA 官方 JSON](https://apps.bea.gov/API/signup/release_dates.json)：读取 Personal Income and Outlays 序列，核对 PCE 查询范围。

BLS ICS 第一次读取被工具以不支持 text/calendar 拒绝；随后读取官方 HTML 正文完成核验，没有将抓取失败视为无事件。事件和 coverage 的 checked_at 是当次阅读核验后的时刻。

## 自动验证及限制

60 项标准库 unittest 通过，包含原有 36 项时区、去重、精度和合约边界测试，以及新增每日请求、状态码、留档、独立支持文件安装包测试。技能格式验证通过。运行环境 Python 3.14，源码通过 Python 3.11 语法解析；尚未在 Python 3.11 解释器上单独运行。

安装包测试按 Hermes 文档的支持文件筛选规则，只复制 SKILL.md 直接链接的文件，在技能目录外执行两个阶段。这验证安装资源完整性，不代表已在实际 Hermes 安装器或用户常驻服务器上完成安装与消息投递验收。

归一化复现可使用 query_events.py --input 读取本目录 input.json，并将 report.json 的 generated_at 传给 --now。这会生成通用查询摘要；日报摘要可通过 daily_brief.daily_summary 对同一归一化结果生成。历史快照中的 artifacts 路径为当时的临时运行目录；日报线上调用仍须先创建本次新请求。
