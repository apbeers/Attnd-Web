#!/usr/bin/env python3
"""
Upgrade every backend dependency to its latest release.

    uv run python scripts/upgrade_deps.py          # rewrite pins + relock + run tests
    uv run python scripts/upgrade_deps.py --check  # only list what is outdated

1. Rewrites each exact pin in pyproject.toml ([project].dependencies) to the newest
   non-prerelease version on PyPI.
2. Runs `uv lock --upgrade` so transitive dependencies move too.
3. Runs the test suite (skip with --no-tests).

Used by .github/workflows/upgrade-deps.yml, which runs weekly and opens a pull request.
Standard library only.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / 'pyproject.toml'
PIN = re.compile(r'^(\s*")([A-Za-z0-9_.\-]+)(\[[^\]]+\])?==([^"]+)(",?\s*)$')
PRERELEASE = re.compile(r'(a|b|rc|dev)\d*', re.I)


def latest(name):
    with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/json', timeout=20) as resp:
        data = json.load(resp)
    version = data['info']['version']
    if PRERELEASE.search(version):
        stable = [v for v, files in data['releases'].items()
                  if files and not PRERELEASE.search(v) and not any(f.get('yanked') for f in files)]
        stable.sort(key=lambda v: [int(p) if p.isdigit() else p for p in re.split(r'[.\-]', v)])
        version = stable[-1] if stable else version
    return version


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='only report outdated pins')
    parser.add_argument('--no-tests', action='store_true')
    args = parser.parse_args()

    lines = PYPROJECT.read_text().splitlines(keepends=True)
    in_deps, changes, out = False, [], []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('dependencies = ['):
            in_deps = True
        elif in_deps and stripped == ']':
            in_deps = False
        m = PIN.match(line) if in_deps else None
        if m:
            prefix, name, extras, current, suffix = m.groups()
            new = latest(name)
            if new != current:
                changes.append((name, current, new))
                line = f'{prefix}{name}{extras or ""}=={new}{suffix}'
        out.append(line)

    for name, old, new in changes:
        print(f'{name}: {old} -> {new}')
    if not changes:
        print('All direct dependencies are current.')
    if args.check:
        return 1 if changes else 0

    PYPROJECT.write_text(''.join(out))
    subprocess.run(['uv', 'lock', '--upgrade'], cwd=ROOT, check=True)
    subprocess.run(['uv', 'sync'], cwd=ROOT, check=True)
    if not args.no_tests:
        env_cmd = ['uv', 'run', 'python', 'manage.py', 'test']
        result = subprocess.run(env_cmd, cwd=ROOT, env={**__import__('os').environ, 'DEBUG': '1',
                                                         'API_KEY': 'ci-test-key'})
        return result.returncode
    return 0


if __name__ == '__main__':
    sys.exit(main())
