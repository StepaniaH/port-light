"""YAML decoding and Compose document inheritance semantics."""
import re
import yaml

MAX_YAML_NODES = 50_000


class _ComposeLoader(yaml.SafeLoader):
    """Keep Compose ``!reset`` / ``!override`` so extends can replace, not merge."""

    def __init__(self, stream):
        super().__init__(stream)
        self._node_count = 0
        self._node_weights: dict[int, int] = {}

    def compose_document(self):
        self._node_weights.clear()
        return super().compose_document()

    def compose_node(self, parent, index):
        is_alias = self.check_event(yaml.AliasEvent)
        before = self._node_count
        node = super().compose_node(parent, index)
        added = self._node_weights.get(id(node), 1) if is_alias else 1
        self._node_count += added
        if self._node_count > MAX_YAML_NODES:
            raise yaml.YAMLError(f"YAML node budget ({MAX_YAML_NODES}) exceeded")
        if not is_alias and node is not None:
            self._node_weights[id(node)] = self._node_count - before
        return node


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


def _merge_list_or_scalar(existing, extra):
    if existing is None:
        return extra
    if isinstance(existing, list) and isinstance(extra, list):
        return [*existing, *extra]
    if isinstance(existing, list):
        return [*existing, extra]
    if isinstance(extra, list):
        return [existing, *extra]
    return [existing, extra]


def _is_replace_tag(val) -> bool:
    return isinstance(val, _ComposeTag) and val.name in ("reset", "override")


