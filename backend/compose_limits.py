"""Per-scan expansion limits shared by Compose readers."""
from dataclasses import dataclass
from contextvars import ContextVar

class ComposeWouldFail(Exception):
    """Compose would refuse this project (required interp / env_file / include)."""


class ComposePortLimit(ComposeWouldFail):
    """A bounded, non-secret diagnostic suitable for display to operators."""
    def __init__(self, code: str, limit: int, spec: str = "", filepath: str = ""):
        super().__init__(f"port expansion limit ({limit}); reduce the scan scope or split the range")
        self.code, self.limit, self.spec, self.filepath = code, limit, spec, filepath


@dataclass
class _PortBudget:
    limit: int = 65536
    expanded: int = 0
    emitted: int = 0


_port_budget: ContextVar[_PortBudget | None] = ContextVar("compose_port_budget", default=None)


def _consume_ports(count: int, *, emitted: bool = False, filepath: str = "") -> None:
    budget = _port_budget.get()
    if budget is None:
        return
    attribute = "emitted" if emitted else "expanded"
    total = getattr(budget, attribute) + count
    if total > budget.limit:
        raise ComposePortLimit("port_budget", budget.limit, filepath=filepath)
    setattr(budget, attribute, total)
