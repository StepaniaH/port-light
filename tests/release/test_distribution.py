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
