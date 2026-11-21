"""One-time online route capture for the guarded local demo. Reuses saved responses.

Requires local housing.json and staging-source.json, both ignored by Git.
Never called by the API, scheduler, tests or production. No live traffic.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".local-demo"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

USER_AGENT = "CharlotteCleaningLocalDemo/1.0 (https://github.com/tylerxia8/airbnb-management-bot)"


def capture(url: str) -> dict:
    cache = STATE / "routing-source-cache"
    cache.mkdir(exist_ok=True)
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if path.exists():
        return dict(json.loads(path.read_text(encoding="utf-8")))
    # Sequential and slower than the public demonstration service's one request/second limit.
    time.sleep(1.1)
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as response:
        data = json.load(response)
    if data.get("error") or data.get("code", "Ok") != "Ok":
        raise RuntimeError("Online source rejected this request; no fallback durations are generated.")
    path.write_text(json.dumps(data), encoding="utf-8")
    return dict(data)


def main() -> None:
    target = STATE / "driving-baseline.json"
    if target.exists():
        print("Driving baseline already saved; no online requests made. Review before replacing it.")
        return
    from app.adapters.routing.baseline import DrivingBaseline
    from app.config import get_settings
    from app.db import session_factory
    from app.demo.server import guard
    from app.models import CleanerAvailability, Property
    from app.workspaces import LEGACY_WORKSPACE_ID
    from sqlalchemy import select

    import demo

    os.environ.update(demo.environment())
    settings = get_settings()
    guard(settings)
    housing = json.loads((STATE / "housing.json").read_text(encoding="utf-8"))
    staging = json.loads((STATE / "staging-source.json").read_text(encoding="utf-8"))
    place = staging["results"][0]
    source = housing["source_url"]
    query = {
        "f": "json",
        "where": "Jurisdiction = 'CHARLOTTE' AND descpropertyuse IN ('Single-Family','Condo/Townhome')",
        "geometry": "-80.81,35.08,-80.72,35.16",
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "Location",
        "returnGeometry": "false",
        "returnCentroid": "true",
        "outSR": 4326,
        "resultRecordCount": 400,
        "orderByFields": "OBJECTID ASC",
    }
    parcels = capture(source + "/query?" + urlencode(query))
    by_address: dict[str, dict] = {}
    for feature in parcels["features"]:
        address = " ".join((feature["attributes"]["Location"] or "").upper().split())
        if feature.get("centroid"):
            by_address.setdefault(address, feature["centroid"])
    coordinates: dict[str, list[float]] = {}
    bindings: dict[str, dict[str, str]] = {}
    with session_factory(settings, LEGACY_WORKSPACE_ID)() as session:
        properties = {str(row.id): row for row in session.scalars(select(Property))}
        origins = sorted(set(session.scalars(select(CleanerAvailability.origin_label))) - {None})
        for item in housing["items"]:
            prop = properties[item["property_id"]]
            if prop.name != item["address"]:
                raise RuntimeError("House name differs from saved housing data; review before routing.")
            address = " ".join(item["address"].removesuffix(", Charlotte, NC").upper().split())
            point = by_address[address]
            coordinates[prop.external_id] = [float(point["x"]), float(point["y"])]
            bindings[prop.external_id] = {"property_id": str(prop.id), "name": prop.name}
    coordinates["demo:public-staging"] = [float(place["lon"]), float(place["lat"])]
    points = sorted(coordinates)
    size = len(points)
    seconds: list[list[int | None]] = [[None] * size for _ in points]
    max_snap = 0.0
    route_source = "https://router.project-osrm.org"
    # 40x40 directed blocks: bounded requests, no per-pair bulk query loop.
    for start in range(0, size, 40):
        sources = list(range(start, min(start + 40, size)))
        for end in range(0, size, 40):
            destinations = list(range(end, min(end + 40, size)))
            combined = sorted(set(sources + destinations))
            local = {key: i for i, key in enumerate(combined)}
            coords = ";".join(f"{coordinates[points[i]][0]:.7f},{coordinates[points[i]][1]:.7f}" for i in combined)
            params = {
                "sources": ";".join(str(local[i]) for i in sources),
                "destinations": ";".join(str(local[i]) for i in destinations),
                "annotations": "duration",
            }
            result = capture(route_source + "/table/v1/driving/" + coords + "?" + urlencode(params))
            if result.get("fallback_speed_cells"):
                raise RuntimeError("Estimated fallback cells are not road routes.")
            durations = result["durations"]
            if len(durations) != len(sources) or any(len(row) != len(destinations) for row in durations):
                raise RuntimeError("Routing matrix dimensions did not match the request.")
            for waypoint in result["sources"] + result["destinations"]:
                max_snap = max(max_snap, float(waypoint["distance"]))
            for i, origin in enumerate(sources):
                for j, destination in enumerate(destinations):
                    value = durations[i][j]
                    seconds[origin][destination] = (
                        None if value is None else (0 if origin == destination else max(60, math.ceil(value)))
                    )
            print(f"Captured driving block {start // 40 + 1},{end // 40 + 1}", flush=True)
    if max_snap > 150:
        raise RuntimeError("A road snap exceeds 150 meters. Review coordinates before enabling this baseline.")
    baseline = DrivingBaseline(
        schema_version=1,
        captured_at=datetime.now(UTC),
        source_url=route_source,
        coordinate_source_url=source,
        staging_name=place["name"],
        staging_source_url=f"https://www.openstreetmap.org/{place['osm_type']}/{place['osm_id']}",
        points=points,
        seconds=seconds,
        property_bindings=bindings,
        coordinates=coordinates,
        origin_aliases={origin: "demo:public-staging" for origin in origins},
        max_snap_distance_meters=max_snap,
    )
    temporary = target.with_suffix(".pending")
    temporary.write_text(baseline.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)
    print(json.dumps(baseline.summary(), indent=2))


if __name__ == "__main__":
    main()
