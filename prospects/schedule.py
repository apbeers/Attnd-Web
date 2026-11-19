"""Scheduled discovery runs: `run_scheduled_tasks` (Heroku Scheduler, every 10 minutes) calls `maybe_run()`. It runs
only when the schedule is on in the dashboard's settings and `interval_days` have passed since the last run started;
the run itself is inline (one-off dyno). `discover.claim` locks the settings row, so two runs never overlap."""

from datetime import timedelta

from django.utils import timezone

from prospects import discover
from prospects.models import ProspectRun, ProspectSettings

SLACK = timedelta(minutes=15)
# Heroku Scheduler's one-off dyno for the 10-minute job may be stopped when the next one starts, so a scheduled run
# stops itself before that (what it found is kept; "Run now" in the web dyno gets the full PROSPECT_RUN_MAX_MINUTES).
SCHEDULED_MAX_MINUTES = 8  # the scheduler fires every 10 min; don't drift a whole interval late


def is_due(conf, now=None):
    now = now or timezone.now()
    if not conf.schedule_enabled or conf.interval_days < 1:
        return False
    return conf.last_run_at is None or now - conf.last_run_at >= timedelta(days=conf.interval_days) - SLACK


def maybe_run(now=None):
    """Run a scheduled discovery when due. Returns the run, or None when nothing was due."""
    conf = ProspectSettings.load()
    if not is_due(conf, now) or discover.running_run():
        return None
    run = discover.create_run(ProspectRun.SCHEDULED)
    return discover.execute(run.pk, max_minutes=SCHEDULED_MAX_MINUTES)
