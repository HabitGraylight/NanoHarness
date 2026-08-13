"""ST tests for Scheduler — one-shot, recurring cron, drain, persistence, ManagedContext integration."""

import os
import json
import time
import tempfile

import pytest

from datetime import datetime

from nanoharness.extensions.scheduler import Scheduler, cron_matches
from nanoharness.extensions.scheduler import register_schedule_tools
from nanoharness.components import DictToolRegistry
from nanoharness.extensions.scheduler.scheduler import (
    _field_matches,
    _schedule_notification,
)


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

    def test_queued_notification_is_a_fire_time_snapshot(self):
        sched = Scheduler(start_checker=False)
        sched.create("Snapshot", cron="* * * * *")

        sched._check_all()
        sched.delete(1)
        notification = sched.drain()[0]

        assert notification["fire_count"] == 1
        assert notification["status"] == "active"
        assert sched.get(1)["status"] == "deleted"
        sched.stop()


class TestPublicCheckDue:
    def test_check_due_returns_structured_trigger_once(self):
        sched = Scheduler(start_checker=False)
        sched.create("Run reflection", delay_seconds=0)

        notifications = sched.check_due()

        assert notifications[0]["prompt"] == "Run reflection"
        assert notifications[0]["fire_count"] == 1
        assert notifications[0]["fired_at"] is not None
        assert sched.check_due() == []
        sched.stop()

    def test_check_due_rejects_closed_scheduler(self):
        sched = Scheduler(start_checker=False)
        sched.stop()
        with pytest.raises(RuntimeError, match="closed"):
            sched.check_due()


class TestScheduleToolMetadata:
    def test_create_tool_exposes_and_forwards_generic_metadata(self):
        scheduler = Scheduler(start_checker=False)
        registry = DictToolRegistry()
        register_schedule_tools(registry, scheduler)
        schema = next(
            item
            for item in registry.get_tool_schemas()
            if item["function"]["name"] == "schedule_create"
        )

        registry.call("schedule_create", {
            "prompt": "Wake host",
            "delay_seconds": 0,
            "metadata": {"route": {"channel": "mock"}},
        })

        assert schema["function"]["parameters"]["properties"]["metadata"] == {
            "type": "object",
            "description": "Transport-neutral host metadata returned on fire",
        }
        assert scheduler.get(1)["metadata"]["route"]["channel"] == "mock"
        scheduler.stop()


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

    def test_notification_preserves_structured_metadata(self):
        sched = Scheduler(start_checker=False)
        sched.create(
            "Route this",
            delay_seconds=0,
            metadata={"route": {"channel": "mock"}, "attempt": 1},
        )

        notification = sched.check_due()[0]
        notification["metadata"]["attempt"] = 2

        assert sched.get(1)["metadata"]["attempt"] == 1
        sched.stop()

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

    def test_loads_legacy_schedule_without_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "schedules.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "next_id": 2,
                    "schedules": {
                        "1": {
                            "id": 1,
                            "prompt": "Legacy",
                            "cron": None,
                            "fire_at": 9999999999,
                            "status": "active",
                            "created_at": 1,
                            "last_fired": None,
                            "last_fired_minute": None,
                            "fire_count": 0,
                            "max_fires": 1,
                        }
                    },
                }, handle)

            scheduler = Scheduler(persist_path=path, start_checker=False)
            assert scheduler.get(1)["metadata"] == {}
            scheduler.stop()
