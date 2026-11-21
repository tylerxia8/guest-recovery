from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ortools.sat.python import cp_model

Travel = Callable[[str, str], int | None]


@dataclass(frozen=True)
class Job:
    task_id: str
    place: str
    release: datetime
    deadline: datetime
    duration: timedelta
    property_id: str
    locked_cleaner_id: str | None = None
    locked_start: datetime | None = None
    locked_end: datetime | None = None
    next_checkin: datetime | None = None
    checkout_at: datetime | None = None
    property_group: str | None = None

    @property
    def priority(self) -> tuple[bool, datetime, datetime]:
        checkout = self.checkout_at or self.release
        same_day = (
            self.next_checkin is not None
            and self.next_checkin.astimezone(ZoneInfo("America/New_York")).date()
            == checkout.astimezone(ZoneInfo("America/New_York")).date()
        )
        return not same_day, self.next_checkin or datetime.max.replace(tzinfo=UTC), checkout


@dataclass(frozen=True)
class Shift:
    cleaner_id: str
    origin: str
    start: datetime
    end: datetime
    breaks: tuple[tuple[datetime, datetime], ...]
    max_jobs: int
    max_service: timedelta
    max_duty: timedelta
    eligible_places: frozenset[str]
    max_cluster_jobs: int | None = None

    def __post_init__(self) -> None:
        if self.max_cluster_jobs is not None and self.max_cluster_jobs < self.max_jobs:
            raise ValueError("Cluster capacity cannot be lower than the normal job limit.")


@dataclass(frozen=True)
class Placed:
    task_id: str
    cleaner_id: str
    start: datetime
    end: datetime
    travel_before: timedelta
    place: str


@dataclass(frozen=True)
class SolveResult:
    placed: tuple[Placed, ...]
    uncovered: tuple[tuple[str, str], ...]
    travel_seconds: int
    solver_status: str
    coverage: int
    solver_version: str
    elapsed_seconds: float
    objective: dict[str, int]


def solve(
    jobs: list[Job],
    shifts: list[Shift],
    travel: Travel,
    *,
    time_limit_seconds: int,
) -> SolveResult:
    """Use bounded joint routing for small plans and a fast deterministic fallback."""
    started = time.monotonic()
    version = "ortools-cp-sat-9.15+bounded-circuit-priority-v3-clustered"
    if time_limit_seconds <= 0:
        uncovered = tuple((job.task_id, "NO_CAPACITY") for job in jobs)
        return SolveResult((), uncovered, 0, "feasible_not_proven_optimal", 0, version, 0.0, {"coverage": 0})
    coverage_target, _coverage_status = _coverage(jobs, shifts, time_limit_seconds)
    greedy = _sequence(jobs, shifts, travel, preferred=None)
    preferred = _sequence(jobs, shifts, travel, preferred=coverage_target)
    chosen = greedy if _better(greedy, preferred, jobs) else preferred
    remaining = time_limit_seconds - (time.monotonic() - started)
    if 0 < len(jobs) <= 12 and len(shifts) <= 4 and remaining > 0:
        candidate = _joint_routes(jobs, shifts, travel, min(remaining, 2.0))
        if candidate is not None:
            checked = SolveResult(
                candidate.placed,
                candidate.uncovered,
                candidate.travel_seconds,
                "feasible_not_proven_optimal",
                candidate.coverage,
                version,
                0.0,
                {},
            )
            fallback_errors = validate(replace(checked, placed=chosen.placed), jobs, shifts, travel)
            if not validate(checked, jobs, shifts, travel) and (fallback_errors or _better(candidate, chosen, jobs)):
                chosen = candidate
    elapsed = time.monotonic() - started
    # Even a proven coverage/travel optimum does not prove all five PRD stages.
    status = "feasible_not_proven_optimal"
    objective = {"coverage": chosen.coverage, "travel_seconds": chosen.travel_seconds}
    return SolveResult(
        chosen.placed,
        chosen.uncovered,
        chosen.travel_seconds,
        status,
        chosen.coverage,
        version,
        elapsed,
        objective,
    )


