from app.demo.housing import HousingContext, HousingFact, housing_context


def test_local_public_records_are_optional_and_preserve_provenance(monkeypatch, tmp_path):
    path = tmp_path / "housing.json"
    monkeypatch.setattr("app.demo.housing.HOUSING_FILE", path)
    assert housing_context() is None
    context = HousingContext(
        source_url="https://example.org/public-records",
        retrieved_at="2026-11-07T00:00:00Z",
        geography="Fictional test area",
        items=[
            HousingFact(
                property_id="fixture",
                address="Fictional test house",
                property_type="Single-Family",
                assessment_year=2023,
                assessed_value=500000,
            )
        ],
    )
    path.write_text(context.model_dump_json(), encoding="utf-8")
    assert housing_context() == context
