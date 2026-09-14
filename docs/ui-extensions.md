# Local UI extensions

An application composed with the Port-Light FastAPI app can register links to
its own pages before startup:

```python
from backend.main import app
from backend.ui_extensions import register_ui_link

# Register the application's page route separately.
register_ui_link(
    app,
    key="tools",
    path="/tools/",
    label="Tools",
    labels={"zh-CN": "工具"},
)
```

The dashboard reads these links from `GET /api/meta` and places them beside its
existing toolbar actions. The default app registers no links. Labels follow the
selected interface language, falling back to `label`. HTML is rendered as text.

The hook accepts up to four unique keys. Paths are absolute local paths using
letters, digits, underscores, hyphens and slashes, without query strings or
fragments. Labels are limited to 40 characters. Register routes on the same app
to preserve its authentication and security middleware; the link itself grants
no permissions and does not register a route or alter the scanner lifecycle.

This hook only provides navigation. Extensions remain responsible for their own
data handling, lifecycle, and authorization beyond the core's instance login.
