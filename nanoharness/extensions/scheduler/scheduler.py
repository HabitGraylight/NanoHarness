"""Local cron and delayed prompt scheduler."""

import json
import os
import queue
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional


def cron_matches(cron_expr: str, dt: datetime) -> bool:
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False
    cron_dow = (dt.weekday() + 1) % 7
    return (
        _field_matches(fields[0], dt.minute)
        and _field_matches(fields[1], dt.hour)
        and _field_matches(fields[2], dt.day)
        and _field_matches(fields[3], dt.month)
        and _field_matches(fields[4], cron_dow)
    )


def _field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return value % step == 0
    if "," in field:
        return any(_field_matches(item.strip(), value) for item in field.split(","))
    if "-" in field and not field.startswith("-"):
        start, end = field.split("-", 1)
        return int(start) <= value <= int(end)
    return int(field) == value


class Scheduler:
    """Own scheduled prompt state, checker thread, and fired notices."""

    def __init__(
        self,
        persist_path: Optional[str] = None,
        *,
        check_interval_seconds: float = 60.0,
        start_checker: bool = True,
    ):
        self._schedules: Dict[int, Dict[str, Any]] = {}
        self._next_id = 1
        self._persist_path = persist_path
        self._check_interval_seconds = check_interval_seconds
        self._lock = threading.RLock()
        self._notifications: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._closed = False
        self._checker: Optional[threading.Thread] = None

        if persist_path:
            directory = os.path.dirname(persist_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            if os.path.exists(persist_path):
                self._load()
        if start_checker:
            self._checker = threading.Thread(
                target=self._check_loop,
                name="nanoharness-scheduler",
                daemon=True,
            )
            self._checker.start()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def checker_alive(self) -> bool:
        return self._checker is not None and self._checker.is_alive()

    def create(
        self,
        prompt: str,
        cron: Optional[str] = None,
        delay_seconds: Optional[int] = None,
        max_fires: Optional[int] = None,
    ) -> Dict[str, Any]:
        if self._closed:
            raise RuntimeError("Scheduler is closed")
        if not prompt:
            raise ValueError("prompt is required")
        if not cron and delay_seconds is None:
            raise ValueError("Either cron or delay_seconds is required")
        if cron and len(cron.strip().split()) != 5:
            raise ValueError("cron must contain five fields")
        if delay_seconds is not None and delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative")
        if max_fires is not None and max_fires < 1:
            raise ValueError("max_fires must be positive")

        fire_at = None
        if delay_seconds is not None:
            fire_at = time.time() + delay_seconds
            if max_fires is None:
                max_fires = 1
        with self._lock:
            schedule_id = self._next_id
            self._next_id += 1
            schedule = {
                "id": schedule_id,
                "prompt": prompt,
                "cron": cron,
                "fire_at": fire_at,
                "status": "active",
                "created_at": time.time(),
                "last_fired": None,
                "last_fired_minute": None,
                "fire_count": 0,
                "max_fires": max_fires,
            }
            self._schedules[schedule_id] = schedule
        self._save()
        return schedule

    def get(self, schedule_id: int) -> Dict[str, Any]:
        return self._require(schedule_id)

    def pause(self, schedule_id: int) -> Dict[str, Any]:
        with self._lock:
            schedule = self._require(schedule_id)
            schedule["status"] = "paused"
        self._save()
        return schedule

    def resume(self, schedule_id: int) -> Dict[str, Any]:
        with self._lock:
            schedule = self._require(schedule_id)
            if schedule["status"] != "paused":
                raise ValueError(
                    f"Schedule {schedule_id} is {schedule['status']}, not paused"
                )
            schedule["status"] = "active"
        self._save()
        return schedule

    def delete(self, schedule_id: int) -> Dict[str, Any]:
        with self._lock:
            schedule = self._require(schedule_id)
            schedule["status"] = "deleted"
        self._save()
        return schedule

    def list(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            schedules = list(self._schedules.values())
            if status:
                schedules = [item for item in schedules if item["status"] == status]
            return schedules

    def drain(self) -> List[Dict[str, Any]]:
        results = []
        while True:
            try:
                schedule_id = self._notifications.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                schedule = self._schedules.get(schedule_id)
                if schedule:
                    results.append(_schedule_notification(schedule))
        return results

    def stop(self, join_timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        checker = self._checker
        if checker is not None and checker is not threading.current_thread():
            checker.join(timeout=join_timeout)
            if checker.is_alive():
                raise RuntimeError("Scheduler checker thread did not stop")

    close = stop

    def _require(self, schedule_id: int) -> Dict[str, Any]:
        schedule = self._schedules.get(schedule_id)
        if schedule is None:
            raise KeyError(f"Schedule {schedule_id} not found")
        return schedule

    def _check_loop(self) -> None:
        while not self._stop_event.is_set():
            self._check_all()
            self._stop_event.wait(self._check_interval_seconds)

    def _check_all(self) -> None:
        now = time.time()
        dt = datetime.now()
        current_minute = f"{dt.year}-{dt.month}-{dt.day}-{dt.hour}-{dt.minute}"
        fired_ids = []
        with self._lock:
            active = [
                schedule
                for schedule in self._schedules.values()
                if schedule["status"] == "active"
            ]
            for schedule in active:
                fired = False
                if schedule["fire_at"] is not None:
                    fired = now >= schedule["fire_at"]
                elif schedule["cron"]:
                    if schedule["last_fired_minute"] == current_minute:
                        continue
                    fired = cron_matches(schedule["cron"], dt)
                if not fired:
                    continue
                schedule["fire_count"] += 1
                schedule["last_fired"] = now
                schedule["last_fired_minute"] = current_minute
                if (
                    schedule["max_fires"]
                    and schedule["fire_count"] >= schedule["max_fires"]
                ):
                    schedule["status"] = "expired"
                fired_ids.append(schedule["id"])
        if fired_ids:
            self._save()
            for schedule_id in fired_ids:
                self._notifications.put(schedule_id)

    def _save(self) -> None:
        if not self._persist_path:
            return
        with self._lock:
            data = {
                "next_id": self._next_id,
                "schedules": {
                    str(key): value for key, value in self._schedules.items()
                },
            }
        temporary = self._persist_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
        os.replace(temporary, self._persist_path)

    def _load(self) -> None:
        assert self._persist_path is not None
        with open(self._persist_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        self._next_id = data.get("next_id", 1)
        self._schedules = {
            int(key): value for key, value in data.get("schedules", {}).items()
        }


def _schedule_notification(schedule: Dict[str, Any]) -> Dict[str, Any]:
    lines = [f"[Scheduled #{schedule['id']} Fired] {schedule['prompt']}"]
    if schedule["cron"]:
        lines.append(
            f"(Schedule: {schedule['cron']} — fired {schedule['fire_count']} time(s))"
        )
    else:
        lines.append(f"(One-shot — fired {schedule['fire_count']} time(s))")
    if schedule["status"] == "expired":
        lines.append("(Schedule expired — will not fire again)")
    return {
        "schedule_id": schedule["id"],
        "status": schedule["status"],
        "message": "\n".join(lines),
    }
