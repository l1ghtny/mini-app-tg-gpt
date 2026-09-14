from openai.types.responses.tool import ImageGeneration, WebSearchTool

from app.api.chat_helpers import _is_image_generation_requested, _resolve_openai_tooling


def _available_tools():
    return [
        WebSearchTool(type="web_search"),
        ImageGeneration(type="image_generation", model="gpt-image-1.5", quality="low"),
    ]


def test_resolve_openai_tooling_for_list_selection():
    tools, tool_choice, ledger_choice = _resolve_openai_tooling(["web_search"], _available_tools())

    assert [tool.type for tool in tools] == ["web_search"]
    assert tool_choice == {
        "type": "allowed_tools",
        "mode": "auto",
        "tools": [{"type": "web_search"}],
    }
    assert ledger_choice == "web_search"


def test_resolve_openai_tooling_for_empty_list():
    tools, tool_choice, ledger_choice = _resolve_openai_tooling([], _available_tools())

    assert tools == []
    assert tool_choice == "none"
    assert ledger_choice == "none"


def test_resolve_openai_tooling_for_single_tool_string():
    tools, tool_choice, ledger_choice = _resolve_openai_tooling("image_generation", _available_tools())

    assert [tool.type for tool in tools] == ["image_generation"]
    assert tool_choice == {
        "type": "allowed_tools",
        "mode": "required",
        "tools": [{"type": "image_generation"}],
    }
    assert ledger_choice == "image_generation"


def test_image_generation_request_detection():
    assert _is_image_generation_requested(["web_search", "image_generation"])
    assert _is_image_generation_requested("image_generation")
    assert not _is_image_generation_requested(["web_search"])


import pytest
from fastapi import HTTPException


def test_required_tool_keeps_other_permitted_tools():
    tools, choice, _ = _resolve_openai_tooling("auto", _available_tools(), required_tool="web_search")
    assert [tool.type for tool in tools] == ["web_search", "image_generation"]
    assert choice == {"type": "web_search"}


@pytest.mark.parametrize("permissions,required,error", [([], "web_search", "required_tool_not_allowed"), (["web_search"], "image_generation", "required_tool_not_allowed"), ("auto", "file_search", "required_tool_unavailable")])
def test_required_tool_fails_closed(permissions, required, error):
    with pytest.raises(HTTPException) as exc:
        _resolve_openai_tooling(permissions, _available_tools(), required_tool=required)
    assert exc.value.detail["error"] == error


def test_missing_optional_tool_does_not_enable_everything():
    tools, choice, _ = _resolve_openai_tooling(["file_search"], _available_tools())
    assert tools == [] and choice == "none"


@pytest.mark.parametrize("provider,required", [("google", "web_search"), ("perplexity", "image_generation")])
def test_unsupported_provider_requirement_is_rejected(provider, required):
    with pytest.raises(HTTPException) as exc:
        _resolve_openai_tooling("auto", _available_tools(), required_tool=required, provider=provider)
    assert exc.value.detail["error"] == "required_tool_not_supported"
