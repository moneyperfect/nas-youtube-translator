from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ytsubviewer.config import Settings
from ytsubviewer.job_state import load_job_state
from ytsubviewer.models import TranslationControlConfig


logger = logging.getLogger(__name__)


@dataclass
class TaskSnapshot:
    task_id: str
    kind: str = "generate"
    url: str = ""
    title: str = ""
    duration_seconds: int | None = None
    strategy_text: str = ""
    thumbnail_url: str | None = None
    work_dir: str = ""
    status: str = "pending"
    stage: str = "pending"
    progress: float = 0.0
    log_lines: list[str] = field(default_factory=list)
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    translation_controls: dict[str, Any] = field(default_factory=dict)
    glossary_text: str = ""
    protected_terms_text: str = ""
    performance_mode: str = "balanced"
    bilingual: bool = False
    preview: bool = False
    request: dict[str, Any] = field(default_factory=dict)
    output_label: str = ""
    source_task_id: str = ""
    batch_id: str = ""
    current_step: int | None = None
    total_steps: int | None = None
    completed_items: int | None = None
    total_items: int | None = None
    eta_seconds: float | None = None
    cancel_requested: bool = False

    @property
    def job_id(self) -> str:
        return self.task_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskSnapshot":
        return cls(
            task_id=str(payload.get("task_id") or payload.get("job_id") or "").strip(),
            kind=str(payload.get("kind", "generate")).strip() or "generate",
            url=str(payload.get("url", "")).strip(),
            title=str(payload.get("title", "")).strip(),
            duration_seconds=int(payload["duration_seconds"]) if payload.get("duration_seconds") else None,
            strategy_text=str(payload.get("strategy_text", "")).strip(),
            thumbnail_url=str(payload.get("thumbnail_url")).strip() if payload.get("thumbnail_url") else None,
            work_dir=str(payload.get("work_dir", "")).strip(),
            status=str(payload.get("status", "pending")).strip() or "pending",
            stage=str(payload.get("stage", "pending")).strip() or "pending",
            progress=float(payload.get("progress", 0.0) or 0.0),
            log_lines=[str(item) for item in (payload.get("log_lines") or []) if str(item).strip()],
            error=str(payload.get("error", "")).strip(),
            created_at=float(payload.get("created_at", time.time()) or time.time()),
            started_at=float(payload["started_at"]) if payload.get("started_at") else None,
            updated_at=float(payload.get("updated_at", time.time()) or time.time()),
            finished_at=float(payload["finished_at"]) if payload.get("finished_at") else None,
            translation_controls=dict(payload.get("translation_controls") or {}),
            glossary_text=str(payload.get("glossary_text", "") or ""),
            protected_terms_text=str(payload.get("protected_terms_text", "") or ""),
            performance_mode=str(payload.get("performance_mode", "balanced") or "balanced"),
            bilingual=bool(payload.get("bilingual", False)),
            preview=bool(payload.get("preview", False)),
            request=dict(payload.get("request") or {}),
            output_label=str(payload.get("output_label", "") or ""),
            source_task_id=str(payload.get("source_task_id", "") or ""),
            batch_id=str(payload.get("batch_id", "") or ""),
            current_step=int(payload["current_step"]) if payload.get("current_step") is not None else None,
            total_steps=int(payload["total_steps"]) if payload.get("total_steps") is not None else None,
            completed_items=int(payload["completed_items"]) if payload.get("completed_items") is not None else None,
            total_items=int(payload["total_items"]) if payload.get("total_items") is not None else None,
            eta_seconds=float(payload["eta_seconds"]) if payload.get("eta_seconds") is not None else None,
            cancel_requested=bool(payload.get("cancel_requested", False)),
        )


GenerationJobSnapshot = TaskSnapshot


class TaskCancelledError(RuntimeError):
    pass


