from __future__ import annotations

import itertools
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domain.scheduling import Job, Shift, _sequence, solve, validate

BASE = datetime(2026, 11, 10, 14, tzinfo=UTC)


def job(name: str, release: int = 0, deadline: int = 180, duration: int = 60, **locks: object) -> Job:
    return Job(
        name,
        name,
        BASE + timedelta(minutes=release),
        BASE + timedelta(minutes=deadline),
        timedelta(minutes=duration),
        name,
        **locks,
    )


def shift(**changes: object) -> Shift:
    values = dict(
        cleaner_id="C",
        origin="O",
        start=BASE,
        end=BASE + timedelta(hours=8),
        breaks=(),
        max_jobs=8,
        max_service=timedelta(hours=8),
        max_duty=timedelta(hours=8),
        eligible_places=frozenset({"A", "B", "C", "D"}),
    )
    values.update(changes)
    return Shift(**values)


def test_joint_successors_recover_coverage_missed_by_greedy() -> None:
    jobs = [job("A", deadline=180), job("B", deadline=181)]
    shifts = [shift()]
    matrix = {("O", "A"): 60, ("O", "B"): 60, ("A", "B"): 7200, ("B", "A"): 60}

    def travel(a: str, b: str) -> int | None:
        return matrix.get((a, b))

    baseline = _sequence(jobs, shifts, travel, None)
    result = solve(jobs, shifts, travel, time_limit_seconds=3)
    assert baseline.coverage == 1
    assert result.coverage == 2
    assert [item.task_id for item in result.placed] == ["B", "A"]
    assert not validate(result, jobs, shifts, travel)
    assert result.solver_status == "feasible_not_proven_optimal"


def test_joint_routes_fill_gap_before_lock_and_recompute_incoming_leg() -> None:
    jobs = [
        job("A", deadline=120),
        job(
            "B",
            release=180,
            deadline=240,
            locked_cleaner_id="C",
            locked_start=BASE + timedelta(minutes=180),
            locked_end=BASE + timedelta(minutes=240),
        ),
    ]
    shifts = [shift()]

    def travel(a: str, b: str) -> int:
        return 60 if a == "A" else 120

    result = solve(jobs, shifts, travel, time_limit_seconds=3)
    assert result.coverage == 2
    assert not validate(result, jobs, shifts, travel)
    assert result.placed[-1].start == jobs[-1].locked_start
    assert result.placed[-1].travel_before == timedelta(minutes=1)


def test_missing_origin_leg_can_be_reached_through_another_job() -> None:
    jobs = [job("A"), job("B")]
    shifts = [shift()]
    matrix = {("O", "B"): 60, ("B", "A"): 60}
    result = solve(jobs, shifts, lambda a, b, matrix=matrix: matrix.get((a, b)), time_limit_seconds=3)
    assert result.coverage == 2
    assert not validate(result, jobs, shifts, lambda a, b, matrix=matrix: matrix.get((a, b)))


def test_travel_cannot_use_a_break() -> None:
    jobs = [job("A", deadline=100, duration=30)]
    shifts = [shift(breaks=((BASE + timedelta(minutes=10), BASE + timedelta(minutes=40)),))]
    result = solve(jobs, shifts, lambda a, b: 1200, time_limit_seconds=3)
    assert result.placed[0].start >= BASE + timedelta(minutes=60)
    assert not validate(result, jobs, shifts, lambda a, b: 1200)
    invalid = replace(
        result,
        placed=(replace(result.placed[0], start=BASE + timedelta(minutes=40), end=BASE + timedelta(minutes=70)),),
    )
    assert "travel" in validate(invalid, jobs, shifts, lambda a, b: 1200)


