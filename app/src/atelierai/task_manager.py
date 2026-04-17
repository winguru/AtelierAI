from __future__ import annotations

import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, Optional


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskCancelledError(RuntimeError):
    pass


@dataclass
class _TaskRecord:
    id: str
    kind: str
    title: str
    status: str = "queued"
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    message: str = "Queued"
    error: Optional[str] = None
    progress_current: int = 0
    progress_total: int = 0
    cancellable: bool = True
    cancel_requested: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    counters: Dict[str, int] = field(default_factory=dict)
    item_status_counts: Dict[str, int] = field(default_factory=dict)
    item_states: Dict[str, str] = field(default_factory=dict)
    recent_items: Deque[Dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=60)
    )
    recent_errors: Deque[str] = field(default_factory=lambda: deque(maxlen=20))
    failed_items: Deque[Dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=120)
    )
    unavailable_items: Deque[Dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=120)
    )
    missing_failures: Deque[Dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=200)
    )
    temporary_failures: Deque[Dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=200)
    )
    result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "message": self.message,
            "error": self.error,
            "progress_current": self.progress_current,
            "progress_total": self.progress_total,
            "cancellable": self.cancellable,
            "cancel_requested": self.cancel_requested,
            "metadata": dict(self.metadata),
            "counters": dict(self.counters),
            "item_status_counts": dict(self.item_status_counts),
            "recent_items": list(self.recent_items),
            "recent_errors": list(self.recent_errors),
            "failed_items": list(self.failed_items),
            "unavailable_items": list(self.unavailable_items),
            "missing_failures": list(self.missing_failures),
            "temporary_failures": list(self.temporary_failures),
            "result": self.result,
        }


class TaskContext:
    def __init__(self, manager: "BackgroundTaskManager", task_id: str):
        self._manager = manager
        self.task_id = task_id

    def set_message(self, message: str) -> None:
        self._manager._set_message(self.task_id, message)

    def set_total(self, total: int) -> None:
        self._manager._set_total(self.task_id, total)

    def increment_total(self, amount: int) -> None:
        self._manager._increment_total(self.task_id, amount)

    def advance(self, amount: int = 1) -> None:
        self._manager._advance(self.task_id, amount)

    def increment_counter(self, key: str, amount: int = 1) -> None:
        self._manager._increment_counter(self.task_id, key, amount)

    def set_metadata(self, key: str, value: Any) -> None:
        self._manager._set_metadata(self.task_id, key, value)

    def get_metadata(self, key: str, default: Any = None) -> Any:
        return self._manager._get_metadata(self.task_id, key, default)

    def add_error(self, message: str) -> None:
        self._manager._add_error(self.task_id, message)

    def mark_item(
        self, item_key: str, status: str, message: Optional[str] = None
    ) -> None:
        self._manager._mark_item(self.task_id, item_key, status, message)

    def mark_unavailable(self, item_key: str, message: str) -> None:
        self._manager._mark_unavailable(self.task_id, item_key, message)

    def mark_missing_failure(
        self, item_key: str, message: str, *, item_data: Optional[Dict[str, Any]] = None
    ) -> None:
        self._manager._mark_missing_failure(
            self.task_id, item_key, message, item_data=item_data
        )

    def mark_temporary_failure(
        self, item_key: str, message: str, *, item_data: Optional[Dict[str, Any]] = None
    ) -> None:
        self._manager._mark_temporary_failure(
            self.task_id, item_key, message, item_data=item_data
        )

    def check_cancelled(self) -> None:
        if self._manager.is_cancel_requested(self.task_id):
            raise TaskCancelledError("Task cancellation requested")

    @property
    def cancel_requested(self) -> bool:
        return self._manager.is_cancel_requested(self.task_id)

    def complete(
        self, result: Optional[dict[str, Any]] = None, message: Optional[str] = None
    ) -> None:
        self._manager._complete(self.task_id, result, message)

    def cancel(
        self, result: Optional[dict[str, Any]] = None, message: Optional[str] = None
    ) -> None:
        self._manager._cancel(self.task_id, message or "Cancelled", result)

    def heartbeat(self, message: Optional[str] = None) -> None:
        self._manager._heartbeat(self.task_id, message)

    def fail(self, error: str) -> None:
        self._manager._fail(self.task_id, error)