class BackgroundGenerationManager:
    def __init__(self, settings: Settings, pipeline) -> None:
        self.settings = settings
        self.pipeline = pipeline
        self.runtime_dir = self.settings.data_root / ".runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir = self.runtime_dir / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.current_snapshot_path = self.runtime_dir / "current_generation_job.json"
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskSnapshot] = {}
        self._active_task_ids: set[str] = set()
        self._worker_threads: dict[str, threading.Thread] = {}
        self._max_concurrent_tasks: int = getattr(settings, "max_concurrent_tasks", 3)
        self._load_persisted_tasks()

    def bind_pipeline(self, pipeline) -> None:
        self.pipeline = pipeline

    def start_generation(
        self,
        *,
        url: str,
        metadata,
        strategy_text: str,
        controls: TranslationControlConfig,
        glossary_text: str,
        protected_terms_text: str,
        performance_mode: str = "balanced",
        batch_id: str = "",
    ) -> GenerationJobSnapshot:
        work_dir = self.pipeline.youtube.prepare_work_dir(metadata)
        with self._lock:
            duplicate = self._find_duplicate_generation_unlocked(work_dir)
            if duplicate is not None:
                return duplicate

            snapshot = TaskSnapshot(
                task_id=uuid.uuid4().hex,
                kind="generate",
                url=url,
                title=metadata.title,
                duration_seconds=metadata.duration_seconds,
                strategy_text=strategy_text,
                thumbnail_url=metadata.thumbnail_url,
                work_dir=str(work_dir),
                status="pending",
                stage="queue",
                progress=0.01,
                log_lines=[
                    "任务已加入后台队列。",
                    "即使刷新页面，任务也会继续运行。",
                ],
                translation_controls=controls.to_dict(),
                glossary_text=glossary_text,
                protected_terms_text=protected_terms_text,
                performance_mode=performance_mode,
                request={
                    "url": url,
                    "strategy_text": strategy_text,
                    "translation_controls": controls.to_dict(),
                    "glossary_text": glossary_text,
                    "protected_terms_text": protected_terms_text,
                    "performance_mode": performance_mode,
                },
                batch_id=batch_id,
            )
            self._tasks[snapshot.task_id] = snapshot
            self._write_snapshot_unlocked(snapshot)
            self._dispatch_next_unlocked()
            return snapshot

    def start_export(
        self,
        *,
        state: dict[str, Any],
        bilingual: bool,
        preview: bool = False,
        performance_mode: str = "balanced",
        source_task_id: str = "",
    ) -> TaskSnapshot:
        work_dir = str(state.get("work_dir", "")).strip()
        if not work_dir:
            raise RuntimeError("当前任务缺少 work_dir，无法导出。")

        title = str(state.get("title", "")).strip()
        duration_seconds = int(state["duration_seconds"]) if state.get("duration_seconds") else None
        output_label = "双语 MP4" if bilingual else "中文字幕 MP4"
        with self._lock:
            duplicate = self._find_duplicate_export_unlocked(work_dir, bilingual, preview)
            if duplicate is not None:
                return duplicate

            snapshot = TaskSnapshot(
                task_id=uuid.uuid4().hex,
                kind="export",
                title=title,
                duration_seconds=duration_seconds,
                work_dir=work_dir,
                status="pending",
                stage="queue",
                progress=0.01,
                log_lines=[
                    f"{output_label} 导出已加入后台队列。",
                    "即使刷新页面，导出也会继续运行。",
                ],
                performance_mode=performance_mode,
                bilingual=bilingual,
                preview=preview,
                request={
                    "work_dir": work_dir,
                    "bilingual": bilingual,
                    "preview": preview,
                    "performance_mode": performance_mode,
                },
                output_label=output_label,
                source_task_id=source_task_id,
            )
            self._tasks[snapshot.task_id] = snapshot
            self._write_snapshot_unlocked(snapshot)
            self._dispatch_next_unlocked()
            return snapshot

    def list_tasks(self, *, limit: int = 100) -> list[TaskSnapshot]:
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda item: (item.created_at, item.updated_at),
                reverse=True,
            )
            return [TaskSnapshot.from_dict(task.to_dict()) for task in tasks[:limit]]

    def get_task(self, task_id: str) -> TaskSnapshot | None:
        if not task_id:
            return None
        with self._lock:
            snapshot = self._tasks.get(task_id)
            if snapshot is None:
                return None
            return TaskSnapshot.from_dict(snapshot.to_dict())

    def get_current_snapshot(self) -> GenerationJobSnapshot | None:
        with self._lock:
            generation_tasks = [task for task in self._tasks.values() if task.kind == "generate"]
            if generation_tasks:
                generation_tasks.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
                return TaskSnapshot.from_dict(generation_tasks[0].to_dict())

        if not self.current_snapshot_path.exists():
            return None
        try:
            payload = json.loads(self.current_snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        snapshot = TaskSnapshot.from_dict(payload)
        if snapshot.status in {"pending", "running"} and not self._is_thread_alive(snapshot.task_id):
            idle_seconds = time.time() - snapshot.updated_at
            if idle_seconds > 8:
                snapshot.status = "failed"
                snapshot.stage = "failed"
                snapshot.error = snapshot.error or "后台任务已中断，请重新开始生成。 (涓柇)"
                snapshot.log_lines = self._append_log(snapshot.log_lines, snapshot.error)
                snapshot.finished_at = time.time()
                self.current_snapshot_path.write_text(
                    json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        return snapshot

    def get_active_task(self) -> TaskSnapshot | None:
        with self._lock:
            for tid in self._active_task_ids:
                if tid in self._tasks:
                    return TaskSnapshot.from_dict(self._tasks[tid].to_dict())
            pending = sorted(
                (task for task in self._tasks.values() if task.status == "pending"),
                key=lambda item: item.created_at,
            )
            if pending:
                return TaskSnapshot.from_dict(pending[0].to_dict())
        return None

    def clear_if_finished(self, job_id: str) -> None:
        return

    def cancel_task(self, task_id: str) -> TaskSnapshot:
        with self._lock:
            snapshot = self._require_task_unlocked(task_id)
            if snapshot.status == "pending":
                snapshot.status = "cancelled"
                snapshot.stage = "cancelled"
                snapshot.finished_at = time.time()
                snapshot.error = ""
                snapshot.log_lines = self._append_log(snapshot.log_lines, "任务已取消。")
                self._write_snapshot_unlocked(snapshot)
                self._dispatch_next_unlocked()
                return TaskSnapshot.from_dict(snapshot.to_dict())
            if snapshot.status == "running":
                snapshot.cancel_requested = True
                snapshot.log_lines = self._append_log(snapshot.log_lines, "已请求取消，当前阶段结束后会停止。")
                self._write_snapshot_unlocked(snapshot)
                return TaskSnapshot.from_dict(snapshot.to_dict())
            return TaskSnapshot.from_dict(snapshot.to_dict())

    def retry_task(self, task_id: str) -> TaskSnapshot:
        with self._lock:
            snapshot = self._require_task_unlocked(task_id)
            if snapshot.status not in {"failed", "completed", "cancelled"}:
                return TaskSnapshot.from_dict(snapshot.to_dict())

        if snapshot.kind == "generate":
            controls = TranslationControlConfig.from_dict(snapshot.translation_controls)
            metadata = self.pipeline.analyze(snapshot.url)
            return self.start_generation(
                url=snapshot.url,
                metadata=metadata,
                strategy_text=snapshot.strategy_text,
                controls=controls,
                glossary_text=snapshot.glossary_text,
                protected_terms_text=snapshot.protected_terms_text,
                performance_mode=snapshot.performance_mode,
                batch_id=snapshot.batch_id,
            )

        work_dir = Path(snapshot.work_dir)
        state = load_job_state(work_dir)
        if not state:
            raise RuntimeError("当前任务缺少可重试的状态文件。")
        return self.start_export(
            state=state,
            bilingual=snapshot.bilingual,
            preview=snapshot.preview,
            performance_mode=snapshot.performance_mode,
            source_task_id=snapshot.source_task_id or snapshot.task_id,
        )

    def _load_persisted_tasks(self) -> None:
        for path in sorted(self.tasks_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            snapshot = TaskSnapshot.from_dict(payload)
            if not snapshot.task_id:
                continue
            if snapshot.status in {"pending", "running"}:
                snapshot.status = "pending"
                snapshot.stage = "queue"
                snapshot.cancel_requested = False
                snapshot.error = ""
                snapshot.log_lines = self._append_log(
                    snapshot.log_lines,
                    "应用已重新启动，任务已恢复到后台队列。已翻译的字幕将被保留。",
                )
            self._tasks[snapshot.task_id] = snapshot
            self._write_snapshot_unlocked(snapshot)
        with self._lock:
            self._dispatch_next_unlocked()

    def _dispatch_next_unlocked(self) -> None:
        # Clean up finished threads
        finished = [tid for tid, t in self._worker_threads.items() if not t.is_alive()]
        for tid in finished:
            self._worker_threads.pop(tid, None)
            self._active_task_ids.discard(tid)

        while len(self._worker_threads) < self._max_concurrent_tasks:
            pending = sorted(
                (
                    task
                    for task in self._tasks.values()
                    if task.status == "pending" and not task.cancel_requested and task.task_id not in self._worker_threads
                ),
                key=lambda item: item.created_at,
            )
            if not pending:
                break
            snapshot = pending[0]
            self._active_task_ids.add(snapshot.task_id)
            worker = threading.Thread(
                target=self._run_task,
                args=(snapshot.task_id,),
                name=f"task-{snapshot.kind}-{snapshot.task_id[:8]}",
                daemon=True,
            )
            self._worker_threads[snapshot.task_id] = worker
            worker.start()

        if not self._worker_threads:
            self._refresh_current_snapshot_file_unlocked()

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            snapshot = self._require_task_unlocked(task_id)
            snapshot.status = "running"
            snapshot.stage = "starting"
            snapshot.started_at = snapshot.started_at or time.time()
            snapshot.progress = max(snapshot.progress, 0.02)
            self._write_snapshot_unlocked(snapshot)

        try:
            if snapshot.kind == "generate":
                self._run_generation_task(task_id)
            elif snapshot.kind == "export":
                self._run_export_task(task_id)
            else:
                raise RuntimeError(f"Unsupported task kind: {snapshot.kind}")
        except TaskCancelledError:
            with self._lock:
                latest = self._require_task_unlocked(task_id)
                latest.status = "cancelled"
                latest.stage = "cancelled"
                latest.finished_at = time.time()
                latest.error = ""
                latest.log_lines = self._append_log(latest.log_lines, "任务已取消。")
                self._write_snapshot_unlocked(latest)
        except Exception as exc:
            logger.exception("Background task failed for task_id=%s kind=%s", task_id, snapshot.kind)
            with self._lock:
                latest = self._require_task_unlocked(task_id)
                latest.status = "failed"
                latest.stage = "failed"
                latest.progress = max(latest.progress, 0.01)
                latest.finished_at = time.time()
                latest.error = str(exc)
                latest.log_lines = self._append_log(latest.log_lines, f"处理失败：{exc}")
                self._write_snapshot_unlocked(latest)
        finally:
            with self._lock:
                self._active_task_ids.discard(task_id)
                self._worker_threads.pop(task_id, None)
                self._dispatch_next_unlocked()

    def _run_generation_task(self, task_id: str) -> None:
        snapshot = self.get_task(task_id)
        if snapshot is None:
            raise RuntimeError("任务不存在。")
        controls = TranslationControlConfig.from_dict(snapshot.translation_controls)
        event_stream = self._generate_event_stream(snapshot.url, controls, snapshot.performance_mode)
        for event in event_stream:
            with self._lock:
                latest = self._require_task_unlocked(task_id)
                if latest.cancel_requested:
                    raise TaskCancelledError("任务已取消。")
                latest.status = "running"
                latest.progress = event.progress
                latest.stage = event.stage or self._infer_stage(latest.kind, event.message, event.progress)
                latest.current_step = event.current_step
                latest.total_steps = event.total_steps
                latest.completed_items = event.completed_items
                latest.total_items = event.total_items
                latest.eta_seconds = event.eta_seconds
                latest.log_lines = self._append_log(latest.log_lines, event.message)
                if event.artifacts is not None:
                    latest.work_dir = str(event.artifacts.work_dir)
                    latest.title = event.artifacts.title
                    latest.duration_seconds = event.artifacts.duration_seconds
                if event.artifacts is not None and event.progress >= 1.0:
                    latest.status = "completed"
                    latest.stage = "completed"
                    latest.finished_at = time.time()
                    latest.progress = 1.0
                self._write_snapshot_unlocked(latest)

    def _run_export_task(self, task_id: str) -> None:
        snapshot = self.get_task(task_id)
        if snapshot is None:
            raise RuntimeError("任务不存在。")
        state = load_job_state(Path(snapshot.work_dir))
        if not state:
            raise RuntimeError("当前任务缺少可导出的 job_state.json。")
        event_stream = self._export_event_stream(
            state,
            bilingual=snapshot.bilingual,
            preview=snapshot.preview,
            performance_mode=snapshot.performance_mode,
        )
        for event in event_stream:
            with self._lock:
                latest = self._require_task_unlocked(task_id)
                if latest.cancel_requested:
                    raise TaskCancelledError("任务已取消。")
                latest.status = "running"
                latest.progress = event.progress
                latest.stage = event.stage or "export"
                latest.current_step = event.current_step
                latest.total_steps = event.total_steps
                latest.completed_items = event.completed_items
                latest.total_items = event.total_items
                latest.eta_seconds = event.eta_seconds
                latest.log_lines = self._append_log(latest.log_lines, event.message)
                if event.artifacts is not None:
                    latest.work_dir = str(event.artifacts.work_dir)
                    latest.title = event.artifacts.title
                    latest.duration_seconds = event.artifacts.duration_seconds
                if event.artifacts is not None and event.progress >= 1.0:
                    latest.status = "completed"
                    latest.stage = "completed"
                    latest.finished_at = time.time()
                    latest.progress = 1.0
                self._write_snapshot_unlocked(latest)

    def _find_duplicate_generation_unlocked(self, work_dir: Path) -> TaskSnapshot | None:
        target = str(work_dir)
        for task in self._tasks.values():
            if task.kind != "generate":
                continue
            if task.status not in {"pending", "running"}:
                continue
            if task.work_dir == target:
                return task
        return None

    def _generate_event_stream(self, url: str, controls: TranslationControlConfig, performance_mode: str):
        try:
            return self.pipeline.generate_events(url, controls=controls, performance_mode=performance_mode)
        except TypeError:
            return self.pipeline.generate_events(url, controls=controls)

    def _export_event_stream(
        self,
        state: dict[str, Any],
        *,
        bilingual: bool,
        preview: bool,
        performance_mode: str,
    ):
        try:
            return self.pipeline.export_video_events(
                state,
                bilingual=bilingual,
                preview=preview,
                performance_mode=performance_mode,
            )
        except TypeError:
            return self.pipeline.export_video_events(state, bilingual=bilingual)

    def _find_duplicate_export_unlocked(self, work_dir: str, bilingual: bool, preview: bool) -> TaskSnapshot | None:
        for task in self._tasks.values():
            if task.kind != "export":
                continue
            if task.status not in {"pending", "running"}:
                continue
            if task.work_dir == work_dir and task.bilingual == bilingual and task.preview == preview:
                return task
        return None

    def _require_task_unlocked(self, task_id: str) -> TaskSnapshot:
        snapshot = self._tasks.get(task_id)
        if snapshot is None:
            raise RuntimeError("任务不存在。")
        return snapshot

    def _write_snapshot_unlocked(self, snapshot: TaskSnapshot) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        snapshot.updated_at = time.time()
        self._tasks[snapshot.task_id] = snapshot
        path = self.tasks_dir / f"{snapshot.task_id}.json"
        path.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._refresh_current_snapshot_file_unlocked()

    def _refresh_current_snapshot_file_unlocked(self) -> None:
        generation_tasks = [task for task in self._tasks.values() if task.kind == "generate"]
        if not generation_tasks:
            self.current_snapshot_path.unlink(missing_ok=True)
            return
        generation_tasks.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
        latest = generation_tasks[0]
        self.current_snapshot_path.write_text(
            json.dumps(latest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _is_thread_alive(self, task_id: str) -> bool:
        with self._lock:
            thread = self._worker_threads.get(task_id)
            return thread is not None and thread.is_alive()

    @staticmethod
    def _infer_stage(kind: str, message: str, progress: float) -> str:
        lowered = message.lower()
        if kind == "export":
            return "export"
        if "分析" in message or "metadata" in lowered:
            return "analyze"
        if "下载" in message or "download" in lowered:
            return "download"
        if "字幕" in message and ("解析" in message or "source" in lowered):
            return "subtitle_source"
        if "转写" in message or "transcrib" in lowered:
            return "transcribe"
        if "翻译" in message or "translate" in lowered:
            return "translate"
        if "质量" in message or "qa" in lowered:
            return "qa"
        if progress >= 1.0:
            return "completed"
        return "running"

    @staticmethod
    def _append_log(lines: list[str], message: str, *, limit: int = 60) -> list[str]:
        updated = list(lines)
        if not updated or updated[-1] != message:
            updated.append(message)
        return updated[-limit:]


BackgroundTaskManager = BackgroundGenerationManager
