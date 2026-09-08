import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
from PIL import Image
from sqlalchemy.dialects import postgresql

from app.api import chat_helpers
from app.db.models import DerivedImage
from app.r2.settings import Settings
from app.services.background import image_deriver as deriver
from app.services.public_image_reachability import ImageReachabilityError

OWNER = uuid.uuid4()


@pytest.fixture(autouse=True)
def rebuild_test_db():
    """These tests use real image bytes and mocked storage, never a shared database."""


def image_bytes(size, fmt="PNG", mode="RGB", **save_kwargs):
    with Image.new(
        mode, size, (120, 80, 160, 110) if mode == "RGBA" else (120, 80, 160)
    ) as im:
        buffer = io.BytesIO()
        im.save(buffer, format=fmt, **save_kwargs)
        return buffer.getvalue()


@pytest.mark.parametrize(
    "size",
    [
        (8160, 6144),  # Exactly 48,960 patches: the reported failure.
        (8192, 6144),
        (40_000, 1000),
        (1000, 40_000),
        (100_000, 1),
        (1, 100_000),
        (5537, 5537),
        (10_000, 10_000),
        (32, 32),
        (1, 1),
    ],
)
def test_dimensions_fit_patch_budget_preserving_aspect_ratio(size):
    width, height = deriver._fit_image_dimensions(*size, deriver.MAX_IMAGE_SIDE)
    assert ((width + 31) // 32) * ((height + 31) // 32) <= 30_000
    assert max(width, height) <= 65_535
    assert width <= size[0] and height <= size[1]
    # Integer rounding loses at most one pixel on the shorter side.
    assert abs(width * size[1] - height * size[0]) < max(size)
    if max(width, height) < min(max(size), 65_535):
        next_side = max(width, height) + 1
        next_w = max(1, size[0] * next_side // max(size))
        next_h = max(1, size[1] * next_side // max(size))
        assert ((next_w + 31) // 32) * ((next_h + 31) // 32) > 30_000


@pytest.mark.parametrize("size", [(4000, 3000), (6400, 4800), (32, 32)])
def test_safe_dimensions_are_not_reduced(size):
    assert deriver._fit_image_dimensions(*size, deriver.MAX_IMAGE_SIDE) == size


@pytest.mark.parametrize(
    "fmt,mime",
    [
        ("JPEG", "image/jpeg"),
        ("PNG", "image/png"),
        ("WEBP", "image/webp"),
        ("GIF", "image/gif"),
    ],
)
def test_safe_supported_images_pass_through_without_reencoding(fmt, mime):
    original = image_bytes((128, 96), fmt)
    assert deriver._derive_image_sync(original, mime, deriver.MAX_IMAGE_SIDE) is None


def test_large_but_safe_photo_keeps_original_detail():
    original = image_bytes((4000, 3000), "JPEG")
    assert (
        deriver._derive_image_sync(original, "image/jpeg", deriver.MAX_IMAGE_SIDE)
        is None
    )


def test_reported_patch_overflow_resizes_real_jpeg():
    original = image_bytes((8160, 6144), "JPEG")
    converted, mime, target = deriver._derive_image_sync(
        original, "image/jpeg", deriver.MAX_IMAGE_SIDE
    )
    with Image.open(io.BytesIO(converted)) as im:
        im.load()
        assert (
            max(im.size) > 6000
        )  # Preserve substantially more detail than a 2048px cap.
        assert ((im.width + 31) // 32) * ((im.height + 31) // 32) <= 30_000
        assert im.format == "JPEG"
    with Image.open(io.BytesIO(original)) as im:
        assert im.size == (8160, 6144)
    assert (mime, target) == ("image/jpeg", "jpeg")


@pytest.mark.parametrize(
    "fmt,mime,target",
    [
        ("PNG", "image/png", "png"),
        ("WEBP", "image/webp", "webp"),
        ("GIF", "image/gif", "png"),
        ("BMP", "image/bmp", "jpeg"),
        ("TIFF", "image/tiff", "jpeg"),
        ("HEIF", "image/heic", "jpeg"),
    ],
)
def test_supported_and_conversion_formats_honor_explicit_size_limit(fmt, mime, target):
    converted, _, actual_target = deriver._derive_image_sync(
        image_bytes((256, 128), fmt), mime, 64
    )
    with Image.open(io.BytesIO(converted)) as im:
        assert im.size == (64, 32)
    assert actual_target == target


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("WEBP", "image/webp")])
def test_resized_images_preserve_transparency(fmt, mime):
    converted, _, _ = deriver._derive_image_sync(
        image_bytes((256, 128), fmt, "RGBA"), mime, 64
    )
    with Image.open(io.BytesIO(converted)) as im:
        assert im.mode == "RGBA"
        assert im.getpixel((32, 16))[3] == 110


def test_conversion_normalizes_exif_orientation():
    exif = Image.Exif()
    exif[274] = 6
    original = image_bytes((256, 128), "JPEG", exif=exif)
    converted, _, _ = deriver._derive_image_sync(original, "image/jpeg", 64)
    with Image.open(io.BytesIO(converted)) as im:
        assert im.size == (32, 64)
        assert im.getexif().get(274, 1) == 1


def test_mislabeled_image_uses_actual_format():
    result = deriver._derive_image_sync(image_bytes((256, 128), "BMP"), "image/png", 64)
    assert result[1:] == ("image/jpeg", "jpeg")


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setattr(
        Settings, "R2_PUBLIC_BASE_URL", "https://images.example/", raising=False
    )
    monkeypatch.setattr(
        Settings,
        "R2_OPENAI_PUBLIC_BASE_URL",
        "https://model-images.example/",
        raising=False,
    )
    mocks = SimpleNamespace(
        find_asset=AsyncMock(
            return_value=SimpleNamespace(
                user_id=OWNER,
                bucket=deriver.R2_BUCKET,
                key="photo.png",
                status="active",
                expires_at=None,
            )
        ),
        head=AsyncMock(return_value={"ContentType": "image/png"}),
        get=AsyncMock(return_value=image_bytes((256, 128))),
        put=AsyncMock(),
        reachable=AsyncMock(),
        mark=AsyncMock(),
    )
    for name, mock in (
        ("find_asset_by_url", mocks.find_asset),
        ("head_object", mocks.head),
        ("get_bytes", mocks.get),
        ("put_bytes", mocks.put),
        ("wait_for_image_url_reachability", mocks.reachable),
        ("mark_asset_status", mocks.mark),
    ):
        monkeypatch.setattr(deriver, name, mock)
    mocks.session = SimpleNamespace(
        exec=AsyncMock(return_value=Mock(first=Mock(return_value=None))),
        commit=AsyncMock(),
    )
    return mocks


@pytest.mark.asyncio
async def test_storage_returns_safe_original_without_writing(storage):
    result = await deriver.ensure_openai_compatible_image_url(
        storage.session, "https://images.example/photo.png", user_id=OWNER
    )
    assert result == "https://model-images.example/photo.png"
    storage.get.assert_awaited_once_with("photo.png")
    storage.put.assert_not_awaited()
    storage.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_saves_only_derived_copy_and_reuses_cached_variant(storage):
    result = await deriver.ensure_openai_compatible_image_url(
        storage.session, "https://images.example/photo.png", max_size=64, user_id=OWNER
    )
    key, converted = storage.put.call_args.args
    assert key.startswith("derived/")
    assert result == f"https://model-images.example/{key}"
    with Image.open(io.BytesIO(converted)) as im:
        assert im.size == (64, 32)
    insert_stmt = storage.session.exec.call_args.args[0]
    compiled = insert_stmt.compile(dialect=postgresql.dialect())
    assert compiled.params["original_key"] == "photo.png"
    assert compiled.params["derived_key"] == key
    assert "ON CONFLICT ON CONSTRAINT uq_derived_image_variant DO NOTHING" in str(
        compiled
    )
    storage.session.commit.assert_awaited_once()

    row = DerivedImage(
        original_key="photo.png", target_format="png", max_side=64, derived_key=key
    )
    storage.session.exec.return_value = Mock(first=Mock(return_value=row))
    storage.get.reset_mock()
    storage.put.reset_mock()
    cached = await deriver.ensure_openai_compatible_image_url(
        storage.session, "https://images.example/photo.png", max_size=64, user_id=OWNER
    )
    assert cached == result
    storage.get.assert_not_awaited()
    storage.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_identical_bytes_from_distinct_originals_get_distinct_derived_keys(
    storage,
):
    storage.find_asset.side_effect = lambda _session, url, **kw: SimpleNamespace(
        user_id=OWNER,
        bucket=deriver.R2_BUCKET,
        key=url.rsplit("/", 1)[-1],
        status="active",
        expires_at=None,
    )
    first = await deriver.ensure_openai_compatible_image_url(
        storage.session, "https://images.example/one.png", max_size=64, user_id=OWNER
    )
    second = await deriver.ensure_openai_compatible_image_url(
        storage.session, "https://images.example/two.png", max_size=64, user_id=OWNER
    )
    assert first != second


@pytest.mark.asyncio
async def test_expired_asset_is_rejected_before_using_cache(storage):
    storage.find_asset.return_value = SimpleNamespace(
        user_id=OWNER,
        bucket=deriver.R2_BUCKET,
        key="photo.png",
        status="expired",
        expires_at=None,
    )
    with pytest.raises(HTTPException) as exc:
        await deriver.ensure_openai_compatible_image_url(
            storage.session, "https://images.example/photo.png", user_id=OWNER
        )
    assert exc.value.status_code == 410
    storage.session.exec.assert_not_awaited()
    storage.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_original_is_rejected_even_if_a_variant_exists(storage):
    storage.head.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}}, "HeadObject"
    )
    with pytest.raises(HTTPException) as exc:
        await deriver.ensure_openai_compatible_image_url(
            storage.session, "https://images.example/photo.png", user_id=OWNER
        )
    assert exc.value.status_code == 410
    storage.session.exec.assert_not_awaited()