class BackgroundTaskManager:
    def __init__(self, max_workers: int = 4, retain_completed: int = 120):
        self._lock = threading.RLock()
        self._tasks: Dict[str, _TaskRecord] = {}
        self._retain_completed = max(10, int(retain_completed))
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="atelier-task"
        )

    def create_task(
        self,
        *,
        kind: str,
        title: str,
        runner: Callable[[TaskContext], dict[str, Any] | None],
        metadata: Optional[dict[str, Any]] = None,
        cancellable: bool = True,
    ) -> dict[str, Any]:
        task_id = uuid.uuid4().hex[:12]
        record = _TaskRecord(
            id=task_id,
            kind=kind,
            title=title,
            cancellable=cancellable,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._tasks[task_id] = record

        def _run() -> None:
            context = TaskContext(self, task_id)
            self._start(task_id)
            try:
                result = runner(context)
                if self.is_cancel_requested(task_id) and self._get_status(
                    task_id
                ) not in {"completed", "failed"}:
                    self._cancel(task_id, "Cancelled", None)
                    return
                if self._get_status(task_id) == "running":
                    self._complete(task_id, result or None, None)
            except TaskCancelledError:
                self._cancel(task_id, "Cancelled", None)
            except Exception as exc:
                self._fail(task_id, str(exc))
            finally:
                self._trim_completed_tasks()

        self._executor.submit(_run)
        return self.get_task(task_id)

    def list_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            records = sorted(
                self._tasks.values(),
                key=lambda task: task.created_at,
                reverse=True,
            )[: max(1, int(limit))]
            return [record.to_dict() for record in records]

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                raise KeyError(task_id)
            return record.to_dict()

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                raise KeyError(task_id)
            if not record.cancellable:
                raise ValueError("Task is not cancellable")
            if record.status in {"completed", "failed", "cancelled"}:
                return record.to_dict()
            record.cancel_requested = True
            if record.status == "queued":
                record.status = "cancelled"
                record.message = "Cancelled"
                record.finished_at = _utc_now()
                record.updated_at = _utc_now()
            else:
                record.message = "Cancellation requested"
                record.updated_at = _utc_now()
            return record.to_dict()

    def is_cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            record = self._tasks.get(task_id)
            return bool(record and record.cancel_requested)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _get_status(self, task_id: str) -> str:
        with self._lock:
            record = self._tasks[task_id]
            return record.status

    def _start(self, task_id: str) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.status = "running"
            record.started_at = _utc_now()
            record.message = "Running"
            record.updated_at = _utc_now()

    def _set_message(self, task_id: str, message: str) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.message = message
            record.updated_at = _utc_now()

    def _heartbeat(self, task_id: str, message: Optional[str]) -> None:
        with self._lock:
            record = self._tasks[task_id]
            if message is not None:
                record.message = message
            record.updated_at = _utc_now()

    def _set_total(self, task_id: str, total: int) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.progress_total = max(0, int(total))
            record.updated_at = _utc_now()

    def _increment_total(self, task_id: str, amount: int) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.progress_total = max(0, record.progress_total + int(amount))
            record.updated_at = _utc_now()

    def _advance(self, task_id: str, amount: int) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.progress_current = max(0, record.progress_current + int(amount))
            record.updated_at = _utc_now()

    def _increment_counter(self, task_id: str, key: str, amount: int) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.counters[key] = int(record.counters.get(key, 0)) + int(amount)
            record.updated_at = _utc_now()

    def _set_metadata(self, task_id: str, key: str, value: Any) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.metadata[key] = value
            record.updated_at = _utc_now()

    def _get_metadata(self, task_id: str, key: str, default: Any = None) -> Any:
        with self._lock:
            record = self._tasks[task_id]
            return record.metadata.get(key, default)

    def _add_error(self, task_id: str, message: str) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.recent_errors.appendleft(message)
            record.updated_at = _utc_now()

    def _mark_item(
        self, task_id: str, item_key: str, status: str, message: Optional[str]
    ) -> None:
        normalized_key = str(item_key)
        normalized_status = str(status)
        with self._lock:
            record = self._tasks[task_id]
            previous_status = record.item_states.get(normalized_key)
            if previous_status:
                previous_count = int(record.item_status_counts.get(previous_status, 0))
                if previous_count > 0:
                    record.item_status_counts[previous_status] = previous_count - 1
            record.item_states[normalized_key] = normalized_status
            record.item_status_counts[normalized_status] = (
                int(record.item_status_counts.get(normalized_status, 0)) + 1
            )
            record.recent_items.appendleft(
                {
                    "item_key": normalized_key,
                    "status": normalized_status,
                    "message": message,
                    "timestamp": _utc_now().isoformat(),
                }
            )
            if normalized_status == "failed":
                record.failed_items.appendleft(
                    {
                        "item_key": normalized_key,
                        "message": message,
                        "timestamp": _utc_now().isoformat(),
                    }
                )
            record.updated_at = _utc_now()

    def _mark_unavailable(self, task_id: str, item_key: str, message: str) -> None:
        normalized_key = str(item_key)
        with self._lock:
            record = self._tasks[task_id]
            record.unavailable_items.appendleft(
                {
                    "item_key": normalized_key,
                    "message": message,
                    "timestamp": _utc_now().isoformat(),
                }
            )
            record.updated_at = _utc_now()

    def _mark_missing_failure(
        self,
        task_id: str,
        item_key: str,
        message: str,
        *,
        item_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized_key = str(item_key)
        with self._lock:
            record = self._tasks[task_id]
            entry: Dict[str, Any] = {
                "item_key": normalized_key,
                "message": message,
                "failure_type": "missing",
                "timestamp": _utc_now().isoformat(),
            }
            if item_data:
                entry["item_data"] = item_data
            record.missing_failures.appendleft(entry)
            record.updated_at = _utc_now()

    def _mark_temporary_failure(
        self,
        task_id: str,
        item_key: str,
        message: str,
        *,
        item_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized_key = str(item_key)
        with self._lock:
            record = self._tasks[task_id]
            entry: Dict[str, Any] = {
                "item_key": normalized_key,
                "message": message,
                "failure_type": "temporary",
                "timestamp": _utc_now().isoformat(),
            }
            if item_data:
                entry["item_data"] = item_data
            record.temporary_failures.appendleft(entry)
            record.updated_at = _utc_now()

    def _complete(
        self, task_id: str, result: Optional[dict[str, Any]], message: Optional[str]
    ) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.status = "completed"
            record.finished_at = _utc_now()
            record.result = result
            record.message = message or record.message or "Completed"
            record.updated_at = _utc_now()

    def _cancel(
        self, task_id: str, message: str, result: Optional[dict[str, Any]]
    ) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.status = "cancelled"
            record.finished_at = _utc_now()
            record.message = message
            if result is not None:
                record.result = result
            record.updated_at = _utc_now()

    def _fail(self, task_id: str, error: str) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.status = "failed"
            record.finished_at = _utc_now()
            record.error = error
            record.message = error
            record.recent_errors.appendleft(error)
            record.updated_at = _utc_now()

    def _trim_completed_tasks(self) -> None:
        with self._lock:
            completed = sorted(
                [
                    record
                    for record in self._tasks.values()
                    if record.status in {"completed", "failed", "cancelled"}
                ],
                key=lambda task: task.finished_at or task.created_at,
                reverse=True,
            )
            for record in completed[self._retain_completed :]:
                self._tasks.pop(record.id, None)
