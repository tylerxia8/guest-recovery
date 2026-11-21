from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.routing.baseline import DrivingBaseline, PropertyBinding
from app.clock import Clock
from app.config import Settings
from app.models import Cleaner, CleanerWeeklyHours, Property, ScheduleDay
from app.services.audit import write_audit
from app.services.field_cipher import reveal_text
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id

OSRM_URL = os.environ.get("ROUTING_SERVICE_URL", "http://hospitable-routing:5000").rstrip("/")
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
MANIFEST = Path(os.environ.get("ROUTING_MANIFEST_FILE", "/private/routing/manifest.json"))


def _read(url: str, params: dict[str, str]) -> dict[str, Any]:
    request = Request(
        url + "?" + urlencode(params),
        headers={"User-Agent": "TurnoverLocalRouting/1.0 (https://github.com/tylerxia8/airbnb-management-bot)"},
    )
    for attempt in range(3):
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
                raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError("Routing response exceeded the size limit.")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Routing response is invalid.")
            return result
        except HTTPError as error:
            if error.code != 429 and error.code < 500:
                raise ValueError("Location service rejected the request.") from None
        except (URLError, TimeoutError, OSError):
            pass
        if attempt < 2:
            time.sleep(2**attempt)
    raise ValueError("Location service is temporarily unavailable.")


def _point(value: object) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("Location coordinates are missing.")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in value):
        raise ValueError("Location coordinates are invalid.")
    longitude, latitude = float(value[0]), float(value[1])
    if not (-81.5 < longitude < -80 and 34.5 < latitude < 36):
        raise ValueError("Location is outside the local road map.")
    return [longitude, latitude]


def _table(
    points: list[list[float]], sources: list[int], destinations: list[int]
) -> tuple[list[list[int | None]], float]:
    encoded = ";".join(f"{point[0]:.7f},{point[1]:.7f}" for point in points)
    result = _read(
        f"{OSRM_URL}/table/v1/driving/{encoded}",
        {
            "sources": ";".join(map(str, sources)),
            "destinations": ";".join(map(str, destinations)),
            "annotations": "duration",
        },
    )
    if result.get("code") != "Ok" or result.get("fallback_speed_cells"):
        raise ValueError("Road routes are unavailable.")
    starts, ends, values = result.get("sources"), result.get("destinations"), result.get("durations")
    if (
        not isinstance(starts, list)
        or not isinstance(ends, list)
        or len(starts) != len(sources)
        or len(ends) != len(destinations)
    ):
        raise ValueError("Road route locations are incomplete.")
    snap = 0.0
    for waypoint in starts + ends:
        distance = waypoint.get("distance") if isinstance(waypoint, dict) else None
        if (
            isinstance(distance, bool)
            or not isinstance(distance, (int, float))
            or not math.isfinite(distance)
            or not 0 <= distance <= 150
        ):
            raise ValueError("A location is too far from a mapped road.")
        snap = max(snap, float(distance))
    if not isinstance(values, list) or len(values) != len(sources):
        raise ValueError("Road route matrix is incomplete.")
    rows: list[list[int | None]] = []
    for source, row in zip(sources, values, strict=True):
        if not isinstance(row, list) or len(row) != len(destinations):
            raise ValueError("Road route matrix is incomplete.")
        durations: list[int | None] = []
        for destination, value in zip(destinations, row, strict=True):
            if value is None:
                durations.append(None)
            elif (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
            ):
                raise ValueError("Road route duration is invalid.")
            else:
                durations.append(0 if source == destination else max(60, math.ceil(value)))
        rows.append(durations)
    return rows, snap


