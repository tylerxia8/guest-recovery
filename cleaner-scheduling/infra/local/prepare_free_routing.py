from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "private-data" / "hospitable" / "routing"
OSRM = (
    "ghcr.io/project-osrm/osrm-backend:v6.0.0@sha256:729461bcc9ae9e6aafa92c0f93db9b060a32e85d5e72092c01ae4a4a9f1eb564"
)
OSMIUM = "airbnb-routing-osmium:1.18.0"
BBOX = "-81.5,34.5,-80,36"
REQUIRED = ("cells", "cell_metrics", "partition", "mldgr", "properties", "ramIndex", "fileIndex", "geometry", "names")


def main() -> None:
    os.umask(0o077)
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    sources = {}
    for region in ("north-carolina", "south-carolina"):
        path = STATE / f"{region}.osm.pbf"
        if not path.is_file() or path.is_symlink():
            raise SystemExit(
                f"Download https://download.geofabrik.de/north-america/us/{region}-latest.osm.pbf to {path} first."
            )
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        sources[region] = {
            "url": f"https://download.geofabrik.de/north-america/us/{region}-latest.osm.pbf",
            "sha256": digest.hexdigest(),
        }
    identity = {
        "sources": sources,
        "osrm_image": OSRM,
        "osmium_version": "1.18.0-1",
        "bbox": BBOX,
        "profile": "car",
        "algorithm": "mld",
    }
    graph_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    graph = STATE / "graphs" / graph_id
    graph.mkdir(parents=True, mode=0o700, exist_ok=True)
    manifest_path = graph / "manifest.json"
    complete = manifest_path.is_file() and all(
        (graph / f"charlotte.osrm.{suffix}").is_file() and (graph / f"charlotte.osrm.{suffix}").stat().st_size > 0
        for suffix in REQUIRED
    )
    if not complete:
        subprocess.run(
            ["docker", "build", "-t", OSMIUM, "-f", str(ROOT / "infra/local/osmium.Dockerfile"), str(ROOT)],
            check=True,
            timeout=300,
        )
        prefix = [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--cpus",
            "4",
            "--memory",
            "5g",
            "-v",
            f"{STATE}:/data",
        ]
        target = f"/data/graphs/{graph_id}"
        for region in sources:
            subprocess.run(
                [
                    *prefix,
                    OSMIUM,
                    "osmium",
                    "extract",
                    f"--bbox={BBOX}",
                    "--strategy=complete_ways",
                    f"/data/{region}.osm.pbf",
                    "--output",
                    f"{target}/{region}.osm.pbf",
                    "--overwrite",
                ],
                check=True,
                timeout=300,
            )
        subprocess.run(
            [
                *prefix,
                OSMIUM,
                "osmium",
                "merge",
                f"{target}/north-carolina.osm.pbf",
                f"{target}/south-carolina.osm.pbf",
                "--output",
                f"{target}/charlotte.osm.pbf",
                "--overwrite",
            ],
            check=True,
            timeout=300,
        )
        subprocess.run(
            [*prefix, OSRM, "osrm-extract", "--threads", "4", "-p", "/opt/car.lua", f"{target}/charlotte.osm.pbf"],
            check=True,
            timeout=900,
        )
        for command in ("osrm-partition", "osrm-customize"):
            subprocess.run(
                [*prefix, OSRM, command, "--threads", "4", f"{target}/charlotte.osrm"], check=True, timeout=900
            )
        if not all(
            (graph / f"charlotte.osrm.{suffix}").is_file() and (graph / f"charlotte.osrm.{suffix}").stat().st_size > 0
            for suffix in REQUIRED
        ):
            raise SystemExit("Road graph preparation is incomplete; the prior graph was retained.")
        manifest = {**identity, "graph_id": graph_id, "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        temporary = graph / f"manifest-{uuid.uuid4().hex}.json"
        temporary.write_text(json.dumps(manifest, sort_keys=True))
        temporary.replace(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    current = STATE / "current"
    if current.exists() and not current.is_symlink():
        raise SystemExit("The current graph path must be a symlink; no data was overwritten.")
    temporary_link = STATE / f"current-{uuid.uuid4().hex}"
    temporary_link.symlink_to(Path("graphs") / graph_id, target_is_directory=True)
    temporary_link.replace(current)
    temporary_manifest = STATE / f"manifest-{uuid.uuid4().hex}.json"
    temporary_manifest.write_text(json.dumps(manifest, sort_keys=True))
    temporary_manifest.replace(STATE / "manifest.json")
    print("Local road map prepared. Recreate the routing service and restart the API to load it.")


if __name__ == "__main__":
    main()
