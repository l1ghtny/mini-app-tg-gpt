from app.services.openai_service import SUMMARY_JSON_SCHEMA, TITLE_JSON_SCHEMA


def _assert_strict_schema_required_properties(schema: dict) -> None:
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_title_json_schema_matches_openai_strict_requirements():
    _assert_strict_schema_required_properties(TITLE_JSON_SCHEMA)


def test_summary_json_schema_matches_openai_strict_requirements():
    _assert_strict_schema_required_properties(SUMMARY_JSON_SCHEMA)
