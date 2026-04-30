"""ST tests for Scheduler — one-shot, recurring cron, drain, persistence, ManagedContext integration."""

import os
import time
import tempfile

import pytest

from datetime import datetime

from app.scheduler import Scheduler, cron_matches, _field_matches, _schedule_notification
from app.context import ManagedContext
from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.core.schema import AgentMessage


# ── One-shot firing ──


class TestOneShot:
    def test_delay_fires(self):
        sched = Scheduler()
        sched.create("Quick fire", delay_seconds=1)
        time.sleep(2)
        # Force a check (checker runs every 60s, so trigger manually)
        sched._check_all()
        notifications = sched.drain()
        assert len(notifications) == 1
        assert "Quick fire" in notifications[0]["message"]

    def test_one_shot_expires(self):
        sched = Scheduler()
        sched.create("Once only", delay_seconds=1)
        time.sleep(2)
        sched._check_all()
        sched.drain()
        # Should be expired now
        s = sched.get(1) if hasattr(sched, 'get') else sched._schedules[1]
        assert s["status"] == "expired"

    def test_does_not_fire_before_time(self):
        sched = Scheduler()
        sched.create("Too early", delay_seconds=300)
        sched._check_all()
        assert sched.drain() == []


# ── Recurring cron firing ──


class TestRecurringCron:
    def test_matching_cron_fires(self):
        sched = Scheduler()
        now = datetime.now()
        cron_expr = f"{now.minute} {now.hour} * * *"
        sched.create("Match now", cron=cron_expr)
        sched._check_all()
        notifications = sched.drain()
        assert len(notifications) == 1

    def test_no_double_fire_same_minute(self):
        sched = Scheduler()
        now = datetime.now()
        cron_expr = f"{now.minute} {now.hour} * * *"
        sched.create("No double", cron=cron_expr)
        sched._check_all()
        assert len(sched.drain()) == 1
        # Second check in same minute — should not fire again
        sched._check_all()
        assert len(sched.drain()) == 0

    def test_non_matching_cron_does_not_fire(self):
        sched = Scheduler()
        sched.create("Never matches", cron="99 99 * * *")
        sched._check_all()
        assert sched.drain() == []


# ── Drain ──


class TestDrain:
    def test_drain_consumes_queue(self):
        sched = Scheduler()
        sched.create("Fire 1", delay_seconds=1)
        time.sleep(2)
        sched._check_all()
        assert len(sched.drain()) == 1
        assert len(sched.drain()) == 0

    def test_empty_drain(self):
        sched = Scheduler()
        assert sched.drain() == []


# ── Notification format ──


class TestNotificationFormat:
    def test_notification_includes_prompt(self):
        sched = Scheduler()
        sched.create("Run the test suite", delay_seconds=1)
        time.sleep(2)
        sched._check_all()
        notif = sched.drain()[0]
        assert "[Scheduled #1 Fired]" in notif["message"]
        assert "Run the test suite" in notif["message"]

    def test_notification_shows_cron_info(self):
        sched = Scheduler()
        sched.create("Daily test", cron="0 22 * * *")
        # Manually fire by setting fire_count
        sched._schedules[1]["fire_count"] = 5
        notif = _schedule_notification(sched._schedules[1])
        assert "0 22 * * *" in notif["message"]
        assert "fired 5 time(s)" in notif["message"]


# ── Persistence ──


class TestPersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "schedules.json")

            sched1 = Scheduler(persist_path=path)
            sched1.create("Daily test", cron="0 22 * * *")
            sched1.create("One-shot", delay_seconds=300)
            sched1.stop()

            sched2 = Scheduler(persist_path=path)
            schedules = sched2.list()
            assert len(schedules) == 2
            assert schedules[0]["cron"] == "0 22 * * *"
            assert schedules[1]["fire_at"] is not None
            sched2.stop()


# ── ManagedContext integration ──


class TestManagedContextSchedulerDrain:
    def test_scheduler_notifications_injected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sched = Scheduler()
            sched.create("Quick fire", delay_seconds=1)
            time.sleep(2)
            sched._check_all()

            context = ManagedContext(
                inner=SimpleContextManager(system_prompt="test"),
                scratch_dir=tmpdir,
                scheduler=sched,
            )

            messages = context.get_full_context()
            sched_msgs = [m for m in messages if "Scheduled" in m.get("content", "")]
            assert len(sched_msgs) == 1
            assert "Quick fire" in sched_msgs[0]["content"]
            sched.stop()

    def test_no_scheduler_no_error(self):
        context = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir="/tmp/test_no_sched",
        )
        context.add_message(AgentMessage(role="user", content="hi"))
        messages = context.get_full_context()
        assert len(messages) >= 2
