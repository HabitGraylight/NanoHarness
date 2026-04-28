"""Scheduled task system — fire prompts at future times.

Three parts:
    1. ScheduleRecord  — what to do + when (cron recurring or delay one-shot)
    2. Checker thread  — daemon, wakes every 60s, matches against current time
    3. Notification queue — drain() feeds into the same main loop as background tasks

Key insight: scheduled tasks are NOT another agent.
They return to the same main loop via drain() → get_full_context().

Usage:
    sched = Scheduler()
    sched.create("Run tests", cron="0 22 * * *")           # daily at 22:00
    sched.create("Remind me", delay_seconds=1800)           # 30 min from now
    notifications = sched.drain()                            # fired prompts
"""

import json
import os
import queue
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.dispatch import DispatchRegistry, inprocess_handler, tool_result


# ── Cron matching ──


def cron_matches(cron_expr: str, dt: datetime) -> bool:
    """Check if a 5-field cron expression matches the given datetime.

    Fields: minute hour day-of-month month day-of-week
    Supports: *, */N, N, N,M, N-M
    Day-of-week uses cron convention: 0=Sunday, 6=Saturday
    """
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False

    # Convert Python weekday (Mon=0) to cron dow (Sun=0)
    cron_dow = (dt.weekday() + 1) % 7

    return (
        _field_matches(fields[0], dt.minute) and
        _field_matches(fields[1], dt.hour) and
        _field_matches(fields[2], dt.day) and
        _field_matches(fields[3], dt.month) and
        _field_matches(fields[4], cron_dow)
    )


def _field_matches(field: str, value: int) -> bool:
    """Match a single cron field against a value."""
    # Wildcard
    if field == "*":
        return True
    # Step: */N
    if field.startswith("*/"):
        step = int(field[2:])
        return value % step == 0
    # List: N,M,K
    if "," in field:
        return any(_field_matches(f.strip(), value) for f in field.split(","))
    # Range: N-M
    if "-" in field and not field.startswith("-"):
        start, end = field.split("-", 1)
        return int(start) <= value <= int(end)
    # Exact value
    return int(field) == value


# ── Scheduler ──


