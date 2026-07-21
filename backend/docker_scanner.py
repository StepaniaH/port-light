"""Docker scanner: gets container info and port mappings via Docker API."""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    import docker
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False


@dataclass
class ContainerInfo:
    name: str
    status: str  # "running", "exited", "created", etc.
    image: str
    ports: list[dict] = field(default_factory=list)
    compose_project: str | None = None
    compose_service: str | None = None


def scan_containers() -> list[ContainerInfo]:
    """Get all containers (running + stopped) with port mappings."""
    if not HAS_DOCKER:
        return []

    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)
    except Exception:
        return []

    result: list[ContainerInfo] = []
    for c in containers:
        labels = c.labels or {}
        result.append(ContainerInfo(
            name=c.name,
            status=c.status,
            image=c.attrs.get('Config', {}).get('Image', 'unknown'),
            ports=_extract_ports(c.attrs),
            compose_project=labels.get('com.docker.compose.project'),
            compose_service=labels.get('com.docker.compose.service'),
        ))
    return result


def _extract_ports(attrs: dict) -> list[dict]:
    """Extract host→container port mappings from container attributes."""
    ports: list[dict] = []
    bindings = attrs.get('HostConfig', {}).get('PortBindings', {})
    for container_port_spec, binding_list in bindings.items():
        # spec: "80/tcp" or "443/udp"
        if '/' in container_port_spec:
            cp, protocol = container_port_spec.split('/')
        else:
            cp, protocol = container_port_spec, 'tcp'

        if not binding_list:
            continue

        for b in binding_list:
            host_port = b.get('HostPort')
            if host_port:
                ports.append({
                    'host_port': int(host_port),
                    'host_ip': b.get('HostIp', '0.0.0.0'),
                    'container_port': int(cp),
                    'protocol': protocol,
                })
    return ports
