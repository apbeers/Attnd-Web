"""Serves the generated bundles (design/bundles.py) as ordinary static files: runserver and the test client find them
here, collectstatic copies them (then hashes, minifies and compresses them like the rest)."""

import os
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.finders import BaseFinder
from django.core.files.storage import FileSystemStorage

from design import bundles

BUILD_DIR = Path(getattr(settings, 'DESIGN_BUILD_DIR', Path(settings.BASE_DIR) / '.design-build'))


def _stale(target, sources):
    if not target.exists():
        return True
    built = target.stat().st_mtime
    return any(os.path.getmtime(path) > built for _, path in sources)


def ensure(bundle):
    target = BUILD_DIR / bundle
    if _stale(target, bundles.sources(bundle)):
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix('.tmp')
        tmp.write_text(bundles.build(bundle), encoding='utf-8')
        os.replace(tmp, target)
    return target


class BundleFinder(BaseFinder):
    def check(self, **kwargs):
        return []

    def find(self, path, find_all=False, **kwargs):
        if path not in bundles.BUNDLES:
            return [] if find_all else None
        found = str(ensure(path))
        return [found] if find_all else found

    def list(self, ignore_patterns):
        storage = FileSystemStorage(location=str(BUILD_DIR))
        for bundle in bundles.BUNDLES:
            ensure(bundle)
            yield bundle, storage