def validate(result: SolveResult, jobs: list[Job], shifts: list[Shift], travel: Travel) -> list[str]:
    errors: list[str] = []
    jobs_by_id = {job.task_id: job for job in jobs}
    shifts_by_id = {shift.cleaner_id: shift for shift in shifts}
    by_cleaner: dict[str, list[Placed]] = {}
    by_property: dict[str, list[Placed]] = {}
    seen: set[str] = set()
    for item in result.placed:
        if item.task_id in seen:
            errors.append("duplicate")
        seen.add(item.task_id)
        job = jobs_by_id.get(item.task_id)
        shift = shifts_by_id.get(item.cleaner_id)
        if job is None or shift is None:
            errors.append("unknown")
            continue
        if item.start < job.release or item.end > job.deadline or item.end - item.start != job.duration:
            errors.append("window")
        if item.start < shift.start or item.end > shift.end:
            errors.append("shift")
        if job.place not in shift.eligible_places:
            errors.append("eligibility")
        if job.locked_cleaner_id and (
            item.cleaner_id != job.locked_cleaner_id or item.start != job.locked_start or item.end != job.locked_end
        ):
            errors.append("lock")
        if _overlaps_break(item.start, item.end, shift.breaks):
            errors.append("break")
        by_cleaner.setdefault(item.cleaner_id, []).append(item)
        by_property.setdefault(job.property_id, []).append(item)
    if any(job.locked_cleaner_id and job.task_id not in seen for job in jobs):
        errors.append("lock")
    for visits in by_property.values():
        ordered_visits = sorted(visits, key=lambda item: item.start)
        if any(
            previous.end > current.start for previous, current in zip(ordered_visits, ordered_visits[1:], strict=False)
        ):
            errors.append("property_overlap")
    for cleaner_id, items in by_cleaner.items():
        shift = shifts_by_id[cleaner_id]
        ordered = sorted(items, key=lambda item: (item.start, item.task_id))
        previous = shift.origin
        cursor = shift.start
        service = timedelta(0)
        travel_total = timedelta(0)
        for item in ordered:
            seconds = travel(previous, item.place)
            if seconds is None:
                errors.append("route")
                continue
            expected = _ceil_minutes(int(seconds))
            arrival = _after_breaks(cursor, expected, shift.breaks) + expected
            if item.travel_before != expected or item.start < arrival:
                errors.append("travel")
            if item.start < item.end and any(
                item.start < other.end and item.end > other.start for other in ordered if other.task_id != item.task_id
            ):
                errors.append("overlap")
            service += item.end - item.start
            travel_total += expected
            cursor = item.end
            previous = item.place
        if (
            len(ordered)
            > job_limit(
                shift.max_jobs, shift.max_cluster_jobs, (jobs_by_id[item.task_id].property_group for item in ordered)
            )
            or service > shift.max_service
        ):
            errors.append("capacity")
        duty = service + travel_total + _break_overlap(shift.start, cursor, shift.breaks)
        if duty > shift.max_duty:
            errors.append("duty")
    return errors


@dataclass(frozen=True)
class _Plan:
    placed: tuple[Placed, ...]
    uncovered: tuple[tuple[str, str], ...]
    travel_seconds: int
    coverage: int


def _better(left: _Plan, right: _Plan, jobs: list[Job]) -> bool:
    left_by_id = {item.task_id: item for item in left.placed}
    right_by_id = {item.task_id: item for item in right.placed}
    priorities = sorted({job.priority for job in jobs})
    for priority in priorities:
        identifiers = {job.task_id for job in jobs if job.priority == priority}
        left_count = len(identifiers & left_by_id.keys())
        right_count = len(identifiers & right_by_id.keys())
        if left_count != right_count:
            return left_count > right_count
    for priority in priorities:
        identifiers = {job.task_id for job in jobs if job.priority == priority}
        left_start = sum(item.start.timestamp() for key, item in left_by_id.items() if key in identifiers)
        right_start = sum(item.start.timestamp() for key, item in right_by_id.items() if key in identifiers)
        if left_start != right_start:
            return left_start < right_start
    return left.travel_seconds <= right.travel_seconds


