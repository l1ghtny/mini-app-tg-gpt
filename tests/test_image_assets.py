from app.r2.settings import Settings
from app.services import image_assets


def test_public_url_for_key_omits_bucket_from_public_url(monkeypatch):
    monkeypatch.setattr(Settings, "R2_PUBLIC_BASE_URL", "https://lightny.ru/images/", raising=False)
    monkeypatch.setattr(image_assets, "R2_BUCKET", "tg-bot-images", raising=False)

    key = "images/paid/uploaded/2026/06/14/test.png"

    assert image_assets.public_url_for_key("tg-bot-images", key) == f"https://lightny.ru/images/{key}"


def test_key_from_public_url_accepts_current_and_legacy_public_urls(monkeypatch):
    monkeypatch.setattr(Settings, "R2_PUBLIC_BASE_URL", "https://lightny.ru/images/", raising=False)
    monkeypatch.setattr(image_assets, "R2_BUCKET", "tg-bot-images", raising=False)

    key = "images/paid/uploaded/2026/06/14/test.png"

    assert image_assets.key_from_public_url(f"https://lightny.ru/images/{key}") == key
    assert image_assets.key_from_public_url(f"https://lightny.ru/images/tg-bot-images/{key}") == key
