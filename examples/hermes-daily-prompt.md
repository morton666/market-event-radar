# 每日任务提示词

将以下正文用作 Hermes agent 型定时任务的提示词。创建任务时绑定 market-event-radar 技能，workdir 指向技能安装目录，并设置接收渠道与每日时刻。任务不依赖之前的聊天记录。

---

使用 market-event-radar 技能，为 ES、NQ、GC 生成今天的一份中文盘前简报。读取技能目录内 references/hermes.md 和 references/sources.md；按配置名单查询财报，未指定 GC 持仓时按合约列出节点。

每次先运行 scripts/daily_brief.py --prepare，--state-dir 使用当前服务用户主目录下 .local/state/market-event-radar 的绝对路径。实时运行不使用 --now；不要读取昨日输入后仅修改日期或 checked_at。自定义配置如已在本任务中指定，在 prepare 和 render 两阶段都显式使用同一 --config。

读取本次 prepare 返回的 input_path，只填写其中 events、coverage。使用可用的联网工具核验本次查询范围内的官方来源。先处理宏观和交易所来源，再处理财报。遵守 prepare 给出的采集预算与截止时间；达到预算后停止新尝试，把尚未核验的来源记录为 unavailable 或 partial，并说明原因。日期尚未宣布使用 not_announced，不能与已核验无事件混淆。财报发布和电话会议分别记录，不补造精确时间。

运行 scripts/daily_brief.py --input，传本次 input_path 及相同状态目录。保留命令退出码并读取输出；0、3、4 都可能已经保存完整报告。不要因返回非零而丢弃已保存摘要，不循环重试整个任务。

最终回复只返回脚本生成的中文日报，不附完整 JSON。日报应包括未来四小时置顶、今天全部事件、未来七天重点预告及缺失来源；如配置改变预告天数，以配置为准。即使没有事件也照常回复，不使用静默标记。不要另调用消息工具发送，交给定时任务投递。

退出码为 2 或 4 时，在最终回复第一行单独写 [CRON_FAILURE]，之后输出具体错误或已有摘要，使任务显示为失败；退出码为 3 时照常保留“部分来源缺失”。来源不可用不能改写成“没有事件”，历史资料不能改写成当天已核验。
