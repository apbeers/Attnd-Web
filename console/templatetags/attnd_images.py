"""``{% img url width height alt="" crop=False sizes="" lazy=True priority=False class="" %}``: every content image the
platform draws (attnd/images.py). Right-sized Cloudinary (or local) copies with a 1x / 2x / 3x ``srcset``, explicit
width and height (nothing shifts), ``loading=lazy`` + ``decoding=async`` below the fold, ``fetchpriority=high`` for
the first visible image. Static files and SVGs pass through unchanged (still with their dimensions)."""
from django import template
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from attnd.images import responsive

register = template.Library()


@register.simple_tag
def img(url, width, height=None, alt='', crop=False, sizes='', lazy=True, priority=False, cls='', **attrs):
    if not url:
        return ''
    width, height = int(width), int(height) if height else None
    src, srcset = responsive(url, width, height, bool(crop))
    parts = [('src', src)]
    if srcset:
        parts += [('srcset', srcset), ('sizes', sizes or f'{width}px')]
    parts += [('width', width)] + ([('height', height)] if height else [])
    parts.append(('alt', alt or ''))
    if cls:
        parts.append(('class', cls))
    if priority:
        parts.append(('fetchpriority', 'high'))
    elif lazy:
        parts.append(('loading', 'lazy'))
    parts.append(('decoding', 'async'))
    for key, value in attrs.items():
        parts.append((key.replace('_', '-'), value))
    return format_html('<img{}>', format_html_join('', ' {}="{}"', parts) if parts else mark_safe(''))
