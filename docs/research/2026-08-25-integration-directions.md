# 集成方向调研：让 Port-Light 在开发工作流中"无感"（ambient）

- 日期：2026-08-25
- 调研问题："用 Web 仪表盘手动查端口太慢了。Port-Light 如何在开发者工作流中变得不可见/无感？"
- 方法：逐条对照一手资料（官方文档、源码仓库、已发布规范），不采信二手博客摘要。
- 结论速览见文末「优先级路线图」与「本周最小可用物」。

---

## 0. 基线：现有 API 面与两条设计约束

以下事实直接读自仓库源码（`backend/main.py`、`mcp/server.py`），是所有后续判断的前提：

| 端点 | 关键参数/行为 | 源码位置 |
|---|---|---|
| `GET /api/ports/suggest` | `count(1–64)/start/end/reserve/label/ttl/scope(self\|all)`；返回 `{ports, reserved, failed, expires_at, scope, range}` | backend/main.py:504 |
| `GET /api/ports/{n}` | 单端口状态 `used/configured/free`，hidden 行被扣留 | backend/main.py:598 |
| `GET /api/ports` | 全量占用图，强 ETag + `If-None-Match` → 304 | backend/main.py:490 |
| `GET /api/free-runs` | 连续空闲段规划（只读，不预约） | backend/main.py:772 |
| `GET /api/events` | SSE `hello`/`refresh` 提示帧 | backend/main.py:758 |
| `GET /api/metrics` | Prometheus 文本格式（需 `METRICS_ENABLED=1`，仅聚合值） | backend/main.py:193 |
| `DELETE /api/manual-ports/{n}` | 释放预约/租约 | backend/main.py:696 |

**约束 A — 写操作只有一处**。`suggest` 是唯一有副作用的端点，且当设置 `AGENT_TOKEN` 时要求 `X-Agent-Token` 头（backend/main.py:521）；其余端点全部只读。因此集成天然分两类：
- **展示型（pull）**：只读轮询，零风险，适合 prompt/status bar/menubar 等高频渲染位；
- **动作型（push）**：调 suggest 预约，一次性调用，适合 "take-before-bind" 场景。

**约束 B — 环境变量约定已存在**。MCP server 已定义 `PORT_LIGHT_URL`（默认 `http://127.0.0.1:2100`）与 `PORT_LIGHT_AUTH="user:password"`（mcp/server.py:34-35）。所有新集成应复用同一对变量名，用户只需配置一次。

另一个常被忽略的事实：**Port-Light 的独特价值不是"找一个本地空闲端口"**——`bind(0)` 或各框架的 auto-port 已能做这件事——而是 **跨主机协调**（`scope=all` 会把 peer 的占用也排除掉并报告 `"scope": "all:<reachable>/<total>"`，backend/main.py:549-565）和 **声明感知**（Compose 里声明了但还没启动的端口也会被避开）。评估每个方向时应问：这个表面能否利用到这两个差异化能力？只是包一层本地空闲检查的方向价值有限。

---

## 1. Shell 集成（zsh hooks + `port-light` 函数）

### (a) 对本应用具体做什么
两个子件，价值不同，应分开评估：

1. **`portlight` 函数族（核心，动作型）**：
   - `portlight take [N] [--label x] [--ttl s] [--scope all]` → 调 `suggest?reserve=true`，把端口号打到 stdout 并复制进剪贴板；供 `$ portlight take && npm run dev -- -p $(...)` 这类用法；
   - `portlight check N` / `portlight release N` / `portlight runs [start end]` → 包装 check/release/free-runs；
   - 全部复用 `PORT_LIGHT_URL`/`PORT_LIGHT_AUTH`，支持 Basic Auth 与可选 `X-Agent-Token`（从 `AGENT_TOKEN_FILE` 或环境读取）。
