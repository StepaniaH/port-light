"""Named allocation ranges and explicit Compose project assignments."""
from pydantic import BaseModel, Field, model_validator


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
            rule = by_project.get(compose.get("project_name"))
            if rule and not rule["start"] <= row["port"] <= rule["end"]:
                violations.append({"rule": rule["name"], "project": compose["project_name"],
                                   "service": compose["service_name"],
                                   "start": rule["start"], "end": rule["end"]})
        if violations:
            row["rule_violations"] = violations