def _overlay_port_fields(base: dict, child: dict, *, preserve_tags: bool = False) -> dict:
    out = dict(base)
    if "extends" in child:
        child_ext = child.get("extends")
        if _is_replace_tag(child_ext):
            ext_val = _unwrap_compose(child_ext.value)
            if ext_val is None or child_ext.name == "reset":
                out.pop("extends", None)
            else:
                out["extends"] = ext_val
        elif child_ext is not None:
            base_ext = _unwrap_compose(out.get("extends"))
            if isinstance(base_ext, dict) and isinstance(child_ext, dict):
                out["extends"] = {**base_ext, **child_ext}
            else:
                out["extends"] = child_ext
    if "env_file" in child:
        base_env_raw = out.get("env_file")
        child_env_raw = child.get("env_file")
        if _is_replace_tag(child_env_raw):
            val = _unwrap_compose(child_env_raw.value)
            if val is None or child_env_raw.name == "reset" and not val:
                out["env_file"] = _ComposeTag("override", []) if preserve_tags else []
            else:
                out["env_file"] = _ComposeTag("override", val) if preserve_tags else val
        elif child_env_raw is not None:
            base_tagged = _is_replace_tag(base_env_raw)
            combined = _merge_list_or_scalar(_unwrap_compose(base_env_raw), _unwrap_compose(child_env_raw))
            out["env_file"] = _ComposeTag("override", combined) if (preserve_tags and base_tagged) else combined
    child_net_raw = child.get("network_mode")
    if isinstance(child_net_raw, _ComposeTag):
        if child_net_raw.name == "reset":
            if preserve_tags:
                out["network_mode"] = _ComposeTag("reset", None)
            else:
                out.pop("network_mode", None)
        else:
            net = _unwrap_compose(child_net_raw.value)
            if preserve_tags:
                out["network_mode"] = _ComposeTag("override", net)
            elif net:
                out["network_mode"] = net
            else:
                out.pop("network_mode", None)
    else:
        child_net = _unwrap_compose(child_net_raw)
        if child_net:
            out["network_mode"] = child_net
    for key in ("ports", "expose"):
        base_raw = base.get(key)
        child_val = child.get(key)
        if _is_replace_tag(child_val):
            entries = _port_entries(child_val.value)
            out[key] = _ComposeTag("override", entries) if preserve_tags else entries
            continue
        base_tagged = _is_replace_tag(base_raw)
        merged: list = []
        for src_val in (base_raw, child_val):
            val = _unwrap_compose(src_val)
            if isinstance(val, list):
                merged.extend(_unwrap_compose(item) for item in val)
            elif isinstance(val, dict):
                merged.extend(_port_entries(val))
            elif val is not None and val is not False:
                merged.append(val)
        if base_tagged:
            out[key] = _ComposeTag("override", merged) if preserve_tags else merged
        elif merged:
            out[key] = merged
    base_nets_raw = out.get("networks")
    child_nets_raw = child.get("networks")
    if _is_replace_tag(child_nets_raw):
        val = _unwrap_compose(child_nets_raw.value)
        out["networks"] = _ComposeTag("override", val) if preserve_tags else val
    elif child_nets_raw is not None:
        base_tagged = _is_replace_tag(base_nets_raw)
        child_nets = _unwrap_compose(child_nets_raw)
        base_nets = _unwrap_compose(base_nets_raw)
        if isinstance(base_nets, dict) and isinstance(child_nets, dict):
            merged_nets = {**base_nets, **child_nets}
        else:
            merged_nets = child_nets
        out["networks"] = _ComposeTag("override", merged_nets) if (preserve_tags and base_tagged) else merged_nets
    base_deploy_raw = out.get("deploy")
    child_deploy_raw = child.get("deploy")
    if child_deploy_raw is not None:
        base_tagged = _is_replace_tag(base_deploy_raw)
        base_deploy = _unwrap_compose(base_deploy_raw)
        if not isinstance(base_deploy, dict):
            base_deploy = {}
        if _is_replace_tag(child_deploy_raw):
            body = _unwrap_compose(child_deploy_raw.value)
            res = body if isinstance(body, dict) else {}
            out["deploy"] = _ComposeTag("override", res) if preserve_tags else res
        else:
            child_deploy = _unwrap_compose(child_deploy_raw)
            if not isinstance(child_deploy, dict):
                out["deploy"] = child_deploy
            else:
                merged_deploy = dict(base_deploy)
                merged_deploy.update({k: v for k, v in child_deploy.items() if k != "ports"})
                if "ports" in child_deploy or "ports" in base_deploy:
                    merged_ports = _overlay_port_fields(
                        {"ports": base_deploy.get("ports")},
                        {"ports": child_deploy.get("ports")},
                        preserve_tags=preserve_tags,
                    ).get("ports")
                    if merged_ports is not None:
                        merged_deploy["ports"] = merged_ports
                out["deploy"] = (
                    _ComposeTag("override", merged_deploy)
                    if (preserve_tags and base_tagged)
                    else merged_deploy
                )
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
        if _is_replace_tag(extra):
            out[key] = _unwrap_compose(extra.value)
            continue
        out[key] = _merge_list_or_scalar(_unwrap_compose(out.get(key)), extra)
    child_nets = child.get("networks")
    if child_nets is not None:
        out["networks"] = _overlay_port_fields(
            {"networks": out.get("networks")}, {"networks": child_nets},
        ).get("networks")
    base_svcs = _service_map(out.get("services"))
    child_svcs_raw = child.get("services")
    if _is_replace_tag(child_svcs_raw):
        out["services"] = _service_map(child_svcs_raw.value)
    elif isinstance(child_svcs_raw, dict) or isinstance(_unwrap_compose(child_svcs_raw), dict):
        child_svcs = child_svcs_raw if isinstance(child_svcs_raw, dict) else _unwrap_compose(child_svcs_raw)
        merged = dict(base_svcs)
        for name, cfg in child_svcs.items():
            tagged = _is_replace_tag(cfg)
            body = _unwrap_compose(cfg)
            if tagged:
                merged[name] = body if isinstance(body, dict) else {}
                continue
            prev = merged.get(name)
            if isinstance(prev, dict) and isinstance(body, dict):
                merged[name] = _overlay_port_fields(prev, body, preserve_tags=True)
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
