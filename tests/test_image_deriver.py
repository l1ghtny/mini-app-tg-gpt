from app.r2.settings import Settings
from app.services.background import image_deriver


def test_key_from_public_url_accepts_user_and_openai_bases(monkeypatch):
    monkeypatch.setattr(Settings, "R2_PUBLIC_BASE_URL", "https://lightny.ru/images/", raising=False)
    monkeypatch.setattr(Settings, "R2_OPENAI_PUBLIC_BASE_URL", "https://tg-bot-images.lightny.pro/", raising=False)
    monkeypatch.setattr(image_deriver, "R2_BUCKET", "tg-bot-images", raising=False)

    key = "images/free/uploaded/2026/06/14/test.png"
    proxied_url = f"https://lightny.ru/images/tg-bot-images/{key}"
    openai_url = f"https://tg-bot-images.lightny.pro/{key}"

    assert image_deriver._key_from_public_url(proxied_url) == key
    assert image_deriver._key_from_public_url(openai_url) == key


def test_public_url_uses_openai_base_only_for_openai_requests(monkeypatch):
    monkeypatch.setattr(Settings, "R2_PUBLIC_BASE_URL", "https://lightny.ru/images/", raising=False)
    monkeypatch.setattr(Settings, "R2_OPENAI_PUBLIC_BASE_URL", "https://tg-bot-images.lightny.pro/", raising=False)
    monkeypatch.setattr(image_deriver, "R2_BUCKET", "tg-bot-images", raising=False)

    key = "images/free/uploaded/2026/06/14/test.png"

    assert image_deriver._public_url(key) == f"https://lightny.ru/images/tg-bot-images/{key}"
    assert image_deriver._public_url(key, for_openai=True) == f"https://tg-bot-images.lightny.pro/{key}"