def job_limit(max_jobs: int, max_cluster_jobs: int | None, property_groups: Iterable[str | None]) -> int:
    groups = set(property_groups)
    if max_cluster_jobs is not None and len(groups) == 1 and None not in groups and "" not in groups:
        return max_cluster_jobs
    return max_jobs


def _add_job_capacity(
    model: cp_model.CpModel,
    shift: Shift,
    options: list[tuple[Job, cp_model.IntVar]],
    locked_jobs: list[Job],
) -> None:
    count = sum(active for _, active in options) + len(locked_jobs)
    if shift.max_cluster_jobs is None or shift.max_cluster_jobs == shift.max_jobs:
        model.add(count <= shift.max_jobs)
        return
    extended = model.new_bool_var(f"clustered_{shift.cleaner_id}")
    model.add(count <= shift.max_jobs + (shift.max_cluster_jobs - shift.max_jobs) * extended)
    groups = {
        group: model.new_bool_var(f"group_{shift.cleaner_id}_{group}")
        for group in sorted(
            {job.property_group for job, _ in options} | {job.property_group for job in locked_jobs},
            key=lambda value: value or "",
        )
        if group
    }
    model.add(sum(groups.values()) <= 1)
    for job, active in options:
        if job.property_group:
            model.add(active <= groups[job.property_group] + 1 - extended)
        else:
            model.add_implication(extended, active.Not())
    for job in locked_jobs:
        if job.property_group:
            model.add_implication(extended, groups[job.property_group])
        else:
            model.add(extended == 0)


