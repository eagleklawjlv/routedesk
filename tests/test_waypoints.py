import threading

from everoute.esi import Outcome, OutcomeKind
from everoute.models import Character, Library, Route, Stop
from everoute.sample import (
    ALPHA_ID,
    BRAVO_ID,
    IGNOITTON_ID,
    JITA_ID,
    ROUTE_TO_JITA,
    ROUTE_TO_SISEIDE,
    SISEIDE_ID,
    sample_library,
)
from everoute.waypoints import (
    CancelToken,
    execute_jobs,
    first_actionable,
    mark_uncertain,
    plan_jobs,
    resume_cancelled,
)


class Recorder:
    def __init__(self, script=None, delay=0.0):
        self.calls = []
        self.script = list(script or [])
        self.delay = delay
        self._lock = threading.Lock()
        self._open = {}
        self.max_open = {}

    def __call__(self, character_id, destination_id, add, clear):
        with self._lock:
            self._open[character_id] = self._open.get(character_id, 0) + 1
            self.max_open[character_id] = max(self.max_open.get(character_id, 0), self._open[character_id])
            self.calls.append((character_id, destination_id, add, clear))
        if self.delay:
            threading.Event().wait(self.delay)
        with self._lock:
            self._open[character_id] -= 1
        if self.script:
            return self.script.pop(0)
        return Outcome(OutcomeKind.APPLIED, "ok", 204)


def _library_with_assignment():
    library = sample_library()
    return library


def test_same_route_to_many_characters_preserves_order_and_replace_flags():
    library = _library_with_assignment()
    route = library.route_by_name(ROUTE_TO_SISEIDE)
    jobs = plan_jobs(
        library,
        [ALPHA_ID, BRAVO_ID],
        target="selected",
        mode="replace",
        selected_route_id=route.id,
    )
    assert [len(job.stops) for job in jobs] == [3, 3]
    recorder = Recorder()
    execute_jobs(jobs, recorder, max_workers=2)
    for character_id in (ALPHA_ID, BRAVO_ID):
        calls = [call for call in recorder.calls if call[0] == character_id]
        assert [call[1] for call in calls] == [JITA_ID, IGNOITTON_ID, SISEIDE_ID]
        assert calls[0][2] is False and calls[0][3] is True
        assert all(call[2] is False and call[3] is False for call in calls[1:])
        assert recorder.max_open[character_id] == 1


def test_assigned_routes_differ_by_character():
    library = _library_with_assignment()
    jobs = plan_jobs(
        library,
        [ALPHA_ID, BRAVO_ID],
        target="assigned",
        mode="replace",
        selected_route_id=None,
    )
    by_name = {job.character_name: [stop.system_id for stop in job.stops] for job in jobs}
    assert by_name["Demo Pilot Alpha"] == [JITA_ID, IGNOITTON_ID, SISEIDE_ID]
    assert by_name["Demo Pilot Bravo"] == [SISEIDE_ID, IGNOITTON_ID, JITA_ID]
    recorder = Recorder()
    execute_jobs(jobs, recorder, max_workers=2)
    alpha = [call[1] for call in recorder.calls if call[0] == ALPHA_ID]
    bravo = [call[1] for call in recorder.calls if call[0] == BRAVO_ID]
    assert alpha == [JITA_ID, IGNOITTON_ID, SISEIDE_ID]
    assert bravo == [SISEIDE_ID, IGNOITTON_ID, JITA_ID]


def test_append_never_clears():
    library = sample_library()
    route = library.route_by_name(ROUTE_TO_JITA)
    jobs = plan_jobs(
        library,
        [ALPHA_ID],
        target="selected",
        mode="append",
        selected_route_id=route.id,
    )
    recorder = Recorder()
    execute_jobs(jobs, recorder)
    assert [call[1] for call in recorder.calls] == [SISEIDE_ID, IGNOITTON_ID, JITA_ID]
    assert all(call[3] is False and call[2] is False for call in recorder.calls)


def test_retry_skips_applied_and_does_not_clear_again():
    library = sample_library()
    route = library.route_by_name(ROUTE_TO_SISEIDE)
    jobs = plan_jobs(
        library,
        [ALPHA_ID],
        target="selected",
        mode="replace",
        selected_route_id=route.id,
    )
    script = [
        Outcome(OutcomeKind.APPLIED, "ok", 204),
        Outcome(OutcomeKind.FAILED, "Character is not online", 400),
    ]
    recorder = Recorder(script)
    execute_jobs(jobs, recorder)
    assert [call[1] for call in recorder.calls] == [JITA_ID, IGNOITTON_ID]
    assert jobs[0].stops[0].status == "applied"
    assert jobs[0].stops[1].status == "failed"
    assert jobs[0].stops[2].status == "pending"
    assert "rollback" in jobs[0].stops[1].message.lower()
    again = Recorder()
    execute_jobs(jobs, again)
    assert [call[1] for call in again.calls] == [IGNOITTON_ID, SISEIDE_ID]
    assert again.calls[0][3] is False


def test_uncertain_is_not_retried_until_the_user_says_so():
    library = sample_library()
    route = library.route_by_name(ROUTE_TO_SISEIDE)
    jobs = plan_jobs(
        library,
        [ALPHA_ID],
        target="selected",
        mode="replace",
        selected_route_id=route.id,
    )
    recorder = Recorder(
        [
            Outcome(OutcomeKind.APPLIED, "ok", 204),
            Outcome(OutcomeKind.UNCERTAIN, "timed out", None),
        ]
    )
    execute_jobs(jobs, recorder)
    assert len(recorder.calls) == 2
    assert jobs[0].stops[1].status == "uncertain"
    assert first_actionable(jobs[0]) is None
    held = Recorder()
    execute_jobs(jobs, held)
    assert held.calls == []
    assert mark_uncertain(jobs[0], applied=False) is True
    resumed = Recorder()
    execute_jobs(jobs, resumed)
    assert [call[1] for call in resumed.calls] == [IGNOITTON_ID, SISEIDE_ID]
    assert resumed.calls[0][3] is False


