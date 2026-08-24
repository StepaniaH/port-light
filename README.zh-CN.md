<p align="center">
  <img src="docs/icon.png" width="96" height="96" alt="Port-Light">
</p>

# Port-Light

本机端口占用的红绿灯看板。给跑很多 Compose 栈、却总记不清端口的 homelab 用户。

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docker Hub](https://img.shields.io/docker/v/stepaniah/port-light?label=docker%20hub&sort=semver)](https://hub.docker.com/r/stepaniah/port-light)
[![Docker Pulls](https://img.shields.io/docker/pulls/stepaniah/port-light)](https://hub.docker.com/r/stepaniah/port-light)
[![GitHub release](https://img.shields.io/github/v/tag/StepaniaH/port-light?label=version)](https://github.com/StepaniaH/port-light/tags)

[English](README.md) · [简体中文](README.zh-CN.md)

<a href="https://www.producthunt.com/products/port-light?embed=true&utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-port-light" target="_blank" rel="noopener noreferrer"><img alt="Port Light - A web dashboard shows your port usage as a traffic-light. | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1203037&theme=light&t=1784992647570"></a>

![Port-Light 截图](https://raw.githubusercontent.com/StepaniaH/port-light/main/docs/screenshot.png)

局域网或 Tailscale 上的其他 Port-Light 实例：

![两台占用图并排](https://raw.githubusercontent.com/StepaniaH/port-light/main/docs/screenshot-multi-host.png)

## 它做什么

把三条**本机**数据合成一张网格：

| 来源 | 你能看到什么 |
|------|----------------|
| 主机监听表（`/proc` 或 `ss`） | 实际绑定的 TCP/UDP 端口 |
| Docker API | 容器名、状态、镜像、发布的端口映射 |
| Compose 文件 | **声明了**但栈可能没在跑的端口 |

| 颜色 | 状态 | 含义 |
|------|------|------|
| 蓝 | 占用 | 有进程在听，或有容器带着这个映射在跑 |
| 黄 | 已配置 | Compose 里声明了（或手动添加了），但当前没人听 |
| 绿 | 空闲 | **搜索某个端口号时**才会出现，并带上附近可用端口作备选 |

默认网格只画**已被占用或已声明**的端口，不会把 1–9999 全涂成绿色。

这是**端口占用图**，不是容器管理器。它不会启停容器、看日志，也不替代 Portainer。

## 功能

- 卡片上直接显示容器 / 服务名
- 按端口号搜索；已被占用时给出附近空闲端口
- 占用数字可筛选占用 / 已配置；类型芯片：运行中、系统、Docker、网页、UDP、localhost、公网、隐藏
- 按端口、名称、状态排序；可限制显示区间
- 扫描不到的端口可以手动登记
- 两个 Compose 项目在重叠的绑定地址上声明同一主机端口时标冲突
- 常见 homelab 端口内置名称（SSH、Jellyfin、Postgres 等），可用本地文件覆盖
- 5 秒自动刷新（设置里可关）
- 点击复制端口号
- 一个界面可以拉取其他 Port-Light 实例的占用图（局域网 / Tailscale）。每台机器仍自己扫描。
- 深色 / 浅色 / 跟随系统，以及 Gruvbox、Catppuccin、Nord 等配色预设；紧凑网格、界面语言（English / 简体中文 / 繁體中文 / 日本語）：设置页可改，也可以写在 Compose 环境变量里
- 可选 HTTP Basic Auth（`AUTH_USER` / `AUTH_PASSWORD`）
- 支持用标签给端口命名：`port-light.port.<端口>.name` / `.category`
- 查找空闲端口：工具栏按钮（或 `GET /api/free-runs?count=N`）返回范围内最大的连续空闲段，可一键预留
- 面向编码智能体：`GET /api/ports/suggest` 直接给出真实空闲端口（支持预留或到期自动释放的租约），并提供 MCP stdio 服务器与智能体 Skill
- 本地历史：端口状态变化写入数据卷内的 `history.db`（默认保留 7 天，`HISTORY_RETENTION_DAYS=0` 关闭）；详情抽屉显示最近变动，也可用 `GET /api/ports/{n}/history`
- 可选 Webhook：设置 `WEBHOOK_URL` 与 `WEBHOOK_EVENTS=new_listener,conflict` 后，端口开始被占用或两个栈冲突时 POST JSON 通知
- 即时刷新：打开的界面通过 `GET /api/events`（SSE）订阅变更，占用一变就重新拉取，不必等下一个 5 秒轮询
- TCP 和 UDP；绑定范围（`0.0.0.0` / localhost / 局域网）
- 前端是原生 HTML/CSS/JS（native ES modules），没有 npm，没有构建步骤

### 已知限制（部署前请读）

- **局域网工具。** 未设置 `AUTH_USER` / `AUTH_PASSWORD` 时没有登录。请放在反向代理后面，或不要暴露到公网。见 [SECURITY.md](SECURITY.md)。
- **从网格隐藏**只是显示过滤。只有配置了 `AUTH_*` 或 `HIDDEN_UNLOCK_PASSWORD` 时，API 才会真正不返回这些端口。
- **多机是只读汇总。** 每台机器仍各自跑 Port-Light。一个界面可以通过局域网或 Tailscale 拉取其他机器的占用图（设置 → 占用图）。不要把 2100 端口暴露到公网。由 Hub 自己去拉这些地址；Docker 桥接容器常常连不上 Tailscale 的 `100.x` — 改填局域网 IP，或让 Hub 使用 `network_mode: host`。
- 挂了 `/host/proc` 时（镜像默认如此），监听端口可以从 inode 对上进程名。没挂则只能看到 Docker 的容器名。`ss -tlnp` 的进程名仍需要 host network 或裸机。
- `network_mode: host` 的容器在挂了 `/host/proc` 时通过 socket inode 关联；否则回退到 `ExposedPorts`。

后续计划与设计：[docs/roadmap.md](docs/roadmap.md)、[docs/architecture.md](docs/architecture.md)。自动化与编码智能体集成：[docs/integrations.md](docs/integrations.md)。

## 快速开始

镜像：[`stepaniah/port-light`](https://hub.docker.com/r/stepaniah/port-light)（`linux/amd64`、`linux/arm64`）。打 tag 发布时也会推到 GHCR（`ghcr.io/stepaniah/port-light`）。重要机器请钉版本标签，不要长期用 `latest`。

```yaml
services:
  port-light:
    image: stepaniah/port-light:v0.7.0
    container_name: port-light
    restart: unless-stopped
    ports:
      - "2100:2100"
    volumes:
      - /path/to/your/compose-stacks:/compose:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /proc:/host/proc:ro
      - ./data:/data
    environment:
      COMPOSE_SCAN_DIR: /compose
```

```bash
mkdir -p data
docker compose up -d
```

打开 `http://localhost:2100`。

`/var/run/docker.sock` 即使只读也权限很高。如果 UI 可能被不完全信任的人访问，请用 [socket proxy](docs/deployment.md#docker-socket-proxy)。更多安装方式（Unraid 模板、Podman、反向代理、从源码构建）见 [docs/deployment.md](docs/deployment.md)。

常规桥接网络**不需要** `NET_ADMIN`。它只对裸机上的 `ss` 回退路径有帮助。

## 配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `COMPOSE_SCAN_DIR` | `/compose` | 扫描 compose 文件的目录（只能用环境变量） |
| `COMPOSE_SCAN_DEPTH` | `4` | 扫描子目录的最大深度 |
| `COMPOSE_SCAN_MAX_FILES` | `400` | 每次刷新最多解析的 compose 文件数 |
| `PORT_RANGE_START` | `1` | **空闲数量**统计的起始端口 |
| `PORT_RANGE_END` | `9999` | 上述区间的结束（不会把网格填满绿格） |
| `PORT_LIGHT_DATA_DIR` | `/data` | 手动端口、隐藏列表、已保存设置（JSON） |
| `CUSTOM_PORTS_FILE` | `/data/custom_ports.json` | 额外 / 覆盖的端口名称（只能用环境变量） |
| `THEME` | `system` | `system` / `dark` / `light`，或命名配色（`gruvbox`、`catppuccin`、`nord`、`dracula`、`tokyo-night`、`one-dark`、`solarized`、`everforest`、`rose-pine`、`kanagawa`，以及 `*-light` / `catppuccin-latte`） |
| `LOCALE` | `auto` | `auto` / `en` / `zh-CN` / `zh-TW` / `ja`。`auto` 跟随浏览器。 |
| `GRID_DENSITY` | `comfortable` | `comfortable` 或 `compact` |
| `REFRESH_MS` | `5000` | 自动刷新间隔 |
| `URL_HOST` | 空 | 猜测链接里用的主机名 |
| `URL_SCHEME` | `auto` | `auto` / `http` / `https` |
| `AUTH_USER` / `AUTH_PASSWORD` | 未设置 | 可选 HTTP Basic Auth。`/api/health` 保持开放。只能用环境变量。 |
| `HIDDEN_UNLOCK_PASSWORD` | 未设置 | 设置后（或启用了 Basic Auth），从网格隐藏的端口不会出现在未解锁的 API 里。只能用环境变量。 |
| `PORT_LIGHT_SETTINGS_SOURCE` | `auto` | `auto`：设置页保存的值覆盖 env。`env`：只认 Compose，设置页只读。 |
| `PORT_LIGHT_HOST_NAME` | 主机名 | 并排显示其他占用图时，本机这一列的名称。 |
| `PORT_LIGHT_PEERS` | 未设置 | `{name, url, username?, password?}` 的 JSON 数组。数据文件没有 `peers` 键时使用，或 `PORT_LIGHT_SETTINGS_SOURCE=env` 时使用。与设置页同一把锁。 |
| `PORT_LIGHT_LOG_LEVEL` | `warning` | 后端日志级别（`debug` / `info` / `warning` / `error`）。扫描器降级（Docker 不可达、Compose 文件解析失败等）会记一条日志，并出现在 `/api/health` 的 `degradations` 里。只能用环境变量。 |
| `WEBHOOK_URL` | 未设置 | 可选 webhook 目标（仅 http/https）。配合 `WEBHOOK_EVENTS=new_listener,conflict`，在端口开始占用或发生冲突时以 fire-and-forget 方式 POST `{event, port}`。 |
| `WEBHOOK_SECRET` | 未设置 | 以 `X-Port-Light-Secret` 头发送。 |
| `WEBHOOK_EVENTS` | 未设置 | 逗号分隔：`new_listener`、`conflict`。 |
| `METRICS_ENABLED` | 未设置 | 设为 `1` 后开放 `GET /api/metrics`（Prometheus 文本格式：占用/已配置/空闲数量、隐藏数、降级数、Compose 文件数）。只输出聚合值，不含端口与名称。只能用环境变量。 |

上表里除路径和密钥外，也可以在 Web UI 的 **Settings** 里改，写入 `/data/port_light.json`。OpenAPI 在 `/docs`。

把 [custom_ports.example.json](custom_ports.example.json) 复制为 `custom_ports.json`（已 gitignore）。分类：`system`、`web`、`database`、`message`、`proxy`、`vpn`、`selfhosted`、`dev`、`infra`、`gaming`。

如果在 Compose 里 bind-mount `custom_ports.json`，请先在宿主机上建好**文件**。路径不存在时 Docker 会建成目录，应用就读不了。

## 隐私

- 无遥测、无统计。
- 应用不会主动访问外网。唯一可选的出站 HTTP 是你在设置里添加的其他 Port-Light 地址（局域网 / Tailscale）上的占用图。
- 扫描数据留在跑那份实例的机器上（监听表、Docker API、Compose 文件、`/data` 里的 JSON）。
- Compose 旁边的 `.env` 只用于本地 `${VAR}` 替换，不会上传。

## 技术栈

- 后端：Python 3.12、FastAPI、Uvicorn
- 前端：静态 HTML/CSS/JS
- 镜像：`python:3.12-slim` + `iproute2`

## 许可证

[MIT](LICENSE) © 2026 StepaniaH

[Changelog](CHANGELOG.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Ko-fi](https://ko-fi.com/stepaniah)
