"""Known ports database — common service names, descriptions, and metadata.

Only standard, well-known ports are included here.
Users can add their own ports via a local ``custom_ports.json`` file
(see ``load_custom_ports``) — that file is gitignored and never shipped.

Fields:
    name:           Short service name
    description:    One-line explanation
    category:       system | web | database | message | proxy | vpn |
                    selfhosted | dev | infra | gaming
    is_access_port: True if users typically connect to this port directly
                    (web UIs, SSH, VNC, admin panels). False for internal
                    services (databases, exporters, sync protocols).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

KNOWN_PORTS: dict[int, dict] = {
    # ── System services ───────────────────────────────────────
    22:   {"name": "SSH",           "description": "Secure Shell — remote terminal access",         "category": "system",    "is_access_port": True},
    23:   {"name": "Telnet",        "description": "Unencrypted remote terminal (avoid)",            "category": "system",    "is_access_port": True},
    25:   {"name": "SMTP",          "description": "Email sending (MTA)",                            "category": "system",    "is_access_port": False},
    53:   {"name": "DNS",           "description": "Domain Name System resolver",                    "category": "system",    "is_access_port": False},
    67:   {"name": "DHCP Server",   "description": "DHCP server",                                    "category": "system",    "is_access_port": False},
    68:   {"name": "DHCP Client",   "description": "DHCP client",                                    "category": "system",    "is_access_port": False},
    80:   {"name": "HTTP",          "description": "Web server",                                     "category": "web",       "is_access_port": True},
    110:  {"name": "POP3",          "description": "Email receiving (plaintext)",                    "category": "system",    "is_access_port": False},
    111:  {"name": "RPC",           "description": "Portmapper / rpcbind (NFS related)",             "category": "system",    "is_access_port": False},
    123:  {"name": "NTP",           "description": "Network Time Protocol",                          "category": "system",    "is_access_port": False},
    143:  {"name": "IMAP",          "description": "Email receiving (plaintext)",                    "category": "system",    "is_access_port": False},
    443:  {"name": "HTTPS",         "description": "Encrypted web server",                           "category": "web",       "is_access_port": True},
    445:  {"name": "SMB",           "description": "Windows file sharing / Samba",                   "category": "system",    "is_access_port": True},
    465:  {"name": "SMTPS",         "description": "Email sending over SSL",                         "category": "system",    "is_access_port": False},
    515:  {"name": "LPR",           "description": "Line Printer Remote (printing)",                 "category": "system",    "is_access_port": False},
    587:  {"name": "SMTP Submit",   "description": "Email submission (MSA)",                         "category": "system",    "is_access_port": False},
    631:  {"name": "IPP",           "description": "CUPS printing",                                  "category": "system",    "is_access_port": True},
    636:  {"name": "LDAPS",         "description": "Encrypted LDAP",                                 "category": "system",    "is_access_port": False},
    873:  {"name": "rsync",         "description": "rsync file synchronization",                     "category": "system",    "is_access_port": False},
    993:  {"name": "IMAPS",         "description": "Encrypted IMAP",                                 "category": "system",    "is_access_port": False},
    995:  {"name": "POP3S",         "description": "Encrypted POP3",                                 "category": "system",    "is_access_port": False},

    # ── VPN / Remote access ───────────────────────────────────
    1194:  {"name": "OpenVPN",       "description": "OpenVPN server",                                "category": "vpn",       "is_access_port": True},
    3389:  {"name": "RDP",           "description": "Windows Remote Desktop",                        "category": "vpn",       "is_access_port": True},
    51820: {"name": "WireGuard",     "description": "WireGuard VPN",                                 "category": "vpn",       "is_access_port": True},
    5800:  {"name": "VNC HTTP",      "description": "VNC web interface",                             "category": "vpn",       "is_access_port": True},
    5900:  {"name": "VNC",           "description": "VNC remote desktop",                            "category": "vpn",       "is_access_port": True},

    # ── Databases (internal, not access ports) ────────────────
    1433:  {"name": "MSSQL",         "description": "Microsoft SQL Server",                           "category": "database",  "is_access_port": False},
    1521:  {"name": "Oracle DB",     "description": "Oracle database",                               "category": "database",  "is_access_port": False},
    3306:  {"name": "MySQL",         "description": "MySQL / MariaDB database",                      "category": "database",  "is_access_port": False},
    5432:  {"name": "PostgreSQL",    "description": "PostgreSQL database",                           "category": "database",  "is_access_port": False},
    6379:  {"name": "Redis",         "description": "Redis in-memory cache",                         "category": "database",  "is_access_port": False},
    9042:  {"name": "Cassandra",     "description": "Cassandra CQL native",                          "category": "database",  "is_access_port": False},
    27017: {"name": "MongoDB",       "description": "MongoDB database",                              "category": "database",  "is_access_port": False},

    # ── Message queues (internal) ─────────────────────────────
    1883:   {"name": "MQTT",         "description": "MQTT broker",                                   "category": "message",   "is_access_port": False},
    5672:   {"name": "RabbitMQ",     "description": "RabbitMQ AMQP",                                 "category": "message",   "is_access_port": False},
    9092:   {"name": "Kafka",        "description": "Kafka broker",                                  "category": "message",   "is_access_port": False},
    15672:  {"name": "RabbitMQ UI",  "description": "RabbitMQ management UI",                        "category": "message",   "is_access_port": True},
    61613:  {"name": "ActiveMQ",     "description": "ActiveMQ STOMP",                                "category": "message",   "is_access_port": False},

    # ── Web / Proxy ───────────────────────────────────────────
    1080: {"name": "SOCKS",        "description": "SOCKS proxy",                                     "category": "proxy",     "is_access_port": False},
    3000: {"name": "Web App",      "description": "Common dev web app port",                         "category": "web",       "is_access_port": True},
    3001: {"name": "Web App",      "description": "Common dev web app port",                         "category": "web",       "is_access_port": True},
    4000: {"name": "Web App",      "description": "Common dev web app port",                         "category": "web",       "is_access_port": True},
    5000: {"name": "Web App",      "description": "Common dev web app port (Flask default)",         "category": "web",       "is_access_port": True},
    7000: {"name": "Web App",      "description": "Common dev web app port",                         "category": "web",       "is_access_port": True},
    8000: {"name": "HTTP Alt",     "description": "Alternate HTTP (Django/Python default)",          "category": "web",       "is_access_port": True},
    8080: {"name": "HTTP Alt",     "description": "Alternate HTTP / reverse proxy",                  "category": "web",       "is_access_port": True},
    8443: {"name": "HTTPS Alt",    "description": "Alternate HTTPS",                                 "category": "web",       "is_access_port": True},
    9000: {"name": "Web App",      "description": "Common admin / app port",                         "category": "web",       "is_access_port": True},
    9001: {"name": "Web App",      "description": "Common admin / app port",                         "category": "web",       "is_access_port": True},

    # ── Self-hosted / Homelab (standard ports only) ───────────
    1900:   {"name": "DLNA",         "description": "UPnP / DLNA media discovery",                   "category": "selfhosted","is_access_port": False},
    4533:   {"name": "Navidrome",    "description": "Navidrome music server (default)",              "category": "selfhosted","is_access_port": True},
    7878:   {"name": "Radarr",       "description": "Radarr movie manager",                         "category": "selfhosted","is_access_port": True},
    8081:   {"name": "AdGuard",      "description": "AdGuard Home web UI",                           "category": "selfhosted","is_access_port": True},
    8096:   {"name": "Jellyfin",     "description": "Jellyfin media server",                         "category": "selfhosted","is_access_port": True},
    8384:   {"name": "Syncthing UI", "description": "Syncthing web UI",                              "category": "selfhosted","is_access_port": True},
    8787:   {"name": "Bazarr",       "description": "Bazarr subtitle manager",                       "category": "selfhosted","is_access_port": True},
    8920:   {"name": "Jellyfin HTTPS","description": "Jellyfin HTTPS",                               "category": "selfhosted","is_access_port": True},
    8989:   {"name": "Sonarr",       "description": "Sonarr TV manager",                            "category": "selfhosted","is_access_port": True},
    9117:   {"name": "Prowlarr",     "description": "Prowlarr indexer manager",                     "category": "selfhosted","is_access_port": True},
    9443:   {"name": "Portainer",    "description": "Portainer HTTPS",                               "category": "selfhosted","is_access_port": True},
    22000:  {"name": "Syncthing",    "description": "Syncthing sync protocol",                      "category": "selfhosted","is_access_port": False},
    21027:  {"name": "Syncthing",    "description": "Syncthing discovery",                          "category": "selfhosted","is_access_port": False},
    5055:   {"name": "Overseerr",    "description": "Overseerr request manager",                    "category": "selfhosted","is_access_port": True},
    11434:  {"name": "Ollama",       "description": "Ollama LLM inference server",                   "category": "selfhosted","is_access_port": True},
    # ── Infrastructure (internal) ─────────────────────────────
    2049:   {"name": "NFS",          "description": "Network File System",                           "category": "infra",     "is_access_port": False},
    2375:   {"name": "Docker API",   "description": "Docker daemon API (insecure)",                  "category": "infra",     "is_access_port": False},
    2376:   {"name": "Docker API",   "description": "Docker daemon API (TLS)",                       "category": "infra",     "is_access_port": False},
    2377:   {"name": "Docker Swarm", "description": "Docker Swarm management",                       "category": "infra",     "is_access_port": False},
    7946:   {"name": "Docker Swarm", "description": "Docker Swarm node communication",               "category": "infra",     "is_access_port": False},
    4789:   {"name": "VXLAN",        "description": "Docker Swarm overlay network",                  "category": "infra",     "is_access_port": False},
    20048:  {"name": "NFS mountd",   "description": "NFS mountd",                                    "category": "infra",     "is_access_port": False},

    # ── Monitoring (internal) ─────────────────────────────────
    9090:   {"name": "Prometheus",   "description": "Prometheus monitoring",                         "category": "infra",     "is_access_port": False},
    9091:   {"name": "Pushgateway",  "description": "Prometheus pushgateway",                        "category": "infra",     "is_access_port": False},
    9100:   {"name": "Node Exporter","description": "Prometheus node exporter",                      "category": "infra",     "is_access_port": False},

    # ── Development ───────────────────────────────────────────
    4200:  {"name": "Angular",       "description": "Angular dev server",                            "category": "dev",       "is_access_port": True},
    5173:  {"name": "Vite",          "description": "Vite dev server",                               "category": "dev",       "is_access_port": True},
    8888:  {"name": "Jupyter",       "description": "Jupyter Notebook",                              "category": "dev",       "is_access_port": True},
    9222:  {"name": "Chrome DevTools","description": "Chrome remote debugging",                      "category": "dev",       "is_access_port": False},

    # ── Gaming ────────────────────────────────────────────────
    27015: {"name": "Steam",         "description": "Steam game server",                             "category": "gaming",    "is_access_port": True},
    25565: {"name": "Minecraft",     "description": "Minecraft server",                              "category": "gaming",    "is_access_port": True},
    25575: {"name": "Minecraft RCON","description": "Minecraft RCON",                                "category": "gaming",    "is_access_port": False},
}


def _load_custom_ports() -> dict[int, dict]:
    """Load user-specific port overrides from a local JSON file.

    Path is set by ``CUSTOM_PORTS_FILE`` env var.
    Default: ``/data/custom_ports.json`` (typically a Docker volume).

    Format — same as KNOWN_PORTS entries::
        {
            "3001": {"name": "Grafana", "description": "...", "category": "selfhosted", "is_access_port": true},
            "8188": {"name": "ComfyUI", "description": "...", "category": "selfhosted", "is_access_port": true}
        }

    This file is user-specific, gitignored, and never included in the repo.
    """
    path = os.environ.get("CUSTOM_PORTS_FILE", "/data/custom_ports.json")
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
        return {int(k): v for k, v in raw.items()}
    except (json.JSONDecodeError, ValueError, OSError):
        return {}


def get_known_port(port: int) -> dict | None:
    """Return known service info for *port*, or None.

    Merges built-in KNOWN_PORTS with user's custom_ports.json.
    Custom ports override built-in entries with the same number.
    """
    custom = _load_custom_ports()
    merged = {**KNOWN_PORTS, **custom}
    entry = merged.get(port)
    if entry:
        return {
            "name": entry.get("name", "Unknown"),
            "description": entry.get("description", ""),
            "category": entry.get("category", "unknown"),
            "is_access_port": entry.get("is_access_port", False),
        }
    return None
