# Market Event Radar · 交易红色事件雷达

供 AI agent 查询 **ES、NQ、GC** 相关事件的独立 Skill。输出中文摘要和 JSON，按美东日期组织，同时显示北京时间，并将未来 **4 小时**内有明确时间的重要事件置顶。

支持单次查询，也支持交给 **Hermes 每天生成一份盘前简报**。Python 脚本使用标准库处理数据；**官方信息的联网读取与核验由调用本技能的 AI 完成**。

## 直接把链接交给 AI

复制下面这段话即可：

> 请安装并运行 https://github.com/morton666/market-event-radar 。先阅读 README.md 和 SKILL.md，根据当前环境选择安装方式，检查 Python 与时区依赖，然后完成一次美东今天 ES、NQ、GC 的真实事件查询。使用你可用的联网工具核验官方来源，返回中文摘要、JSON 文件位置和来源覆盖情况。如果来源无法读取，请保留缺失说明。若我要求每天定时运行，请按仓库的 Hermes 接入说明和任务模板配置；发送时刻及渠道未确定时再向我确认。

给执行任务的 AI：

1. 阅读 [SKILL.md](SKILL.md)，确定本次查询范围和过滤条件；默认美东今天、ES/NQ/GC。
2. 按下文安装，检查依赖。已有安装时复用实际目录，保留用户自定义配置。
3. 阅读[官方来源规则](references/sources.md)和[JSON 接口](references/interface.md)，执行 `--prepare`。
4. 联网核验本次来源，填写生成文件中的 `events`、`coverage`，再执行 `--input`。读取运行结果后向用户报告。
5. 定时运行使用[每日任务模板](examples/hermes-daily-prompt.md)和[Hermes 接入说明](references/hermes.md)。先完成一次查询，再验证调度与投递。

