# Roadmap

Port-Light maps host listeners, Docker port mappings, and Compose declarations. It does not manage containers.

Release history belongs in [CHANGELOG.md](../CHANGELOG.md). This page lists planned work only.

## Next

- Improve first-run diagnostics when host, Docker, or Compose scanning is unavailable

## Candidates

These items depend on user feedback and are not scheduled:

- A command-line client for checking, reserving, and releasing ports
- Declarative port ranges and policy checks
- Additional read-only collectors such as Podman or TrueNAS
- A multi-host comparison view
- Community Applications packaging for Unraid

## Out of scope

- Starting or stopping containers, logs, and image updates
- Bookmark or home-page dashboards
- Kubernetes control-plane operations
- Hosted scanning of local Docker, `/proc`, or Compose data
