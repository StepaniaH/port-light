"""Disposable four-machine demo with synthetic listeners and Compose projects."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import secrets

ROOT = Path(__file__).resolve().parents[1]

PROFILES = [
 ('NAS · 媒体与存储','模拟 NAS / 192.0.2.10',2100,{'media':{'jellyfin':['8096:8096'],'plex':['32400:32400'],'streaming':['20000-20031:20000-20031/udp']},'downloads':{'qbittorrent':['8080:8080','6881:6881','6881:6881/udp'],'sonarr':['8989:8989'],'radarr':['7878:7878']},'legacy-dashboard':{'dashboard':['8080:80']}},[(22,'sshd'),(8096,'jellyfin'),(32400,'plex'),(8080,'qbittorrent'),(8989,'sonarr')]),
 ('Mini PC · 应用服务','模拟应用节点 / 192.0.2.20',2101,{'cloud':{'nextcloud':['8080:80'],'redis':['127.0.0.1:6379:6379'],'postgres':['127.0.0.1:5432:5432']},'photos':{'immich':['2283:2283'],'search':['7700:7700']},'home':{'homeassistant':['8123:8123'],'mqtt':['1883:1883','9001:9001']}},[(22,'sshd'),(8080,'nginx'),(5432,'postgres'),(6379,'redis'),(2283,'immich'),(8123,'homeassistant'),(1883,'mosquitto')]),
 ('Dev Box · 开发环境','模拟开发节点 / 192.0.2.30',2102,{'shop-dev':{'frontend':['3000:3000'],'api':['8000:8000'],'database':['5432:5432']},'blog-dev':{'frontend':['3000:80'],'api':['8001:8000']},'test-workers':{'workers':['24000-24015:24000-24015']}},[(22,'sshd'),(3000,'node'),(8000,'uvicorn'),(5432,'postgres')]),
 ('Edge · 网关与监控','模拟边缘节点 / 192.0.2.40',2103,{'gateway':{'traefik':['80:80','443:443','8088:8080'],'dns':['53:53','53:53/udp']},'monitoring':{'grafana':['3000:3000'],'prometheus':['9090:9090'],'node-exporter':['9100:9100'],'uptime-kuma':['3001:3001']}},[(22,'sshd'),(80,'traefik'),(443,'traefik'),(53,'dnsmasq'),(3000,'grafana'),(9090,'prometheus'),(9100,'node_exporter')])]

def request(port: int, path: str, body: dict | None = None, method: str | None = None, headers: dict | None = None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}" + path,
        data=json.dumps(body).encode() if body is not None else None, method=method,
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.load(response)


def run_worker(data: str, port: int):
    import backend.main as app
    from backend.port_scanner import ListeningPort
    import uvicorn
    listeners = json.loads((Path(data) / 'listeners.json').read_text())
    app.scan_listening_ports = lambda **kwargs: [ListeningPort(p, 'tcp', '127.0.0.1' if p in (5432, 6379) else '0.0.0.0', name) for p, name in listeners]
    app.host_listen_trusted = lambda: True
    uvicorn.run(app.app, host='127.0.0.1', port=port, log_level='warning')


def run_fleet(port: int) -> int:
    children = []
    ports = [port]
    for _ in PROFILES[1:]:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            ports.append(listener.getsockname()[1])
    def stop(_signal, _frame):
        raise KeyboardInterrupt
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    with tempfile.TemporaryDirectory(prefix='port-light-fleet-preview-') as temporary:
        try:
            for index, (name, description, _, projects, listeners) in enumerate(PROFILES):
                data = Path(temporary) / str(index)
                compose = data / 'compose'
                compose.mkdir(parents=True)
                for project, services in projects.items():
                    directory = compose / project
                    directory.mkdir()
                    (directory / 'compose.yaml').write_text(json.dumps({'name': project, 'services': {
                        service: {'image': 'example.invalid/' + service + ':demo', 'ports': mappings}
                        for service, mappings in services.items()}}))
                settings = {'host_name': name, 'host_description': description, 'host_layout': 'waterfall',
                    'locale': 'zh-CN', 'port_range_start': 1, 'port_range_end': 35000, 'copy_on_click': False,
                    'show_bind_addresses': True, 'show_bind_ipv4': True, 'local_scanners': ['listen', 'compose']}
                rules = [{'name': 'media-range' if index == 0 else 'dev-range', 'start': 20000 if index == 0 else 24000,
                    'end': 20999 if index == 0 else 24999,
                    'projects': ['media'] if index == 0 else ['shop-dev', 'test-workers']}] if index in (0, 2) else []
                (data / 'port_light.json').write_text(json.dumps({'manual_ports': [{'port': 9200 + index,
                    'label': ['NAS 管理口', '备份接收服务', '临时调试服务', '路由管理服务'][index], 'machine': 'localhost'}],
                    'hidden_ports': [], 'peers': [], 'settings': settings, 'port_rules': rules}))
                (data / 'listeners.json').write_text(json.dumps(listeners))
                env = {key: value for key, value in os.environ.items() if not key.startswith((
                    'PORT_LIGHT_', 'AUTH_', 'HIDDEN_', 'AGENT_', 'WEBHOOK_', 'COMPOSE_', 'DOCKER_', 'URL_', 'PORT_RANGE_', 'HISTORY_'))}
                env.update(PORT_LIGHT_DATA_DIR=str(data), COMPOSE_SCAN_DIR=str(compose), PORT_LIGHT_SETTINGS_SOURCE='file',
                    PORT_LIGHT_SCANNERS='listen,compose', HISTORY_RETENTION_DAYS='0', PORT_LIGHT_PORT=str(ports[index]))
                children.append(subprocess.Popen([sys.executable, '-m', 'scripts.preview_fleet', str(data), str(ports[index])], cwd=ROOT, env=env))
            for index, host_port in enumerate(ports):
                for attempt in range(100):
                    if children[index].poll() is not None:
                        raise RuntimeError('Demo instance exited during startup')
                    try:
                        request(host_port, '/api/ports')
                        break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Demo instance startup timed out')
                for label, start, ttl in [('CI 构建预留（24 小时）', 27000, 86400), ('集成环境预留（长期）', 27100, None)]:
                    request(host_port, '/api/reservations', {'count': 2, 'start': start, 'end': start + 9, 'label': label, 'ttl': ttl},
                        'POST', {'Idempotency-Key': secrets.token_urlsafe(32)})
            # Use the same validation and ID generation as the settings page.
            request(port, '/api/hosts', {'peers': [{'name': profile[0], 'description': profile[1],
                'url': f'http://127.0.0.1:{host_port}'} for profile, host_port in zip(PROFILES[1:], ports[1:])]}, 'PUT')
            print(f'http://127.0.0.1:{port}/ (four simulated machines; temporary data removed on exit)', flush=True)
            while all(child.poll() is None for child in children):
                time.sleep(.2)
            return 1
        except KeyboardInterrupt:
            return 0
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == '__main__':
    run_worker(sys.argv[1], int(sys.argv[2]))
