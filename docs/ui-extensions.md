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
existing toolbar actions. The default app registers its bundled troubleshooting
workspace. Labels follow the selected interface language, falling back to
`label`. HTML is rendered as text.

The hook accepts up to four unique keys. Paths are absolute local paths using
letters, digits, underscores, hyphens and slashes, without query strings or
fragments. Labels are limited to 40 characters. Register routes on the same app
to preserve its authentication and security middleware; the link itself grants
no permissions and does not register a route or alter the scanner lifecycle.

## Shared workspaces

For an integrated view, add this optional argument to `register_ui_link`:

```python
workspace={
    "api": 1,
    "entry": "/tools/assets/main.js",
    "stylesheet": "/tools/assets/main.css",
    "port_action": True,
}
```

The toolbar opens `#/workspace/tools` inside the existing application shell.
`port_action` also adds a link from local port details, passing the port as
`#/workspace/tools/port/8080`. Remote host observations are not passed implicitly.
Assets must be local `.js` and `.css` files under the registered path. The
bundled workbench host adds a hash of its static assets as `revision` for asset
cache keys. Callers cannot supply that revision through registration.

The entry is an ES module exporting `mount({ root, locale, port, signal,
enhanceSelects, revision })`. Render inside `root`, inherit the core CSS variables,
and scope module styles to its content. `locale` is the current interface language;
module translations remain with the module. Use `revision` when loading additional
assets. Single-select controls share the core's in-page menu, including keyboard
navigation, labels, disabled options, focus and input/change events.

`mount` may be asynchronous and return a cleanup function. Navigation aborts
`signal`, removes the content and stylesheet, and runs cleanup. Use that signal for
requests and listeners, stop polling on abort, and ignore late responses. Leaving
a view must not repeat or cancel a server-side job implicitly.

Extensions remain responsible for data handling, lifecycle, and authorization
beyond the core's instance login. Navigation registration grants no capability.
