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
from functools import lru_cache

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


@lru_cache(maxsize=1)
def sample_catalogs():
    return {locale: json.loads((ROOT / 'frontend' / 'locales' / f'{locale}.json').read_text())['preview']
            for locale in ('en', 'zh-CN', 'zh-TW', 'de', 'es', 'fr', 'ja')}


def localize_samples(document, locale):
    """Translate unchanged example text, preserving user edits and technical values."""
    catalogs = sample_catalogs()
    selected = catalogs.get(locale, catalogs['en'])
    translations = {text: selected[key] for catalog in catalogs.values() for key, text in catalog.items()}
    fields = {'name', 'description', 'host_name', 'host_description', 'label', 'manual_label'}

    def visit(value, field=None):
        if isinstance(value, dict):
            return {key: visit(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item, field) for item in value]
        if isinstance(value, str) and field in fields:
            return translations.get(value, value)
        return value

    return visit(document)


def create_app():
    import backend.main as core
    from backend import settings
    from starlette.responses import JSONResponse

    @core.app.middleware('http')
    async def localized_examples(request, call_next):
        if not request.url.path.startswith('/api/'):
            return await call_next(request)
        # Sample names depend on the selected language as well as occupancy.
        request.scope['headers'] = [(key, value) for key, value in request.scope['headers']
                                    if key.lower() != b'if-none-match']
        response = await call_next(request)
        if not response.headers.get('content-type', '').startswith('application/json'):
            return response
        body = b''.join([chunk async for chunk in response.body_iterator])
        locale = settings.resolve()[0]['locale']
        if locale == 'auto':
            requested = [item.split(';')[0].strip() for item in request.headers.get('accept-language', 'en').split(',')]
            locale = next((item if item in sample_catalogs() else item.split('-')[0]
                           for item in requested if item in sample_catalogs() or item.split('-')[0] in sample_catalogs()), 'en')
        headers = {key: value for key, value in response.headers.items()
                   if key.lower() not in {'content-length', 'etag'}}
        headers['Cache-Control'] = 'no-store'
        document = localize_samples(json.loads(body), locale)
        if request.url.path == '/api/meta' and response.status_code == 200:
            document['preview'] = {'samples': sample_catalogs()}
        return JSONResponse(document,
                            status_code=response.status_code, headers=headers)

    return core.app


def run_worker(data: str, port: int):
    application = create_app()
    import backend.main as core
    from backend.port_scanner import ListeningPort
    import uvicorn
    listeners = json.loads((Path(data) / 'listeners.json').read_text())
    core.scan_listening_ports = lambda **kwargs: [ListeningPort(p, 'tcp', '127.0.0.1' if p in (5432, 6379) else '0.0.0.0', name) for p, name in listeners]
    core.host_listen_trusted = lambda: True
    uvicorn.run(application, host='127.0.0.1', port=port, log_level='warning')


def run_fleet(port: int) -> int:
    from scripts.dev import configure_ai_proxy

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
                configure_ai_proxy(env)
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
