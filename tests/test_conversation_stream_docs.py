from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router as chat_router


def test_conversation_stream_endpoint_has_documented_redirect_response():
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")
    client = TestClient(app)

    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/v1/conversations/{cid}/stream"]["get"]
    responses = op["responses"]

    assert "307" in responses
    assert "204" in responses

    redirect = responses["307"]
    assert redirect["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ConversationStreamRedirect"
    )
    assert redirect["headers"]["Location"]["schema"]["type"] == "string"
    assert "No active stream for this conversation." in responses["204"]["description"]
