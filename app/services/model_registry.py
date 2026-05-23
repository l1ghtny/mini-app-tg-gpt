from typing import Literal

TextModelName = Literal[
    "gpt-5.5",
    "gpt-5.2",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
]

ImageModelName = Literal[
    "gpt-image-1.5",
    "gpt-image-2",
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-3-pro-image-preview",
]

ProviderName = Literal["openai", "google"]

TEXT_MODEL_PROVIDER: dict[str, ProviderName] = {
    "gpt-5.5": "openai",
    "gpt-5.2": "openai",
    "gpt-5.4": "openai",
    "gpt-5.4-mini": "openai",
    "gpt-5.4-nano": "openai",
    "gemini-3.1-flash-lite": "google",
    "gemini-3.5-flash": "google",
    "gemini-3.1-pro-preview": "google",
}

IMAGE_MODEL_PROVIDER: dict[str, ProviderName] = {
    "gpt-image-1.5": "openai",
    "gpt-image-2": "openai",
    "gemini-2.5-flash-image": "google",
    "gemini-3.1-flash-image-preview": "google",
    "gemini-3-pro-image-preview": "google",
}

DEFAULT_TEXT_MODEL_BY_PROVIDER: dict[ProviderName, str] = {
    "openai": "gpt-5.4-nano",
    "google": "gemini-3.1-flash-lite",
}

DEFAULT_IMAGE_MODEL_BY_PROVIDER: dict[ProviderName, str] = {
    "openai": "gpt-image-1.5",
    "google": "gemini-2.5-flash-image",
}

GOOGLE_THINKING_MODELS = {
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
}


def get_text_model_provider(model_name: str) -> ProviderName:
    return TEXT_MODEL_PROVIDER[model_name]


def get_image_model_provider(model_name: str) -> ProviderName:
    return IMAGE_MODEL_PROVIDER[model_name]


def get_default_image_model_for_provider(provider: ProviderName) -> str:
    return DEFAULT_IMAGE_MODEL_BY_PROVIDER[provider]


def get_default_text_model_for_provider(provider: ProviderName) -> str:
    return DEFAULT_TEXT_MODEL_BY_PROVIDER[provider]


def models_share_provider(text_model: str, image_model: str) -> bool:
    return get_text_model_provider(text_model) == get_image_model_provider(image_model)
