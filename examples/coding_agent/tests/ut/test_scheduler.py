"""Tests for Scheduler -- cron matching, CRUD, pause/resume, delete, list."""

import os
import tempfile
import time

import pytest

from datetime import datetime

from app.scheduler import Scheduler, cron_matches, _field_matches


# -- Cron matching --


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


# -- Scheduler CRUD --


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
