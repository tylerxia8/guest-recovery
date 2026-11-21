"""One-time address routing for the explicitly selected local demonstration baseline."""

from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.adapters.routing.baseline import DrivingBaseline

_capture_lock = threading.Lock()
_last_request = 0.0


def normalized_address(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _get(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    global _last_request
    # This public demonstration service permits at most one request per second.
    time.sleep(max(0, 1.1 - (time.monotonic() - _last_request)))
    _last_request = time.monotonic()
    if params:
        url += "?" + urlencode(params)
    with urlopen(Request(url, headers={"User-Agent": "CharlotteCleaningLocalDemo/1.0"}), timeout=15) as response:
        return dict(json.load(response))


def capture_starting_routes(address: str, baseline: DrivingBaseline) -> dict[str, object]:
    """Cache only measured outbound legs; failures never invent routes or expose an address."""
    with _capture_lock:
        try:
            # Reuse already captured property coordinates/routes when the typed address matches.
            matched = next(
                (
                    key
                    for key, binding in baseline.property_bindings.items()
                    if normalized_address(binding.name) == normalized_address(address)
                ),
                None,
            )
            if matched:
                index = baseline.points.index(matched)
                seconds = {
                    key: baseline.seconds[index][baseline.points.index(key)] for key in baseline.property_bindings
                }
                return {
                    "status": "ready",
                    "baseline_id": baseline.digest,
                    "seconds": seconds,
                    "captured_at": baseline.captured_at.isoformat(),
                    "source": "saved_property_baseline",
                }
            geocode = _get(
                "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                {
                    "address": address,
                    "benchmark": "Public_AR_Current",
                    "format": "json",
                },
            )
            matches = geocode["result"]["addressMatches"]
            if len(matches) != 1:
                return {"status": "address_not_found"}
            coordinate = matches[0]["coordinates"]
            longitude, latitude = float(coordinate["x"]), float(coordinate["y"])
            # Bound this local prototype to the Charlotte region; no unexpected long-distance captures.
            if not (-81.2 < longitude < -80.3 and 34.8 < latitude < 35.6):
                return {"status": "outside_demo_area"}
            keys = sorted(baseline.property_bindings)
            durations: dict[str, int | None] = {}
            for offset in range(0, len(keys), 40):
                batch = keys[offset : offset + 40]
                points = [[longitude, latitude]] + [baseline.coordinates[key] for key in batch]
                coordinates = ";".join(f"{point[0]:.7f},{point[1]:.7f}" for point in points)
                result = _get(
                    "https://router.project-osrm.org/table/v1/driving/" + coordinates,
                    {
                        "sources": "0",
                        "destinations": ";".join(str(i) for i in range(1, len(points))),
                        "annotations": "duration",
                    },
                )
                if result.get("code") != "Ok" or result.get("fallback_speed_cells"):
                    return {"status": "routing_unavailable"}
                if any(float(point["distance"]) > 150 for point in result["sources"] + result["destinations"]):
                    return {"status": "location_needs_review"}
                values = result["durations"]
                if len(values) != 1 or len(values[0]) != len(batch):
                    return {"status": "routing_unavailable"}
                for key, value in zip(batch, values[0], strict=True):
                    if value is not None and (
                        isinstance(value, bool)
                        or not isinstance(value, (float, int))
                        or not math.isfinite(value)
                        or value < 0
                    ):
                        return {"status": "routing_unavailable"}
                    durations[key] = None if value is None else max(60, math.ceil(value))
            return {
                "status": "ready",
                "baseline_id": baseline.digest,
                "seconds": durations,
                "captured_at": datetime.now(UTC).isoformat(),
                "source": "census_osrm_cached",
            }
        except (OSError, KeyError, ValueError, TypeError, IndexError):
            return {"status": "routing_unavailable"}
