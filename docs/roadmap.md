# Roadmap

Port-Light maps host listeners, Docker port mappings, and Compose declarations. It does not manage containers.

Release history belongs in [CHANGELOG.md](../CHANGELOG.md). This page lists planned work only.

## Candidates

These items depend on user feedback and are not scheduled:

- Declarative port ranges and policy checks
- Additional read-only collectors such as Podman or TrueNAS
- Community Applications packaging for Unraid
- A lower-chrome Settings information-architecture redesign. The current
  four-panel Settings flow is reliable and remains in place while CLI usage
  feedback has higher priority.

## Out of scope

- Starting or stopping containers, logs, and image updates
- Bookmark or home-page dashboards
- Kubernetes control-plane operations
- Hosted scanning of local Docker, `/proc`, or Compose data
