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
    21:   {"name": "FTP",           "description": "File Transfer Protocol",                          "category": "system",    "is_access_port": True},
    22:   {"name": "SSH",           "description": "Secure Shell — remote terminal access",         "category": "system",    "is_access_port": True},
    23:   {"name": "Telnet",        "description": "Unencrypted remote terminal (avoid)",            "category": "system",    "is_access_port": True},
    25:   {"name": "SMTP",          "description": "Email sending (MTA)",                            "category": "system",    "is_access_port": False},
    53:   {"name": "DNS",           "description": "Domain Name System resolver",                    "category": "system",    "is_access_port": False},
    67:   {"name": "DHCP Server",   "description": "DHCP server",                                    "category": "system",    "is_access_port": False},
    68:   {"name": "DHCP Client",   "description": "DHCP client",                                    "category": "system",    "is_access_port": False},
    80:   {"name": "HTTP",          "description": "Web server",                                     "category": "web",       "is_access_port": True},
    81:   {"name": "NPM",           "description": "Nginx Proxy Manager admin UI",                    "category": "proxy",     "is_access_port": True},
    110:  {"name": "POP3",          "description": "Email receiving (plaintext)",                    "category": "system",    "is_access_port": False},
    111:  {"name": "RPC",           "description": "Portmapper / rpcbind (NFS related)",             "category": "system",    "is_access_port": False},
    123:  {"name": "NTP",           "description": "Network Time Protocol",                          "category": "system",    "is_access_port": False},
    139:  {"name": "NetBIOS",       "description": "NetBIOS session (SMB related)",                  "category": "system",    "is_access_port": False},
    143:  {"name": "IMAP",          "description": "Email receiving (plaintext)",                    "category": "system",    "is_access_port": False},
    161:  {"name": "SNMP",          "description": "Simple Network Management Protocol",             "category": "system",    "is_access_port": False},
    162:  {"name": "SNMP trap",     "description": "SNMP trap",                                      "category": "system",    "is_access_port": False},
    389:  {"name": "LDAP",          "description": "Directory service",                             "category": "system",    "is_access_port": False},
    443:  {"name": "HTTPS",         "description": "Encrypted web server",                           "category": "web",       "is_access_port": True},
    445:  {"name": "SMB",           "description": "Windows file sharing / Samba",                   "category": "system",    "is_access_port": True},
    554:  {"name": "RTSP",          "description": "Real Time Streaming Protocol (cameras)",        "category": "system",    "is_access_port": False},
    465:  {"name": "SMTPS",         "description": "Email sending over SSL",                         "category": "system",    "is_access_port": False},
    514:  {"name": "Syslog",        "description": "Syslog",                                         "category": "system",    "is_access_port": False},
    515:  {"name": "LPR",           "description": "Line Printer Remote (printing)",                 "category": "system",    "is_access_port": False},
    548:  {"name": "AFP",           "description": "Apple Filing Protocol",                          "category": "system",    "is_access_port": True},
    587:  {"name": "SMTP Submit",   "description": "Email submission (MSA)",                         "category": "system",    "is_access_port": False},
    631:  {"name": "IPP",           "description": "CUPS printing",                                  "category": "system",    "is_access_port": True},
    636:  {"name": "LDAPS",         "description": "Encrypted LDAP",                                 "category": "system",    "is_access_port": False},
    853:  {"name": "DoT",           "description": "DNS over TLS",                                   "category": "system",    "is_access_port": False},
    873:  {"name": "rsync",         "description": "rsync file synchronization",                     "category": "system",    "is_access_port": False},
    9418: {"name": "Git",           "description": "Git daemon",                                     "category": "system",    "is_access_port": False},
    993:  {"name": "IMAPS",         "description": "Encrypted IMAP",                                 "category": "system",    "is_access_port": False},
    995:  {"name": "POP3S",         "description": "Encrypted POP3",                                 "category": "system",    "is_access_port": False},

    # ── VPN / Remote access ───────────────────────────────────
    1194:  {"name": "OpenVPN",       "description": "OpenVPN server",                                "category": "vpn",       "is_access_port": True},
    2222:  {"name": "SSH Alt",       "description": "Alternate SSH",                                 "category": "system",    "is_access_port": True},
    3389:  {"name": "RDP",           "description": "Windows Remote Desktop",                        "category": "vpn",       "is_access_port": True},
    51820: {"name": "WireGuard",     "description": "WireGuard VPN",                                 "category": "vpn",       "is_access_port": True},
    5060:  {"name": "SIP",           "description": "SIP (Asterisk / FreePBX)",                      "category": "system",    "is_access_port": True},
    5061:  {"name": "SIPS",          "description": "SIP over TLS",                                  "category": "system",    "is_access_port": True},
    41641: {"name": "Tailscale",     "description": "Tailscale (default UDP)",                       "category": "vpn",       "is_access_port": False},
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
    5222:   {"name": "XMPP",         "description": "XMPP client (Prosody / ejabberd)",              "category": "message",   "is_access_port": True},
    8883:   {"name": "MQTT TLS",     "description": "MQTT over TLS",                                 "category": "message",   "is_access_port": False},
    5672:   {"name": "RabbitMQ",     "description": "RabbitMQ AMQP",                                 "category": "message",   "is_access_port": False},
    9092:   {"name": "Kafka",        "description": "Kafka broker",                                  "category": "message",   "is_access_port": False},
    15672:  {"name": "RabbitMQ UI",  "description": "RabbitMQ management UI",                        "category": "message",   "is_access_port": True},
    61613:  {"name": "ActiveMQ",     "description": "ActiveMQ STOMP",                                "category": "message",   "is_access_port": False},

    # ── Web / Proxy ───────────────────────────────────────────
    1080: {"name": "SOCKS",        "description": "SOCKS proxy",                                     "category": "proxy",     "is_access_port": False},
    8118: {"name": "Privoxy",      "description": "Privoxy HTTP proxy",                              "category": "proxy",     "is_access_port": False},
    9050: {"name": "Tor SOCKS",    "description": "Tor SOCKS proxy",                                 "category": "proxy",     "is_access_port": False},
    3000: {"name": "Grafana",      "description": "Grafana (also a common app port)",               "category": "selfhosted","is_access_port": True},
    3001: {"name": "Uptime Kuma",  "description": "Uptime Kuma (also a common app port)",            "category": "selfhosted","is_access_port": True},
    4000: {"name": "Web App",      "description": "Common web app port",                             "category": "web",       "is_access_port": True},
    5000: {"name": "Synology DSM", "description": "Synology DSM HTTP (also Flask default)",          "category": "selfhosted","is_access_port": True},
    5001: {"name": "Synology HTTPS","description": "Synology DSM HTTPS",                             "category": "selfhosted","is_access_port": True},
    7000: {"name": "Web App",      "description": "Common web app port",                             "category": "web",       "is_access_port": True},
    8000: {"name": "HTTP Alt",     "description": "Alternate HTTP (Django / Paperless / many apps)", "category": "web",       "is_access_port": True},
    8080: {"name": "HTTP Alt",     "description": "Alternate HTTP / reverse proxy / qBittorrent",    "category": "web",       "is_access_port": True},
    8443: {"name": "HTTPS Alt",    "description": "Alternate HTTPS / UniFi controller",              "category": "web",       "is_access_port": True},
    8843: {"name": "UniFi HTTPS",  "description": "UniFi guest portal HTTPS",                        "category": "infra",     "is_access_port": True},
    8880: {"name": "UniFi HTTP",   "description": "UniFi HTTP redirect",                             "category": "infra",     "is_access_port": True},
    9000: {"name": "Portainer HTTP","description": "Portainer HTTP (also a common admin port)",      "category": "selfhosted","is_access_port": True},
    9001: {"name": "Web App",      "description": "Common admin / app port",                         "category": "web",       "is_access_port": True},

    # ── Self-hosted / Homelab (standard ports only) ───────────
    1900:   {"name": "DLNA",         "description": "UPnP / DLNA media discovery",                   "category": "selfhosted","is_access_port": False},
    1935:   {"name": "RTMP",         "description": "RTMP ingest (OBS / media servers)",             "category": "selfhosted","is_access_port": False},
    1984:   {"name": "Changedetection","description": "changedetection.io",                           "category": "selfhosted","is_access_port": True},
    4533:   {"name": "Navidrome",    "description": "Navidrome music server (default)",              "category": "selfhosted","is_access_port": True},
    7878:   {"name": "Radarr",       "description": "Radarr movie manager",                         "category": "selfhosted","is_access_port": True},
    8081:   {"name": "AdGuard",      "description": "AdGuard Home web UI",                           "category": "selfhosted","is_access_port": True},
    8096:   {"name": "Jellyfin",     "description": "Jellyfin media server",                         "category": "selfhosted","is_access_port": True},
    8384:   {"name": "Syncthing UI", "description": "Syncthing web UI",                              "category": "selfhosted","is_access_port": True},
    8787:   {"name": "Readarr",      "description": "Readarr book manager (also older Bazarr port)", "category": "selfhosted","is_access_port": True},
    8920:   {"name": "Jellyfin HTTPS","description": "Jellyfin HTTPS",                               "category": "selfhosted","is_access_port": True},
    8989:   {"name": "Sonarr",       "description": "Sonarr TV manager",                            "category": "selfhosted","is_access_port": True},
    9117:   {"name": "Prowlarr",     "description": "Prowlarr indexer manager (older default)",     "category": "selfhosted","is_access_port": True},
    9443:   {"name": "Portainer",    "description": "Portainer HTTPS",                               "category": "selfhosted","is_access_port": True},
    22000:  {"name": "Syncthing",    "description": "Syncthing sync protocol",                      "category": "selfhosted","is_access_port": False},
    21027:  {"name": "Syncthing",    "description": "Syncthing discovery",                          "category": "selfhosted","is_access_port": False},
    51413:  {"name": "Transmission", "description": "Transmission BitTorrent peer",                 "category": "selfhosted","is_access_port": False},
    5055:   {"name": "Overseerr",    "description": "Overseerr request manager",                    "category": "selfhosted","is_access_port": True},
    11434:  {"name": "Ollama",       "description": "Ollama LLM inference server",                   "category": "selfhosted","is_access_port": True},
    2100:   {"name": "Port-Light",   "description": "Port-Light dashboard",                          "category": "selfhosted","is_access_port": True},
    2283:   {"name": "Immich",       "description": "Immich photo management",                       "category": "selfhosted","is_access_port": True},
    2342:   {"name": "PhotoPrism",   "description": "PhotoPrism",                                    "category": "selfhosted","is_access_port": True},
    1880:   {"name": "Node-RED",     "description": "Node-RED",                                      "category": "selfhosted","is_access_port": True},
    6052:   {"name": "ESPHome",      "description": "ESPHome dashboard",                             "category": "selfhosted","is_access_port": True},
    6767:   {"name": "Bazarr",       "description": "Bazarr subtitle manager (default)",             "category": "selfhosted","is_access_port": True},
    7575:   {"name": "Homarr",       "description": "Homarr dashboard",                              "category": "selfhosted","is_access_port": True},
    6789:   {"name": "NZBGet",       "description": "NZBGet",                                        "category": "selfhosted","is_access_port": True},
    8123:   {"name": "Home Assistant","description": "Home Assistant",                               "category": "selfhosted","is_access_port": True},
    8181:   {"name": "Tautulli",     "description": "Tautulli (Plex stats)",                         "category": "selfhosted","is_access_port": True},
    8188:   {"name": "ComfyUI",      "description": "ComfyUI",                                       "category": "selfhosted","is_access_port": True},
    8191:   {"name": "FlareSolverr", "description": "FlareSolverr",                                  "category": "selfhosted","is_access_port": False},
    8082:   {"name": "Duplicati",    "description": "Duplicati web UI",                              "category": "selfhosted","is_access_port": True},
    8112:   {"name": "Deluge",       "description": "Deluge web UI",                                 "category": "selfhosted","is_access_port": True},
    8200:   {"name": "ReadyMedia",   "description": "ReadyMedia / MiniDLNA",                         "category": "selfhosted","is_access_port": False},
    8554:   {"name": "RTSP Alt",     "description": "go2rtc / Frigate RTSP",                         "category": "selfhosted","is_access_port": False},
    8265:   {"name": "Tdarr",        "description": "Tdarr server",                                  "category": "selfhosted","is_access_port": True},
    8686:   {"name": "Lidarr",       "description": "Lidarr music manager",                          "category": "selfhosted","is_access_port": True},
    9696:   {"name": "Prowlarr",     "description": "Prowlarr (current default)",                    "category": "selfhosted","is_access_port": True},
    32400:  {"name": "Plex",         "description": "Plex Media Server",                             "category": "selfhosted","is_access_port": True},
    32469:  {"name": "Plex GDM",     "description": "Plex GDM discovery",                            "category": "selfhosted","is_access_port": False},
    19999:  {"name": "Netdata",      "description": "Netdata",                                       "category": "selfhosted","is_access_port": True},
    7860:   {"name": "Gradio",       "description": "Gradio / Automatic1111",                        "category": "selfhosted","is_access_port": True},
    15777:  {"name": "Tdarr UI",     "description": "Tdarr web UI",                                  "category": "selfhosted","is_access_port": True},
    13378:  {"name": "RDTClient",    "description": "rdtclient",                                     "category": "selfhosted","is_access_port": True},
    9093:   {"name": "Alertmanager", "description": "Prometheus Alertmanager",                       "category": "infra",     "is_access_port": True},
    16686:  {"name": "Jaeger",       "description": "Jaeger UI",                                     "category": "infra",     "is_access_port": True},
    4317:   {"name": "OTLP gRPC",    "description": "OpenTelemetry OTLP gRPC",                       "category": "infra",     "is_access_port": False},
    4318:   {"name": "OTLP HTTP",    "description": "OpenTelemetry OTLP HTTP",                       "category": "infra",     "is_access_port": False},
    3100:   {"name": "Loki",         "description": "Grafana Loki",                                  "category": "infra",     "is_access_port": False},
    8086:   {"name": "InfluxDB",     "description": "InfluxDB",                                      "category": "database",  "is_access_port": False},
    9200:   {"name": "Elasticsearch","description": "Elasticsearch",                                 "category": "database",  "is_access_port": False},
    5601:   {"name": "Kibana",       "description": "Kibana",                                        "category": "infra",     "is_access_port": True},
    7700:   {"name": "Meilisearch",  "description": "Meilisearch",                                   "category": "database",  "is_access_port": False},
    6333:   {"name": "Qdrant",       "description": "Qdrant vector DB",                              "category": "database",  "is_access_port": False},
    11211:  {"name": "Memcached",    "description": "Memcached",                                     "category": "database",  "is_access_port": False},
    5984:   {"name": "CouchDB",      "description": "CouchDB",                                       "category": "database",  "is_access_port": False},
    3478:   {"name": "STUN",         "description": "STUN / UniFi STUN",                             "category": "infra",     "is_access_port": False},
    5353:   {"name": "mDNS",         "description": "Multicast DNS / Bonjour",                       "category": "infra",     "is_access_port": False},
    5357:   {"name": "WSD",          "description": "Web Services Discovery (Windows / NAS)",        "category": "infra",     "is_access_port": False},
    6443:   {"name": "Kubernetes",   "description": "Kubernetes API",                                "category": "infra",     "is_access_port": False},
    10000:  {"name": "Webmin",       "description": "Webmin",                                        "category": "infra",     "is_access_port": True},
    19132:  {"name": "MC Bedrock",   "description": "Minecraft Bedrock",                             "category": "gaming",    "is_access_port": True},
    2456:   {"name": "Valheim",      "description": "Valheim",                                       "category": "gaming",    "is_access_port": True},
    7777:   {"name": "Game server",  "description": "Common game server port",                       "category": "gaming",    "is_access_port": True},
    # ── Infrastructure (internal) ─────────────────────────────
    2049:   {"name": "NFS",          "description": "Network File System",                           "category": "infra",     "is_access_port": False},
    3260:   {"name": "iSCSI",        "description": "iSCSI (TrueNAS / SAN)",                         "category": "infra",     "is_access_port": False},
    2375:   {"name": "Docker API",   "description": "Docker daemon API (insecure)",                  "category": "infra",     "is_access_port": False},
    2376:   {"name": "Docker API",   "description": "Docker daemon API (TLS)",                       "category": "infra",     "is_access_port": False},
    2377:   {"name": "Docker Swarm", "description": "Docker Swarm management",                       "category": "infra",     "is_access_port": False},
    2379:   {"name": "etcd",         "description": "etcd client API",                               "category": "infra",     "is_access_port": False},
    2181:   {"name": "ZooKeeper",    "description": "Apache ZooKeeper",                              "category": "infra",     "is_access_port": False},
    7946:   {"name": "Docker Swarm", "description": "Docker Swarm node communication",               "category": "infra",     "is_access_port": False},
    4789:   {"name": "VXLAN",        "description": "Docker Swarm overlay network",                  "category": "infra",     "is_access_port": False},
    8291:   {"name": "Winbox",       "description": "MikroTik Winbox",                               "category": "infra",     "is_access_port": True},
    8728:   {"name": "RouterOS API", "description": "MikroTik RouterOS API",                         "category": "infra",     "is_access_port": False},
    20048:  {"name": "NFS mountd",   "description": "NFS mountd",                                    "category": "infra",     "is_access_port": False},

    # ── Monitoring (internal) ─────────────────────────────────
    9090:   {"name": "Prometheus",   "description": "Prometheus monitoring",                         "category": "infra",     "is_access_port": False},
    9091:   {"name": "Pushgateway",  "description": "Prometheus pushgateway",                        "category": "infra",     "is_access_port": False},
    9100:   {"name": "Node Exporter","description": "Prometheus node exporter",                      "category": "infra",     "is_access_port": False},
    10050:  {"name": "Zabbix agent", "description": "Zabbix agent",                                  "category": "infra",     "is_access_port": False},
    10051:  {"name": "Zabbix",       "description": "Zabbix server / proxy",                         "category": "infra",     "is_access_port": False},

    # ── Development ───────────────────────────────────────────
    4200:  {"name": "Angular",       "description": "Angular dev server",                            "category": "dev",       "is_access_port": True},
    5173:  {"name": "Vite",          "description": "Vite dev server",                               "category": "dev",       "is_access_port": True},
    8888:  {"name": "Jupyter",       "description": "Jupyter Notebook",                              "category": "dev",       "is_access_port": True},
    9222:  {"name": "Chrome DevTools","description": "Chrome remote debugging",                      "category": "dev",       "is_access_port": False},

    # ── Gaming ────────────────────────────────────────────────
    27015: {"name": "Steam",         "description": "Steam game server",                             "category": "gaming",    "is_access_port": True},
    25565: {"name": "Minecraft",     "description": "Minecraft server",                              "category": "gaming",    "is_access_port": True},
    25575: {"name": "Minecraft RCON","description": "Minecraft RCON",                                "category": "gaming",    "is_access_port": False},
    8211:  {"name": "Palworld",      "description": "Palworld",                                      "category": "gaming",    "is_access_port": True},
    9987:  {"name": "TeamSpeak",     "description": "TeamSpeak 3 voice",                             "category": "gaming",    "is_access_port": True},
}


_custom_cache: dict[int, dict] | None = None
_custom_mtime: float | None = None
_custom_path: str | None = None


def _load_custom_ports() -> dict[int, dict]:
    """Load user-specific port overrides from a local JSON file (mtime-cached)."""
    global _custom_cache, _custom_mtime, _custom_path
    path = os.environ.get("CUSTOM_PORTS_FILE", "/data/custom_ports.json")
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        _custom_cache, _custom_mtime, _custom_path = {}, None, path
        return {}
    if _custom_cache is not None and _custom_path == path and _custom_mtime == mtime:
        return _custom_cache
    try:
        raw = json.loads(p.read_text())
        parsed = {int(k): v for k, v in raw.items() if str(k).lstrip("-").isdigit()}
    except (json.JSONDecodeError, ValueError, OSError):
        parsed = {}
    _custom_cache, _custom_mtime, _custom_path = parsed, mtime, path
    return parsed


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
