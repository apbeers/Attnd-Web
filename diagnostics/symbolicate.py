"""iOS symbolication (API-013) for MetricKit reports.

The server can't run `atos`, and the dSYMs live as GitHub Actions artifacts of attnd-ios. So the panel's
"Symbolicate" action dispatches attnd-ios's `symbolicate.yml` workflow (GitHub REST API, token in
settings.GITHUB_SYMBOLICATE_TOKEN) with the report id, the build and a one-time callback token. The workflow
fetches the frames to look up (`GET /api/diagnostics/symbolicate/<id>/`), downloads the build's dSYM artifact,
runs `atos` on a macOS runner and posts the symbols back (`POST` to the same URL). The token is single use and
expires after 2 hours; only its SHA-256 is stored.

MetricKit call stacks (`MXCallStackTree.jsonRepresentation()`): callStacks[].callStackRootFrames[], each
frame {binaryUUID, binaryName, offsetIntoBinaryTextSegment, address, subFrames: [...]}; the root frame is
the top of the stack and subFrames go towards the caller.
"""
import hashlib
import json
import logging
import secrets
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from diagnostics.models import DiagnosticReport

logger = logging.getLogger(__name__)
TOKEN_TTL = timedelta(hours=2)
MAX_FRAMES = 500


def _walk(frame, depth=0):
    """A frame and its subFrames chain, top of the stack first."""
    while isinstance(frame, dict) and depth < 512:
        yield frame
        subs = frame.get('subFrames')
        frame = subs[0] if isinstance(subs, list) and subs else None
        depth += 1


def threads_of(payload):
    """[(attributed, [frame dicts])] for every call stack of a MetricKit diagnostic (crash/hang/cpu/...)."""
    tree = payload.get('callStackTree') if isinstance(payload, dict) else None
    stacks = tree.get('callStacks') if isinstance(tree, dict) else None
    out = []
    for stack in stacks if isinstance(stacks, list) else []:
        if not isinstance(stack, dict):
            continue
        roots = stack.get('callStackRootFrames') or []
        frames = []
        for root in roots if isinstance(roots, list) else []:
            frames.extend(_walk(root))
        out.append((bool(stack.get('threadAttributed')), frames))
    out.sort(key=lambda t: not t[0])  # the crashing / attributed thread first
    return out


def _frame_key(frame):
    uuid = str(frame.get('binaryUUID') or '').upper()
    offset = frame.get('offsetIntoBinaryTextSegment')
    if not uuid or not isinstance(offset, int):
        return None
    return uuid, offset


def frames_of(payload):
    """Unique frames to symbolicate: [{uuid, binary, offset}], attributed thread first, at most MAX_FRAMES."""
    seen, out = set(), []
    for _, frames in threads_of(payload):
        for frame in frames:
            key = _frame_key(frame)
            if key and key not in seen:
                seen.add(key)
                out.append({'uuid': key[0], 'binary': str(frame.get('binaryName') or '?')[:80], 'offset': key[1]})
                if len(out) >= MAX_FRAMES:
                    return out
    return out


def render(report):
    """The report's stacks as text, with symbols where the workflow found them."""
    payload = report.payload or {}
    symbols = report.symbolication or {}
    meta = payload.get('diagnosticMetaData') if isinstance(payload.get('diagnosticMetaData'), dict) else {}
    lines = [f'{k}: {v}' for k, v in meta.items() if isinstance(v, (str, int, float))]
    for number, (attributed, frames) in enumerate(threads_of(payload)):
        lines.append('')
        lines.append(f'Thread {number}{" (crashed / attributed)" if attributed else ""}')
        for index, frame in enumerate(frames):
            key = _frame_key(frame)
            binary = str(frame.get('binaryName') or '?')
            offset = frame.get('offsetIntoBinaryTextSegment')
            symbol = symbols.get(f'{key[0]}:{key[1]}') if key else None
            where = symbol or (f'+ {offset:#x}' if isinstance(offset, int) else '?')
            lines.append(f'{index:<3} {binary:<28} {where}')
    return '\n'.join(lines).strip()


