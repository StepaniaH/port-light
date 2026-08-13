# Port-Light

本机端口占用的红绿灯看板。给跑很多 Compose 栈、却总记不清端口的 homelab 用户。

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docker Hub](https://img.shields.io/docker/v/stepaniah/port-light?label=docker%20hub&sort=semver)](https://hub.docker.com/r/stepaniah/port-light)
[![Docker Pulls](https://img.shields.io/docker/pulls/stepaniah/port-light)](https://hub.docker.com/r/stepaniah/port-light)
[![GitHub release](https://img.shields.io/github/v/tag/StepaniaH/port-light?label=version)](https://github.com/StepaniaH/port-light/tags)

[English](README.md) · [简体中文](README.zh-CN.md)

<a href="https://www.producthunt.com/products/port-light?embed=true&utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-port-light" target="_blank" rel="noopener noreferrer"><img alt="Port Light - A web dashboard shows your port usage as a traffic-light. | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1203037&theme=light&t=1784992647570"></a>

![Port-Light 截图](https://raw.githubusercontent.com/StepaniaH/port-light/main/docs/screenshot.png)

## 它做什么

把三条**本机**数据合成一张网格：

| 来源 | 你能看到什么 |
|------|----------------|
| 主机 TCP 表（`/proc` 或 `ss`） | 哪些端口真正在监听 |
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
- 筛选：运行中、占用、已配置、系统、Docker、访问端口
- 按端口、名称、状态排序；可限制显示区间
- 扫描不到的端口可以手动登记
- 同一主机端口出现在多个 Compose 文件时标冲突
- 常见 homelab 端口内置名称（SSH、Jellyfin、Postgres 等），可用本地文件覆盖
- 5 秒自动刷新（设置里可关）
- 点击复制端口号
- 可选 HTTP Basic Auth（`AUTH_USER` / `AUTH_PASSWORD`）
- 前端是原生 HTML/CSS/JS，没有 npm，没有构建步骤

### 已知限制（部署前请读）

- **局域网工具。** 未设置 `AUTH_USER` / `AUTH_PASSWORD` 时没有登录。请放在反向代理后面，或不要暴露到公网。见 [SECURITY.md](SECURITY.md)。
- **从网格隐藏**只是显示过滤。只有配置了 `AUTH_*` 或 `HIDDEN_UNLOCK_PASSWORD` 时，API 才会真正不返回这些端口。
- **多机尚未实现。** 每台机器各自跑一份 Port-Light。
- 常见 Docker 部署**看不到进程名**（只能看到 Docker API 给的容器名）。`ss -tlnp` 的进程名需要 host network 或裸机。
- `network_mode: host` 的容器在挂了 `/host/proc` 时通过 socket inode 关联；否则回退到 `ExposedPorts`。

后续计划与设计：[docs/roadmap.md](docs/roadmap.md)、[docs/architecture.md](docs/architecture.md)。

## 快速开始

镜像：[`stepaniah/port-light`](https://hub.docker.com/r/stepaniah/port-light)（`linux/amd64`、`linux/arm64`）。打 tag 发布时也会推到 GHCR（`ghcr.io/stepaniah/port-light`）。重要机器请钉版本标签，不要长期用 `latest`。

```yaml
services:
  port-light:
    image: stepaniah/port-light:v0.4.0
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

`/var/run/docker.sock` 即使只读也权限很高。如果 UI 可能被不完全信任的人访问，请用 [socket proxy](docs/deployment.md#docker-socket-proxy)。更多安装方式见 [docs/deployment.md](docs/deployment.md)。

常规桥接网络**不需要** `NET_ADMIN`。它只对裸机上的 `ss` 回退路径有帮助。

## 配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `COMPOSE_SCAN_DIR` | `/compose` | 扫描 `compose.y*ml` / `docker-compose.y*ml` 的目录 |
| `COMPOSE_SCAN_DEPTH` | `4` | 扫描子目录的最大深度 |
| `COMPOSE_SCAN_MAX_FILES` | `400` | 每次刷新最多解析的 compose 文件数 |
| `PORT_RANGE_START` | `1` | **空闲数量**统计的起始端口 |
| `PORT_RANGE_END` | `9999` | 上述区间的结束（不会把网格填满绿格） |
| `PORT_LIGHT_DATA_DIR` | `/data` | 手动端口、隐藏列表等 JSON |
| `CUSTOM_PORTS_FILE` | `/data/custom_ports.json` | 额外 / 覆盖的端口名称 |
| `AUTH_USER` / `AUTH_PASSWORD` | 未设置 | 可选 HTTP Basic Auth。`/api/health` 保持开放。 |
| `HIDDEN_UNLOCK_PASSWORD` | 未设置 | 设置后（或启用了 Basic Auth），从网格隐藏的端口不会出现在未解锁的 API 里。 |

把 [custom_ports.example.json](custom_ports.example.json) 复制为 `custom_ports.json`（已 gitignore）。分类：`system`、`web`、`database`、`message`、`proxy`、`vpn`、`selfhosted`、`dev`、`infra`、`gaming`。

如果在 Compose 里 bind-mount `custom_ports.json`，请先在宿主机上建好**文件**。路径不存在时 Docker 会建成目录，应用就读不了。

## 隐私

- 无遥测、无统计、无出站请求
- 数据留在跑容器的那台机器上
- 用户修改存在 `/data` 下的本地 JSON

## 技术栈

- 后端：Python 3.12、FastAPI、Uvicorn
- 前端：静态 HTML/CSS/JS
- 镜像：`python:3.12-slim` + `iproute2`

## 许可证

[MIT](LICENSE) © 2026 StepaniaH

如果 Port-Light 让你少撞一次 `bind: address already in use`，可选请作者喝杯咖啡：[Ko-fi](https://ko-fi.com/stepaniah)。