def test_rate_limit_retries_the_same_unapplied_stop_only():
    library = sample_library()
    route = library.route_by_name(ROUTE_TO_SISEIDE)
    jobs = plan_jobs(
        library,
        [BRAVO_ID],
        target="selected",
        mode="append",
        selected_route_id=route.id,
    )
    recorder = Recorder(
        [
            Outcome(OutcomeKind.RATE_LIMITED, "limited", 420, retry_after=0),
            Outcome(OutcomeKind.APPLIED, "ok", 204),
        ]
    )
    execute_jobs(jobs, recorder)
    assert [call[1] for call in recorder.calls] == [JITA_ID, JITA_ID, IGNOITTON_ID, SISEIDE_ID]
    assert [stop.status for stop in jobs[0].stops] == ["applied", "applied", "applied"]


def test_cancel_and_resume_does_not_duplicate():
    stops = [
        Stop(index, f"S{index}", "origin" if index == 1 else "gate")
        for index in range(1, 21)
    ]
    library = Library(
        routes=[Route(id="long", name="Long", stops=stops)],
        characters=[Character(7, "Pilot", demo=True)],
    )
    jobs = plan_jobs(library, [7], target="selected", mode="replace", selected_route_id="long")
    cancel = CancelToken()

    def send(character_id, destination_id, add, clear):
        send.calls.append(destination_id)
        if len(send.calls) == 6:
            cancel.cancel()
        return Outcome(OutcomeKind.APPLIED, "ok", 204)

    send.calls = []
    execute_jobs(jobs, send, cancel=cancel)
    assert send.calls == [1, 2, 3, 4, 5, 6]
    assert jobs[0].stops[5].status == "applied"
    assert jobs[0].stops[6].status == "cancelled"
    assert jobs[0].stops[-1].status == "cancelled"
    resume_cancelled(jobs[0])
    send.calls = []
    execute_jobs(jobs, send)
    assert send.calls == list(range(7, 21))


def test_same_character_jobs_do_not_overlap():
    library = Library(
        routes=[
            Route(id="a", name="A", stops=[Stop(i, f"A{i}", "origin" if i == 1 else "gate") for i in range(1, 6)]),
            Route(id="b", name="B", stops=[Stop(i, f"B{i}", "origin" if i == 101 else "gate") for i in range(101, 106)]),
        ],
        characters=[Character(3, "Pilot", demo=True)],
    )
    first = plan_jobs(library, [3], target="selected", mode="append", selected_route_id="a")
    second = plan_jobs(library, [3], target="selected", mode="append", selected_route_id="b")
    recorder = Recorder(delay=0.01)
    execute_jobs(first + second, recorder, max_workers=4)
    assert recorder.max_open[3] == 1
    destinations = [call[1] for call in recorder.calls]
    assert destinations == [1, 2, 3, 4, 5, 101, 102, 103, 104, 105] or destinations == [
        101, 102, 103, 104, 105, 1, 2, 3, 4, 5
    ]


def test_long_route_is_not_truncated_when_sent():
    stops = [Stop(1000 + index, f"N{index}", "origin" if index == 0 else "cyno") for index in range(750)]
    library = Library(
        routes=[Route(id="huge", name="Huge", stops=stops)],
        characters=[Character(9, "Pilot", demo=True)],
    )
    jobs = plan_jobs(library, [9], target="selected", mode="replace", selected_route_id="huge")
    assert len(jobs[0].stops) == 750
    recorder = Recorder()
    execute_jobs(jobs, recorder)
    assert [call[1] for call in recorder.calls] == [1000 + index for index in range(750)]
    assert recorder.calls[0][3] is True
    assert all(call[3] is False for call in recorder.calls[1:])
    from everoute.messages import abbreviate_names

    summary = abbreviate_names([stop.system_name for stop in jobs[0].stops])
    assert "still sent" in summary
    assert len(jobs[0].stops) == 750


def test_missing_assignment_does_not_send():
    library = sample_library()
    library.character(ALPHA_ID).assigned_route_id = None
    jobs = plan_jobs(library, [ALPHA_ID], target="assigned", mode="replace", selected_route_id=None)
    assert jobs[0].error
    recorder = Recorder()
    execute_jobs(jobs, recorder)
    assert recorder.calls == []


def test_live_mode_refuses_demo_characters():
    library = sample_library()
    route = library.route_by_name(ROUTE_TO_JITA)
    jobs = plan_jobs(
        library,
        [ALPHA_ID],
        target="selected",
        mode="replace",
        selected_route_id=route.id,
        live=True,
    )
    assert "demo" in jobs[0].error.lower()
    recorder = Recorder()
    execute_jobs(jobs, recorder)
    assert recorder.calls == []


def test_duplicate_stops_are_preserved():
    library = Library(
        routes=[
            Route(
                id="dup",
                name="Dup",
                stops=[
                    Stop(JITA_ID, "Jita", "origin"),
                    Stop(JITA_ID, "Jita", "gate"),
                ],
            )
        ],
        characters=[Character(1, "Pilot", demo=True)],
    )
    jobs = plan_jobs(library, [1], target="selected", mode="append", selected_route_id="dup")
    recorder = Recorder()
    execute_jobs(jobs, recorder)
    assert [call[1] for call in recorder.calls] == [JITA_ID, JITA_ID]