def configured():
    return bool(settings.GITHUB_SYMBOLICATE_TOKEN)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def request(report, site_url=None):
    """Dispatch the workflow for one iOS report. Returns (ok, message)."""
    if not configured():
        return False, 'GITHUB_SYMBOLICATE_TOKEN is not set (SETUP.md → Diagnostics).'
    if report.platform != 'ios' or not frames_of(report.payload or {}):
        return False, 'Only iOS reports with a MetricKit call stack can be symbolicated.'
    token = secrets.token_urlsafe(32)
    repo = settings.GITHUB_SYMBOLICATE_REPO
    body = json.dumps({'ref': settings.GITHUB_SYMBOLICATE_REF, 'inputs': {
        'report_id': str(report.pk), 'build': report.build or '', 'version': report.app_version or '',
        'server': (site_url or settings.SITE_URL).rstrip('/'), 'token': token}}).encode()
    req = urllib.request.Request(
        f'https://api.github.com/repos/{repo}/actions/workflows/symbolicate.yml/dispatches', data=body,
        method='POST', headers={'Authorization': f'Bearer {settings.GITHUB_SYMBOLICATE_TOKEN}',
                                'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                                'Content-Type': 'application/json', 'User-Agent': 'attnd-web'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        logger.warning('Symbolicate dispatch failed: HTTP %s', e.code)
        return False, f'GitHub answered {e.code}. Check the token (Actions: read and write on {repo}).'
    except OSError as e:
        logger.warning('Symbolicate dispatch failed: %s', e)
        return False, 'Could not reach GitHub.'
    if status not in (200, 201, 204):
        return False, f'GitHub answered {status}.'
    DiagnosticReport.objects.filter(pk=report.pk).update(
        symbolicate_status=DiagnosticReport.SYMBOLICATE_REQUESTED, symbolicate_token_hash=_hash(token),
        symbolicate_requested_at=timezone.now(), symbolicate_error='')
    return True, f'Symbolication started on {repo} (build {report.build or "?"}); refresh in a few minutes.'


def report_for_token(report_id, token):
    """The report whose pending symbolication this token belongs to, or None."""
    if not token or not str(report_id).isdigit():
        return None
    report = DiagnosticReport.objects.filter(pk=report_id, symbolicate_status=DiagnosticReport.SYMBOLICATE_REQUESTED,
                                             symbolicate_token_hash=_hash(token)).first()
    if report is None or report.symbolicate_requested_at is None:
        return None
    if timezone.now() - report.symbolicate_requested_at > TOKEN_TTL:
        return None
    return report


def job(report):
    """What the workflow needs (GET)."""
    return {'report_id': report.pk, 'platform': report.platform, 'app': report.app_version, 'build': report.build,
            'frames': frames_of(report.payload or {})}


def store_result(report, body):
    """The workflow's answer (POST): {"frames": [{uuid, offset, symbol}]} or {"error": "..."}. Single use."""
    error = body.get('error') if isinstance(body.get('error'), str) else ''
    symbols = {}
    frames = body.get('frames') if isinstance(body.get('frames'), list) else []
    for frame in frames[:MAX_FRAMES * 2]:
        if not isinstance(frame, dict):
            continue
        uuid, offset, symbol = str(frame.get('uuid') or '').upper(), frame.get('offset'), frame.get('symbol')
        if uuid and isinstance(offset, int) and isinstance(symbol, str) and symbol.strip():
            symbols[f'{uuid}:{offset}'] = symbol.strip()[:300]
    done = bool(symbols) and not error
    DiagnosticReport.objects.filter(pk=report.pk).update(
        symbolication=symbols or None, symbolicate_token_hash='',
        symbolicate_status=DiagnosticReport.SYMBOLICATE_DONE if done else DiagnosticReport.SYMBOLICATE_FAILED,
        symbolicate_error=(error or ('' if symbols else 'No frames could be symbolicated.'))[:500])
    return len(symbols)
