import importlib


class _RecordingBind:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))


class _Inspector:
    def has_table(self, _table_name: str) -> bool:
        return True

    def get_columns(self, table_name: str):
        if table_name == "conversation":
            return [{"name": "image_model"}, {"name": "image_size"}]
        return []


def test_upgrade_rewrites_retired_google_image_models_without_losing_unlimited_limits(
    monkeypatch,
):
    migration = importlib.import_module(
        "migrations.versions.xj7e8f9a0b1c_migrate_google_image_models_to_stable"
    )
    bind = _RecordingBind()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: _Inspector())

    migration.upgrade()

    parameters = [params for _sql, params in bind.calls]
    assert {
        "legacy_model": "gemini-3.1-flash-image-preview",
        "stable_model": "gemini-3.1-flash-image",
    } in parameters
    assert {
        "legacy_model": "gemini-3-pro-image-preview",
        "stable_model": "gemini-3-pro-image",
    } in parameters
    assert any(
        "SET image_size = '1k'" in sql
        and params["stable_model"] == "gemini-3-pro-image"
        for sql, params in bind.calls
    )
    assert any(
        "monthly_requests < 0" in sql
        and "THEN -1" in sql
        for sql, _params in bind.calls
    )
    assert any("lower(quality) = '512'" in sql for sql, _params in bind.calls)


def test_downgrade_does_not_restore_retired_provider_endpoints(monkeypatch):
    migration = importlib.import_module(
        "migrations.versions.xj7e8f9a0b1c_migrate_google_image_models_to_stable"
    )
    bind = _RecordingBind()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    migration.downgrade()

    assert bind.calls == []
