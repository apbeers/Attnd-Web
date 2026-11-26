"""`{% stylesheets "console" "agent" %}`: the one way a page links its CSS (design/bundles.py)."""

from django import template
from django.templatetags.static import static
from django.utils.html import format_html_join

from design import bundles

register = template.Library()


@register.simple_tag
def stylesheets(*names):
    hrefs = []
    for name in names:
        rel = f'design/{name}.css' if f'design/{name}.css' in bundles.BUNDLES else bundles.EXTRAS[name]
        hrefs.append((static(rel),))
    return format_html_join('\n', '<link rel="stylesheet" href="{}">', hrefs)
