from app.services.google_service import STYLE_GUIDE as GOOGLE_STYLE_GUIDE
from app.services.openai_service import STYLE_GUIDE as OPENAI_STYLE_GUIDE


def test_provider_visible_reasoning_uses_the_users_language():
    for style_guide in (OPENAI_STYLE_GUIDE, GOOGLE_STYLE_GUIDE):
        assert "latest message" in style_guide
        assert "visible reasoning summary" in style_guide
        assert "explicitly asks for another language" in style_guide
