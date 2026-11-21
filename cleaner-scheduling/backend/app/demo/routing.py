"""Explicit opt-in loader, called only after demo isolation guard passes."""

import os
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import select

from app.adapters.routing.baseline import DrivingBaseline
from app.models import Property

BASELINE_FILE = Path(__file__).resolve().parents[3] / ".local-demo" / "driving-baseline.json"


def install_driving_baseline(app: FastAPI) -> None:
    if os.getenv("LOCAL_DEMO_ROUTING_BASELINE") != "1":
        return
    baseline = DrivingBaseline.model_validate_json(BASELINE_FILE.read_text(encoding="utf-8"))
    with app.state.session_factory() as session:
        properties = {row.external_id: row for row in session.scalars(select(Property))}
        for key, binding in baseline.property_bindings.items():
            prop = properties.get(key)
            if prop is None or str(prop.id) != binding.property_id or prop.name != binding.name:
                raise RuntimeError(
                    "Driving baseline does not match demo houses; rebuild after reviewing local changes."
                )
    app.state.session_factory.configure(info={"driving_baseline": baseline})
