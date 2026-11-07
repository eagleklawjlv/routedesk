"""Plan and send waypoint jobs.

Requests for one character are serial. Confirmed stops are not sent again.
There is no coded cap on route length. ESI itself is one destination per call.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum

from everoute.esi import Outcome, OutcomeKind
from everoute.http import NetworkError, RequestTimeout
from everoute.models import Library, new_id


class StopStatus(str, Enum):
    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    CANCELLED = "cancelled"


@dataclass
class ApplyStop:
    system_id: int
    system_name: str
    leg_type: str = ""
    destination_id: int | None = None
    destination_name: str = ""
    status: str = StopStatus.PENDING.value
    message: str = ""
    clear_other_waypoints: bool | None = None
    add_to_beginning: bool | None = None

    def __post_init__(self) -> None:
        if self.destination_id is None:
            self.destination_id = int(self.system_id)
        else:
            self.destination_id = int(self.destination_id)
        if not self.destination_name:
            self.destination_name = self.system_name

    def to_dict(self) -> dict:
        return {
            "system_id": int(self.system_id),
            "system_name": self.system_name,
            "destination_id": int(self.destination_id),
            "destination_name": self.destination_name,
            "leg_type": self.leg_type,
            "status": self.status,
            "message": self.message,
            "clear_other_waypoints": self.clear_other_waypoints,
            "add_to_beginning": self.add_to_beginning,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ApplyStop:
        system_id = int(data["system_id"])
        return cls(
            system_id=system_id,
            system_name=str(data.get("system_name") or system_id),
            destination_id=int(data["destination_id"]) if data.get("destination_id") is not None else system_id,
            destination_name=str(data.get("destination_name") or data.get("system_name") or system_id),
            leg_type=str(data.get("leg_type") or ""),
            status=str(data.get("status") or StopStatus.PENDING.value),
            message=str(data.get("message") or ""),
            clear_other_waypoints=data.get("clear_other_waypoints"),
            add_to_beginning=data.get("add_to_beginning"),
        )


@dataclass
class ApplyJob:
    id: str
    character_id: int
    character_name: str
    route_id: str
    route_name: str
    mode: str
    stops: list[ApplyStop] = field(default_factory=list)
    error: str = ""
    finished: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "character_id": int(self.character_id),
            "character_name": self.character_name,
            "route_id": self.route_id,
            "route_name": self.route_name,
            "mode": self.mode,
            "error": self.error,
            "finished": self.finished,
            "stops": [stop.to_dict() for stop in self.stops],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ApplyJob:
        return cls(
            id=str(data.get("id") or new_id()),
            character_id=int(data["character_id"]),
            character_name=str(data.get("character_name") or ""),
            route_id=str(data.get("route_id") or ""),
            route_name=str(data.get("route_name") or ""),
            mode=str(data.get("mode") or "replace"),
            stops=[ApplyStop.from_dict(item) for item in data.get("stops") or []],
            error=str(data.get("error") or ""),
            finished=bool(data.get("finished")),
        )


class CancelToken:
    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


def plan_jobs(
    library: Library,
    character_ids: list[int],
    *,
    target: str,
    mode: str,
    selected_route_id: str | None,
    live: bool = False,
) -> list[ApplyJob]:
    """Copy every stop into the job. The saved route is not shortened."""
    if mode not in ("replace", "append"):
        raise ValueError("mode must be replace or append")
    if target not in ("selected", "assigned"):
        raise ValueError("target must be selected or assigned")
    jobs: list[ApplyJob] = []
    for character_id in character_ids:
        character = library.character(character_id)
        if character is None:
            jobs.append(
                ApplyJob(
                    id=new_id(),
                    character_id=int(character_id),
                    character_name=str(character_id),
                    route_id="",
                    route_name="",
                    mode=mode,
                    error="That character is not in the local list.",
                )
            )
            continue
        if live and character.demo:
            jobs.append(
                _error_job(
                    character,
                    mode,
                    f"{character.character_name} is a demo character and has no EVE token.",
                )
            )
            continue
        if target == "selected":
            route = library.route(selected_route_id)
            if route is None:
                jobs.append(_error_job(character, mode, "Select a saved route first."))
                continue
        else:
            if not character.assigned_route_id:
                jobs.append(
                    _error_job(
                        character,
                        mode,
                        f"No route is assigned to {character.character_name}. "
                        "Assign one, or apply the selected route instead.",
                    )
                )
                continue
            route = library.route(character.assigned_route_id)
            if route is None:
                jobs.append(
                    _error_job(
                        character,
                        mode,
                        f"The route assigned to {character.character_name} is no longer in the library.",
                    )
                )
                continue
        if not route.stops:
            jobs.append(_error_job(character, mode, f"{route.name} has no stops."))
            continue
        jobs.append(
            ApplyJob(
                id=new_id(),
                character_id=character.character_id,
                character_name=character.character_name,
                route_id=route.id,
                route_name=route.name,
                mode=mode,
                stops=[
                    ApplyStop(
                        system_id=stop.system_id,
                        system_name=stop.system_name,
                        destination_id=stop.destination_id,
                        destination_name=stop.destination_name or stop.system_name,
                        leg_type=stop.leg_type,
                    )
                    for stop in route.stops
                ],
            )
        )
    return jobs


def _error_job(character, mode: str, message: str) -> ApplyJob:
    return ApplyJob(
        id=new_id(),
        character_id=character.character_id,
        character_name=character.character_name,
        route_id="",
        route_name="",
        mode=mode,
        error=message,
    )


def first_actionable(job: ApplyJob) -> int | None:
    for index, stop in enumerate(job.stops):
        if stop.status == StopStatus.APPLIED.value:
            continue
        if any(previous.status != StopStatus.APPLIED.value for previous in job.stops[:index]):
            return None
        if stop.status in (StopStatus.PENDING.value, StopStatus.FAILED.value):
            return index
        return None
    return None


def clear_flag_for(job: ApplyJob) -> bool:
    if job.mode != "replace":
        return False
    return not any(stop.status == StopStatus.APPLIED.value for stop in job.stops)


def cancel_pending(job: ApplyJob) -> None:
    for stop in job.stops:
        if stop.status == StopStatus.PENDING.value:
            stop.status = StopStatus.CANCELLED.value
            stop.message = "Cancelled before this stop was sent."


def resume_cancelled(job: ApplyJob) -> None:
    for stop in job.stops:
        if stop.status == StopStatus.CANCELLED.value:
            stop.status = StopStatus.PENDING.value
            stop.message = ""


def mark_uncertain(job: ApplyJob, *, applied: bool) -> bool:
    for stop in job.stops:
        if stop.status != StopStatus.UNCERTAIN.value:
            continue
        if applied:
            stop.status = StopStatus.APPLIED.value
            stop.message = (
                "Marked applied by you. It was not sent again. "
                "ESI cannot show the current autopilot, so confirm in the client if you are unsure."
            )
        else:
            stop.status = StopStatus.PENDING.value
            stop.message = (
                "Marked not applied by you. The next run will send it. "
                "If the client already has it, that send can duplicate the waypoint."
            )
        return True
    return False


def pace_after(outcome: Outcome, honor_pace: bool) -> float:
    if not honor_pace:
        return 0.0
    if outcome.kind == OutcomeKind.RATE_LIMITED:
        return max(outcome.retry_after, 1.0)
    remain = outcome.error_limit_remain
    if remain is None:
        return 0.2
    if remain < 10:
        return 2.0
    if remain < 30:
        return 0.5
    return 0.2


def _applied_message(job: ApplyJob, index: int, clear: bool, status: int | None) -> str:
    total = len(job.stops)
    code = status if status is not None else "2xx"
    base = (
        f"Stop {index + 1} of {total}. ESI {code}. "
        f"add_to_beginning=false, clear_other_waypoints={'true' if clear else 'false'}."
    )
    if clear:
        base += " Existing autopilot waypoints were cleared first."
    elif job.mode == "append" and index == 0:
        base += " Append mode leaves waypoints already in the client in place. This program cannot see them."
    base += " Leg labels were not sent."
    return base


def execute_job(
    job: ApplyJob,
    send,
    *,
    cancel: CancelToken | None = None,
    sleeper=None,
    on_progress=None,
    on_persist=None,
    token_refresh=None,
    honor_pace: bool = False,
) -> None:
    if sleeper is None:
        sleeper = lambda _seconds: None
    if job.error:
        job.finished = True
        _emit(job, -1, on_progress, on_persist)
        return
    rate_retries = 0
    net_retries = 0
    did_refresh = False
    while True:
        if cancel is not None and cancel.is_cancelled():
            cancel_pending(job)
            _emit(job, -1, on_progress, on_persist)
            return
        index = first_actionable(job)
        if index is None:
            job.finished = bool(job.stops) and all(
                stop.status == StopStatus.APPLIED.value for stop in job.stops
            )
            _emit(job, -1, on_progress, on_persist)
            return
        stop = job.stops[index]
        clear = clear_flag_for(job)
        stop.clear_other_waypoints = clear
        stop.add_to_beginning = False
        try:
            outcome = send(job.character_id, int(stop.destination_id), False, clear)
        except RequestTimeout as exc:
            outcome = Outcome(
                OutcomeKind.UNCERTAIN,
                f"The request timed out ({exc}). It will not be retried automatically.",
            )
        except NetworkError as exc:
            outcome = Outcome(OutcomeKind.NETWORK, str(exc))
        if not isinstance(outcome, Outcome):
            raise TypeError("Waypoint sender must return an Outcome")
        if outcome.kind == OutcomeKind.APPLIED:
            stop.status = StopStatus.APPLIED.value
            stop.message = _applied_message(job, index, clear, outcome.status)
            rate_retries = 0
            net_retries = 0
            did_refresh = False
            _emit(job, index, on_progress, on_persist)
            pause = pace_after(outcome, honor_pace)
            if pause:
                sleeper(pause)
            continue
        if outcome.kind == OutcomeKind.RATE_LIMITED:
            rate_retries += 1
            stop.message = (
                f"Waiting on the ESI error limit ({outcome.retry_after:.0f}s). "
                "This stop was not applied."
            )
            _emit(job, index, on_progress, on_persist)
            if rate_retries > 4:
                stop.status = StopStatus.FAILED.value
                stop.message = (
                    "ESI kept error-limiting this stop. It was not applied. "
                    "Later stops were not sent. Retry when the limit resets. "
                    "The saved route was not shortened."
                )
                _emit(job, index, on_progress, on_persist)
                return
            sleeper(max(outcome.retry_after, 0))
            if cancel is not None and cancel.is_cancelled():
                cancel_pending(job)
                _emit(job, index, on_progress, on_persist)
                return
            continue
        if outcome.kind == OutcomeKind.NEEDS_REFRESH:
            if did_refresh or token_refresh is None:
                stop.status = StopStatus.FAILED.value
                stop.message = (
                    "EVE SSO rejected the access token. Authorize this character again. "
                    "Later stops were not sent. Confirmed stops are not repeated."
                )
                _emit(job, index, on_progress, on_persist)
                return
            try:
                token_refresh(job.character_id)
            except Exception as exc:
                stop.status = StopStatus.FAILED.value
                stop.message = f"Could not refresh the access token: {exc}"
                _emit(job, index, on_progress, on_persist)
                return
            did_refresh = True
            continue
        if outcome.kind == OutcomeKind.NETWORK:
            net_retries += 1
            if net_retries > 2:
                stop.status = StopStatus.FAILED.value
                stop.message = (
                    f"Network error before a response: {outcome.message}. "
                    "Later stops were not sent. A retry sends this stop again and does not repeat confirmed stops."
                )
                _emit(job, index, on_progress, on_persist)
                return
            sleeper(0)
            continue
        if outcome.kind == OutcomeKind.UNCERTAIN:
            stop.status = StopStatus.UNCERTAIN.value
            stop.message = outcome.message or "Uncertain whether ESI applied this stop."
            if "not be retried" not in stop.message and "not be retried automatically" not in stop.message:
                stop.message += " It will not be retried automatically."
            _emit(job, index, on_progress, on_persist)
            return
        stop.status = StopStatus.FAILED.value
        stop.message = outcome.message or "The waypoint was rejected."
        if "rollback" not in stop.message.lower():
            stop.message += (
                " Earlier stops that were confirmed stay in the client. "
                "Later stops were not sent. ESI has no rollback."
            )
        _emit(job, index, on_progress, on_persist)
        return


def _emit(job: ApplyJob, index: int, on_progress, on_persist) -> None:
    if on_persist is not None:
        on_persist(job)
    if on_progress is not None:
        on_progress(job, index)


def execute_jobs(
    jobs: list[ApplyJob],
    send,
    *,
    cancel: CancelToken | None = None,
    sleeper=None,
    on_progress=None,
    on_persist=None,
    token_refresh=None,
    honor_pace: bool = False,
    max_workers: int = 4,
) -> None:
    """Run jobs. Two jobs for the same character never send at the same time."""
    locks: dict[int, threading.Lock] = {}
    lock_guard = threading.Lock()

    def lock_for(character_id: int) -> threading.Lock:
        with lock_guard:
            if character_id not in locks:
                locks[character_id] = threading.Lock()
            return locks[character_id]

    def run(job: ApplyJob) -> None:
        with lock_for(job.character_id):
            execute_job(
                job,
                send,
                cancel=cancel,
                sleeper=sleeper,
                on_progress=on_progress,
                on_persist=on_persist,
                token_refresh=token_refresh,
                honor_pace=honor_pace,
            )

    if not jobs:
        return
    workers = max(1, min(max_workers, len(jobs)))
    if workers == 1:
        for job in jobs:
            run(job)
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run, jobs))


def demo_send(_character_id: int, _destination_id: int, _add: bool, _clear: bool) -> Outcome:
    return Outcome(
        OutcomeKind.APPLIED,
        "Simulated accept. No request left this computer.",
        204,
    )


def describe_plan(jobs: list[ApplyJob], *, demo: bool) -> str:
    from everoute.messages import abbreviate_names

    lines = []
    if demo:
        lines.append("Demo / test mode. Nothing is sent to EVE.")
    else:
        lines.append("This will call ESI. It is not atomic and it cannot be rolled back.")
    for job in jobs:
        if job.error:
            lines.append(f"{job.character_name}: {job.error}")
            continue
        names = [stop.system_name for stop in job.stops]
        lines.append(f"{job.character_name}: {job.route_name} — {len(job.stops)} stops, {job.mode}")
        lines.append(abbreviate_names(names))
    lines.append("The saved route is not shortened. Confirmed stops are not sent again if you retry.")
    return "\n".join(lines)


def sleep_interruptible(seconds: float, cancel: CancelToken | None, step: float = 0.05) -> None:
    if seconds <= 0:
        return
    end = time.monotonic() + seconds
    while True:
        if cancel is not None and cancel.is_cancelled():
            return
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(step, left))
