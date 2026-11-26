"""Ask Attnd: runs, their steps (tool calls, confirmations), cost per model call and files people attach."""

from django.contrib.auth.models import User
from django.db import models
from django.conf import settings
from django.utils import timezone


class AgentRun(models.Model):
    RUNNING, WAITING_CONFIRMATION, WAITING_REPLY = 'running', 'waiting_confirmation', 'waiting_reply'
    DONE, FAILED, STOPPED = 'done', 'failed', 'stopped'
    STATUS_CHOICES = [(RUNNING, 'Running'), (WAITING_CONFIRMATION, 'Waiting for confirmation'),
                      (WAITING_REPLY, 'Waiting for a reply'), (DONE, 'Done'), (FAILED, 'Failed'),
                      (STOPPED, 'Stopped')]
    ACTIVE = (RUNNING, WAITING_CONFIRMATION, WAITING_REPLY)

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='agent_runs', db_index=False)  # db_review §4
    event = models.ForeignKey('events.Event', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    goal = models.TextField()
    page_url = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=RUNNING)
    provider = models.CharField(max_length=16, blank=True)
    model = models.CharField(max_length=64, blank=True)
    previous_response_id = models.CharField(max_length=128, blank=True)  # (OpenAI's chain position: unused since stage C)
    state = models.JSONField(default=dict, blank=True)  # conversation items + pending results while paused
    cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    step_count = models.PositiveIntegerField(default=0)  # model calls
    summary = models.TextField(blank=True)
    error = models.TextField(blank=True)
    # The provider's error details (status, type, request id) for staff; the person sees only `error` (a friendly line).
    error_details = models.CharField(max_length=500, blank=True)
    revisions = models.JSONField(default=dict, blank=True)  # {event id: History revision before the first change}
    suggestions = models.JSONField(default=list, blank=True)  # follow-up chips (finish's `suggest`)
    target = models.JSONField(default=dict, blank=True)  # Add with AI: the form being filled {component, ids}
    # The conversation this run belongs to (stage I4): the same chat on every device and after signing in again.
    thread = models.CharField(max_length=32, blank=True, db_index=True)
    undone_at = models.DateTimeField(null=True, blank=True)
    # 👍 / 👎 on the answer (stage I), for staff review in the admin; the note is the person's own words (≤ 500).
    feedback = models.SmallIntegerField(null=True, blank=True, choices=[(1, 'Helpful'), (-1, 'Not helpful')])
    feedback_note = models.CharField(max_length=500, blank=True)
    feedback_at = models.DateTimeField(null=True, blank=True)
    # The person's own session (internal requests use it read-only) and where they were.
    session_key = models.CharField(max_length=64, blank=True)
    host = models.CharField(max_length=255, blank=True)
    secure = models.BooleanField(default=True)
    # Where the run came from: the in-app chat, or one MCP tool call (mcp_server/: no model, the client is e.g. Claude).
    # MCP runs stay out of the chat's own lists; the Platform's Connected apps page shows them.
    SOURCE_CHAT, SOURCE_MCP = 'chat', 'mcp'
    source = models.CharField(max_length=8, choices=[(SOURCE_CHAT, 'Ask Attnd chat'), (SOURCE_MCP, 'MCP client')],
                              default=SOURCE_CHAT)
    client = models.CharField(max_length=120, blank=True)  # the MCP client's name ("Claude"), for the run list
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)  # last (re)start of the loop, for the time cap
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        return f'Run {self.pk} ({self.status}): {self.goal[:60]}'

    @property
    def active(self):
        return self.status in self.ACTIVE