def capture_local_start(address: str, baseline: DrivingBaseline) -> dict[str, object]:
    try:
        result = _read(CENSUS_URL, {"address": address, "benchmark": "Public_AR_Current", "format": "json"})
        matches = result.get("result", {}).get("addressMatches")
        if not isinstance(matches, list) or len(matches) != 1:
            return {"status": "address_needs_review"}
        coordinate = matches[0].get("coordinates", {})
        origin = _point([coordinate.get("x"), coordinate.get("y")])
        seconds: dict[str, int | None] = {}
        for offset in range(0, len(baseline.points), 40):
            keys = baseline.points[offset : offset + 40]
            points = [origin] + [baseline.coordinates[key] for key in keys]
            rows, _ = _table(points, [0], list(range(1, len(points))))
            seconds.update(zip(keys, rows[0], strict=True))
        return {
            "status": "ready"
            if seconds and all(value is not None for value in seconds.values())
            else "routing_needs_review",
            "unreachable_count": sum(value is None for value in seconds.values()),
            "baseline_id": baseline.digest,
            "seconds": seconds,
            "captured_at": datetime.now(UTC).isoformat(),
            "source": "local_osrm",
            "geocoder": "us_census",
            "traffic": "none",
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        return {"status": "routing_needs_review"}


def _properties(session: Session, settings: Settings) -> tuple[dict[str, PropertyBinding], dict[str, list[float]]]:
    bindings: dict[str, PropertyBinding] = {}
    coordinates: dict[str, list[float]] = {}
    for prop in session.scalars(select(Property).where(Property.active.is_(True)).order_by(Property.external_id)):
        if not prop.location_ciphertext:
            raise ValueError("A house has no source coordinates.")
        location = json.loads(reveal_text(prop.location_ciphertext, settings.field_encryption_secret))
        coordinates[prop.external_id] = _point(location.get("coordinates"))
        bindings[prop.external_id] = PropertyBinding(property_id=str(prop.id), name=prop.name)
    if not bindings:
        raise ValueError("No houses are available for routing.")
    return bindings, coordinates


def current_local_baseline(session: Session, settings: Settings) -> DrivingBaseline | None:
    baseline = session.info.get("driving_baseline")
    if not isinstance(baseline, DrivingBaseline):
        return None
    try:
        manifest = json.loads(MANIFEST.read_text())
        bindings, coordinates = _properties(session, settings)
        if (
            baseline.source_url != f"{OSRM_URL}#{manifest['graph_id']}"
            or baseline.property_bindings != bindings
            or baseline.coordinates != coordinates
        ):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return baseline


def refresh_local_routing(settings: Settings, factory: sessionmaker[Session], clock: Clock) -> None:
    if not settings.local_free_routing:
        return
    with factory() as session:
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
        previous = session.info.get("driving_baseline")
        factory.configure(info={key: value for key, value in session.info.items() if key != "driving_baseline"})
    manifest = json.loads(MANIFEST.read_text())
    graph_id = manifest["graph_id"]
    if not isinstance(graph_id, str) or re.fullmatch(r"[0-9a-f]{64}", graph_id) is None:
        raise ValueError("Local road map identity is invalid.")
    observed = datetime.fromisoformat(manifest["prepared_at"])
    if observed.tzinfo is None:
        raise ValueError("Local road map timestamp is invalid.")
    source = f"{OSRM_URL}#{graph_id}"
    with factory() as session:
        bindings, coordinates = _properties(session, settings)
        baseline = previous
    if (
        not isinstance(baseline, DrivingBaseline)
        or baseline.source_url != source
        or baseline.property_bindings != bindings
        or baseline.coordinates != coordinates
    ):
        points = list(bindings)
        matrix: list[list[int | None]] = [[None] * len(points) for _ in points]
        max_snap = 0.0
        for a in range(0, len(points), 40):
            for b in range(0, len(points), 40):
                sources = list(range(a, min(a + 40, len(points))))
                destinations = list(range(b, min(b + 40, len(points))))
                combined = sorted(set(sources + destinations))
                indices = {value: index for index, value in enumerate(combined)}
                rows, snap = _table(
                    [coordinates[points[index]] for index in combined],
                    [indices[index] for index in sources],
                    [indices[index] for index in destinations],
                )
                max_snap = max(max_snap, snap)
                for row_index, source_index in enumerate(sources):
                    for column_index, destination_index in enumerate(destinations):
                        matrix[source_index][destination_index] = rows[row_index][column_index]
        baseline = DrivingBaseline(
            schema_version=1,
            captured_at=observed,
            source_url=source,
            coordinate_source_url="https://developer.hospitable.com",
            staging_name="Cleaner starting address",
            staging_source_url=CENSUS_URL,
            points=points,
            seconds=matrix,
            origin_aliases={},
            property_bindings=bindings,
            coordinates=coordinates,
            max_snap_distance_meters=max_snap,
        )
    with factory() as session:
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
        if _properties(session, settings) != (bindings, coordinates):
            raise ValueError("House locations changed during route capture.")
        factory.configure(info={**session.info, "driving_baseline": baseline})
    with factory() as session:
        cleaners = list(
            session.scalars(
                select(CleanerWeeklyHours)
                .join(Cleaner, Cleaner.id == CleanerWeeklyHours.cleaner_id)
                .where(Cleaner.active.is_(True))
            )
        )
        pending = [
            (row.cleaner_id, row.version, row.starting_address_ciphertext)
            for row in cleaners
            if row.starting_address_ciphertext
            and (
                (row.origin_routes or {}).get("baseline_id") != baseline.digest
                or (row.origin_routes or {}).get("status") != "ready"
            )
        ]
    for cleaner_id, version, encrypted in pending:
        if encrypted is None:
            continue
        address = reveal_text(encrypted, settings.field_encryption_secret)
        routes = capture_local_start(address, baseline)
        with factory() as session:
            acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
            cleaner = session.get(Cleaner, cleaner_id)
            row = session.get(CleanerWeeklyHours, cleaner_id)
            if (
                row is None
                or row.version != version
                or row.starting_address_ciphertext != encrypted
                or cleaner is None
                or not cleaner.active
            ):
                continue
            if row.origin_routes == routes:
                continue
            row.origin_routes = routes
            row.version += 1
            row.updated_at = clock.now()
            for day in session.scalars(select(ScheduleDay).with_for_update()):
                day.revision += 1
                day.updated_at = clock.now()
            values = routes.get("seconds")
            write_audit(
                session,
                clock=clock,
                actor_type="system",
                actor_subject="local-free-routing",
                action="cleaner.starting_routes_captured",
                entity_type="cleaner",
                entity_id=cleaner_id,
                correlation_id=uuid.uuid4(),
                reason="Captured static local road estimates.",
                redacted_changes={
                    "status": routes["status"],
                    "route_count": len(values) if isinstance(values, dict) else 0,
                },
                request_id=None,
            )
            session.commit()
