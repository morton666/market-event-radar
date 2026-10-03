# Hermes 每日盘前简报

适用于常驻服务器，每天生成一份中文简报。联网查询由 Hermes agent 执行，脚本完成校验、时间转换和留档。完整 JSON 不直接发送到聊天。

## 安装与运行条件

技能仓库：[morton666/market-event-radar](https://github.com/morton666/market-event-radar)。通过服务器上 Hermes 的技能安装入口安装此仓库；安装参数以当地版本的帮助为准。无需在服务器部署本地 Mac 路径。

[Hermes 技能说明](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/)说明，GitHub／URL 安装会筛选 SKILL.md 引用的支持文件。本包直接链接两个 scripts 文件、assets/config.json、三个 references 文件和一个 examples 模板。安装后核对这些文件齐全，Python 为 3.11+，系统包含 America/New_York 和 Asia/Shanghai 时区。Linux 精简镜像缺时区时，由服务器环境安装系统 tzdata。

自定义配置复制到技能目录外，两个阶段都传同一个 `--config`。旧版根目录 config.json 的自定义值需迁入外部配置；新版默认配置在 assets/config.json。

## Fake-IP 网关和来源 403 排障

模型 API 网关、DNS／透明代理、网页提取服务是不同的访问路径。使用相同模型网关不代表两个 agent 从同一出口读取网页。先记录实际失败工具、错误信息和所选 web_extract 后端，分别判断：

- URL 在请求前被报为 private/internal address，且 DNS 返回 198.18.x.x：检查本地代理 fake-IP 配置与 Hermes 版本。
- 已拿到 HTTP 403／Access Denied 正文：当前访问方式被网站拒绝；单凭 403 不能断定只有出口 IP 被拉黑，浏览器指纹、请求方式及代理也可能影响结果。
- 页面可读但只有导航／动态空壳，或内容标记 TRUNCATED：读取正文、动态组件或工具保存的全文后再判断覆盖状态。

[当前 Hermes 官方文档](https://hermes-agent.nousresearch.com/docs/user-guide/security/#local-proxy-fake-ip-ranges)支持在当前 profile 的 config.yaml 声明本地代理实际使用的 fake-IP 网段。例如网关确实使用 198.18.0.0/15 时，将下面字段合并到已有 security 节点，再重启对应 Hermes 进程：

```yaml
security:
  fake_ip_ranges:
    - 198.18.0.0/15
```

它只豁免已声明的代理网段；其余私网目标仍受保护。先确认服务器版本包含这个配置能力，旧版可以更新，或在网关 DNS 侧让相关公网域名返回真实 IP。不要为此全局打开 allow_private_urls，也不要覆盖现有配置文件。该设置解决本地 URL 检查，网站端的 403 要继续单独核验。

来源读取受阻时，先尝试 Hermes 的云端 web_extract 后端，通过服务提供方抓取官网正文。使用 hermes tools 选择已有可用的提取后端；[当前文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-search/#per-capability-configuration)支持单独指定提取服务，例如：

```yaml
web:
  extract_backend: firecrawl
  cache_enabled: false
```

把这些字段合并到当前 profile，按该版本配置服务所需凭据或可用的 keyless 模式。cloud API 与部署在同一服务器的自托管服务有不同出口，切到同机服务不能证明出口问题已经解决。cache_enabled 控制 Hermes 缓存；还要核对提取服务的抓取时间与缓存说明，避免将旧正文当成本次核验。任何服务都不保证可读取每个站点，失败仍保留缺失报告。

BLS 可按官方导航依次核验发布表、月度日历、全年日历或 ICS，并读取时区说明；月度／年度页面从 [BLS 日历入口](https://www.bls.gov/schedule/)进入，核对查询年度。CME 动态表受阻时，核对查询年度的官方合约日历／PDF和清算公告。备用页面只能证明其实际覆盖的类型和日期，不能把某一份 PDF 视为全部合约已核验。

若云端提取仍无法覆盖关键来源，可把实时采集放到能正常访问官方来源的机器，让 Hermes 接收当次输入及来源证据。收到历史输入时保留旧 checked_at，不能通过改时间戳恢复为 checked。选择付费代理或浏览器服务前，先用实际 BLS、CME、公司 IR 页面验证覆盖效果。

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