class AgentStep(models.Model):
    KIND_TOOL, KIND_NOTE, KIND_QUESTION, KIND_REPLY, KIND_UNDO = 'tool', 'note', 'question', 'reply', 'undo'
    # A console component shown in the chat (the model's `show`, or the person tapping one for its detail):
    # arguments = {component, items: [ids…], picked}, result = the components' JSON (never HTML).
    KIND_COMPONENT = 'component'
    # A component's always-ask button pressed in the chat: waits for Confirm like the model's own actions.
    KIND_BUTTON = 'button'
    KIND_CHOICES = [(KIND_TOOL, 'Tool call'), (KIND_NOTE, 'Note'), (KIND_QUESTION, 'Question'),
                    (KIND_REPLY, 'Reply'), (KIND_UNDO, 'Undo all'), (KIND_COMPONENT, 'Component'),
                    (KIND_BUTTON, 'Button in the chat')]
    CONFIRM_PENDING, CONFIRM_CONFIRMED, CONFIRM_CANCELLED = 'pending', 'confirmed', 'cancelled'
    CONFIRM_EXECUTED, CONFIRM_EXPIRED = 'executed', 'expired'
    CONFIRM_CHOICES = [('', 'Not needed'), (CONFIRM_PENDING, 'Waiting'), (CONFIRM_CONFIRMED, 'Confirmed'),
                       (CONFIRM_CANCELLED, 'Cancelled'), (CONFIRM_EXECUTED, 'Done'), (CONFIRM_EXPIRED, 'Expired')]

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name='steps', db_index=False)  # db_review §4
    seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default=KIND_TOOL)
    tool = models.CharField(max_length=64, blank=True)
    call_id = models.CharField(max_length=128, blank=True)  # the provider's tool call id
    arguments = models.JSONField(default=dict, blank=True)
    text = models.TextField(blank=True)  # what the panel shows
    result = models.TextField(blank=True)  # what the model got back (trimmed)
    ok = models.BooleanField(default=True)
    changed = models.BooleanField(default=False)
    url = models.CharField(max_length=500, blank=True)  # the page this step touched
    event_id = models.IntegerField(null=True, blank=True)
    confirm_state = models.CharField(max_length=12, choices=CONFIRM_CHOICES, blank=True)
    confirm_summary = models.TextField(blank=True)
    confirm_hash = models.CharField(max_length=64, blank=True)  # sha256 of the exact tool + arguments confirmed
    cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    # The console.UndoEntry this step's change recorded (console/undo.py): the chat's Undo next to it.
    undo_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['run', 'seq']
        constraints = [models.UniqueConstraint(fields=['run', 'seq'], name='agent_step_seq_unique')]

    def __str__(self):
        return f'{self.run_id}#{self.seq} {self.tool or self.kind}'


class AgentUsage(models.Model):
    """One model call and its cost (the per-person daily cap sums these)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+', db_index=False)  # db_review §4
    run = models.ForeignKey(AgentRun, on_delete=models.SET_NULL, null=True, blank=True, related_name='usage')
    provider = models.CharField(max_length=16)
    model = models.CharField(max_length=64)
    input_tokens = models.PositiveIntegerField(default=0)
    cached_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=['user', 'created_at'])]


class AgentAttachment(models.Model):
    """A file someone attached in the chat (a flyer, a PDF); tools pass it to views that take files."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    run = models.ForeignKey(AgentRun, on_delete=models.SET_NULL, null=True, blank=True, related_name='attachments')
    name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    data = models.BinaryField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']


class ChangeProposal(models.Model):
    """Stage I1: several changes Ask Attnd proposes at once ("move every Saturday set 30 min later"), previewed as a
    `change_preview` card (old → new per row, untick any) and applied by the person in one undo entry."""
    PENDING, APPLIED, CANCELLED = 'pending', 'applied', 'cancelled'
    STATUS_CHOICES = [(PENDING, 'Waiting'), (APPLIED, 'Applied'), (CANCELLED, 'Cancelled')]

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name='proposals')
    event = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='+')
    title = models.CharField(max_length=200)
    # [{action, args, label, rows: [[field, old, new], …]}]: validated registered writes, never always-ask ones.
    changes = models.JSONField(default=list)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    results = models.JSONField(default=list, blank=True)  # [{index, ok, text}]
    # Live ops (stage I6): a notification drafted with the changes {title, body, urgent}; after Apply the chat shows
    # it in the notification form, prefilled, and sending still asks to confirm.
    notification = models.JSONField(default=dict, blank=True)
    undo_id = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.title} ({self.status})'


class SupportMessage(models.Model):
    """A "Talk to a person" message (agent:contact), kept so staff can read and answer them (MCP
    `list_support_messages`, owner Nov 26, 2026); the email to ADMIN_EMAILS still goes out."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    email = models.EmailField(max_length=254)
    message = models.TextField()
    page = models.CharField(max_length=300, blank=True)
    run = models.ForeignKey(AgentRun, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    handled = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at', '-id']
