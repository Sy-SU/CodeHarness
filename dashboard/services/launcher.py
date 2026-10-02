"""Local, serial, bounded jobs; never invoke a shell or accept browser config paths."""
from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import asdict
from pathlib import Path

from dotenv import dotenv_values

from agent.config import ClientSettings
from agent.core.harness import HarnessPolicy
from agent.execution import ExecutionService, RunRequest, fingerprint
from agent.workspace.task import TASK_ID_PATTERN, TaskState, TaskWorkspace


class LaunchConflict(ValueError):
    pass


class LaunchBusy(ValueError):
    pass


def default_service(settings):
    import os
    from agent.models.registry import ModelRegistry
    values = dict(dotenv_values(settings.env_file)) if settings.env_file else {}
    values.update(os.environ)
    client_settings = ClientSettings.from_environment(values,
        model_config_override=str(settings.model_config) if settings.model_config else None)
    registry = ModelRegistry.from_yaml(client_settings.model_config, environ=values)
    return ExecutionService(client_settings, settings.workspace_root, registry=registry)


class LaunchService:
    def __init__(self, settings, *, service_factory=None, autostart=True, capacity=8):
        self.settings = settings
        self.service_factory = service_factory or (lambda: default_service(settings))
        self.capacity = capacity
        self.queue = queue.Queue(capacity)
        self.lock = threading.RLock()
        self.active = set()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._worker, name="codeharness-local-jobs", daemon=True)
        if autostart:
            self.thread.start()

    def _store(self, request_id, *, create=False):
        if not isinstance(request_id, str) or not TASK_ID_PATTERN.fullmatch(request_id) or request_id in {".", ".."}:
            raise ValueError("Invalid request ID")
        base = self.settings.workspace_root.resolve() / ".jobs"
        if base.is_symlink():
            raise ValueError("Unsafe job storage")
        if create:
            return TaskWorkspace.create(base, request_id, "local-job", "job")
        root = base / request_id
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Job unavailable")
        return TaskWorkspace(root, TaskState(request_id, "local-job", mode="job"))

    @staticmethod
    def _public(job):
        value = {key: job.get(key) for key in ("request_id", "task_id", "status", "operation", "error_kind")}
        if job.get("contest_run_id"):
            value["contest_run_id"] = job["contest_run_id"]
        return value

    def job(self, request_id):
        with self.lock:
            job = self._store(request_id).read_json("job.json")
            if job["status"] in {"queued", "running"} and request_id not in self.active:
                job["status"] = "interrupted"  # previous server; never automatically restart
            return self._public(job)

    def task_job(self, task_id):
        return self._resource_job("task_id", task_id)

    def contest_job(self, run_id):
        return self._resource_job("contest_run_id", run_id)

    def _resource_job(self, key, resource_id):
        base = self.settings.workspace_root.resolve() / ".jobs"
        if base.is_symlink():
            return None
        # UUIDs have no temporal order. Prefer an active intent, then the latest
        # durable record, so an old finished job cannot permit a second resume.
        with self.lock:
            paths = []
            for path in base.glob("*/job.json"):
                try:
                    if not path.parent.is_symlink():
                        paths.append((path.parent.name in self.active, path.stat().st_mtime_ns, path))
                except OSError:
                    continue
            paths.sort(key=lambda entry: entry[:2], reverse=True)
        for _, _, path in paths[:1000]:
            try:
                job = self._store(path.parent.name).read_json("job.json")
                if job.get(key) == resource_id:
                    return self.job(path.parent.name)
            except (OSError, ValueError, KeyError):
                continue
        return None

    def can_resume_contest(self, run_id):
        from experiments.contest import contest_store
        job = self.contest_job(run_id)
        if job is None or job["status"] not in {"failed", "interrupted", "finished"}:
            return False
        try:
            record = contest_store(self.settings.workspace_root, run_id).read_json("contest.json")
            return record["status"] in {"queued", "running", "interrupted"}
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def can_resume(self, task_id):
        job = self.task_job(task_id)
        if job is None or job["status"] not in {"failed", "interrupted", "finished"}:
            return False
        try:
            workspace = TaskWorkspace.load(self.settings.workspace_root, task_id)
            checkpoint = workspace.read_json("checkpoint.json")
            return checkpoint["phase"] != "DONE" or (
                workspace.state.terminal_status == "result_unknown"
                and checkpoint["test_stage"] == "wait" and not checkpoint.get("pending_operation")
                and workspace.state.last_submission_id is not None)
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def enqueue(self, request_id, request, *, resume_task_id=None):
        operation = "resume" if resume_task_id else "solve"
        request_data = asdict(request)
        if request_data.get("contest_id") is None:
            request_data.pop("contest_id", None)  # old single-task nonces stay equivalent
        if request_data.get("sample_checking") is None:
            request_data.pop("sample_checking", None)
        intent = fingerprint({"operation": operation, "request": request_data, "task_id": resume_task_id})
        with self.lock:
            try:
                existing = self._store(request_id).read_json("job.json")
            except (FileNotFoundError, ValueError):
                existing = None
            if existing is not None:
                if existing["intent"] != intent:
                    raise LaunchConflict("Request ID already belongs to a different task")
                return self.job(request_id)
            if self.stop.is_set() or self.queue.full():
                raise LaunchBusy("Local task queue is full or stopped")
            service = self.service_factory()
            try:
                if resume_task_id:
                    if not self.can_resume(resume_task_id):
                        raise LaunchConflict("Task is active or has no resumable checkpoint")
                    workspace = TaskWorkspace.load(self.settings.workspace_root, resume_task_id)
                    if workspace.state.configuration_fingerprint != fingerprint(service.snapshot(request)):
                        raise LaunchConflict("Resume config differs from the saved task")
                else:
                    workspace = service.create_workspace(request, "ui-" + uuid.uuid4().hex[:20])
                store = self._store(request_id, create=True)
                job = {"request_id": request_id, "task_id": workspace.state.task_id,
                    "operation": operation, "intent": intent, "status": "queued",
                    "request": request_data, "error_kind": None}
                store.write_json("job.json", job)
                self.active.add(request_id)
                self.queue.put_nowait((store, job, service, request, workspace))
                return self._public(job)
            except BaseException:
                service.close()
                raise

    def resume_request(self, task_id):
        job = self.task_job(task_id)
        if not job:
            raise ValueError("Task was not launched by this Dashboard")
        data = dict(self._store(job["request_id"]).read_json("job.json")["request"])
        data["policy"] = HarnessPolicy(**data["policy"])
        data.setdefault("sample_checking", None)  # restore, never upgrade historical jobs
        return RunRequest(**data)

    def enqueue_contest(self, request_id, request, *, resume_run_id=None):
        from experiments.contest import ContestRunner, contest_store
        intent = fingerprint({"operation": "contest", "request": asdict(request),
                              "resume_run_id": resume_run_id})
        with self.lock:
            try:
                existing = self._store(request_id).read_json("job.json")
            except (FileNotFoundError, ValueError):
                existing = None
            if existing is not None:
                if existing["intent"] != intent:
                    raise LaunchConflict("Request ID already belongs to a different task")
                return self.job(request_id)
            if self.stop.is_set() or self.queue.full():
                raise LaunchBusy("Local task queue is full or stopped")
            service = self.service_factory()
            try:
                run_id = resume_run_id or "contest-ui-" + uuid.uuid4().hex[:20]
                if resume_run_id:
                    record = contest_store(self.settings.workspace_root, run_id).read_json("contest.json")
                    if record["status"] not in {"running", "interrupted", "queued"}:
                        raise LaunchConflict("Contest has no resumable run")
                    if any(self._store(active).read_json("job.json").get("contest_run_id") == run_id
                           for active in self.active):
                        raise LaunchConflict("Contest run is already active")
                    if record["configuration_fingerprint"] != ContestRunner(service)._record(request, run_id)["configuration_fingerprint"]:
                        raise LaunchConflict("Resume config differs from the saved contest")
                else:
                    ContestRunner(service).prepare(request, run_id)
                store = self._store(request_id, create=True)
                job = {"request_id": request_id, "task_id": None, "contest_run_id": run_id,
                    "operation": "contest", "intent": intent, "status": "queued",
                    "request": asdict(request), "error_kind": None}
                store.write_json("job.json", job)
                self.active.add(request_id)
                self.queue.put_nowait((store, job, service, request, None))
                return self._public(job)
            except BaseException:
                service.close()
                raise

    def contest_request(self, run_id):
        from experiments.contest import ContestRequest, contest_store
        record = contest_store(self.settings.workspace_root, run_id).read_json("contest.json")
        return ContestRequest.from_dict(record["request"])

    def _execute(self, item):
        store, job, service, request, workspace = item
        try:
            job["status"] = "running"
            store.write_json("job.json", job)
            if job["operation"] == "contest":
                from experiments.contest import ContestRunner
                ContestRunner(service).run(request, job["contest_run_id"], resume=True)
            else:
                service.run(request, workspace.state.task_id, workspace=workspace,
                            resume=job["operation"] == "resume")
            job["status"] = "finished"
        except Exception as exc:
            job["status"], job["error_kind"] = "failed", type(exc).__name__
            # Do not modify a harness checkpoint or pretend uncertain effects are WA.
            if workspace is not None and not workspace.state.terminal_status:
                workspace.state.terminal_status = "result_unknown" if workspace.state.llm_call_count else "internal_failure"
                workspace.state.termination_reason = "local_launcher_runtime_failure"
                workspace.state.current_phase = "DONE"
                workspace.save_state()
        finally:
            store.write_json("job.json", job)
            service.close()
            with self.lock:
                self.active.discard(job["request_id"])
            self.queue.task_done()

    def _worker(self):
        while not self.stop.is_set():
            try:
                item = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            self._execute(item)

    def run_next(self):
        """Deterministic test hook, using the exact production job execution path."""
        self._execute(self.queue.get_nowait())

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1)
        while True:
            try:
                store, job, service, _, _ = self.queue.get_nowait()
            except queue.Empty:
                break
            job["status"] = "interrupted"
            store.write_json("job.json", job)
            service.close()
            self.active.discard(job["request_id"])
            self.queue.task_done()
