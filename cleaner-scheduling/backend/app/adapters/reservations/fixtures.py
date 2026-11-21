from __future__ import annotations

FIXTURE_CLEANERS: tuple[str, ...] = tuple(f"Synthetic Cleaner {index:02d}" for index in range(1, 11))

PROPERTY_COUNT = 100


def synthetic_properties() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(1, PROPERTY_COUNT + 1):
        rows.append(
            {
                "provider": "mock",
                "external_id": f"synthetic-property-{index:03d}",
                "name": f"Synthetic Property {index:03d}",
                "bedrooms": (index - 1) % 5,
                "latitude": round(1 + (index % 50) / 100, 6),
                "longitude": round(-1 - (index % 50) / 100, 6),
            }
        )
    rows.append(
        {
            "provider": "mock",
            "external_id": "P-A",
            "name": "Synthetic Property A",
            "bedrooms": 2,
            "latitude": 1.01,
            "longitude": -1.01,
        }
    )
    rows.append(
        {
            "provider": "mock",
            "external_id": "P-B",
            "name": "Synthetic Property B",
            "bedrooms": 1,
            "latitude": 1.02,
            "longitude": -1.02,
        }
    )
    return rows
