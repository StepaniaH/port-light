from pathlib import Path
import xml.etree.ElementTree as ET

from scripts.dockerhub_description import render
from port_light_client import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_dockerhub_links_resolve_to_release_tag_and_keep_screenshot():
    source = (ROOT / 'README.md').read_text()
    rendered = render(source, 'v0.8.2')
    assert 'src="https://raw.githubusercontent.com/StepaniaH/port-light/v0.8.2/docs/screenshots/dashboard.png"' in rendered
    assert '(https://github.com/StepaniaH/port-light/blob/v0.8.2/docs/cli.md)' in rendered
    assert '(https://hub.docker.com/r/stepaniah/port-light)' in rendered
    assert '(docs/' not in rendered


def test_unraid_template_and_profile_have_installation_metadata():
    template = ET.parse(ROOT / 'deploy/unraid/port-light.xml').getroot()
    profile = ET.parse(ROOT / 'ca_profile.xml').getroot()
    assert profile.findtext('Profile').strip()
    assert template.findtext('Repository') == f'stepaniah/port-light:v{__version__}'
    assert template.findtext('Privileged') == 'false'
    fields = {node.attrib['Target']: node for node in template.findall('Config')}
    assert fields['/data'].attrib['Mode'] == 'rw'
    assert fields['/host/proc'].text == '/proc'
    assert fields['AUTH_PASSWORD'].attrib['Mask'] == 'true'
    assert fields['AGENT_TOKEN'].attrib['Mask'] == 'true'


def test_dockerhub_reports_permission_failure_without_leaking_response(monkeypatch, capsys):
    from io import BytesIO
    import urllib.error
    from scripts import dockerhub_description as hub

    monkeypatch.setattr('sys.argv', ['dockerhub_description', '--publish'])
    monkeypatch.setenv('DOCKERHUB_USERNAME', 'example-user')
    monkeypatch.setenv('DOCKERHUB_TOKEN', 'secret-pat-value')
    for failed_method, expected_phase in [('POST', 'authentication'), ('PATCH', 'repository update')]:
        def request(method, path, body, token=''):
            if method == failed_method:
                raise urllib.error.HTTPError(path, 403, 'secret-error-detail', {}, BytesIO(b'secret-response-body'))
            return {'access_token': 'secret-bearer-value'}
        monkeypatch.setattr(hub, 'request', request)
        assert hub.main() == 1
        output = capsys.readouterr()
        assert f'Docker Hub {expected_phase} failed (HTTP 403)' in output.err
        assert ('Read, Write & Delete' in output.err) == (failed_method == 'PATCH')
        assert 'secret-' not in output.err + output.out
