from types import SimpleNamespace

import pytest

from scripts import dev, preview_fleet


@pytest.mark.parametrize("preview", ["single", "fleet"])
@pytest.mark.parametrize("explicit", [None, "http://127.0.0.1:48102", ""])
def test_preview_preserves_the_selected_ai_proxy(tmp_path, monkeypatch, preview, explicit):
    for name in ("PORT_LIGHT_AI_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:48101")
    if explicit is not None:
        monkeypatch.setenv("PORT_LIGHT_AI_PROXY", explicit)
    configured = []

    def launch(*_args, env, **_kwargs):
        configured.append(env.get("PORT_LIGHT_AI_PROXY"))
        raise KeyboardInterrupt

    monkeypatch.setattr(dev.subprocess, "Popen", launch)
    if preview == "fleet":
        assert preview_fleet.run_fleet(0) == 0
    else:
        with pytest.raises(KeyboardInterrupt):
            dev.serve(SimpleNamespace(port=0), tmp_path, tmp_path, demo=True)
    assert configured == [explicit if explicit is not None else "http://127.0.0.1:48101"]


@pytest.mark.parametrize("locale", ["en", "zh-CN", "zh-TW", "de", "es", "fr", "ja"])
def test_sample_names_follow_the_locale_without_translating_user_edits(locale):
    catalogs = preview_fleet.sample_catalogs()
    chinese = catalogs['zh-CN']
    example = {
        'values': {'host_name': chinese['nasName'], 'host_description': chinese['nasDescription']},
        'peers': [{'name': chinese['appsName'], 'description': 'My NAS · 用户自己写的描述'}],
        'ports': [{'manual_label': chinese['nasPort'], 'reservation': {'label': chinese['temporaryReservation']}}],
        'url': chinese['nasName'],
        'model': chinese['nasName'],
    }
    result = preview_fleet.localize_samples(example, locale)
    assert result['values']['host_name'] == catalogs[locale]['nasName']
    assert result['values']['host_description'] == catalogs[locale]['nasDescription']
    assert result['peers'][0]['name'] == catalogs[locale]['appsName']
    assert result['peers'][0]['description'] == example['peers'][0]['description']
    assert result['ports'][0]['manual_label'] == catalogs[locale]['nasPort']
    assert result['ports'][0]['reservation']['label'] == catalogs[locale]['temporaryReservation']
    assert result['url'] == example['url']
    assert result['model'] == example['model']
    assert preview_fleet.localize_samples(result, 'zh-CN') == example