2. **prompt 展示段（可选，展示型）**：zsh `precmd_functions` 数组里挂一个函数，每 15s（本地文件缓存 TTL）拉一次 `/api/ports` 的 `summary.free`，只在异常时显示（如 `pl:3000⚠`）。zsh 官方手册明确列出 hook 函数 `chpwd`/`precmd`/`preexec`/`periodic` 及同名 `_functions` 数组机制：「if `$chpwd_functions` is an array … the shell attempts to execute the functions in that order」（[zsh 手册 §9.3.1 Hook Functions](https://zsh.sourceforge.io/Doc/Release/Functions.html)）。

### (b) 一手证据
- zsh hook 数组机制：[Functions — ZSH Manual](https://zsh.sourceforge.io/Doc/Release/Functions.html)（当前版本 5.9.2，2026-07 更新）。
- bash 无原生 hook；社区标准做法是 bash-preexec 提供 `preexec`/`precmd` 与 `preexec_functions`/`precmd_functions` 数组（Bash 3.1+），iTerm2/Ghostty/WezTerm/Atuin 生产使用，并提供 `bash_preexec_imported` 变量给库作者探测（[rcaloras/bash-preexec README](https://github.com/rcaloras/bash-preexec)）。注意 starship 官方警告 bash 下不要自行 hook DEBUG trap，应改用 bash-preexec（[starship config](https://starship.rs/config/) 的 cmd_duration 一节）。
- fish 用事件模型：`function x --on-event fish_prompt/fish_preexec/fish_postexec`（[fish 语言文档 #event-handlers](https://fishshell.com/docs/current/language.html#event-handlers)）。
- 分发形态范本：direnv 对每种 shell 给一行接入代码（bash/zsh 用 `eval "$(direnv hook zsh)"`，fish 用 `direnv hook fish | source`），由工具自己生成 shell 特定 hook 代码（[direnv Setup/hook](https://direnv.net/docs/hook.html)）。Port-Light 可采用更简单的变体：发布一个可 source 的脚本文件 + rc 里一行 `source .../portlight.zsh`。
- 若想更进一步做 direnv 式"进入项目目录自动导出 PORT"，direnv 本身就是载体（`.envrc` 里调一次 CLI 并 export），无需自研目录监听。

### (c) 工作量
- `portlight take/check/release/runs`：**S**（纯 curl+jq 即可起步，~80 行；若并入第 4 节的单文件 Python CLI 则几乎零成本）。
- 多 shell 插件化（zsh+bash+fish 三套、缓存、降级处理）：**M**。

### (d) 维护风险
低-中。风险点集中在：bash DEBUG trap 兼容性（交给 bash-preexec 后消失）、fish 版本差异、以及 **preexec 方案本身的伪需求**——无法可靠预测哪条命令会绑端口，且给每条命令加 HTTP 往返延迟不值得。建议明确不做 preexec 自动查询，只做 opt-in `take` 与缓存型 prompt 段。

### (e) 安装摩擦
低。自托管用户本就熟悉 rc 配置；一行 source 即可用。

### (f) 判定
**现在就做**。人类高频面里性价比最高的一项；`take` 是把 Port-Light 从"查一下的工具"变成"工作流默认步骤"的关键动作。

---

## 2. VS Code 扩展

### (a) 对本应用具体做什么
三个能力按优先级：

1. **锚点功能：`${command:...}` 变量实现 Run & Debug 前自动预约端口**。扩展注册命令 `portLight.reserve`，返回字符串端口号；用户的 `launch.json` 写成 `"env": { "PORT": "${command:portLight.reserve}" }`，每次 F5 启动即拿一个真正空闲且已预约的端口。官方变量参考明确：「You can use any VS Code command as a variable with the `${command:commandID}` syntax… Command variables must return a string」，并给出 input variables 的 `command` 类型在插值时执行任意命令（[Variables reference](https://code.visualstudio.com/docs/editor/variables-reference)）。已知限制同页写明：替换为两遍进行，命令变量之间不能互相依赖。
2. **状态栏项**：显示 `summary.free` 或冲突警示（warning/error 底色仅限特殊情况）。状态栏是稳定的公开扩展面（[UX Guidelines — Status Bar](https://code.visualstudio.com/api/ux-guidelines/status-bar)，API 参考 `vscode-api#StatusBarItem`）。
3. **终端 Shell Integration API**（1.93 起稳定）：可监听集成终端里的命令执行、退出码与命令行（`window.onDidStartTerminalShellExecution` 等）（[v1.93 Release Notes — Terminal shell integration API](https://code.visualstudio.com/updates/v1_93)）。潜在用途：检测到用户跑 `*-dev-server` 类命令时提示冲突。属锦上添花，非首期。

### (b) 一手证据（含否定性证据）
- **Ports 视图 / AttributesProvider 不可用于第三方市场扩展**。`registerPortAttributesProvider` 只存在于 proposed API：[vscode.proposed.portsAttributes.d.ts](https://github.com/microsoft/vscode/blob/main/src/vscode-dts/vscode.proposed.portsAttributes.d.ts)（issue [#115616](https://github.com/microsoft/vscode/issues/115616)）。而官方政策明确：proposed API「only available in Insiders distribution and should not be used in published extensions」，共享只能走 VSIX + `--enable-proposed-api` 启动参数或 argv.json 白名单（[Using Proposed API](https://code.visualstudio.com/api/advanced-topics/using-proposed-api)）。→ **放弃"接管 Ports 视图/转发行为"的路线**，用状态栏 + 命令变量替代。
- Task 侧：自定义 task type 有稳定 API（Task Provider 指南），但本应用场景用 `${input:{type:"command"}}` 注入 tasks.json 即可，无需自定义 task provider。

### (c) 工作量
**M**：脚手架 + 设置（`portLight.url/auth/token`）+ 一个命令 + 状态栏项 + 打包。无原生代码，但 VSIX 发布、engine 版本跟进、marketplace 审核流程都是持续成本。

### (d) 维护风险
中低。只用稳定面则破坏概率小；主要成本是 marketplace 账号与版本节奏。VSIX 手动分发可作为过渡。

### (e) 安装摩擦
上架后一键安装（低）；上架前 VSIX side-load（中）。

### (f) 判定
**稍后（later），但排期要早于直觉**——前提先验证第 1 节的 shell 流程是否已被日常使用。如果 owner 本人重度使用 VS Code 调试，`${command:portLight.reserve}` 这一个功能就值得提前到第二梯队。

---

## 3. JetBrains IDE 插件（简评）

平台支持状态栏小组件：`StatusBarWidgetFactory` 注册到 `com.intellij.statusBarWidgetFactory` 扩展点（[Status Bar Widgets — IntelliJ Platform Plugin SDK](https://plugins.jetbrains.com/docs/intellij/status-bar-widgets.html)；接口源码见 [intellij-community](https://github.com/JetBrains/intellij-community/blob/idea/253.30387.20/platform/platform-api/src/com/intellij/openapi/wm/StatusBarWidgetFactory.java)）。Run Configuration 层面也有扩展点可注入 env。

(a) 能做的等价于 VS Code 的 1+2；(b) 平台能力确认存在但 API 面大、Kotlin/Java 工具链重、插件市场需要签名与兼容范围维护；(c) **L**；(d) 中（IDE 大版本兼容矩阵）；(e) 市场 one-click 但受众窄；(f) **跳过（skip）**——除非出现明确的 JetBrains 用户需求信号，投入产出远低于 shell/CLI 路线。

---

## 4. 独立 CLI（单文件 Python，pipx/brew 分发）

### (a) 对本应用具体做什么
`cli/portlight.py`：**纯 stdlib 单文件**（照搬 mcp/server.py 的 `urllib` 客户端模式，零依赖），子命令 `take/check/release/runs/map/status`，输出人读格式 + `--json`。它是第 1、7 节所有集成的公共引擎：shell 函数薄包装它，CI step 直接 curl 或用它，direnv `.envrc` 调用它。与裸 curl alias 相比的增量：token/Basic Auth 统一处理、错误信息可读、`--scope all` 默认提示、shell completion、以及一个可以被 brew/pipx 安装的稳定入口名。

### (b) 一手证据
- pipx 定位：「installs and runs end-user Python applications in isolated environments」，每个 app 独立 venv、入口上 PATH、无需 sudo，另有 `pipx run` 免安装临时运行（[pipx.pypa.io](https://pipx.pypa.io/)）。零依赖的 console script 完美适配。
- Homebrew 用户路径：`brew install swiftbar` 同款 tap/formula 模式已是 macOS 自托管生态惯例（参考 SwiftBar 的分发方式，[SwiftBar README](https://github.com/swiftbar/SwiftBar#how-to-get-swiftbar)）。
- 反面诚实评估：`docs/integrations.md` 已给出全部 curl 配方，jq 就位的用户增量很小。CLI 的真正价值在于**成为其他表面的构建块**，而非替代 curl。

### (c) 工作量
**S-M**（argparse ~150 行；brew formula/tap 另加 S）。

### (d) 维护风险
低。API 变化时只有这一个客户端层需要同步（MCP server 同理，可共享同一份 client 逻辑）。

### (e) 安装摩擦
Python 用户：`pipx install port-light`（低）；非 Python 用户：curl 下载单文件即可运行（最低）。

### (f) 判定
**现在就做**，并与第 1 节合并交付（同一份代码，两种入口）。

---

## 5. 启动器集成（Raycast / Alfred / uLauncher）

### Raycast
(a) 一个 `mode:"menu-bar"` 命令：菜单栏常驻图标，下拉显示 free 计数、active leases（`/api/meta` 的 `agent_events.active_leases`）、点击 item 触发 reserve 并 toast 端口号；manifest 支持 background refresh `interval`（如 `"5m"`）与 Preferences 配 `PORT_LIGHT_URL`。
(b) 一手证据充分：MenuBarExtra 组件、menu-bar 命令模式、interval 后台刷新、item onAction 全部官方文档化（[Menu Bar Commands](https://developers.raycast.com/api-reference/menu-bar-commands.md)、[Manifest](https://developers.raycast.com/information/manifest.md)）；扩展为 TypeScript/React，经 Store 发布（[Publish an Extension](https://developers.raycast.com/basics/publish-an-extension.md)），也可 dev-mode 导入不分发。
(c) **M**。(d) 低-中（Raycast API 迭代较快，但 MenuBarExtra 属核心稳定面）。(e) 需要 Raycast 账号 + npm 工具链；Store 上架后低。
(f) **稍后**。诚实的替代方案：Raycast Quicklink 直接打开 `$PORT_LIGHT_URL/api/ports/suggest?count=1&reserve=true&label=quick` 就能覆盖 80% 场景（浏览器 JSON 响应），先把这条配方写进 docs 即可。

### Alfred
(b) Script Filter JSON 格式完整文档化（items/title/subtitle/arg/rerun/cache TTL 5–86400s）（[Script Filter JSON Format](https://www.alfredapp.com/help/workflows/inputs/script-filter/json/)）；但 workflows 功能属于付费 Powerpack（Alfred 官网将 Powerpack 作为独立售卖项，[Powerpack](https://www.alfredapp.com/powerpack/)）。
(f) **跳过（skip）**——付费墙显著缩小受众，且 SwiftBar（免费开源）在同一平台提供更好的常驻展示面（见第 9 节）。

### uLauncher
(b) v6 扩展 API 已升到 api_version 3，但官方承认迁移指南与扩展站仍在完善中：「We are still working on the extension API, including the documentation and the extension site」（[Ulauncher discussion #1468](https://github.com/Ulauncher/Ulauncher/discussions/1468)、迁移指南 issue [#1062](https://github.com/Ulauncher/Ulauncher/issues/1062)）。
(f) **跳过（skip）**——平台本身文档未定稿，追平成本的回报不确定。

---

## 6. 终端复用器状态栏（tmux / zellij）

### tmux
(a) `status-right` 加一段 `#{}` 格式串内嵌 `#(command)`：tmux FORMATS 明确支持插入 shell 命令输出，「'#(uptime)' will insert the output of uptime」，且 tmux 不等待命令完成而是沿用上次结果（[tmux(1) FORMATS/OPTIONS](https://man7.org/linux/man-pages/man1/tmux.1.html)）；刷新频率由 `status-interval` 控制，默认 15 秒（同页 OPTIONS 节）。
(b) 证据为 man page 一手内容，稳定十年以上。
(c) **S**：一个 ~30 行脚本（读 `/api/ports` summary，**必须带本地文件 TTL 缓存**——多 pane/session 会各自触发 `#()`，无缓存会打爆实例）+ `.tmux.conf` 两行配置，作为 `extras/tmux-status.sh` 附带。
(d) 低。唯一坑是缓存纪律，文档写清即可。
(e) 极低（tmux 自托管用户标配）。
(f) **现在就做**（随第 1 节一起打包）。

### zellij
(b) 插件系统是 WebAssembly/WASI，「Currently, Rust is the only language officially supported for plugins」（[Zellij Plugins 文档](https://zellij.dev/documentation/plugins.html)）。
(c) **L**（Rust 工具链 + WASM 构建）。(f) **跳过（skip）**；Linux tmux 用户可直接用上面的 tmux 配方，或等待社区插件生态出现同类需求。

---

## 7. Git hooks / Dev Container / GitHub Actions

### Git hooks —— 弱想法，明确剪掉
(b) post-checkout 等 hook 由 git 官方定义（[githooks](https://git-scm.com/docs/githooks)），技术上可行；(a) 但语义错位：端口占用不是 VCS 关注点，"切分支时自动释放 lease"极易误删同事或 CI 的有效 reservation（lease 的意义就是防止别人占用）。(f) **跳过（skip）**。剪枝本身就是有价值的结论。

### GitHub Actions —— 文档 + composite action，成本低
(a) 自托管 runner 与 Port-Light 同处一个 LAN 时：测试 job 第一步向 suggest 要端口（`label=$GITHUB_RUN_ID&ttl=3600` 自动过期兜底），写入 `GITHUB_ENV` 供后续 step 使用；job 结束 DELETE 释放。
(b) 证据：runner 通过 environment files 向后续 step 传递环境变量——「echo "{environment_variable_name}={value}" >> "$GITHUB_ENV"」（[Workflow commands for GitHub Actions](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/workflow-commands-for-github-actions)）；composite action 把这套逻辑封装成 `uses:` 一步（action.yml `runs.using: "composite"`，[Metadata syntax reference](https://docs.github.com/en/actions/reference/workflows-and-actions/metadata-syntax)、教程 [Creating a composite action](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/create-a-composite-action)）。
(c) 文档配方 **S**；composite action 仓库 **S**。
(d) 低。
(e) 低（YAML only）。
重要边界：GitHub-hosted runner 是隔离 VM，够不到你的 LAN 实例——此方向**只对自托管 runner 成立**，文档必须写明。
(f) **现在做文档配方；composite action 放第二梯队**（等有人真的在 actuated/self-hosted 上要用再抽仓库）。

### Dev Container feature —— 稍后
(b) `initializeCommand` 在 **宿主机** 上执行（容器创建及后续 start 时），正好可以调宿主侧 Port-Light 预约并把结果写进 `remoteEnv`；`forwardPorts`/`portsAttributes` 支持 label 与 `requireLocalPort`（[Dev Container metadata reference](https://containers.dev/implementors/json_reference/)）。
(a) 一个 feature 装 CLI 进容器 + postStart 预约 PORT 并注入 remoteEnv。
(c) **M**。(d) 中（spec 演进、feature 分布机制）。(e) 中（用户需理解 feature 引用语法）。
(f) **稍后**——依赖第 4 节 CLI 先存在。

---

## 8. Home Assistant / 可观测推送

### Home Assistant REST sensor
(a) 轮询 `/api/meta`（版本/auth/automation 概览）或 `/api/health`（scanners + degradations）生成实体；配合 `json_attributes` 暴露 `degradations` 数组，自动化可在 Docker scanner 降级时通知。
(b) RESTful integration 官方支持：`resource`、`authentication: basic`、`scan_interval`（默认 30s）、sensor 子项的 `value_template`/`json_attributes_path`/`json_attributes`（[RESTful integration](https://www.home-assistant.io/integrations/rest/)）。Basic Auth 参数与本应用完全对得上。
(c) **S（纯配置，零代码）**。(d) 极低。(e) HA 用户粘贴 YAML 即可。
(f) **以文档配方形式现在收录**（放 `docs/integrations.md`），不写任何代码。另外 push 型告警其实已有更好的通道：后端自带 `WEBHOOK_URL` 事件推送（integrations.md），HA 侧收 webhook 比 30s 轮询更及时。

### Grafana/Prometheus —— 无事可做
Prometheus 文本暴露已实现且仅聚合指标（backend/main.py:193-238），Grafana 侧只是加 scrape job（官方 exposition format 见 [prometheus.io](https://prometheus.io/docs/instrumenting/exposition_formats/)）。(f) **已完成，无需开发**；至多附一段示例 dashboard JSON。

---

## 9. 其他有证据支撑的 ambient 表面

### SwiftBar / xbar（macOS 菜单栏）★ 推荐关注
(a) 菜单栏标题显示 free 计数或 `N⚠` 冲突数；下拉列出 active leases（端口+剩余时间）、`href` 直达仪表盘深链（`$URL/#/port/8080`）、`bash`+`refresh` 参数实现点击重新预约。
(b) 插件协议极简且完整文档化：插件 = 可执行脚本，文件名 `{name}.{interval}.{ext}` 控制刷新（如 `portlight.30s.sh`）；STDOUT 中第一个 `---` 之前是菜单栏 Header、之后是下拉 Body；每行支持 `href=/bash=/refresh=/color=` 等参数；`brew install swiftbar`；还有 `swiftbar://refreshplugin?name=...` URL scheme（[SwiftBar README — Plugin API](https://github.com/swiftbar/SwiftBar)）。
(c) **S-M**（脚本本体 S；打磨 alternate/color/深链接 M）。
(d) 低（协议多年稳定，向下兼容 BitBar/xbar）。
(e) mac 用户 `brew install swiftbar` + 把脚本放进插件目录，两步。
(f) **第二梯队推荐**——这是 macOS 上最接近"环境光"的表面；先出脚本示例放 extras/，不阻塞主线。

### Starship prompt custom module（跨 shell prompt）
(b) `[custom.port-light]` 表格：`command` 输出即模块内容，`when` 控制显隐，`format` 控制样式，甚至可换 shell 执行（[Starship Config — Custom commands](https://starship.rs/config/#custom-commands)）。等于免写 shell hook 的 prompt 段方案，天然覆盖 zsh/bash/fish/nushell 等所有 starship 支持的 shell。
(c) **S（纯配置配方）**。同样必须本地 TTL 缓存（prompt 渲染频率高）。
(f) **现在收录为文档配方**（10 行 TOML + 缓存脚本引用）。

### espanso（文本展开）
(b) shell 扩展可在输入 `:plport` 时执行命令并展开结果（espanso 官方 executions 文档，[espanso.org/docs/extensions/executions](https://espanso.org/docs/extensions/executions/)）。场景是在任意输入框/issue 里粘一个新分配的端口。
(f) **仅提及配方，不开发**——受众极窄。

### wud 式对比说明
wud（What's up Docker）类工具的价值在"发现变化并推送"；Port-Light 的 webhooks（`new_listener/conflict`）已经承担该角色，不需要再建独立守护进程。任何"后台常驻监听 SSE 再转桌面通知"的新组件都属于重复建设（webhook → HA/shoutrrr 即可），不建议。

---

## 10. 优先级路线图

原则：MCP + skill 已覆盖 AI-agent 面，本轮全部投向 **人类高频表面**；优先"零后端改动、复用既有 API、共享一份客户端代码"的项；展示型表面必须带本地缓存纪律。

**第一梯队（本周内）**
1. `cli/portlight`（stdlib-only 单文件 Python，`take/check/release/runs/map/status`，`--json`，复用 `PORT_LIGHT_URL`/`PORT_LIGHT_AUTH`）——第 1、4 节合并交付，是其余一切的地基。
2. shell 接入：`portlight take` 主打 + 可选缓存型 prompt 段（zsh `precmd_functions` / bash-preexec / fish `--on-event fish_prompt` 各一小段）。
3. 文档配方批量收录 `docs/integrations.md`：tmux `status-right` 片段、starship `[custom.port-light]`、SwiftBar 脚本示例、HA rest sensor YAML、GitHub Actions GITHUB_ENV 步骤、Raycast Quicklink。全是配置，零代码。

理由：三项合计 <400 行代码 + 一篇文档，立刻让"打开网页查端口"这个动作在大多数时刻消失。

**第二梯队**
4. `extras/` 正式化：tmux/SwiftBar 脚本进 repo 并测试；Homebrew tap。
5. GitHub Actions composite action 独立仓库（等自托管 runner 需求落地）。
6. VS Code 扩展 MVP：`${command:portLight.reserve}` 锚点 + 状态栏项（明确不用 proposed portsAttributes）。
7. Dev Container feature（依赖 1 的 CLI）。

**第三梯队 / 观察名单**
8. Raycast 完整扩展（Quicklink 配方先垫着）。

**明确不做（skip 清单及一句话理由）**
- JetBrains 插件：工具链与兼容矩阵成本 L，受众未验证。
- zellij WASM 插件：仅官方支持 Rust，成本 L，tmux 配方已覆盖 Linux 终端用户。
- Alfred workflow：Powerpack 付费墙缩小受众；macOS 上 SwiftBar 更优。
- uLauncher 扩展：API v3 文档未定稿，平台自身在迁移中。
- Git hooks 自动预约/释放：语义错位，易误删他人 reservation。
- preexec 每命令查询：无法预测绑端口行为且徒增延迟；opt-in `take` 才是正确形状。
- 独立 SSE→桌面通知守护进程：webhooks 已覆盖 push 场景，重复建设。

---

## 11. 本周最小可用物（smallest useful thing）

一个文件 + 一篇文档：

```bash
# extras/portlight.sh —— 或 cli/portlight.py，二选一起步
export PORT_LIGHT_URL="${PORT_LIGHT_URL:-http://127.0.0.1:2100}"

portlight() {
  case "$1" in
    take)    shift; curl -su "$PORT_LIGHT_AUTH" \
               -H "X-Agent-Token: $AGENT_TOKEN" \
               "$PORT_LIGHT_URL/api/ports/suggest?count=${2:-1}&reserve=true&label=${LABEL:-cli}" \
             | jq -r '.ports[]' ;;
    check)   curl -su "$PORT_LIGHT_AUTH" "$PORT_LIGHT_URL/api/ports/$2" | jq -r '.status' ;;
    release) curl -su "$PORT_LIGHT_AUTH" -X DELETE \
               "$PORT_LIGHT_URL/api/manual-ports/$2" ;;
  esac
}
```

配上 `.tmux.conf`: `set -g status-right '#(extras/portlight-status.sh)'` 与一篇 recipes 文档，本周即可交付"端口意识常驻终端、不再开网页"的最小闭环。

---

*调研中引用的所有外部链接均于 2026-08-25 验证可访问；仓库内部断言标注了源码文件与函数位置。*
