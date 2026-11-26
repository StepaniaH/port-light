# Port-Light Documentation

Landing pages: [English README](../README.md) · [简体中文 README](../README.zh-CN.md)

## Getting started

| Guide | Description |
|-------|-------------|
| [Deployment](deployment.md) | Docker Compose, `docker run`, image tags, socket proxy, reverse proxy, Podman, and health checks |
| [Unraid guide](../deploy/unraid/README.md) | Unraid Community Applications template, storage paths, authentication, and upgrades |
| [Configuration](configuration.md) | All environment variables, settings precedence, access control, custom port names, and data/privacy notes |

## Port management and diagnostics

| Guide | Description |
|-------|-------------|
| [Port management](port-management.md) | Grouping, Compose conflicts, reservations, and named port-range rules |
| [Troubleshooting](troubleshooting.md) | Scanner warnings, Doctor diagnostics, upgrade guidance, and socket permissions |
| [Troubleshooting and BYOK](analysis.md) | Local checks, saved reports, rechecks, and optional bring-your-own-key AI assistance |

## Automation, CLI, and AI

| Guide | Description |
|-------|-------------|
| [AI setup](ai-setup.md) | Connect Codex, Claude Code, Cursor, VS Code, Gemini CLI, or other AI tools via MCP or CLI + skill |
| [CLI](cli.md) | Install and use the dependency-free `port-light` command-line client |
| [API and MCP integrations](integrations.md) | HTTP API, SSE events, Prometheus metrics, webhooks, agent skill, and MCP server; OpenAPI docs live at `/docs` on a running instance |

## Security, architecture, and contributing

| Guide | Description |
|-------|-------------|
| [Security](../SECURITY.md) | Trust model, hardening checklist, and vulnerability reporting |
| [Architecture](architecture.md) | Tech stack, scanner merge model, occupancy monitor, and multi-host hub polling |
| [UI extensions](ui-extensions.md) | Registering local UI navigation links in composed FastAPI apps |
| [Contributing](../CONTRIBUTING.md) | Local development, tests, i18n workflow, and maintainer release process |
| [Changelog](../CHANGELOG.md) | Release notes |