@pytest.mark.asyncio
async def test_disappearing_original_is_marked_missing(storage):
    asset = SimpleNamespace(
        status="active",
        expires_at=None,
        key="photo.png",
        bucket=deriver.R2_BUCKET,
        user_id=OWNER,
    )
    storage.find_asset.return_value = asset
    storage.get.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    with pytest.raises(HTTPException) as exc:
        await deriver.ensure_openai_compatible_image_url(
            storage.session, "https://images.example/photo.png", user_id=OWNER
        )
    assert exc.value.status_code == 410
    storage.mark.assert_awaited_once_with(storage.session, asset, "missing")


@pytest.mark.asyncio
async def test_invalid_image_returns_client_error_without_storage_write(storage):
    storage.get.return_value = b"invalid image data"
    with pytest.raises(HTTPException) as exc:
        await deriver.ensure_openai_compatible_image_url(
            storage.session, "https://images.example/photo.png", user_id=OWNER
        )
    assert exc.value.status_code == 400
    storage.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreachable_derived_image_is_not_sent_to_provider(storage):
    storage.reachable.side_effect = ImageReachabilityError(
        "https://model-images.example/derived/photo.png"
    )
    with pytest.raises(HTTPException) as exc:
        await deriver.ensure_openai_compatible_image_url(
            storage.session,
            "https://images.example/photo.png",
            max_size=64,
            user_id=OWNER,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "image_not_ready"


@pytest.mark.asyncio
async def test_tracked_image_on_old_domain_is_still_resized(storage):
    storage.find_asset.return_value = SimpleNamespace(
        status="active",
        expires_at=None,
        key="photo.png",
        bucket=deriver.R2_BUCKET,
        user_id=OWNER,
    )
    result = await deriver.ensure_openai_compatible_image_url(
        storage.session,
        "https://old-images.example/photo.png",
        max_size=64,
        user_id=OWNER,
    )
    assert result.startswith("https://model-images.example/derived/")


@pytest.mark.asyncio
async def test_external_urls_are_not_fetched_by_the_server(storage):
    url = "https://external.example/photo.png"
    storage.find_asset.return_value = None
    with pytest.raises(HTTPException) as exc:
        await deriver.ensure_openai_compatible_image_url(
            storage.session, url, user_id=OWNER
        )
    assert exc.value.status_code == 403
    storage.head.assert_not_awaited()
    storage.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_prepares_both_new_and_replayed_images_with_detail_preserving_budget(
    storage, monkeypatch
):
    storage.session.exec.side_effect = [
        Mock(first=Mock(return_value=OWNER)),
        Mock(first=Mock(return_value=None)),
    ] * 2
    prepare = AsyncMock(wraps=deriver.ensure_openai_compatible_image_url)
    monkeypatch.setattr(chat_helpers, "ensure_openai_compatible_image_url", prepare)
    candidate = chat_helpers._HistoryCandidate(
        message_id=uuid.uuid4(),
        estimated_tokens=300,
        payload={
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": "https://images.example/photo.png"}
            ],
        },
    )
    for replay in (False, True):
        result = await chat_helpers._finalize_history_payload(
            storage.session, candidate, skip_unavailable_images=replay
        )
        assert result["content"] == [
            {
                "type": "input_image",
                "image_url": "https://model-images.example/photo.png",
            }
        ]
        # No forced 2048px limit and no forced low-detail provider setting.
        assert prepare.call_args.kwargs == {"user_id": OWNER}
