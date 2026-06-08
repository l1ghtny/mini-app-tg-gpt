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
    "google": "gemini-3.1-flash-image-preview",
}

LEGACY_IMAGE_MODEL_REPLACEMENTS: dict[str, str] = {
    "gemini-2.5-flash-image": "gemini-3.1-flash-image-preview",
}

GOOGLE_THINKING_MODELS = {
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
}

TEXT_USAGE_BUCKET_MEMBERS: dict[str, tuple[str, ...]] = {
    "gpt-5.4-nano": ("gpt-5.4-nano", "gemini-3.1-flash-lite"),
    "gpt-5.4-mini": ("gpt-5.4-mini", "gemini-3.5-flash"),
    "gpt-5.5": ("gpt-5.5", "gemini-3.1-pro-preview"),
}

TEXT_MODEL_DISPLAY_NAMES: dict[str, tuple[str, str]] = {
    "gpt-5.5": ("Flagship", "Флагман"),
    "gpt-5.2": ("Balanced", "Сбалансированный"),
    "gpt-5.4": ("Balanced", "Сбалансированный"),
    "gpt-5.4-mini": ("Smart", "Умный"),
    "gpt-5.4-nano": ("Fast", "Быстрый"),
    "gemini-3.1-flash-lite": ("Gemini 3.1 Flash Lite", "Gemini 3.1 Flash Lite"),
    "gemini-3.5-flash": ("Gemini 3.5 Flash", "Gemini 3.5 Flash"),
    "gemini-3.1-pro-preview": ("Gemini 3.1 Pro", "Gemini 3.1 Pro"),
}

TEXT_USAGE_BUCKET_BY_MODEL: dict[str, str] = {}
for _bucket_name, _members in TEXT_USAGE_BUCKET_MEMBERS.items():
    for _member in _members:
        TEXT_USAGE_BUCKET_BY_MODEL[_member] = _bucket_name


def canonicalize_image_model(model_name: str) -> str:
    return LEGACY_IMAGE_MODEL_REPLACEMENTS.get(model_name, model_name)


def get_text_model_provider(model_name: str) -> ProviderName:
    return TEXT_MODEL_PROVIDER[model_name]


def get_image_model_provider(model_name: str) -> ProviderName:
    return IMAGE_MODEL_PROVIDER[canonicalize_image_model(model_name)]


def get_default_image_model_for_provider(provider: ProviderName) -> str:
    return DEFAULT_IMAGE_MODEL_BY_PROVIDER[provider]


def get_default_text_model_for_provider(provider: ProviderName) -> str:
    return DEFAULT_TEXT_MODEL_BY_PROVIDER[provider]


def get_text_usage_bucket(model_name: str) -> str:
    return TEXT_USAGE_BUCKET_BY_MODEL.get(model_name, model_name)


def get_text_usage_bucket_models(model_name: str) -> tuple[str, ...]:
    bucket = get_text_usage_bucket(model_name)
    return TEXT_USAGE_BUCKET_MEMBERS.get(bucket, (bucket,))


def list_text_usage_bucket_models(model_name: str) -> list[str]:
    return list(get_text_usage_bucket_models(model_name))


def get_text_model_display_names(model_name: str) -> tuple[str, str]:
    return TEXT_MODEL_DISPLAY_NAMES.get(model_name, (model_name, model_name))


def get_text_usage_bucket_display_names(model_name: str) -> tuple[str, str]:
    bucket = get_text_usage_bucket(model_name)
    return get_text_model_display_names(bucket)


def models_share_provider(text_model: str, image_model: str) -> bool:
    return get_text_model_provider(text_model) == get_image_model_provider(image_model)
