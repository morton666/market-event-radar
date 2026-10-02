# Hermes 每日盘前简报

适用于常驻服务器，每天生成一份中文简报。联网查询由 Hermes agent 执行，脚本完成校验、时间转换和留档。完整 JSON 不直接发送到聊天。

## 安装与运行条件

技能仓库：[morton666/market-event-radar](https://github.com/morton666/market-event-radar)。通过服务器上 Hermes 的技能安装入口安装此仓库；安装参数以当地版本的帮助为准。无需在服务器部署本地 Mac 路径。

[Hermes 技能说明](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/)说明，GitHub／URL 安装会筛选 SKILL.md 引用的支持文件。本包直接链接两个 scripts 文件、assets/config.json、三个 references 文件和一个 examples 模板。安装后核对这些文件齐全，Python 为 3.11+，系统包含 America/New_York 和 Asia/Shanghai 时区。Linux 精简镜像缺时区时，由服务器环境安装系统 tzdata。

自定义配置复制到技能目录外，两个阶段都传同一个 `--config`。旧版根目录 config.json 的自定义值需迁入外部配置；新版默认配置在 assets/config.json。

## 每次运行

工作目录设为安装后的技能绝对路径，状态目录使用技能外的绝对路径。以下状态目录由当前服务用户持有，无需管理员权限：

```bash
python3 scripts/daily_brief.py --prepare \
  --state-dir "$HOME/.local/state/market-event-radar" \
  --instruments ES NQ GC
```

标准输出是运行清单，包含 `input_path`、美东范围、来源标识、采集截止时间及预算。脚本创建独立 `run_id`；在该 input.json 中只填写 `events`、`coverage`，不要改 query_started_at、range、query、daily 或旁边的 request.json。按来源规则逐项联网核验，填写真实 checked_at；无法读取的来源保留缺失状态及原因。

填好后，将清单里的完整 input_path 原样传入 `--input`，同时提供同一个 `--state-dir` 和自定义 `--config`（如有）。日报入口默认输出 Markdown；`--format json` 可改为完整 JSON。实时运行不传 `--now`。

- 默认范围为美东今天及之后 7 天，含首尾共 8 个日历日。`--preview-days N` 可在 prepare 时覆盖；渲染自动沿用该数值及筛选条件。
- `daily.preview_limit` 默认 12，只限制未来重点预告；今天全部事件和 JSON 都不截断。预告优先选择红色事件，再按时间展示；省略数量明确列出。
- 每次 prepare 创建全新运行。跨美东日期、超过 verification_max_age_minutes、配置或请求范围改变时，日报拒绝旧输入，返回 2；需要重新准备和核验。不要给旧文件更新时间戳以绕过检查。
- 失败来源不会阻止其他已确认事件写入报告。报告按 `状态目录/runs/美东日期/run_id/` 存为 request.json、input.json、report.json、summary.md；每个文件原子写入。生成成功不代表消息已送达。
- 运行历史不会自动删除；根据服务器存储策略归档。每个定时任务使用自己的状态目录。此版留存历史，但不推断改期或取消；事件跨日标识和变更对比留待后续版本。

## 状态与投递

| 退出码 | 意义 | agent 处理 |
|---|---|---|
| 0 | 核验正常，可有尚未宣布的财报日期 | 返回中文摘要；无事件也发一份简短结果 |
| 3 | 非关键来源不完整 | 保留摘要中的缺失说明，照常交给调度器投递 |
| 4 | 关键来源缺失或所有来源不可用 | 返回失败标记和已保存的摘要，不能报成“今日无事件” |
| 2 | 输入、配置、路径、过期请求或保存失败 | 返回失败标记与具体错误；不要用昨日摘要代替 |

先读取脚本输出和退出码，不要用 `&&` 把返回 3／4 的报告跳过，也不要无限重跑。来源健康和日历覆盖分别记录在 run_status、source_health、coverage。

按[当前 Hermes 定时任务文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron/#declaring-a-failed-run)，agent 正常结束并不会自动把子脚本失败记为调度失败。退出码 2／4 时，最终回复第一行单独写 `[CRON_FAILURE]`，随后保留具体错误或可用摘要。部署时确认服务器版本支持该标记；旧版先保留明确的失败正文并升级或核对其状态接口。不要在正常报告中引用这个标记，也不要使用静默标记，日报应让用户知道任务实际执行了。

## 定时任务设置

使用 Hermes 的 agent 型任务，绑定 market-event-radar 技能和[完整任务提示词](../examples/hermes-daily-prompt.md)。cron 工具环境需要 Python、文件读写、联网读取能力；动态来源需要可用的浏览器或对应官方公告入口。此包没有独立联网采集器，不使用仅执行脚本的 no-agent 模式。

配置每天的发送时刻、接收渠道和实际安装路径后，先触发一次手动运行，检查 report.json、投递结果及 Hermes 的运行状态。调度时区以 America/New_York 为目标，核对服务器版本实际采用的时区和 next_run_at；不能用固定北京时间代替全年美东盘前时间。时间和渠道由部署方设置，本包不创建定时任务或修改服务器配置。

Hermes 的独立会话需要明确工作目录和任务条件，最终回复由调度器投递；不要再调用消息工具额外发送一份。[任务会话、工作目录及投递说明](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron/)

## 部署验收

1. 在安装包外的工作目录，用绝对脚本路径执行 prepare；确认默认配置加载成功，输入日期等于此刻的美东日期。
2. 完成一次真实联网核验并生成报告，检查中文摘要与留档 JSON 一致。未读到的来源应明确显示，不能以测试夹具代替。
3. 用独立临时状态目录测试全来源不可用、仅财报日期未宣布、隔天输入。预期分别为 4、0、2；失败仍能显示原因。
4. 触发 Hermes 任务，检查技能加载、文件位置、用户实际收件与任务状态。脚本层测试不等于已通过服务器投递验收。

仓库测试还覆盖夏令时、北京时间跨日、完整今日事件和预告截断。Hermes 的调度去重及投递记录由它自己管理，脚本不宣称消息已送达。