def test_joint_route_matches_exhaustive_small_instances() -> None:
    def score(jobs: list[Job], starts: dict[str, datetime], total: int) -> tuple[int, ...]:
        priorities = sorted({task.priority for task in jobs})
        counts = tuple(sum(task.task_id in starts for task in jobs if task.priority == value) for value in priorities)
        times = tuple(
            -sum(
                int((starts[task.task_id] - BASE).total_seconds())
                for task in jobs
                if task.priority == value and task.task_id in starts
            )
            for value in priorities
        )
        return (*counts, *times, -total)

    rng = random.Random(204)
    for _ in range(12):
        jobs = [
            job(name, release=rng.randrange(0, 45), deadline=rng.randrange(90, 200), duration=rng.randrange(15, 45))
            for name in "ABCD"
        ]
        shifts = [shift()]
        matrix = {(a, b): rng.randrange(1, 31) * 60 for a in "OABCD" for b in "ABCD" if a != b}
        best = score(jobs, {}, 0)
        for count in range(1, 5):
            for route in itertools.permutations(jobs, count):
                cursor, origin, total = BASE, "O", 0
                starts = {}
                for task in route:
                    seconds = matrix[origin, task.place]
                    start = max(task.release, cursor + timedelta(seconds=seconds))
                    cursor = start + task.duration
                    starts[task.task_id] = start
                    if cursor > task.deadline:
                        break
                    total += seconds
                    origin = task.place
                else:
                    best = max(best, score(jobs, starts, total))
        result = solve(jobs, shifts, lambda a, b, matrix=matrix: matrix.get((a, b)), time_limit_seconds=3)
        assert not validate(result, jobs, shifts, lambda a, b, matrix=matrix: matrix.get((a, b)))
        assert score(jobs, {item.task_id: item.start for item in result.placed}, result.travel_seconds) == best


def test_arrival_before_break_can_wait_for_release_after_break() -> None:
    jobs = [job("A", release=60, deadline=90, duration=30)]
    shifts = [shift(breaks=((BASE + timedelta(minutes=40), BASE + timedelta(minutes=60)),))]
    result = solve(jobs, shifts, lambda a, b: 1800, time_limit_seconds=3)
    assert result.coverage == 1
    assert result.placed[0].start == BASE + timedelta(minutes=60)
    assert not validate(result, jobs, shifts, lambda a, b: 1800)


def test_multi_cleaner_eligibility_and_daily_caps() -> None:
    jobs = [job("A"), job("B"), job("C")]
    shifts = [
        shift(max_jobs=1, eligible_places=frozenset({"A"})),
        shift(cleaner_id="D", max_service=timedelta(minutes=120), max_duty=timedelta(minutes=122)),
    ]
    result = solve(jobs, shifts, lambda a, b: 60, time_limit_seconds=3)
    assert result.coverage == 3
    assert not validate(result, jobs, shifts, lambda a, b: 60)
    assert next(item.cleaner_id for item in result.placed if item.task_id == "A") == "C"
    reduced = solve(jobs, [replace(shifts[0], max_jobs=0), shifts[1]], lambda a, b: 60, time_limit_seconds=3)
    assert reduced.coverage == 2
    assert not validate(reduced, jobs, [replace(shifts[0], max_jobs=0), shifts[1]], lambda a, b: 60)


def test_independent_validator_rejects_dropped_lock() -> None:
    jobs = [
        job(
            "A",
            locked_cleaner_id="C",
            locked_start=BASE + timedelta(minutes=60),
            locked_end=BASE + timedelta(minutes=120),
        )
    ]
    result = solve(jobs, [shift()], lambda a, b: 60, time_limit_seconds=3)
    assert not validate(result, jobs, [shift()], lambda a, b: 60)
    assert "lock" in validate(replace(result, placed=()), jobs, [shift()], lambda a, b: 60)


def test_valid_candidate_replaces_understated_travel_in_locked_fallback() -> None:
    jobs = [
        job(
            "A",
            locked_cleaner_id="C",
            locked_start=BASE + timedelta(minutes=60),
            locked_end=BASE + timedelta(minutes=120),
        ),
        job(
            "B",
            deadline=300,
            locked_cleaner_id="C",
            locked_start=BASE + timedelta(minutes=180),
            locked_end=BASE + timedelta(minutes=240),
        ),
    ]
    shifts = [shift()]
    matrix = {("O", "A"): 60, ("O", "B"): 60, ("A", "B"): 600}
    result = solve(jobs, shifts, lambda a, b: matrix.get((a, b)), time_limit_seconds=3)
    assert result.coverage == 2
    assert result.travel_seconds == 660
    assert not validate(result, jobs, shifts, lambda a, b: matrix.get((a, b)))
