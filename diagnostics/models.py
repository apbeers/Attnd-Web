"""Diagnostics (API-013): app log sessions, crash/hang reports, Android symbol files, endpoint timings.

Everything here is operator data for staff, never shown to organizers, and kept small (see purge.py):
log sessions are deleted after 7 days (dev builds) / 14 days (production debug mode), reports after
30 days, and the tables together stay under settings.DIAGNOSTICS_MAX_BYTES (250 MB), oldest sessions first.
Nothing links a session or report to a device id or a person: a session is keyed by its random debug code.
"""
from django.contrib.postgres.indexes import BrinIndex, GinIndex, OpClass
from django.db import models
from django.db.models.functions import Upper

LEVELS = ('trace', 'debug', 'info', 'notice', 'warning', 'error', 'fault')
LEVEL_ERROR = LEVELS.index('error')
LEVEL_WARNING = LEVELS.index('warning')
# Categories the apps may send. "server" is only written by the server (its request lines).
APP_CATEGORIES = ('net', 'push', 'background', 'live', 'location', 'map', 'planner', 'assistant', 'search',
                  'storage', 'ui', 'perf', 'auth', 'lifecycle', 'logging')
SERVER_CATEGORY = 'server'
REASON_DEV = 'dev'
REASON_DEBUG = 'debug_mode'
REASONS = (REASON_DEV, REASON_DEBUG)


class DiagnosticSession(models.Model):
    """One debug code (`D-XXXXX`): a 10-minute production debug session, or a dev build's install."""
    code = models.CharField(max_length=8, unique=True)
    platform = models.CharField(max_length=12, blank=True)
    reason = models.CharField(max_length=12, default=REASON_DEBUG)
    app_version = models.CharField(max_length=32, blank=True)
    build = models.CharField(max_length=32, blank=True)
    os = models.CharField(max_length=48, blank=True)
    model = models.CharField(max_length=48, blank=True)
    event_id = models.IntegerField(null=True, blank=True)  # the last event the app was in (no FK: may be deleted)
    started_at = models.DateTimeField(auto_now_add=True)
    last_entry_at = models.DateTimeField(db_index=True)
    first_ts = models.DateTimeField(null=True, blank=True)
    last_ts = models.DateTimeField(null=True, blank=True)
    entry_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    bytes = models.BigIntegerField(default=0)  # approximate stored size of its entries
    # Per-session upload cap: entries accepted in the current window (settings.DIAGNOSTICS_SESSION_WINDOW).
    window_start = models.DateTimeField(null=True, blank=True)
    window_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-last_entry_at']

    def __str__(self):
        return self.code

    @property
    def has_errors(self):
        return self.error_count > 0


class DiagnosticEntry(models.Model):
    """One log record (LOG_EVENTS.md, record format v1), from the app or (source "server") from the server."""
    SOURCE_APP = 'app'
    SOURCE_SERVER = 'server'

    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(DiagnosticSession, on_delete=models.CASCADE, related_name='entries')
    received_at = models.DateTimeField()
    ts = models.DateTimeField()
    mono_ms = models.BigIntegerField(null=True, blank=True)
    seq = models.BigIntegerField(null=True, blank=True)
    level = models.PositiveSmallIntegerField()
    cat = models.CharField(max_length=16)
    name = models.CharField(max_length=64)  # the record's "event" (dotted name)
    msg = models.TextField(blank=True)
    fields = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=4, blank=True)
    net = models.CharField(max_length=8, blank=True)
    event_id = models.IntegerField(null=True, blank=True)
    app_version = models.CharField(max_length=32, blank=True)
    req_id = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=8, default=SOURCE_APP)

    class Meta:
        indexes = [
            models.Index(fields=['session', 'ts'], name='diag_entry_session_ts'),
            models.Index(fields=['level'], name='diag_entry_level'),
            models.Index(fields=['cat'], name='diag_entry_cat'),
            models.Index(fields=['name'], name='diag_entry_name'),
            # Text search on msg (icontains) across sessions; pg_trgm is created by migration 0001.
            GinIndex(OpClass(Upper('msg'), name='gin_trgm_ops'), name='diag_entry_msg_trgm'),
            # Append-only time column: a BRIN index is a few pages and makes the retention purge cheap.
            BrinIndex(fields=['received_at'], name='diag_entry_received_brin'),
        ]


class DiagnosticReport(models.Model):
    """An anonymous crash / hang / exit report (iOS MetricKit, Android ApplicationExitInfo + crash handler)."""
    KINDS = ('crash', 'hang', 'anr', 'low_memory', 'exit', 'launch', 'cpu', 'disk_write', 'memory', 'metrics')
    SYMBOLICATE_NONE, SYMBOLICATE_REQUESTED, SYMBOLICATE_DONE, SYMBOLICATE_FAILED = '', 'requested', 'done', 'failed'

    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    platform = models.CharField(max_length=12)
    app_version = models.CharField(max_length=32, blank=True)
    build = models.CharField(max_length=32, blank=True)
    os = models.CharField(max_length=48, blank=True)
    model = models.CharField(max_length=48, blank=True)
    kind = models.CharField(max_length=16)
    reason = models.CharField(max_length=200, blank=True)
    summary = models.CharField(max_length=500, blank=True)  # exception type: message (first line)
    signature = models.CharField(max_length=40, db_index=True)  # groups the same problem
    stack = models.TextField(blank=True)  # Android: Java stack trace / ANR thread dump (obfuscated)
    payload = models.JSONField(null=True, blank=True)  # iOS: MetricKit diagnostic JSON
    diag = models.CharField(max_length=8, blank=True)  # the debug code, when sent during a debug session
    bytes = models.IntegerField(default=0)
    retraced = models.TextField(blank=True)  # Android stack after R8 retrace (cached)
    symbolication = models.JSONField(null=True, blank=True)  # iOS: {"<uuid>:<offset>": "symbol"}
    symbolicate_status = models.CharField(max_length=12, blank=True)
    symbolicate_token_hash = models.CharField(max_length=64, blank=True)
    symbolicate_requested_at = models.DateTimeField(null=True, blank=True)
    symbolicate_error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ['-received_at']


class SymbolFile(models.Model):
    """An Android R8 mapping file for one build, gzip-compressed (uploaded by CI, API-013)."""
    KIND_R8 = 'r8_mapping'

    platform = models.CharField(max_length=12, default='android')
    kind = models.CharField(max_length=16, default=KIND_R8)
    app_version = models.CharField(max_length=32)
    build = models.CharField(max_length=32)
    data = models.BinaryField()  # gzip
    size = models.BigIntegerField()  # uncompressed bytes
    sha256 = models.CharField(max_length=64)
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['platform', 'kind', 'build'], name='diag_symbol_unique_build')]
        ordering = ['-uploaded_at']

    def text(self):
        import gzip
        return gzip.decompress(bytes(self.data)).decode('utf-8', 'replace')


class EndpointTiming(models.Model):
    """Server request timings per hour, method and path template (diagnostics.timing), for p50/p95."""
    hour = models.DateTimeField()
    method = models.CharField(max_length=8)
    route = models.CharField(max_length=160)
    count = models.PositiveIntegerField(default=0)
    client_errors = models.PositiveIntegerField(default=0)  # 4xx
    server_errors = models.PositiveIntegerField(default=0)  # 5xx
    total_ms = models.BigIntegerField(default=0)
    max_ms = models.PositiveIntegerField(default=0)
    hist = models.JSONField(default=list)  # counts per diagnostics.timing.BUCKETS_MS bucket

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hour', 'method', 'route'], name='diag_timing_unique')]
