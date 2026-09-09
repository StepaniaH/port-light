<p align="center">
  <img src="docs/icon.png" width="96" height="96" alt="Port-Light">
</p>

# Port-Light

自托管的主机端口占用看板，汇总主机监听、Docker 端口映射和 Compose 声明。

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docker Hub](https://img.shields.io/docker/v/stepaniah/port-light?label=docker%20hub&sort=semver)](https://hub.docker.com/r/stepaniah/port-light)
[![Docker Pulls](https://img.shields.io/docker/pulls/stepaniah/port-light)](https://hub.docker.com/r/stepaniah/port-light)
[![GitHub release](https://img.shields.io/github/v/tag/StepaniaH/port-light?label=version)](https://github.com/StepaniaH/port-light/tags)

[English](README.md) · [简体中文](README.zh-CN.md)

[快速开始](#快速开始) · [Unraid](docs/deployment.md#unraid) · [使用文档](docs/port-management.md)

<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="Port-Light 双主机自适应端口看板">
</p>

## 快速开始

镜像：[`stepaniah/port-light`](https://hub.docker.com/r/stepaniah/port-light)（`linux/amd64`、`linux/arm64`）。打 tag 发布时也会推到 GHCR（`ghcr.io/stepaniah/port-light`）。固定版本可使用版本标签或镜像摘要。

```yaml
services:
  port-light:
    image: stepaniah/port-light:v0.8.2
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

挂载 Docker socket 会授予 Docker API 访问权限，包括修改操作。可通过 [socket proxy](docs/deployment.md#docker-socket-proxy) 限制权限。Unraid、Podman 和反向代理配置见[部署文档](docs/deployment.md)。

默认启用监听、Docker 和 Compose 三种扫描来源。无 Docker 的部署可设置 `PORT_LIGHT_SCANNERS=listen,compose`。扫描失败或数据过期时，页面会显示警告，端口分配暂停；排查方法见[故障排查](docs/troubleshooting.md#occupancy-scan-warning)。

## 功能

- 按端口、服务、项目、进程或绑定地址搜索，支持筛选和排序。
- 按 Compose 项目或服务分组，折叠连续端口范围。
- 查看 Compose 冲突，生成替代端口的映射片段。
- 创建和管理端口预留，按到期状态筛选，复制释放命令。
- 定义命名端口范围，并检查项目声明是否超出范围。
- 汇总最多 32 台其他实例的端口，支持瀑布流和标签页布局。每台机器运行独立实例，管理操作在对应实例中执行。
- 通过 CLI、API 或 MCP 检查和预留端口。
- 支持七种界面语言、主题配色、端口历史、Webhook 和 Doctor 诊断。

分组、管理页面和命名端口规则目前位于 `dev` 分支，尚未包含在 v0.8.2 中。体验方法见[开发指南](CONTRIBUTING.md#development)。

看板默认显示已占用和已配置的端口，搜索端口号时显示空闲建议：

| 状态 | 含义 |
|------|------|
| 占用 | 存在监听进程或运行中的容器映射 |
| 已配置 | 已在 Compose 或手动条目中登记，尚未检测到监听 |
| 空闲 | 当前扫描范围内可用 |

## 访问控制

通过 `AUTH_USER` 和 `AUTH_PASSWORD` 为页面和 API 启用 Basic Auth。公网部署应配合 HTTPS 反向代理。

启用 Basic Auth 或 `HIDDEN_UNLOCK_PASSWORD` 后，隐藏端口需解锁才能通过 API 读取；其他情况下，隐藏操作仅影响显示。详见 [SECURITY.md](SECURITY.md)。

## 配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `PORT_LIGHT_SCANNERS` | `listen,docker,compose` | 启用的扫描来源，逗号分隔，至少选择一项。 |
| `PORT_LIGHT_SCAN_TIMEOUT_S` | `10` | 后台刷新超时秒数（1–60），超时后保留快照并标记过期。只能用环境变量。 |
| `COMPOSE_SCAN_DIR` | `/compose` | 扫描 compose 文件的目录（只能用环境变量） |
| `COMPOSE_SCAN_DEPTH` | `4` | 扫描子目录的最大深度 |
| `COMPOSE_SCAN_EXCLUDE_DIRS` | 未设置 | 自动发现时跳过的目录名，逗号分隔；Compose 中显式 `include` / `extends` 的文件仍会读取。 |
| `COMPOSE_SCAN_MAX_FILES` | `400` | 每次刷新最多解析的 compose 文件数 |
| `PORT_RANGE_START` | `1` | **空闲数量**统计的起始端口 |
| `PORT_RANGE_END` | `9999` | 空闲数量统计的结束端口 |
| `PORT_LIGHT_DATA_DIR` | `/data` | 手动端口、隐藏列表、已保存设置（JSON） |
| `PORT_LIGHT_PORT` | `2100` | 容器内的 HTTP 监听端口 |
| `CUSTOM_PORTS_FILE` | `/data/custom_ports.json` | 额外 / 覆盖的端口名称（只能用环境变量） |
| `THEME_MODE` | `system` | `system` / `dark` / `light` |
| `THEME_PALETTE` | 内置 | 配色：`gruvbox`、`catppuccin`、`solarized`、`nord`、`dracula`、`tokyo-night`、`one-dark`、`everforest`、`rose-pine`、`kanagawa`。留空使用内置颜色。 |
| `LOCALE` | `auto` | `auto` / `en` / `fr` / `de` / `es` / `zh-CN` / `zh-TW` / `ja`。`auto` 跟随浏览器。 |
| `GRID_DENSITY` | `standard` | 卡片密度预设：`loose`(宽松)、`standard`(标准)、`compact`(紧凑)。旧值 `comfortable` 视同 `standard`。 |
| `SHOW_BIND_ADDRESSES` | `false` | 在已占用卡片上显示紧凑的绑定地址摘要。 |
| `SHOW_BIND_IPV4` | `true` | 开启卡片绑定地址摘要时包含 IPv4 地址。 |
| `SHOW_BIND_IPV6` | `true` | 开启卡片绑定地址摘要时包含 IPv6 地址。 |
| `REFRESH_MS` | `5000` | 看板轮询间隔（1,000–300,000 毫秒）。设置页提供 5 秒至 5 分钟选项，并提示建议的其他机器容量；本机后台扫描间隔仍不超过 30 秒。 |
| `PORT_LIGHT_HOST_LAYOUT` | `waterfall` | 默认以响应式瀑布流展示全部机器；`tabs` 为逐台切换。桌面和移动端均遵循此选择。 |
| `URL_HOST` | 空 | 猜测链接里用的主机名 |
| `URL_SCHEME` | `auto` | `auto` / `http` / `https` |
| `AUTH_USER` / `AUTH_PASSWORD` | 未设置 | 可选 HTTP Basic Auth。`/api/health` 保持开放。只能用环境变量。 两项都必须非空；缺一项或空值会返回 503。完全取消两项环境变量才会关闭认证。 |
| `HIDDEN_UNLOCK_PASSWORD` | 未设置 | 设置后（或启用了 Basic Auth），从网格隐藏的端口不会出现在未解锁的 API 里。只能用环境变量。 |
| `PORT_LIGHT_SETTINGS_SOURCE` | `auto` | `auto`：设置页的值覆盖 env 默认值。`env`：只认 Compose，设置页只读。 |
| `PORT_LIGHT_HOST_NAME` | 主机名 | 多机器视图中本机占用图的名称。也可在设置 → 占用图中修改。 |
| `PORT_LIGHT_HOST_DESCRIPTION` | 空 | 多机器视图中本机名称下方的可选纯文本短描述，最多 120 字。 |
| `PORT_LIGHT_PEERS` | 未设置 | 最多 32 个 `{name, url, description?, username?, password?}` 条目的 JSON 数组。短描述为可选纯文本，最多 120 字。数据文件没有 `peers` 键时使用，或 `PORT_LIGHT_SETTINGS_SOURCE=env` 时使用。 |
| `PORT_LIGHT_LOG_LEVEL` | `warning` | 后端日志级别（`debug` / `info` / `warning` / `error`）。扫描器降级（Docker 不可达、Compose 文件解析失败等）会记一条日志，并出现在 `/api/health` 的 `degradations` 里。只能用环境变量。 |
| `WEBHOOK_URL` | 未设置 | 可选 webhook 目标（仅 http/https）。配合 `WEBHOOK_EVENTS=new_listener,conflict`，在端口开始占用或发生冲突时 POST `{event, port}`。 |
| `WEBHOOK_SECRET` | 未设置 | 以 `X-Port-Light-Secret` 头发送。 |
| `WEBHOOK_EVENTS` | 未设置 | 逗号分隔：`new_listener`、`conflict`。 |
| `METRICS_ENABLED` | 未设置 | 设为 `1` 后开放 `GET /api/metrics`（Prometheus 文本格式：占用/已配置/空闲数量、隐藏数、降级数、Compose 文件数）。只输出聚合值，不含端口与名称。只能用环境变量。 |
| `AGENT_TOKEN` | 未设置 | 设置后，端口建议及预留创建、恢复接口需要匹配的 `X-Agent-Token` 头。只能用环境变量。 |

多数选项也可在设置页修改，自动保存到 `/data/port_light.json`。超时、路径和密钥通过环境变量配置；`PORT_LIGHT_SETTINGS_SOURCE=env` 可将设置页设为只读。

自定义端口名称可参考 [custom_ports.example.json](custom_ports.example.json)。挂载前请先创建对应文件。

## 数据与隐私

Port-Light 无遥测。出站 HTTP 请求用于已配置的实例查询和 Webhook；Webhook 发送 `{event, port}`。

扫描结果、机器描述和端口规则可由页面及 API 用户读取，多机汇总实例也会接收这些数据。Compose 的 `.env` 在本地用于变量替换。Doctor 报告提供脱敏后的汇总信息。

设置、标签、历史和 CLI 凭据保存在数据卷中，其中 `port_light.json` 可包含其他实例的访问密码。浏览器创建的预留凭据保存在标签页会话存储中，复制的释放命令含有令牌。请保护数据卷、命令和截图中的敏感信息。

## 文档

- [端口管理](docs/port-management.md)：分组、冲突、预留与范围规则
- [部署](docs/deployment.md)与[故障排查](docs/troubleshooting.md)
- [CLI](docs/cli.md)、[API 与 MCP](docs/integrations.md)；运行实例的 `/docs` 提供 OpenAPI 文档
- [架构](docs/architecture.md)、[路线图](docs/roadmap.md)与[贡献指南](CONTRIBUTING.md)

## 技术栈

- 后端：Python 3.11+（CI 覆盖 3.11–3.13）、FastAPI、Uvicorn
- 前端：静态 HTML/CSS/JS
- 镜像：`python:3.12-slim` + `iproute2`

## 许可证

[MIT](LICENSE) © 2026 StepaniaH

[Changelog](CHANGELOG.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Ko-fi](https://ko-fi.com/stepaniah)
