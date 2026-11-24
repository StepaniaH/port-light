"""Named allocation ranges and explicit Compose project assignments."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, model_validator

_RULE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
_RULE_KEYS = frozenset({"name", "start", "end", "projects"})


def _valid_project_ref(value: str) -> bool:
    if "\\" in value or "\x00" in value or any(ord(ch) < 32 for ch in value):
        return False
    if value.startswith("/") or value.endswith("/") or "//" in value:
        return False
    parts = value.split("/")
    return not any(part in ("", ".", "..") for part in parts)


def _normalize_legacy_project_ref(value: str) -> str:
    candidate = value
    while candidate.startswith("./"):
        candidate = candidate[2:]
    return candidate.rstrip("/")


def load_stored_rules(raw_rules) -> list[dict]:
    """Validate stored rules structurally and normalize safe legacy project refs."""
    if not isinstance(raw_rules, list) or len(raw_rules) > 64:
        raise ValueError("port_rules must be a list of at most 64 rules")
    parsed: list[dict] = []
    seen_names: set[str] = set()
    seen_raw_projects: set[str] = set()
    for entry in raw_rules:
        if not isinstance(entry, dict) or not set(entry) <= _RULE_KEYS:
            raise ValueError("invalid port rule entry")
        name = entry.get("name")
        if not isinstance(name, str) or not 1 <= len(name) <= 64 or not _RULE_NAME_RE.fullmatch(name):
            raise ValueError("invalid port rule name")
        if name in seen_names:
            raise ValueError("rule names must be unique")
        seen_names.add(name)
        start = entry.get("start")
        end = entry.get("end")
        if type(start) is not int or type(end) is not int or not (1 <= start <= end <= 65535):
            raise ValueError("invalid port rule range")
        raw_projects = entry.get("projects", [])
        if not isinstance(raw_projects, list) or len(raw_projects) > 64:
            raise ValueError("invalid port rule projects")
        cleaned_projects: list[str] = []
        for item in raw_projects:
            if not isinstance(item, str):
                raise ValueError("project entries must be strings")
            stripped = item.strip()
            if not stripped or len(stripped) > 256 or "\x00" in stripped:
                raise ValueError("project names must contain 1–256 non-NUL characters")
            if stripped not in cleaned_projects:
                cleaned_projects.append(stripped)
        for project in cleaned_projects:
            if project in seen_raw_projects:
                raise ValueError("assign each project to at most one rule")
            seen_raw_projects.add(project)
        parsed.append({"name": name, "start": start, "end": end, "projects": cleaned_projects})

    # Collect raw and valid-normalized claimers across every rule so alias collision handling is order-independent.
    claimers: dict[str, set[int]] = {}
    for idx, rule in enumerate(parsed):
        for project in rule["projects"]:
            claimers.setdefault(project, set()).add(idx)
            candidate = _normalize_legacy_project_ref(project)
            if candidate and _valid_project_ref(candidate):
                claimers.setdefault(candidate, set()).add(idx)

    out: list[dict] = []
    for idx, rule in enumerate(parsed):
        normalized_projects: list[str] = []
        for project in rule["projects"]:
            candidate = _normalize_legacy_project_ref(project)
            if (
                candidate
                and _valid_project_ref(candidate)
                and (candidate == project or claimers.get(candidate) == {idx})
            ):
                chosen = candidate
            else:
                chosen = project
            if chosen not in normalized_projects:
                normalized_projects.append(chosen)
        out.append({
            "name": rule["name"],
            "start": rule["start"],
            "end": rule["end"],
            "projects": normalized_projects,
        })
    return out


def validate_rule_update(raw_body, existing_rules: list[dict]) -> list[dict]:
    """Validate a PUT /api/port-rules payload while letting untouched legacy entries survive."""
    if not isinstance(raw_body, dict) or set(raw_body) != {"rules"}:
        raise ValueError("body must contain only a rules list")
    raw_rules = raw_body["rules"]
    if not isinstance(raw_rules, list) or len(raw_rules) > 64:
        raise ValueError("rules must be a list of at most 64 items")

    allowed_legacy = {
        (r.get("name"), p)
        for r in existing_rules
        if isinstance(r, dict)
        for p in r.get("projects", [])
        if isinstance(p, str)
    }

    out: list[dict] = []
    seen_names: set[str] = set()
    seen_projects: set[str] = set()
    for entry in raw_rules:
        if not isinstance(entry, dict) or not set(entry) <= _RULE_KEYS:
            raise ValueError("invalid port rule entry")
        if "name" not in entry or "start" not in entry or "end" not in entry:
            raise ValueError("rule entries must include name, start, and end")
        name = entry["name"]
        if type(name) is not str or not 1 <= len(name) <= 64 or not _RULE_NAME_RE.fullmatch(name):
            raise ValueError("invalid port rule name")
        if name in seen_names:
            raise ValueError("rule names must be unique")
        seen_names.add(name)
        start = entry["start"]
        end = entry["end"]
        if type(start) is not int or type(end) is not int or not (1 <= start <= 65535 and 1 <= end <= 65535):
            raise ValueError("invalid port rule range")
        if end < start:
            raise ValueError("end must be greater than or equal to start")
        raw_projects = entry.get("projects", [])
        if not isinstance(raw_projects, list) or len(raw_projects) > 64:
            raise ValueError("projects must be a list of at most 64 strings")
        if any(type(p) is not str for p in raw_projects):
            raise ValueError("project entries must be strings")
        projects = list(dict.fromkeys(p.strip() for p in raw_projects))
        if any(not p or len(p) > 256 for p in projects):
            raise ValueError("project names must contain 1–256 characters")
        if any(not (_valid_project_ref(p) or (name, p) in allowed_legacy) for p in projects):
            raise ValueError("project entries must be clean names or relative paths")
        for p in projects:
            if p in seen_projects:
                raise ValueError("assign each project to at most one rule")
            seen_projects.add(p)
        out.append({"name": name, "start": start, "end": end, "projects": projects})
    return out


class PortRule(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    start: int = Field(ge=1, le=65535)
    end: int = Field(ge=1, le=65535)
    projects: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end < self.start:
            raise ValueError("end must be greater than or equal to start")
        self.projects = list(dict.fromkeys(p.strip() for p in self.projects))
        if any(not p or len(p) > 256 for p in self.projects):
            raise ValueError("project names must contain 1–256 characters")
        if any(not _valid_project_ref(p) for p in self.projects):
            raise ValueError("project entries must be clean names or relative paths")
        return self


class RuleDocument(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    rules: list[PortRule] = Field(max_length=64)

    @model_validator(mode="after")
    def unique_names(self):
        if len({r.name for r in self.rules}) != len(self.rules):
            raise ValueError("rule names must be unique")
        projects = [p for r in self.rules for p in r.projects]
        if len(set(projects)) != len(projects):
            raise ValueError("assign each project to at most one rule")
        return self


def allocation_range(rules: list[dict], name: str, start: int | None, end: int | None):
    rule = next((r for r in rules if r["name"] == name), None)
    if rule is None:
        raise ValueError("unknown port rule")
    lo = rule["start"] if start is None else start
    hi = rule["end"] if end is None else end
    if not rule["start"] <= lo <= hi <= rule["end"]:
        raise ValueError("requested range is outside the selected rule")
    return lo, hi


def annotate(rows: list[dict], rules: list[dict]) -> None:
    by_project = {p: r for r in rules for p in r.get("projects", [])}
    for row in rows:
        violations = []
        for compose in row.get("compose_configs", []):
            project_dir = compose.get("project_dir")
            project_name = compose.get("project_name")
            if project_dir and project_dir in by_project:
                rule = by_project[project_dir]
                matched = project_dir
            else:
                rule = by_project.get(project_name)
                matched = project_name or project_dir or ""
            if rule and not rule["start"] <= row["port"] <= rule["end"]:
                violations.append({"rule": rule["name"], "project": matched,
                                   "service": compose["service_name"],
                                   "start": rule["start"], "end": rule["end"]})
        if violations:
            row["rule_violations"] = violations
