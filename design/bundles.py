"""The stylesheets pages load, built from one set of layers (docs/design_system.md).

Every page gets exactly one bundle: the shared layers (tokens → base → layout → components → motion) followed by its
area's own file. The bundles are generated files served under `design/` (design.finders.BundleFinder), so
collectstatic hashes, minifies and compresses them like any static file and pages make one stylesheet request.

Areas only add what is theirs (console chrome and features, the public site's marketing sections). Shared things
(colours, spacing, type, breakpoints, layout primitives, buttons, panels, fields…) live once, in the layers."""

LAYERS = ('design/tokens.css', 'design/base.css', 'design/layout.css', 'design/components.css', 'design/motion.css')

BUNDLES = {
    'design/console.css': LAYERS + ('console/console.css',),   # the console, sign-in pages, Studio, Platform
    'design/site.css': LAYERS + ('home/site.css',),             # the public site: home, join, privacy, brand
}

# Extra area files a page adds after its bundle (only where they're used).
EXTRAS = {
    'agent': 'agent/agent.css',                  # Ask Attnd's panel and full-screen chat
    'onboarding': 'home/onboarding.css',         # the home page's hero, gallery and composer (also the console's)
    'architecture': 'platform_admin/architecture.css',
}


def source_path(rel):
    """The file behind a static name ('console/console.css' → its app's static folder)."""
    from django.contrib.staticfiles import finders
    path = finders.find(rel)
    if path is None:
        raise FileNotFoundError(rel)
    return path


def sources(bundle):
    from django.contrib.staticfiles import finders
    out = []
    for rel in BUNDLES[bundle]:
        path = finders.find(rel)
        if path is None:
            raise FileNotFoundError(rel)
        out.append((rel, path))
    return out


def build(bundle):
    """The bundle's text: each source in order, labelled."""
    chunks = []
    for rel, path in sources(bundle):
        with open(path, encoding='utf-8') as f:
            chunks.append(f'/* ---- {rel} ---- */\n{f.read().rstrip()}\n')
    return '\n'.join(chunks)


def text(name):
    """A bundle or an extra by its short name ('console', 'site', 'agent'…): what tests and the MCP App read."""
    if f'design/{name}.css' in BUNDLES:
        return build(f'design/{name}.css')
    from django.contrib.staticfiles import finders
    with open(finders.find(EXTRAS[name]), encoding='utf-8') as f:
        return f.read()
