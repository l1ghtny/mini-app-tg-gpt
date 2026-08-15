from app.services.google_service import STYLE_GUIDE


def test_google_visible_reasoning_uses_the_users_language():
    assert "latest message" in STYLE_GUIDE
    assert "visible thought summary" in STYLE_GUIDE
    assert "explicitly asks for another language" in STYLE_GUIDE
