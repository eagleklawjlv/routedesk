"""Background work so the window stays responsive."""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from everoute.oauth import AuthError, OAuthClient, run_browser_login
from everoute.waypoints import ApplyJob, CancelToken, execute_jobs, sleep_interruptible


class ApplyWorker(QThread):
    progress = Signal(object)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, jobs: list[ApplyJob], send, store, cancel: CancelToken, honor_pace: bool = True, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.send = send
        self.store = store
        self.cancel = cancel
        self.honor_pace = honor_pace

    def run(self) -> None:
        try:
            def on_progress(job: ApplyJob, index: int) -> None:
                self.progress.emit({"job": job.to_dict(), "index": index})

            def on_persist(_job: ApplyJob) -> None:
                self.store.save_jobs(self.jobs)

            execute_jobs(
                self.jobs,
                self.send,
                cancel=self.cancel,
                sleeper=lambda seconds: sleep_interruptible(seconds, self.cancel),
                on_progress=on_progress,
                on_persist=on_persist,
                honor_pace=self.honor_pace,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit([job.to_dict() for job in self.jobs])


class LoginWorker(QThread):
    """Browser login. The parent window is the QObject parent, never the scope list."""

    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)

    def __init__(self, client: OAuthClient, port: int, parent=None, *, scopes: list[str] | None = None):
        super().__init__(parent)
        self.client = client
        self.port = port
        self.scopes = scopes
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            import webbrowser

            tokens, claims = run_browser_login(
                self.client,
                self.port,
                webbrowser.open,
                scopes=self.scopes,
                cancel=self._cancel,
            )
        except AuthError as exc:
            if self._cancel.is_set():
                self.cancelled.emit(str(exc))
                return
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            if self._cancel.is_set():
                self.cancelled.emit("Login cancelled. No token was stored.")
                return
            self.failed.emit(str(exc))
            return
        if self._cancel.is_set():
            self.cancelled.emit("Login cancelled. No token was stored.")
            return
        self.succeeded.emit({"tokens": tokens, "claims": claims})


class UniverseWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, dest: str, parent=None):
        super().__init__(parent)
        self.dest = dest

    def run(self) -> None:
        try:
            from pathlib import Path

            from everoute.http import HttpClient
            from everoute.universe import build_cache_from_sde

            url = "https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip"
            blob = HttpClient().get_bytes(url, timeout=300)
            zip_path = Path(self.dest).with_suffix(".zip")
            zip_path.write_bytes(blob)
            meta = build_cache_from_sde(zip_path, Path(self.dest))
            try:
                zip_path.unlink()
            except OSError:
                pass
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(meta)


class StructureSearchWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, directory, characters, query, token_for, structure_id=None, parent=None):
        super().__init__(parent)
        self.directory = directory
        self.characters = characters
        self.query = query
        self.token_for = token_for
        self.structure_id = structure_id

    def run(self) -> None:
        try:
            if self.structure_id is None:
                discovery = self.directory.discover(self.characters, self.query, self.token_for)
            else:
                discovery = self.directory.resolve_id(self.structure_id, self.characters, self.token_for)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(discovery)
