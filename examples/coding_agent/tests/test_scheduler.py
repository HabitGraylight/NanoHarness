"""Tests for Scheduler — cron matching, one-shot, recurring, pause/resume, drain, persistence."""

import sys
import os
import time
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import pytest

from datetime import datetime

from app.scheduler import Scheduler, cron_matches, _field_matches
from app.context import ManagedContext
from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.core.schema import AgentMessage


# ── Cron matching ──


class TestFieldMatches:
    def test_wildcard(self):
        assert _field_matches("*", 5) is True
        assert _field_matches("*", 0) is True

    def test_exact(self):
        assert _field_matches("5", 5) is True
        assert _field_matches("5", 6) is False

    def test_step(self):
        assert _field_matches("*/5", 0) is True
        assert _field_matches("*/5", 5) is True
        assert _field_matches("*/5", 10) is True
        assert _field_matches("*/5", 3) is False

    def test_list(self):
        assert _field_matches("1,3,5", 1) is True
        assert _field_matches("1,3,5", 3) is True
        assert _field_matches("1,3,5", 4) is False

    def test_range(self):
        assert _field_matches("1-5", 1) is True
        assert _field_matches("1-5", 3) is True
        assert _field_matches("1-5", 5) is True
        assert _field_matches("1-5", 6) is False
        assert _field_matches("1-5", 0) is False


class TestCronMatches:
    def test_wildcard_matches_everything(self):
        dt = datetime(2026, 4, 28, 14, 30)  # Tue
        assert cron_matches("* * * * *", dt) is True

    def test_specific_minute_hour(self):
        dt = datetime(2026, 4, 28, 22, 0)
        assert cron_matches("0 22 * * *", dt) is True
        assert cron_matches("30 22 * * *", dt) is False

    def test_day_of_week(self):
        # 2026-04-27 is Monday (Python weekday=0, cron dow=1)
        monday = datetime(2026, 4, 27, 9, 0)
        assert cron_matches("0 9 * * 1", monday) is True  # Monday in cron = 1

        tuesday = datetime(2026, 4, 28, 9, 0)
        assert cron_matches("0 9 * * 1", tuesday) is False
        assert cron_matches("0 9 * * 2", tuesday) is True  # Tuesday = 2

    def test_sunday(self):
        # 2026-05-03 is Sunday (Python weekday=6, cron dow=0)
        sunday = datetime(2026, 5, 3, 10, 0)
        assert cron_matches("0 10 * * 0", sunday) is True

    def test_month(self):
        dt = datetime(2026, 4, 15, 0, 0)
        assert cron_matches("0 0 15 4 *", dt) is True
        assert cron_matches("0 0 15 5 *", dt) is False

    def test_invalid_fields(self):
        dt = datetime(2026, 4, 28, 14, 30)
        assert cron_matches("* * *", dt) is False
        assert cron_matches("", dt) is False


# ── Scheduler CRUD ──


class TestSchedulerCreate:
    def test_cron_recurring(self):
        sched = Scheduler()
        s = sched.create("Run tests", cron="0 22 * * *")
        assert s["id"] == 1
        assert s["cron"] == "0 22 * * *"
        assert s["status"] == "active"
        assert s["max_fires"] is None  # unlimited

    def test_delay_one_shot(self):
        sched = Scheduler()
        s = sched.create("Remind me", delay_seconds=300)
        assert s["fire_at"] is not None
        assert s["max_fires"] == 1  # one-shot default
        assert s["fire_at"] > time.time()

    def test_delay_with_custom_max_fires(self):
        sched = Scheduler()
        s = sched.create("Repeat 3 times", delay_seconds=60, max_fires=3)
        assert s["max_fires"] == 3

    def test_requires_cron_or_delay(self):
        sched = Scheduler()
        with pytest.raises(ValueError):
            sched.create("Nothing")

    def test_requires_prompt(self):
        sched = Scheduler()
        with pytest.raises(ValueError):
            sched.create("", cron="* * * * *")


class TestSchedulerPauseResume:
    def test_pause_and_resume(self):
        sched = Scheduler()
        sched.create("Test", cron="* * * * *")
        s = sched.pause(1)
        assert s["status"] == "paused"
        s = sched.resume(1)
        assert s["status"] == "active"

    def test_cannot_resume_active(self):
        sched = Scheduler()
        sched.create("Test", cron="* * * * *")
        with pytest.raises(ValueError):
            sched.resume(1)


class TestSchedulerDelete:
    def test_delete(self):
        sched = Scheduler()
        sched.create("Test", cron="* * * * *")
        s = sched.delete(1)
        assert s["status"] == "deleted"

    def test_not_found(self):
        sched = Scheduler()
        with pytest.raises(KeyError):
            sched.delete(99)


class TestSchedulerList:
    def test_list_all(self):
        sched = Scheduler()
        sched.create("A", cron="* * * * *")
        sched.create("B", delay_seconds=60)
        assert len(sched.list()) == 2

    def test_filter_by_status(self):
        sched = Scheduler()
        sched.create("A", cron="* * * * *")
        sched.create("B", delay_seconds=60)
        sched.pause(1)
        active = sched.list(status="active")
        assert len(active) == 1
        assert active[0]["id"] == 2


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
        from app.scheduler import _schedule_notification
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
