"""Synthetic comparison with the pre-circuit coverage + greedy planner.

Run from backend: python -m scripts.benchmark_routes
No database, provider network requests, or personal data are used.
"""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime, timedelta

from app.domain.scheduling import Job, Shift, _better, _coverage, _sequence, solve, validate


def main() -> None:
    base = datetime(2026, 11, 10, 14, tzinfo=UTC)
    rng = random.Random(204)
    cases = []
    for case in range(12):
        jobs = [
            Job(
                name,
                name,
                base + timedelta(minutes=rng.randrange(45)),
                base + timedelta(minutes=rng.randrange(90, 200)),
                timedelta(minutes=rng.randrange(15, 45)),
                name,
            )
            for name in "ABCD"
        ]
        matrix = {(a, b): rng.randrange(1, 31) * 60 for a in "OABCD" for b in "ABCD" if a != b}
        shifts = [
            Shift(
                "C",
                "O",
                base,
                base + timedelta(hours=8),
                (),
                8,
                timedelta(hours=8),
                timedelta(hours=8),
                frozenset("ABCD"),
            )
        ]
        cases.append((str(case), jobs, shifts, matrix))
    names = [f"P{i}" for i in range(100)]
    jobs = [Job(n, n, base, base + timedelta(hours=8), timedelta(hours=1), n) for n in names]
    shifts = [
        Shift(
            str(i),
            "O",
            base,
            base + timedelta(hours=8),
            (),
            12,
            timedelta(hours=8),
            timedelta(hours=8),
            frozenset(names),
        )
        for i in range(10)
    ]
    cases.append(("100x10", jobs, shifts, {(a, b): 60 for a in ["O", *names] for b in names}))
    print("case,old_coverage,new_coverage,old_travel_s,new_travel_s,old_ms,new_ms,valid")
    for name, jobs, shifts, matrix in cases:

        def travel(a: str, b: str, matrix: dict[tuple[str, str], int] = matrix) -> int | None:
            return matrix.get((a, b))

        started = time.perf_counter()
        target, _ = _coverage(jobs, shifts, 3)
        greedy = _sequence(jobs, shifts, travel, None)
        preferred = _sequence(jobs, shifts, travel, target)
        old = greedy if _better(greedy, preferred) else preferred
        old_ms = (time.perf_counter() - started) * 1000
        new = solve(jobs, shifts, travel, time_limit_seconds=3)
        errors = validate(new, jobs, shifts, travel)
        print(
            f"{name},{old.coverage},{new.coverage},{old.travel_seconds},{new.travel_seconds},"
            f"{old_ms:.2f},{new.elapsed_seconds * 1000:.2f},{not errors}"
        )
        assert not errors
        assert new.coverage >= old.coverage
        if new.coverage == old.coverage:
            assert new.travel_seconds <= old.travel_seconds


if __name__ == "__main__":
    main()
