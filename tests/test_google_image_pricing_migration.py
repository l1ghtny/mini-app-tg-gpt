import importlib


class _RecordingBind:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((statement, params))


def test_google_image_pricing_upgrade_uses_bound_connection(monkeypatch):
    migration = importlib.import_module(
        "migrations.versions.g2a3b4c5d6e7_reprice_google_image_energy_costs"
    )
    bind = _RecordingBind()

    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    migration.upgrade()

    expected_count = len(migration.GOOGLE_IMAGE_ROWS) + len(migration.GOOGLE_IMAGE_ROWS_TO_DISABLE)
    assert len(bind.calls) == expected_count
    assert bind.calls[0][1] == {
        "image_model": "gemini-2.5-flash-image",
        "quality": "1k",
        "credit_cost": 50.0,
        "description": "1k resolution (~$0.039/image)",
        "description_ru": None,
    }


def test_google_image_pricing_downgrade_uses_bound_connection(monkeypatch):
    migration = importlib.import_module(
        "migrations.versions.g2a3b4c5d6e7_reprice_google_image_energy_costs"
    )
    bind = _RecordingBind()

    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    migration.downgrade()

    expected_count = 9 + len(migration.GOOGLE_IMAGE_ROWS_TO_DISABLE)
    assert len(bind.calls) == expected_count
    assert bind.calls[0][1] == {
        "image_model": "gemini-2.5-flash-image",
        "quality": "512",
        "credit_cost": 1.0,
        "description": "512 resolution",
    }