def _coverage(jobs: list[Job], shifts: list[Shift], time_limit_seconds: int) -> tuple[tuple[int, dict[str, str]], str]:
    started = time.monotonic()
    model = cp_model.CpModel()
    chosen: dict[tuple[str, str], cp_model.IntVar] = {}
    free = [job for job in jobs if not job.locked_cleaner_id]
    for job in free:
        for shift in shifts:
            if job.place not in shift.eligible_places or not shift.origin:
                continue
            chosen[job.task_id, shift.cleaner_id] = model.new_bool_var(f"a_{job.task_id}_{shift.cleaner_id}")
    for job in free:
        options = [var for (task_id, _cleaner), var in chosen.items() if task_id == job.task_id]
        if options:
            model.add(sum(options) <= 1)
    for shift in shifts:
        variables = [var for (task_id, cleaner_id), var in chosen.items() if cleaner_id == shift.cleaner_id]
        locked_jobs = [job for job in jobs if job.locked_cleaner_id == shift.cleaner_id]
        _add_job_capacity(
            model,
            shift,
            [(job, chosen[job.task_id, shift.cleaner_id]) for job in free if (job.task_id, shift.cleaner_id) in chosen],
            locked_jobs,
        )
        if variables:
            terms = []
            for (task_id, cleaner_id), var in chosen.items():
                if cleaner_id != shift.cleaner_id:
                    continue
                minutes = next(int(job.duration.total_seconds() // 60) for job in free if job.task_id == task_id)
                terms.append(var * minutes)
            locked_minutes = sum(int(job.duration.total_seconds() // 60) for job in locked_jobs)
            model.add(sum(terms) <= max(int(shift.max_service.total_seconds() // 60) - locked_minutes, 0))
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 1
    solver.parameters.num_search_workers = 1
    status = cp_model.OPTIMAL
    assignment: dict[str, str] = {}
    for priority in sorted({job.priority for job in free}):
        identifiers = {job.task_id for job in free if job.priority == priority}
        options = [var for (task_id, _), var in chosen.items() if task_id in identifiers]
        if not options:
            continue
        remaining = time_limit_seconds - (time.monotonic() - started)
        if remaining <= 0:
            status = cp_model.UNKNOWN
            break
        solver.parameters.max_time_in_seconds = remaining
        count = sum(options)
        model.maximize(count)
        status = solver.solve(model)
        if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            break
        assignment = {task_id: cleaner_id for (task_id, cleaner_id), var in chosen.items() if solver.value(var) == 1}
        if status != cp_model.OPTIMAL:
            break
        model.add(count == solver.value(count))
    label = "optimal" if status == cp_model.OPTIMAL else "feasible_not_proven_optimal"
    covered = len(assignment) + sum(1 for job in jobs if job.locked_cleaner_id)
    return (covered, assignment), label


def _sequence(
    jobs: list[Job],
    shifts: list[Shift],
    travel: Travel,
    preferred: tuple[int, dict[str, str]] | None,
) -> _Plan:
    locked = [job for job in jobs if job.locked_cleaner_id and job.locked_start and job.locked_end]
    locked.sort(key=lambda job: (job.locked_start or job.release, job.task_id))
    free = [job for job in jobs if job not in locked]
    free.sort(key=lambda job: (job.priority, job.task_id))
    preference = {} if preferred is None else preferred[1]
    placed: list[Placed] = [
        Placed(
            job.task_id,
            job.locked_cleaner_id or "",
            job.locked_start or job.release,
            job.locked_end or job.release,
            _locked_travel(job, locked, shifts, travel),
            job.place,
        )
        for job in locked
    ]
    jobs_by_id = {job.task_id: job for job in jobs}
    cursor = {shift.cleaner_id: shift.start for shift in shifts}
    previous = {shift.cleaner_id: shift.origin for shift in shifts}
    used = {shift.cleaner_id: 0 for shift in shifts}
    service = {shift.cleaner_id: timedelta(0) for shift in shifts}
    traveled = {shift.cleaner_id: timedelta(0) for shift in shifts}
    for job in locked:
        cleaner_id = job.locked_cleaner_id or ""
        if cleaner_id not in cursor or job.locked_end is None:
            continue
        cursor[cleaner_id] = max(cursor[cleaner_id], job.locked_end)
        previous[cleaner_id] = job.place
        used[cleaner_id] += 1
        service[cleaner_id] += job.duration
        traveled[cleaner_id] += _locked_travel(job, locked, shifts, travel)
    uncovered: list[tuple[str, str]] = []
    for job in free:
        ordered = sorted(
            shifts,
            key=lambda shift: (
                0 if preference.get(job.task_id) == shift.cleaner_id else 1,
                _travel_rank(travel, previous[shift.cleaner_id], job.place),
                shift.cleaner_id,
            ),
        )
        placed_job = False
        reason = _base_reason(job, shifts)
        for shift in ordered:
            decision = _place(job, shift, previous[shift.cleaner_id], cursor[shift.cleaner_id], travel, placed)
            if isinstance(decision, str):
                reason = decision
                continue
            assigned_jobs = [jobs_by_id[item.task_id] for item in placed if item.cleaner_id == shift.cleaner_id]
            if used[shift.cleaner_id] + 1 > job_limit(
                shift.max_jobs, shift.max_cluster_jobs, (item.property_group for item in [*assigned_jobs, job])
            ):
                reason = "NO_CAPACITY"
                continue
            if service[shift.cleaner_id] + job.duration > shift.max_service:
                reason = "NO_CAPACITY"
                continue
            duty = (
                service[shift.cleaner_id]
                + job.duration
                + traveled[shift.cleaner_id]
                + decision.travel_before
                + _break_overlap(shift.start, decision.end, shift.breaks)
            )
            if duty > shift.max_duty:
                reason = "NO_CAPACITY"
                continue
            placed.append(decision)
            cursor[shift.cleaner_id] = decision.end
            previous[shift.cleaner_id] = job.place
            used[shift.cleaner_id] += 1
            service[shift.cleaner_id] += job.duration
            traveled[shift.cleaner_id] += decision.travel_before
            placed_job = True
            break
        if not placed_job:
            uncovered.append((job.task_id, reason))
    travel_seconds = int(sum((item.travel_before.total_seconds() for item in placed), 0))
    return _Plan(tuple(placed), tuple(uncovered), travel_seconds, len(placed))


def _place(
    job: Job,
    shift: Shift,
    origin: str,
    cursor: datetime,
    travel: Travel,
    placed: list[Placed],
) -> Placed | str:
    if job.place not in shift.eligible_places:
        return "INELIGIBLE"
    if not shift.origin:
        return "NO_LOCATION"
    seconds = travel(origin, job.place)
    if seconds is None:
        return "NO_ROUTE"
    leg = _ceil_minutes(int(seconds))
    arrival = _after_breaks(cursor, leg, shift.breaks) + leg
    start = _ceil_instant(max(job.release, arrival))
    start = _after_breaks(start, job.duration, shift.breaks)
    end = start + job.duration
    deadline = _floor_instant(job.deadline)
    shift_end = _floor_instant(shift.end)
    if end > deadline or end > shift_end or _overlaps_break(start, end, shift.breaks):
        return "NO_CAPACITY"
    if _overlaps_existing(shift.cleaner_id, start, end, placed):
        return "NO_CAPACITY"
    return Placed(job.task_id, shift.cleaner_id, start, end, leg, job.place)


def _base_reason(job: Job, shifts: list[Shift]) -> str:
    eligible = [shift for shift in shifts if job.place in shift.eligible_places]
    if not eligible:
        return "INELIGIBLE"
    if all(not shift.origin for shift in eligible):
        return "NO_LOCATION"
    return "NO_CAPACITY"


def _locked_travel(job: Job, locked: list[Job], shifts: list[Shift], travel: Travel) -> timedelta:
    shift = next((item for item in shifts if item.cleaner_id == job.locked_cleaner_id), None)
    if shift is None:
        return timedelta(0)
    origin = shift.origin
    for previous in locked:
        if previous.task_id == job.task_id:
            break
        if previous.locked_cleaner_id == job.locked_cleaner_id:
            origin = previous.place
    seconds = travel(origin, job.place)
    if seconds is None:
        return timedelta(0)
    return _ceil_minutes(int(seconds))


def _travel_rank(travel: Travel, origin: str, dest: str) -> int:
    seconds = travel(origin, dest)
    if seconds is None:
        return 10**9
    return int(seconds)


def _ceil_minutes(seconds: int) -> timedelta:
    return timedelta(minutes=(seconds + 59) // 60)


def _ceil_instant(value: datetime) -> datetime:
    if value.second or value.microsecond:
        return value.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return value


def _floor_instant(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _after_breaks(start: datetime, duration: timedelta, breaks: tuple[tuple[datetime, datetime], ...]) -> datetime:
    changed = True
    while changed:
        changed = False
        end = start + duration
        for break_start, break_end in breaks:
            if start < break_end and end > break_start:
                start = _ceil_instant(break_end)
                changed = True
    return start


def _overlaps_break(start: datetime, end: datetime, breaks: tuple[tuple[datetime, datetime], ...]) -> bool:
    return any(start < break_end and end > break_start for break_start, break_end in breaks)


def _break_overlap(start: datetime, end: datetime, breaks: tuple[tuple[datetime, datetime], ...]) -> timedelta:
    total = timedelta(0)
    for break_start, break_end in breaks:
        left = max(start, break_start)
        right = min(end, break_end)
        if right > left:
            total += right - left
    return total


def _overlaps_existing(cleaner_id: str, start: datetime, end: datetime, placed: list[Placed]) -> bool:
    return any(item.cleaner_id == cleaner_id and start < item.end and end > item.start for item in placed)


def _joint_routes(jobs: list[Job], shifts: list[Shift], travel: Travel, budget: float) -> _Plan | None:
    """Optional circuit successors jointly choose assignments and route order.

    Uses the OR-Tools circuit/transition modeling technique, with independent
    validation at the caller. Deliberately bounded: this is not the full PRD
    lexicographic optimizer. No return-to-origin leg is required.
    """
    started = time.monotonic()
    if not shifts:
        return None
    epoch = min(shift.start for shift in shifts).replace(second=0, microsecond=0)

    def offset(instant: datetime) -> int:
        return int(instant.timestamp() - epoch.timestamp())

    horizon = max(offset(shift.end) for shift in shifts)
    if horizon <= 0:
        return None
    model = cp_model.CpModel()
    presence: dict[tuple[int, int], cp_model.IntVar] = {}
    starts: dict[tuple[int, int], cp_model.IntVar] = {}
    legs: list[tuple[int, int, int, int, cp_model.IntVar]] = []
    costs = []
    for c, shift in enumerate(shifts):
        nodes = []
        for i, job in enumerate(jobs):
            if not shift.origin or job.place not in shift.eligible_places:
                continue
            if job.locked_cleaner_id and job.locked_cleaner_id != shift.cleaner_id:
                continue
            nodes.append(i)
            active = model.new_bool_var(f"present_{c}_{i}")
            start = model.new_int_var(0, horizon // 60, f"start_{c}_{i}")
            presence[c, i], starts[c, i] = active, start
            model.add(start == 0).only_enforce_if(active.Not())
            duration = int(job.duration.total_seconds())
            end = start * 60 + duration
            model.add(start * 60 >= max(offset(job.release), offset(shift.start))).only_enforce_if(active)
            model.add(end <= min(offset(job.deadline), offset(shift.end))).only_enforce_if(active)
            if job.locked_cleaner_id:
                if job.locked_start is None or job.locked_end is None:
                    return None
                model.add(start * 60 == offset(job.locked_start)).only_enforce_if(active)
                model.add(end == offset(job.locked_end)).only_enforce_if(active)
            for b, (left, right) in enumerate(shift.breaks):
                before = model.new_bool_var(f"service_before_{c}_{i}_{b}")
                model.add(end <= offset(left)).only_enforce_if([active, before])
                model.add(start * 60 >= offset(right)).only_enforce_if([active, before.Not()])
        if not nodes:
            continue
        arcs: list[tuple[int, int, cp_model.LiteralT]] = []
        empty = model.new_bool_var(f"empty_{c}")
        arcs.append((0, 0, empty))
        for i in nodes:
            model.add_implication(empty, presence[c, i].Not())
            arcs.append((i + 1, i + 1, presence[c, i].Not()))
            back = model.new_bool_var(f"back_{c}_{i}")
            arcs.append((i + 1, 0, back))
            # The depot return arc closes the circuit, with no physical return.
            for previous in [-1, *nodes]:
                if previous == i:
                    continue
                origin = shift.origin if previous == -1 else jobs[previous].place
                seconds = travel(origin, jobs[i].place)
                if seconds is None or seconds < 0:
                    continue
                leg = int(_ceil_minutes(seconds).total_seconds())
                arc = model.new_bool_var(f"leg_{c}_{previous}_{i}")
                arcs.append((previous + 1, i + 1, arc))
                predecessor_end = (
                    offset(shift.start)
                    if previous == -1
                    else (starts[c, previous] * 60 + int(jobs[previous].duration.total_seconds()))
                )
                depart = model.new_int_var(0, horizon, f"depart_{c}_{previous}_{i}")
                model.add(depart >= predecessor_end).only_enforce_if(arc)
                model.add(starts[c, i] * 60 >= depart + leg).only_enforce_if(arc)
                # Waiting may occur before or after travel, including across a break.
                for b, (left, right) in enumerate(shift.breaks):
                    before = model.new_bool_var(f"travel_before_{c}_{previous}_{i}_{b}")
                    model.add(depart + leg <= offset(left)).only_enforce_if([arc, before])
                    model.add(depart >= offset(right)).only_enforce_if([arc, before.Not()])
                costs.append(arc * leg)
                legs.append((c, previous, i, leg, arc))
        model.add_circuit(arcs)
        service = sum(presence[c, i] * int(jobs[i].duration.total_seconds()) for i in nodes)
        route_travel = sum(arc * leg for cleaner, _, _, leg, arc in legs if cleaner == c)
        _add_job_capacity(model, shift, [(jobs[i], presence[c, i]) for i in nodes], [])
        model.add(service <= int(shift.max_service.total_seconds()))
        # Last completion is zero for an empty route. Charge only elapsed breaks.
        finishes = []
        for i in nodes:
            finish = model.new_int_var(0, horizon, f"finish_{c}_{i}")
            model.add(finish == starts[c, i] * 60 + int(jobs[i].duration.total_seconds())).only_enforce_if(
                presence[c, i]
            )
            model.add(finish == 0).only_enforce_if(presence[c, i].Not())
            finishes.append(finish)
        last = model.new_int_var(0, horizon, f"last_{c}")
        model.add_max_equality(last, finishes)
        break_terms = []
        for b, (left, right) in enumerate(shift.breaks):
            lower, upper = max(offset(shift.start), offset(left)), min(offset(shift.end), offset(right))
            if upper <= lower:
                continue
            clipped = model.new_int_var(0, upper - lower, f"break_{c}_{b}")
            positive = model.new_int_var(0, horizon, f"positive_{c}_{b}")
            model.add_max_equality(positive, [0, last - lower])
            model.add_min_equality(clipped, [positive, upper - lower])
            break_terms.append(clipped)
        model.add(service + route_travel + sum(break_terms) <= int(shift.max_duty.total_seconds()))
    for i, job in enumerate(jobs):
        options = [var for (c, index), var in presence.items() if index == i]
        model.add(sum(options) == 1 if job.locked_cleaner_id else sum(options) <= 1)
    if not presence:
        return None
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 1
    solver.parameters.num_search_workers = 1

    def extract() -> _Plan:
        placed = []
        for (c, i), active in presence.items():
            if not solver.value(active):
                continue
            start = datetime.fromtimestamp(epoch.timestamp() + solver.value(starts[c, i]) * 60, tz=epoch.tzinfo)
            leg = next(
                seconds for cleaner, _, dest, seconds, arc in legs if cleaner == c and dest == i and solver.value(arc)
            )
            placed.append(
                Placed(
                    jobs[i].task_id,
                    shifts[c].cleaner_id,
                    start,
                    start + jobs[i].duration,
                    timedelta(seconds=leg),
                    jobs[i].place,
                )
            )
        placed.sort(key=lambda item: (item.cleaner_id, item.start, item.task_id))
        covered = {item.task_id for item in placed}
        uncovered = tuple((job.task_id, _base_reason(job, shifts)) for job in jobs if job.task_id not in covered)
        return _Plan(
            tuple(placed), uncovered, sum(int(item.travel_before.total_seconds()) for item in placed), len(placed)
        )

    best: _Plan | None = None
    priorities = sorted({job.priority for job in jobs})
    objectives = [
        sum(var for (_, index), var in presence.items() if jobs[index].priority == priority) for priority in priorities
    ]
    starts_by_priority = [
        sum(start for (_, index), start in starts.items() if jobs[index].priority == priority)
        for priority in priorities
    ]
    for objective in [*objectives, *(-value for value in starts_by_priority), -sum(costs)]:
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            break
        solver.parameters.max_time_in_seconds = remaining
        model.maximize(objective)
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        candidate = extract()
        if best is None or _better(candidate, best, jobs):
            best = candidate
        if status != cp_model.OPTIMAL:
            break
        model.add(objective == solver.value(objective))
    return best
