# Troubleshooting

## Occupancy scan warning

The warning “Occupancy data is incomplete or stale; free ports cannot be confirmed” means that at least one enabled scanner failed, returned incomplete data, or did not refresh in time. Port-Light keeps known occupied ports visible but does not treat missing observations as proof that a port is free.

Hover over, focus, or click the warning to see guidance based on the current scan. Press Escape to close the disclosure. Remote-machine warnings describe that machine; the hub's local settings do not modify a peer. The guidance does not include raw log messages, private paths, or environment values.

### Upgrading from v0.7.6

No mandatory configuration migration is required. The scan-depth fix prevents discovery from entering directories beyond the selected depth. Upgrading also fixes repeated interleaved degradation logs. Neither fix grants access to unreadable directories or Docker sockets.

If your enabled scanners already work, updating the image and recreating the container is enough:

```bash
docker compose pull port-light
docker compose up -d port-light
```

Keep your chosen image tag (`latest` or a release tag). Existing scanner selections and deployment permissions are not changed automatically.

If the warning remains, follow the relevant check below.

| Current failure | What to check |
| --- | --- |
| Docker | Daemon availability, socket mount or `DOCKER_HOST`, and socket permissions for the container user |
| Host listeners | The host `/proc` mount at `/host/proc` and its read permissions; for direct installation, `ss` or `/proc` availability |
| Compose directory | The `/compose` mount and directory read permissions; limit discovery to configuration directories |
| Incomplete Compose scan | Directory/file read permissions and scan scope; container logs for invalid YAML, missing required variables, or unresolved `include`/`extends` files |
| Compose file limit | Narrow discovery, exclude non-configuration folders, or increase the maximum file count |
| Stale snapshot | Wait for the next scan, then check logs for persistent scan failures or timeouts |

### Non-root Docker socket access

On the Docker host, obtain the socket group:

```bash
stat -c '%g' /var/run/docker.sock
```

Store that numeric result as `DOCKER_SOCKET_GID` in the deployment's `.env`, then add this to the existing Port-Light service:

```yaml
services:
  port-light:
    group_add:
      - "${DOCKER_SOCKET_GID}"
```

Use the GID from your host, not a copied example. Do not use `chmod 666`, privileged mode, or switch to root just to suppress the warning. A read-only socket mount does not bypass Unix permissions or make Docker API calls read-only; a [socket proxy](deployment.md#docker-socket-proxy) can restrict API access.

Apply changes to `user`, `group_add`, mounts, or environment variables with `docker compose up -d port-light`. A plain `docker compose restart` restarts the existing container with its old configuration.

### Compose discovery and scanner selection

In **Settings → Occupancy**, choose the local scanners, scan depth, excluded folder names, and maximum Compose file count. These settings are applied to subsequent scans without restarting the container. Set the mount source and `COMPOSE_SCAN_DIR` in the deployment configuration; the web UI cannot grant filesystem permissions or mount host directories.

For example, if each stack has its Compose file directly under a common parent and its database data below that, a scan depth of `1` reads the stack-level files without entering their children. Alternatively, exclude specific data-folder names using the settings page or `COMPOSE_SCAN_EXCLUDE_DIRS=data,postgres-data`. Exclusions apply to directory discovery; explicit Compose `include` and `extends` references are still followed and must remain readable.

Disable a scanner only if that source is intentionally outside your occupancy checks. Disabling Compose omits declarations for services that are not running. Disabling Docker omits information that only the Docker API supplies. A successful scan is complete only for the sources and discovery scope you selected.

Saved settings normally override environment defaults. If `PORT_LIGHT_SETTINGS_SOURCE=env` or `SETTINGS_READONLY=1` is set, edit the deployment configuration instead; the settings page remains read-only. For v0.7.6 instances that do not yet expose scanner selection in the UI, use `PORT_LIGHT_SCANNERS` in Compose, then recreate the container. Do not assume that upgrading the hub upgrades its peers.

## 从 v0.7.6 升级后仍有警告

“占用信息不完整或已过期”表示至少一个已启用的扫描源失败、返回不完整数据，或者扫描结果未能及时更新。已知占用端口仍会显示，但不会把缺失信息的端口判断为空闲。

本次修复不要求统一迁移配置。扫描深度修复会阻止程序进入超出设定深度的目录，日志修复会避免交替出现的相同错误重复刷屏。但镜像升级不会自动补齐 Docker socket 权限、修改挂载，或关闭扫描源。

- **原配置已正常工作**：保留自己选择的镜像标签（包括 `latest`），运行 `docker compose pull port-light`、`docker compose up -d port-light` 即可。
- **Docker 扫描失败**：检查 Docker 服务、socket 挂载或 `DOCKER_HOST`。使用非 root 容器时，在宿主机运行 `stat -c '%g' /var/run/docker.sock`，将结果写入部署目录 `.env` 的 `DOCKER_SOCKET_GID`，并按上面的 YAML 为服务添加 `group_add`。不要直接复制其他机器的组号，也不建议改成 root、特权模式或 `chmod 666`。
- **Compose 目录扫描失败**：检查 `/compose` 挂载及读取权限。如果数据库等数据目录与 Compose 文件混放，在“设置 → 占用图”中减小扫描深度或排除数据目录。深度 `1` 会读取根目录及其直接子目录中的 Compose 文件，不进入更深层目录。
- **Compose 配置解析不完整**：按该机器的容器日志修正 YAML、必填环境变量、`include` 或 `extends` 引用。排除目录只影响文件发现，明确引用的文件仍会读取。
- **达到 Compose 文件上限**：缩小扫描范围、排除非配置目录，或提高最大文件数。确认需要检查的配置没有被遗漏。
- **结果已过期**：等待下一次扫描；持续出现时，检查该机器的日志和扫描耗时。旧扫描器状态不代表当前失败原因。

在新版“设置 → 占用图”中修改扫描源、深度、排除目录和文件上限后，后续扫描自动应用，无需重启。挂载、文件权限、`user`、`group_add` 和环境变量仍属于部署配置；修改后运行 `docker compose up -d port-light`，单独 `docker compose restart` 不会应用这些更改。

仅在不需要某个扫描源时关闭它。关闭 Compose 后，不会检查未运行服务声明的端口；关闭 Docker 后，会缺少只有 Docker API 才能提供的信息。“扫描完整”只针对你选择的扫描来源及目录范围。

设置页保存的值通常优先于环境变量。若设置了 `PORT_LIGHT_SETTINGS_SOURCE=env` 或 `SETTINGS_READONLY=1`，设置页为只读，应从部署配置修改。尚未升级的 v0.7.6 可通过 `PORT_LIGHT_SCANNERS` 选择扫描源，再重建容器。远端机器需要分别处理；升级本机不会自动升级远端。

新版警告支持鼠标悬浮、键盘聚焦和点击展开，Escape 关闭。详情只使用当前响应中的扫描状态给出排查建议，不会展示原始日志、私有路径或环境变量值。