class Scheduler:
    """Create and manage scheduled tasks with cron or delay-based firing.

    Fires prompts into a notification queue. The main loop drains them
    via get_full_context() — same path as background task notifications.
    """

    def __init__(self, persist_path: Optional[str] = None):
        self._schedules: Dict[int, Dict[str, Any]] = {}
        self._next_id = 1
        self._persist_path = persist_path
        self._lock = threading.Lock()
        self._notifications: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()

        if persist_path:
            dir_path = os.path.dirname(persist_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            if os.path.exists(persist_path):
                self._load()

        # Start checker daemon
        self._checker = threading.Thread(target=self._check_loop, daemon=True)
        self._checker.start()

    def create(
        self,
        prompt: str,
        cron: Optional[str] = None,
        delay_seconds: Optional[int] = None,
        max_fires: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a scheduled task.

        Args:
            prompt:         What to do when the schedule fires.
            cron:           5-field cron expression (recurring).
            delay_seconds:  Fire once after this many seconds (one-shot).
            max_fires:      Max times to fire. None=unlimited. Default: 1 for delay, None for cron.
        """
        if not prompt:
            raise ValueError("prompt is required")
        if not cron and delay_seconds is None:
            raise ValueError("Either cron or delay_seconds is required")

        with self._lock:
            schedule_id = self._next_id
            self._next_id += 1

        fire_at = None
        if delay_seconds is not None:
            fire_at = time.time() + delay_seconds
            if max_fires is None:
                max_fires = 1  # delay is one-shot by default

        schedule = {
            "id": schedule_id,
            "prompt": prompt,
            "cron": cron,
            "fire_at": fire_at,
            "status": "active",
            "created_at": time.time(),
            "last_fired": None,
            "last_fired_minute": None,  # prevent double-fire within same minute
            "fire_count": 0,
            "max_fires": max_fires,
        }

        with self._lock:
            self._schedules[schedule_id] = schedule
        self._save()
        return schedule

    def pause(self, schedule_id: int) -> Dict[str, Any]:
        """Pause a schedule. It won't fire until resumed."""
        schedule = self._require(schedule_id)
        schedule["status"] = "paused"
        self._save()
        return schedule

    def resume(self, schedule_id: int) -> Dict[str, Any]:
        """Resume a paused schedule."""
        schedule = self._require(schedule_id)
        if schedule["status"] != "paused":
            raise ValueError(f"Schedule {schedule_id} is {schedule['status']}, not paused")
        schedule["status"] = "active"
        self._save()
        return schedule

    def delete(self, schedule_id: int) -> Dict[str, Any]:
        """Delete a schedule."""
        schedule = self._require(schedule_id)
        schedule["status"] = "deleted"
        self._save()
        return schedule

    def list(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List schedules, optionally filtered by status."""
        schedules = list(self._schedules.values())
        if status:
            schedules = [s for s in schedules if s["status"] == status]
        return schedules

    def drain(self) -> List[Dict[str, Any]]:
        """Pull all fired schedule notifications (non-blocking)."""
        results = []
        while True:
            try:
                schedule_id = self._notifications.get_nowait()
            except queue.Empty:
                break
            schedule = self._schedules.get(schedule_id)
            if schedule:
                results.append(_schedule_notification(schedule))
        return results

    def stop(self):
        """Stop the checker thread."""
        self._stop_event.set()

    # ── Internal ──

    def _require(self, schedule_id: int) -> Dict[str, Any]:
        schedule = self._schedules.get(schedule_id)
        if schedule is None:
            raise KeyError(f"Schedule {schedule_id} not found")
        return schedule

    def _check_loop(self):
        """Daemon loop: check schedules every ~60 seconds."""
        while not self._stop_event.is_set():
            self._check_all()
            self._stop_event.wait(60)

    def _check_all(self):
        """Check all active schedules against current time."""
        now = time.time()
        dt = datetime.now()
        current_minute = f"{dt.year}-{dt.month}-{dt.day}-{dt.hour}-{dt.minute}"

        with self._lock:
            active = [s for s in self._schedules.values() if s["status"] == "active"]

        for schedule in active:
            fired = False

            # One-shot: fire_at timestamp
            if schedule["fire_at"] is not None:
                if now >= schedule["fire_at"]:
                    fired = True

            # Recurring: cron match
            elif schedule["cron"]:
                # Prevent double-fire within same minute
                if schedule["last_fired_minute"] == current_minute:
                    continue
                if cron_matches(schedule["cron"], dt):
                    fired = True

            if fired:
                schedule["fire_count"] += 1
                schedule["last_fired"] = now
                schedule["last_fired_minute"] = current_minute

                # Check if expired
                if schedule["max_fires"] and schedule["fire_count"] >= schedule["max_fires"]:
                    schedule["status"] = "expired"

                self._save()
                self._notifications.put(schedule["id"])

    def _save(self):
        if not self._persist_path:
            return
        data = {
            "next_id": self._next_id,
            "schedules": {str(k): v for k, v in self._schedules.items()},
        }
        tmp = self._persist_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._persist_path)

    def _load(self):
        with open(self._persist_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._next_id = data.get("next_id", 1)
        self._schedules = {int(k): v for k, v in data.get("schedules", {}).items()}


# ── Notification formatting ──


def _schedule_notification(schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Format a fired schedule as a notification message."""
    schedule_type = "one-shot" if schedule["fire_at"] else "cron"
    lines = [f"[Scheduled #{schedule['id']} Fired] {schedule['prompt']}"]

    if schedule["cron"]:
        lines.append(f"(Schedule: {schedule['cron']} — fired {schedule['fire_count']} time(s))")
    else:
        lines.append(f"(One-shot — fired {schedule['fire_count']} time(s))")

    if schedule["status"] == "expired":
        lines.append("(Schedule expired — will not fire again)")

    return {
        "schedule_id": schedule["id"],
        "status": schedule["status"],
        "message": "\n".join(lines),
    }


# ── Tool registration ──


def register_schedule_tools(registry: DispatchRegistry, scheduler: Scheduler):
    """Register schedule_create, schedule_list, schedule_pause, schedule_resume, schedule_delete."""

    def schedule_create(args: Dict) -> tool_result:
        prompt = args.get("prompt", "")
        if not prompt:
            return tool_result(ok=False, output="", error="prompt is required")

        cron = args.get("cron")
        delay_seconds = args.get("delay_seconds")
        max_fires = args.get("max_fires")

        if not cron and delay_seconds is None:
            return tool_result(ok=False, output="", error="Either cron or delay_seconds is required")

        try:
            schedule = scheduler.create(
                prompt=prompt,
                cron=cron,
                delay_seconds=int(delay_seconds) if delay_seconds is not None else None,
                max_fires=int(max_fires) if max_fires is not None else None,
            )
            when = ""
            if schedule["cron"]:
                when = f"cron: {schedule['cron']}"
            else:
                when = f"fires in {delay_seconds}s"
            return tool_result(
                ok=True,
                output=f"Created schedule #{schedule['id']}: {prompt}\n  {when}",
            )
        except (ValueError, KeyError) as e:
            return tool_result(ok=False, output="", error=str(e))

    def schedule_list(args: Dict) -> tool_result:
        status = args.get("status")
        schedules = scheduler.list(status=status)
        if not schedules:
            return tool_result(ok=True, output="No schedules found.")

        lines = []
        for s in schedules:
            when = s["cron"] or f"one-shot at {time.strftime('%H:%M:%S', time.localtime(s['fire_at']))}"
            lines.append(
                f"  #{s['id']} [{s['status']}] {s['prompt'][:60]} — {when} (fired {s['fire_count']}x)"
            )
        return tool_result(ok=True, output="\n".join(lines))

    def schedule_pause(args: Dict) -> tool_result:
        sid = args.get("id")
        if sid is None:
            return tool_result(ok=False, output="", error="id is required")
        try:
            s = scheduler.pause(int(sid))
            return tool_result(ok=True, output=f"Paused schedule #{s['id']}: {s['prompt'][:60]}")
        except (KeyError, ValueError) as e:
            return tool_result(ok=False, output="", error=str(e))

    def schedule_resume(args: Dict) -> tool_result:
        sid = args.get("id")
        if sid is None:
            return tool_result(ok=False, output="", error="id is required")
        try:
            s = scheduler.resume(int(sid))
            return tool_result(ok=True, output=f"Resumed schedule #{s['id']}: {s['prompt'][:60]}")
        except (KeyError, ValueError) as e:
            return tool_result(ok=False, output="", error=str(e))

    def schedule_delete(args: Dict) -> tool_result:
        sid = args.get("id")
        if sid is None:
            return tool_result(ok=False, output="", error="id is required")
        try:
            s = scheduler.delete(int(sid))
            return tool_result(ok=True, output=f"Deleted schedule #{s['id']}: {s['prompt'][:60]}")
        except KeyError as e:
            return tool_result(ok=False, output="", error=str(e))

    tools = [
        (
            "schedule_create",
            schedule_create,
            "Schedule a prompt to fire at a future time. Either cron (recurring) or delay_seconds (one-shot). "
            "When it fires, the prompt is injected into the agent's context as a notification.",
            {
                "prompt": {"type": "string", "description": "What to do when the schedule fires"},
                "cron": {"type": "string", "description": "5-field cron expression (e.g. '0 22 * * *' = daily at 22:00)"},
                "delay_seconds": {"type": "integer", "description": "Fire once after this many seconds"},
                "max_fires": {"type": "integer", "description": "Max times to fire (default: 1 for delay, unlimited for cron)"},
            },
        ),
        (
            "schedule_list",
            schedule_list,
            "List all scheduled tasks.",
            {
                "status": {"type": "string", "description": "Filter by status: active, paused, expired, deleted"},
            },
        ),
        (
            "schedule_pause",
            schedule_pause,
            "Pause a scheduled task. It won't fire until resumed.",
            {"id": {"type": "integer", "description": "Schedule ID"}},
        ),
        (
            "schedule_resume",
            schedule_resume,
            "Resume a paused scheduled task.",
            {"id": {"type": "integer", "description": "Schedule ID"}},
        ),
        (
            "schedule_delete",
            schedule_delete,
            "Delete a scheduled task.",
            {"id": {"type": "integer", "description": "Schedule ID"}},
        ),
    ]

    for name, handler, desc, params in tools:
        required = ["prompt"] if name == "schedule_create" else (["id"] if name != "schedule_list" else [])
        registry.register(
            name=name,
            handler=handler,
            schema={
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": params,
                        "required": required,
                    },
                },
            },
        )