无法访问 GitHub 页面正文的工具，可以读取 [README 原文](https://raw.githubusercontent.com/morton666/market-event-radar/main/README.md)和 [SKILL 原文](https://raw.githubusercontent.com/morton666/market-event-radar/main/SKILL.md)。

## 覆盖什么

| 类别 | 默认覆盖 | 关注级别 |
|---|---|---|
| 宏观 | FOMC 决议、记者会，CPI／核心 CPI，PCE／核心 PCE，非农 | ES、NQ、GC 标红 |
| 财报 | NVDA、AAPL、MSFT、AMZN、GOOGL、META、TSLA、AMD、SPCX | ES、NQ 标红；GC 作为背景 |
| 指数到期 | 美股月度与季度到期、ES／NQ 季度期货到期 | 橙色 |
| 黄金合约 | GC 月度期权到期、期货第一通知日、最后交易日 | 期权到期橙色；通知日及最后交易日红色 |

名单、品种关联和颜色均可修改。“红色”表示用户关注级别。当前内容是已知事件日历，不包含实时公布值、预期值或突发新闻流。

## 安装

### Hermes：通过技能安装器

支持 URL 安装的 Hermes 版本可直接执行：

```bash
hermes skills install https://raw.githubusercontent.com/morton666/market-event-radar/main/SKILL.md
```

安装器会处理 SKILL.md 及其引用的支持文件。记录安装器返回的**实际技能目录**，后续命令在该目录执行。参数和安装位置以当前 profile 及 `hermes skills install --help` 为准。[Hermes 官方技能安装说明](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/)

安装后应包含 `scripts/query_events.py`、`scripts/daily_brief.py`、`assets/config.json` 及 SKILL.md 链接的 references、examples 文件。只下载 SKILL.md 无法运行脚本。Hub 安装不一定包含仓库测试；需要测试时使用下方 Git 克隆方式。

若代理网关使 DNS 返回 `198.18.x.x`，URL 安装可能在发出请求前被拦截；当前 Hermes 提供 `security.fake_ip_ranges` 配置。官网返回 403 则需要检查网页提取后端和访问路径。具体步骤见 [Fake-IP 与来源 403 排障](references/hermes.md#fake-ip-网关和来源-403-排障)，与 Git 克隆安装路线可以配合使用。

### 其他 AI 或本地终端：克隆仓库

以下命令适用于 macOS 和 Linux；示例目录可换成自己的工作目录：

```bash
radar_repo="$HOME/.local/share/market-event-radar"
mkdir -p "$(dirname "$radar_repo")"
git clone https://github.com/morton666/market-event-radar.git "$radar_repo"
cd "$radar_repo"
```

目录已存在时先检查并复用，不重复克隆。其他支持 SKILL.md 的 agent 可把完整技能目录放入其技能目录，或读取本仓库的 SKILL.md 后直接执行脚本。

### 检查依赖

运行需要 **Python 3.11+**、系统 IANA 时区数据库，以及 AI 可用的联网读取工具。脚本无需 `pip install`，无需行情账号或券商凭据。Git 仅用于克隆和更新仓库。

```bash
python3 - <<'PY'
import sys
assert sys.version_info >= (3, 11), "需要 Python 3.11+"
from zoneinfo import ZoneInfo
for name in ("America/New_York", "Asia/Shanghai", "America/Chicago"):
    print("时区可用：", ZoneInfo(name))
print("Python：", sys.version.split()[0])
PY
```

Python 版本不足时选择或安装 3.11+ 解释器；Linux 精简镜像若缺时区数据库，安装该系统的 `tzdata` 包。后续两个脚本使用同一解释器。

## 运行一次真实查询

以下命令在技能目录、同一终端会话执行。AI 工具若每次启动新 shell，应保存并传入实际绝对路径，不依赖 shell 变量跨调用保留。

**第一步：生成本次查询骨架。**

```bash
radar_work="$(mktemp -d "${TMPDIR:-/tmp}/market-event-radar.XXXXXX")"
python3 scripts/query_events.py --prepare > "$radar_work/input.json"
```

**第二步：由 AI 联网核验。**

读取 input.json 的 `required_sources`，按[来源规则](references/sources.md)查阅官方正文或日历，并按[接口定义](references/interface.md)填写 `events`、`coverage`。保留本次 `query_started_at`、`range` 和查询条件，记录实际 `checked_at`。无法读取的来源使用 `unavailable` 或 `partial`，财报日期尚未公布使用 `not_announced`。

`--prepare` 只准备输入，两个 Python 脚本都不自动抓取网站；这一步需要 AI 的联网工具。

**第三步：生成报告。**

```bash
python3 scripts/query_events.py \
  --input "$radar_work/input.json" \
  --format json > "$radar_work/report.json"
```

AI 读取 report.json，把 `summary_zh` 返回给用户，并提供 JSON 文件位置。希望直接在终端显示中文摘要和 JSON 时，省略 `--format json` 和输出重定向即可。

普通查询退出码 `0` 表示报告生成成功，来源是否完整仍需看 `run_status`、`source_health` 和 `coverage`；输入错误返回 `2`。

### 常用查询参数

把参数加在 `--prepare` 后；核验完成后，`--input` 会沿用文件中保存的范围和筛选条件。

| 查询 | 参数 |
|---|---|
| 今天，只看宏观 | `--period today --categories macro` |
| 本周重点公司财报 | `--period week --categories earnings --companies NVDA AMD SPCX` |
| 未来 14 天 | `--period next --days 14` |
| 指定日期 | `--start 2026-10-02` |
| 指定日期范围 | `--start 2026-10-02 --end 2026-10-09` |
| 指定 GC 合约未来 30 天节点 | `--period next --days 30 --instruments GC --categories expiry --contracts GCZ26` |

表内日期和合约仅作语法示例，执行时换成用户要查询的日期及实际合约。本周为美东周一至周日；`--period next --days N` 包括今天，共 N 天。每次新的真实查询都重新执行 `--prepare`。

## 每日盘前简报

日报默认查询**美东今天及之后 7 天**，含首尾共 8 个日历日；输出四小时置顶、今天全部事件、未来重点预告和来源状态。完整 JSON 自动留档。

**准备本次日报：**

```bash
radar_state="$HOME/.local/state/market-event-radar"
radar_work="$(mktemp -d "${TMPDIR:-/tmp}/market-event-radar.XXXXXX")"
python3 scripts/daily_brief.py --prepare \
  --state-dir "$radar_state" \
  --instruments ES NQ GC > "$radar_work/manifest.json"
radar_input="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["input_path"])' "$radar_work/manifest.json")"
```

AI 读取 manifest.json 中的采集预算和 `input_path`，在该 input.json 中联网填写 `events`、`coverage`。保持 `request.json` 和请求条件不变。确认读到的是当次官方资料后生成日报：

```bash
python3 scripts/daily_brief.py \
  --input "$radar_input" \
  --state-dir "$radar_state"
```

状态目录必须在技能目录外。每次运行的文件保存在 `状态目录/runs/美东日期/run_id/`：

| 文件 | 用途 |
|---|---|
| `request.json` | 固定本次查询条件和配置校验值 |
| `input.json` | AI 核验后的事件及来源状态 |
| `report.json` | 完整结果、运行状态及中文摘要 |
| `summary.md` | 用于发送的中文日报 |

| 每日入口退出码 | 含义 |
|---|---|
| `0` | 核验正常；可以存在尚未宣布的财报日期 |
| `3` | 非关键来源部分缺失，已保存可用报告 |
| `4` | 关键来源缺失或所有来源不可用，已保存缺失报告 |
| `2` | 输入、配置、存储错误，或请求跨日／过期 |

读取非零退出码时仍保留输出，3／4 的报告可用。跨日或过期输入要重新准备和核验。普通查询与每日入口的退出码规则不同。

### 交给 Hermes 定时运行

使用 **agent 型定时任务**，加载 market-event-radar 技能，工作目录设为实际技能目录，提示词采用[每日任务模板](examples/hermes-daily-prompt.md)。cron 环境需有终端、文件读写和联网工具；网站动态加载时还需浏览器或可读取的官方公告入口。

设置用户指定的每日时刻和投递渠道，并核对 `America/New_York` 时区及下一次触发时间。4 小时窗口是查询时的置顶规则；每天一份日报不会自动变成事件前的额外通知。

详细的失败标记、安装检查和投递验收见 [Hermes 接入说明](references/hermes.md)。由 Hermes 投递最终回复；本项目不另发消息，也不包含独立联网采集器。不要将这套流程配置为只执行 Python 的 no-agent 任务。

## 自定义配置与更新

默认配置在 [assets/config.json](assets/config.json)。可修改公司名单、品种分级、`imminent_hours`、核验时效和 `daily` 策略。建议把自定义文件复制到技能目录外，准备和渲染两个阶段都传 `--config /绝对路径/config.json`；两个阶段之间保持配置一致。

Git 克隆安装可在确认本地改动已妥善保存后更新：

```bash
git -C "$radar_repo" pull --ff-only
```

通过 Hermes 安装器安装的技能使用：

```bash
hermes skills update market-event-radar
```

使用与安装时相同的 Hermes profile；安装器的更新机制见[官方说明](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/#update-lifecycle)。

## 验证与阅读入口

完整克隆的仓库可在根目录运行测试：

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖夏令时、美东日期边界、四小时边界、财报时间精度、来源故障、每日输入时效和支持文件打包。合成测试与历史快照用于验证程序，真实查询应使用当次核验数据，实时调用省略 `--now`。

| 文档 | 何时阅读 |
|---|---|
| [SKILL.md](SKILL.md) | AI 执行技能前 |
| [官方来源规则](references/sources.md) | 联网核验时 |
| [输入输出接口](references/interface.md) | 填写事件 JSON、读取状态时 |
| [Hermes 接入说明](references/hermes.md) | 部署每日任务时 |
| [每日任务提示词](examples/hermes-daily-prompt.md) | 创建 Hermes 任务时 |
| [真实日报验收记录](tests/acceptance/2026-10-02/verification.md) | 查看已验证流程及实际部署边界时 |
