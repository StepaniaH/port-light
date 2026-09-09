"""YAML decoding and Compose document inheritance semantics."""
import re
import yaml

class _ComposeLoader(yaml.SafeLoader):
    """Keep Compose ``!reset`` / ``!override`` so extends can replace, not merge."""


class _ComposeTag:
    __slots__ = ("name", "value")

    def __init__(self, name: str, value):
        self.name = name
        self.value = value


def _unknown_compose_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node)
    else:
        value = None
    suffix = str(tag_suffix or "").lstrip("!").lower()
    if suffix in ("reset", "override"):
        return _ComposeTag(suffix, value)
    return value


_ComposeLoader.add_multi_constructor("!", _unknown_compose_tag)

# Compose uses YAML 1.2. PyYAML's 1.1 sexagesimal ints turn unquoted
# ``22:22`` / ``8080:22`` into numbers and we drop them as unpublished.
_ComposeLoader.yaml_implicit_resolvers = {
    key: list(resolvers)
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_INT_NO_SEXAGESIMAL = re.compile(
    r"""^(?:[-+]?0b[0-1_]+
        |[-+]?0[0-7_]+
        |[-+]?(?:0|[1-9][0-9_]*)
        |[-+]?0x[0-9a-fA-F_]+)$""",
    re.X,
)
for _ch, _resolvers in list(_ComposeLoader.yaml_implicit_resolvers.items()):
    _ComposeLoader.yaml_implicit_resolvers[_ch] = [
        (tag, regexp) for tag, regexp in _resolvers
        if tag != "tag:yaml.org,2002:int"
    ]
    if not _ComposeLoader.yaml_implicit_resolvers[_ch]:
        del _ComposeLoader.yaml_implicit_resolvers[_ch]
_ComposeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    _INT_NO_SEXAGESIMAL,
    list("-+0123456789"),
)



def _unwrap_compose(val):
    return val.value if isinstance(val, _ComposeTag) else val


def _service_map(raw) -> dict:
    data = _unwrap_compose(raw)
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for name, cfg in data.items():
        body = _unwrap_compose(cfg)
        if isinstance(body, dict):
            out[name] = body
    return out


def _port_entries(ports_cfg) -> list:
    ports_cfg = _unwrap_compose(ports_cfg)
    if ports_cfg is None or ports_cfg is False:
        return []
    if isinstance(ports_cfg, dict):
        if any(k in ports_cfg for k in ("published", "target", "host_ip", "protocol", "mode")):
            return [ports_cfg]
        return [{k: v} for k, v in ports_cfg.items()]
    if isinstance(ports_cfg, list):
        return [_unwrap_compose(item) for item in ports_cfg]
    return [ports_cfg]


def _overlay_port_fields(base: dict, child: dict) -> dict:
    out = dict(base)
    child_net_raw = child.get("network_mode")
    if isinstance(child_net_raw, _ComposeTag):
        if child_net_raw.name == "reset":
            out.pop("network_mode", None)
        else:
            net = _unwrap_compose(child_net_raw.value)
            if net:
                out["network_mode"] = net
            else:
                out.pop("network_mode", None)
    else:
        child_net = _unwrap_compose(child_net_raw)
        if child_net:
            out["network_mode"] = child_net
    for key in ("ports", "expose"):
        child_val = child.get(key)
        if isinstance(child_val, _ComposeTag) and child_val.name in ("reset", "override"):
            out[key] = _port_entries(child_val.value)
            continue
        merged: list = []
        for src in (base, child):
            val = _unwrap_compose(src.get(key))
            if isinstance(val, list):
                merged.extend(_unwrap_compose(item) for item in val)
            elif isinstance(val, dict):
                merged.extend(_port_entries(val))
            elif val is not None and val is not False:
                merged.append(val)
        if merged:
            out[key] = merged
    child_nets_raw = child.get("networks")
    if isinstance(child_nets_raw, _ComposeTag) and child_nets_raw.name in ("reset", "override"):
        out["networks"] = _unwrap_compose(child_nets_raw.value)
    elif child_nets_raw is not None:
        child_nets = _unwrap_compose(child_nets_raw)
        base_nets = _unwrap_compose(out.get("networks"))
        if isinstance(base_nets, dict) and isinstance(child_nets, dict):
            merged_nets = dict(base_nets)
            merged_nets.update(child_nets)
            out["networks"] = merged_nets
        else:
            out["networks"] = child_nets
    child_deploy_raw = child.get("deploy")
    if child_deploy_raw is not None:
        base_deploy = _unwrap_compose(out.get("deploy"))
        if not isinstance(base_deploy, dict):
            base_deploy = {}
        if isinstance(child_deploy_raw, _ComposeTag) and child_deploy_raw.name in ("reset", "override"):
            body = _unwrap_compose(child_deploy_raw.value)
            out["deploy"] = body if isinstance(body, dict) else {}
        else:
            child_deploy = _unwrap_compose(child_deploy_raw)
            if not isinstance(child_deploy, dict):
                out["deploy"] = child_deploy
            else:
                merged_deploy = dict(base_deploy)
                merged_deploy.update({k: v for k, v in child_deploy.items() if k != "ports"})
                if "ports" in child_deploy:
                    merged_deploy["ports"] = _overlay_port_fields(
                        {"ports": base_deploy.get("ports")},
                        {"ports": child_deploy.get("ports")},
                    ).get("ports")
                out["deploy"] = merged_deploy
    return out


def _overlay_compose_docs(base: dict, child: dict) -> dict:
    """Merge a Compose override (or a later YAML document) onto *base*."""
    out = dict(base)
    child_name = child.get("name")
    if isinstance(child_name, str) and child_name.strip():
        out["name"] = child_name
    for key in ("include", "env_file"):
        extra = child.get(key)
        if extra is None:
            continue
        existing = out.get(key)
        if existing is None:
            out[key] = extra
        elif isinstance(existing, list) and isinstance(extra, list):
            out[key] = [*existing, *extra]
        elif isinstance(existing, list):
            out[key] = [*existing, extra]
        elif isinstance(extra, list):
            out[key] = [existing, *extra]
        else:
            out[key] = [existing, extra]
    child_nets = child.get("networks")
    if child_nets is not None:
        out["networks"] = _overlay_port_fields(
            {"networks": out.get("networks")}, {"networks": child_nets},
        ).get("networks")
    base_svcs = _service_map(out.get("services"))
    child_svcs_raw = child.get("services")
    if isinstance(child_svcs_raw, _ComposeTag) and child_svcs_raw.name in ("reset", "override"):
        out["services"] = _service_map(child_svcs_raw.value)
    elif isinstance(child_svcs_raw, dict) or isinstance(_unwrap_compose(child_svcs_raw), dict):
        child_svcs = child_svcs_raw if isinstance(child_svcs_raw, dict) else _unwrap_compose(child_svcs_raw)
        merged = dict(base_svcs)
        for name, cfg in child_svcs.items():
            tagged = isinstance(cfg, _ComposeTag) and cfg.name in ("reset", "override")
            body = _unwrap_compose(cfg)
            if tagged:
                merged[name] = body if isinstance(body, dict) else {}
                continue
            prev = merged.get(name)
            if isinstance(prev, dict) and isinstance(body, dict):
                merged[name] = _overlay_port_fields(prev, body)
            elif isinstance(body, dict):
                merged[name] = body
        out["services"] = merged
    return out


def _load_yaml(text: str):
    docs = [doc for doc in yaml.load_all(text, Loader=_ComposeLoader) if doc is not None]
    if not docs:
        return None
    merged = None
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        merged = doc if merged is None else _overlay_compose_docs(merged, doc)
    return merged
