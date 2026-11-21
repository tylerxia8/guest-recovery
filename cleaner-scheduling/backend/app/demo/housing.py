"""Optional local public-record context; never bundled with the synthetic seed."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

HOUSING_FILE = Path(__file__).resolve().parents[3] / ".local-demo" / "housing.json"


class HousingFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    property_id: str
    address: str
    property_type: str
    assessment_year: int
    assessed_value: int


class HousingContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_url: str
    retrieved_at: str
    geography: str
    items: list[HousingFact]


def housing_context() -> HousingContext | None:
    if not HOUSING_FILE.exists():
        return None
    return HousingContext.model_validate_json(HOUSING_FILE.read_text(encoding="utf-8"))
