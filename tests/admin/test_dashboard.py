import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from jose import jwt
from sqlalchemy import text

from app.api import admin_dashboard as api
from app.api.dependencies import get_session
from app.core.config import settings
from app.db.allowance import AllowanceAccount, AllowanceRequest, ProviderAttempt
from app.db.models import AppUser, Payment, RequestLedger, State, TokenUsage
from app.db.subscription_tiers import SubscriptionTier, UserSubscription
from app.services import admin_dashboard as reports


@pytest.fixture
def window():
    now = datetime.now(UTC).replace(tzinfo=None)
    return reports.Window(now - timedelta(days=7), now + timedelta(hours=1))


async def seed(session, window):
    user = AppUser(
        telegram_id=111, telegram_first_name="Owner", telegram_username="owner"
    )
    other = AppUser(telegram_id=222, telegram_first_name="Other")
    session.add_all([user, other])
    await session.flush()
    account = AllowanceAccount(
        user_id=user.id,
        scope="shared",
        period_start=window.start,
        period_end=window.end + timedelta(days=20),
        plan="premium",
        rate_version="v1",
        granted=1000,
        spent=100,
        reserved=50,
        luna_granted=1000,
        luna_spent=100,
    )
    session.add(account)
    await session.flush()
    request = AllowanceRequest(
        user_id=user.id,
        account_id=account.id,
        scope="shared",
        request_id="question-1",
        model="gpt-5.6-terra",
        ceiling=200,
        charged=170,
        status="complete",
        created_at=window.start,
    )
    failed = AllowanceRequest(
        user_id=user.id,
        account_id=account.id,
        scope="shared",
        request_id="failed-1",
        model="gpt-5.6-terra",
        ceiling=200,
        status="failed",
        created_at=window.start + timedelta(hours=1),
    )
    session.add_all([request, failed])
    await session.flush()
    session.add_all(
        [
            ProviderAttempt(
                request_id=request.id,
                step_key="plan",
                model="gpt-5.6-luna",
                budget=100,
                supplier_units=100,
                customer_units=100,
                included=True,
                status="complete",
                created_at=window.start,
            ),
            ProviderAttempt(
                request_id=request.id,
                step_key="final",
                model="gpt-5.6-terra",
                budget=200,
                supplier_units=200,
                customer_units=200,
                status="complete",
                created_at=window.start + timedelta(minutes=1),
            ),
            ProviderAttempt(
                request_id=failed.id,
                step_key="failed",
                model="gpt-5.6-terra",
                budget=50,
                supplier_units=50,
                status="failed",
                created_at=window.start,
            ),
            ProviderAttempt(
                request_id=failed.id,
                step_key="old-unknown",
                model="gpt-5.6-terra",
                budget=999,
                supplier_units=None,
                status="unknown",
                created_at=window.start - timedelta(days=2),
            ),
            RequestLedger(
                user_id=user.id,
                request_id="question-1",
                model_name="gpt-5.6-terra",
                feature="text",
                state=State.consumed,
                created_at=window.start,
            ),
            RequestLedger(
                user_id=other.id,
                request_id="legacy-1",
                model_name="legacy",
                feature="text",
                state=State.consumed,
                created_at=window.start,
            ),
            TokenUsage(
                user_id=user.id,
                request_id="question-1",
                model_name="gpt-5.6-terra",
                total_cost=Decimal(".0003"),
                created_at=window.start,
            ),
            TokenUsage(
                user_id=other.id,
                request_id="legacy-1",
                model_name="legacy",
                total_cost=Decimal(".002"),
                created_at=window.start,
            ),
            Payment(
                user_id=user.id,
                tier_name="Premium",
                amount=10000,
                currency="RUB",
                tbank_status="CONFIRMED",
                created_at=window.start,
            ),
            Payment(
                user_id=user.id,
                tier_name="Premium",
                amount=9000,
                currency="RUB",
                tbank_status="REFUNDED",
                created_at=window.start,
            ),
            Payment(
                user_id=user.id,
                tier_name="Premium",
                amount=4000,
                currency="USD",
                tbank_status="NEW",
                created_at=window.start,
            ),
        ]
    )
    await session.commit()
    return user, other, request, failed


@pytest.mark.asyncio
async def test_totals_do_not_multiply_tasks_or_charges(db, window):
    _, session = db
    user, other, request, _ = await seed(session, window)
    result = await reports.overview(session, window)
    assert result["totals"]["tasks"] == 3
    assert result["totals"]["completed"] == 2
    assert result["totals"]["failed"] == 1
    assert result["totals"]["supplier_units"] == 2350
    assert result["totals"]["charged_units"] == 170
    assert result["totals"]["failed_supplier_units"] == 50
    assert result["totals"]["unresolved_exposure_units"] == 999
    assert result["totals"]["active_users"] == 2
    users = await reports.users(session, window)
    assert [r["id"] for r in users["items"]] == [other.id, user.id]
    owner = users["items"][1]
    assert owner["remaining_units"] == 850
    assert owner["luna_remaining_units"] == 900
    assert owner["tasks"] == 2
    assert owner["provider_attempts"] == 3
    attempts = await reports.attempts(session, request.id)
    assert len(attempts["items"]) == 2
    assert "usage_details" not in attempts["items"][0]


@pytest.mark.asyncio
async def test_purchase_currency_refunds_and_current_status(db, window):
    _, session = db
    user, _, _, _ = await seed(session, window)
    summary = {r["currency"]: r for r in await reports.payment_summary(session, window)}
    assert summary["RUB"]["confirmed_amount_minor"] == 10000
    assert summary["RUB"]["refunded_amount_minor"] == 9000
    assert summary["RUB"]["confirmed"] == 1
    assert summary["USD"]["pending"] == 1
    page = await reports.purchases(session, window, user_id=user.id, status="REFUNDED")
    assert page["total"] == 1
    assert page["items"][0]["amount_minor"] == 9000
    assert page["items"][0]["current_entitlement_active"] is False
    assert "bound_method_snapshot" not in page["items"][0]
    assert "tbank_payment_id" not in page["items"][0]


@pytest.mark.asyncio
async def test_test_user_filter_does_not_reduce_guard(db, window, monkeypatch):
    _, session = db
    user, other, _, _ = await seed(session, window)
    monkeypatch.setattr(
        settings, "ADMIN_DASHBOARD_TEST_USER_IDS", frozenset([str(user.id)])
    )
    filtered = await reports.overview(session, window)
    included = await reports.overview(
        session, reports.Window(window.start, window.end, True)
    )
    assert filtered["totals"]["tasks"] == 1
    assert included["totals"]["tasks"] == 3
    assert filtered["spending_guard"] == included["spending_guard"]
    assert (await reports.users(session, window))["items"][0]["id"] == other.id
    assert (
        await reports.users(
            session, reports.Window(window.start, window.end, True), cohort="test"
        )
    )["total"] == 1


@pytest.mark.asyncio
async def test_window_boundaries_pagination_and_literal_search(db, window):
    _, session = db
    user, other, _, _ = await seed(session, window)
    session.add(
        RequestLedger(
            user_id=other.id,
            request_id="boundary-end",
            model_name="legacy",
            feature="text",
            state=State.consumed,
            created_at=window.end,
        )
    )
    await session.commit()
    result = await reports.users(session, window, offset=1, limit=1)
    assert result["total"] == 2 and len(result["items"]) == 1
    assert (await reports.users(session, window, search="%"))["total"] == 0
    assert (await reports.users(session, window, search="' OR TRUE --"))["total"] == 0
    assert (await reports.users(session, window, search="OWNER"))["items"][0][
        "id"
    ] == user.id
    assert (await reports.tasks(session, window, other.id, 0, 20))["total"] == 1


@pytest.mark.asyncio
async def test_current_balances_never_create_missing_grants(db, window):
    _, session = db
    _, other, _, _ = await seed(session, window)
    detail = await reports.user_detail(session, window, other.id)
    assert detail["user"]["remaining_units"] is None
    assert detail["text_usage"]["models"] == []
    assert detail["image_usage"]["models"] == []
    assert detail["transcription"]["remaining_minutes"] == 0
    assert (
        await session.execute(text("SELECT count(*) FROM allowance_account"))
    ).scalar() == 1


@pytest.mark.asyncio
async def test_actual_feature_balances_in_read_only_transaction(db, window):
    _, session = db
    user, _, _, _ = await seed(session, window)
    tier = SubscriptionTier(
        name="Premium",
        name_ru="Премиум",
        price_cents=10000,
        monthly_images=10,
        daily_image_energy=2,
        monthly_transcription_minutes=100,
    )
    session.add(tier)
    await session.flush()
    session.add(
        UserSubscription(
            user_id=user.id,
            tier_id=tier.id,
            started_at=window.start,
            expires_at=window.end + timedelta(days=20),
        )
    )
    session.add(
        RequestLedger(
            user_id=user.id,
            request_id="audio-1",
            tier_id=tier.id,
            model_name="transcribe",
            feature="transcription",
            state=State.consumed,
            cost=7,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    await session.commit()
    await session.execute(
        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    )
    detail = await reports.user_detail(session, window, user.id)
    assert detail["transcription"]["remaining_minutes"] == 93
    assert detail["user"]["tier_name"] == "Premium"
    assert detail["purchases"]["items"][0]["current_entitlement_active"] is True
    assert detail["image_energy"]["sources"]
    assert (await session.execute(text("SHOW transaction_read_only"))).scalar() == "on"


@pytest.mark.asyncio
async def test_unknown_zero_priced_and_non_usd_rows_are_not_complete(db, window):
    _, session = db
    user, _, _, _ = await seed(session, window)
    session.add_all(
        [
            TokenUsage(
                user_id=user.id,
                request_id="unpriced",
                model_name="unknown",
                input_tokens=100,
                total_cost=0,
                created_at=window.start,
            ),
            TokenUsage(
                user_id=user.id,
                request_id="other-currency",
                model_name="unknown",
                total_cost=1,
                currency="EUR",
                created_at=window.start,
            ),
        ]
    )
    await session.commit()
    totals = (await reports.overview(session, window))["totals"]
    assert totals["supplier_units"] == 2350
    assert totals["unknown_cost_rows"] == 2


@pytest.mark.asyncio
async def test_settings_whitelist_and_effective_environment(db, monkeypatch):
    _, session = db
    monkeypatch.setattr(settings, "SECRET_KEY", "never-return-this-secret")
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS", 160000000)
    result = await reports.configuration(session)
    assert "never-return-this-secret" not in str(result)
    assert "DATABASE_URL" not in str(result)
    assert {r["key"]: r["value"] for r in result["flags"]}[
        "SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS"
    ] == 160000000
    assert len(result["plans"]) == 5
    assert result["effective_generation_limits"]["parallelism"] <= 2


@pytest.mark.parametrize("kind", ["empty", "owner", "telegram", "other", "deleted"])
def test_access_is_fail_closed(kind, monkeypatch):
    user = AppUser(telegram_id=111)
    monkeypatch.setattr(
        settings,
        "ADMIN_DASHBOARD_USER_IDS",
        frozenset([str(user.id)]) if kind in ("owner", "deleted") else frozenset(),
    )
    monkeypatch.setattr(
        settings,
        "ADMIN_DASHBOARD_TELEGRAM_IDS",
        frozenset(["111"])
        if kind == "telegram"
        else frozenset(["222"])
        if kind == "other"
        else frozenset(),
    )
    if kind == "deleted":
        user.deleted_at = datetime.now()
    assert api.is_dashboard_admin(user) == (kind in ("owner", "telegram"))


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-01", "2026-10-04"),
        ("2026-10-04T00:00:00+00:00", "2026-10-01T00:00:00+00:00"),
        ("2024-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    ],
)
def test_invalid_windows(start, end):
    with pytest.raises(HTTPException):
        api.reporting_window(datetime.fromisoformat(start), datetime.fromisoformat(end))


@pytest.mark.asyncio
async def test_every_report_route_requires_real_authenticated_admin(
    db, window, monkeypatch
):
    engine, session = db
    user, other, request, _ = await seed(session, window)
    monkeypatch.setattr(settings, "SECRET_KEY", "admin-test-signing-secret")
    monkeypatch.setattr(settings, "ADMIN_DASHBOARD_USER_IDS", frozenset([str(user.id)]))
    monkeypatch.setattr(api, "read_engine", engine)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")

    async def sessions():
        yield session

    app.dependency_overrides[get_session] = sessions

    def token(uid):
        return jwt.encode(
            {"sub": str(uid), "exp": datetime.now(UTC) + timedelta(hours=1)},
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )

    urls = [
        "overview",
        "users",
        "users/" + str(user.id),
        "users/" + str(user.id) + "/tasks",
        "tasks/" + str(request.id) + "/attempts",
        "purchases",
        "settings",
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for url in urls:
            path = "/api/v1/admin/dashboard/" + url
            assert (await client.get(path)).status_code == 401
            assert (
                await client.get(
                    path, headers={"Authorization": "Bearer " + token(other.id)}
                )
            ).status_code == 403
            assert (
                await client.post(
                    path, headers={"Authorization": "Bearer " + token(user.id)}
                )
            ).status_code == 405
            response = await client.get(
                path, headers={"Authorization": "Bearer " + token(user.id)}
            )
            assert response.status_code == 200, (url, response.text)
            assert response.headers["cache-control"] == "private, no-store"
        assert (
            await client.get(
                "/api/v1/admin/dashboard/access",
                headers={"Authorization": "Bearer " + token(other.id)},
            )
        ).json() == {"allowed": False}
        assert (
            await client.get(
                "/api/v1/admin/dashboard/access",
                headers={"Authorization": "Bearer " + token(user.id)},
            )
        ).json() == {"allowed": True}
        assert (
            await client.get(
                "/api/v1/admin/dashboard/users?sort=unsafe",
                headers={"Authorization": "Bearer " + token(user.id)},
            )
        ).status_code == 422


@pytest.mark.asyncio
async def test_unknown_user_and_task_not_found(db, window):
    _, session = db
    with pytest.raises(HTTPException) as err:
        await reports.user_detail(session, window, uuid.uuid4())
    assert err.value.status_code == 404
    with pytest.raises(HTTPException) as err:
        await reports.attempts(session, uuid.uuid4())
    assert err.value.status_code == 404


@pytest.mark.asyncio
async def test_browser_cookie_auth_and_forged_admin_headers(db, window, monkeypatch):
    from app.api.session_helpers import create_browser_session

    engine, session = db
    user, other, _, _ = await seed(session, window)
    owner_cookie = await create_browser_session(session, user)
    other_cookie = await create_browser_session(session, other)
    monkeypatch.setattr(settings, "ADMIN_DASHBOARD_USER_IDS", frozenset([str(user.id)]))
    monkeypatch.setattr(api, "read_engine", engine)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")

    async def sessions():
        yield session

    app.dependency_overrides[get_session] = sessions
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = "/api/v1/admin/dashboard/overview"
        assert (
            await client.get(
                path,
                headers={"X-Admin-Token": "pretend-owner", "X-User-Id": str(user.id)},
            )
        ).status_code == 401
        client.cookies.set(settings.AUTH_COOKIE_NAME, other_cookie)
        assert (await client.get(path)).status_code == 403
        client.cookies.set(settings.AUTH_COOKIE_NAME, owner_cookie)
        result = await client.get(path)
        assert result.status_code == 200
        assert result.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_overlapping_paid_and_private_subscriptions_do_not_hide_grant(db, window):
    _, session = db
    user, _, _, _ = await seed(session, window)
    private = SubscriptionTier(name="Private", name_ru="Private", is_public=False)
    paid = SubscriptionTier(
        name="Paid", name_ru="Paid", is_public=True, price_cents=10000
    )
    session.add_all([private, paid])
    await session.flush()
    session.add_all(
        [
            UserSubscription(
                user_id=user.id,
                tier_id=t.id,
                started_at=window.start,
                expires_at=window.end + timedelta(days=1),
            )
            for t in (private, paid)
        ]
    )
    await session.commit()
    result = await reports.users(session, window, cohort="private")
    assert result["total"] == 1
    assert result["items"][0]["id"] == user.id
    assert result["items"][0]["tier_name"] == "Paid"
    assert result["items"][0]["tasks"] == 2


@pytest.mark.asyncio
async def test_distinct_legacy_helper_cost_is_not_dropped_by_shared_parent(db, window):
    _, session = db
    user, _, _, _ = await seed(session, window)
    session.add(
        TokenUsage(
            user_id=user.id,
            request_id="question-1",
            model_name="legacy-helper",
            total_cost=Decimal(".001"),
            created_at=window.start,
        )
    )
    await session.commit()
    result = await reports.overview(session, window)
    assert result["totals"]["tasks"] == 3
    assert result["totals"]["supplier_units"] == 3350


@pytest.mark.asyncio
async def test_failed_legacy_task_retains_successful_attempt_cost(db, window):
    _, session = db
    user, _, _, _ = await seed(session, window)
    session.add(
        RequestLedger(
            user_id=user.id,
            request_id="legacy-failed",
            model_name="legacy",
            feature="text",
            state=State.failed,
            created_at=window.start,
        )
    )
    session.add(
        TokenUsage(
            user_id=user.id,
            request_id="legacy-failed",
            model_name="legacy",
            total_cost=Decimal(".001"),
            status="success",
            created_at=window.start,
        )
    )
    await session.commit()
    result = await reports.overview(session, window)
    assert result["totals"]["failed"] == 2
    assert result["totals"]["failed_supplier_units"] == 1050


@pytest.mark.asyncio
async def test_missing_provider_records_are_flagged_instead_of_assumed_free(db, window):
    _, session = db
    user, _, _, _ = await seed(session, window)
    session.add(
        RequestLedger(
            user_id=user.id,
            request_id="untracked-audio",
            model_name="transcribe",
            feature="transcription",
            state=State.consumed,
            cost=3,
            created_at=window.start,
        )
    )
    await session.commit()
    overview = await reports.overview(session, window)
    assert overview["totals"]["untracked_cost_tasks"] == 1
    result = await reports.users(session, window, search="owner")
    assert result["items"][0]["untracked_cost_tasks"] == 1


@pytest.mark.asyncio
async def test_legacy_task_reports_recorded_attempt_costs(db, window):
    _, session = db
    _, other, _, _ = await seed(session, window)
    session.add(TokenUsage(
        user_id=other.id, request_id="legacy-1", model_name="legacy-helper",
        total_cost=Decimal(".003"), created_at=window.start,
    ))
    await session.commit()
    task = (await reports.tasks(session, window, other.id, 0, 20))["items"][0]
    assert task["source"] == "legacy"
    assert task["attempts"] == 2
    assert task["supplier_units"] == 5000
    assert task["cost_scope"] == "request"
    assert task["missing_cost"] is False
    assert (await reports.overview(session, window))["totals"]["supplier_units"] == 5350


@pytest.mark.asyncio
async def test_generated_image_ledgers_share_parent_coverage_without_allocated_cost(db, window):
    _, session = db
    user, other, _, _ = await seed(session, window)
    request_id = str(uuid.uuid4())
    session.add_all([
        RequestLedger(
            user_id=user.id, request_id=request_id, model_name="gpt-5.6-terra",
            feature="text", state=State.consumed, created_at=window.start,
        ),
        *[RequestLedger(
            user_id=user.id, request_id=f"{request_id}:img:{ordinal}",
            model_name="gpt-image-1.5", feature="image", state=State.consumed,
            created_at=window.start,
        ) for ordinal in (0, 1)],
        TokenUsage(
            user_id=user.id, request_id=request_id, model_name="gpt-5.6-terra",
            images_generated=2, input_tokens=100, total_cost=Decimal(".3"),
            created_at=window.start,
        ),
        # Another user's identically named child must not inherit this coverage.
        RequestLedger(
            user_id=other.id, request_id=f"{request_id}:img:0",
            model_name="gpt-image-1.5", feature="image", state=State.consumed,
            created_at=window.start,
        ),
    ])
    await session.commit()
    tasks = (await reports.tasks(session, window, user.id, 0, 20))["items"]
    generated = [t for t in tasks if t["request_id"].startswith(request_id)]
    parent = next(t for t in generated if t["feature"] == "text")
    children = [t for t in generated if t["feature"] == "image"]
    assert parent["supplier_units"] == 300000
    assert parent["attempts"] == 1
    assert parent["cost_scope"] == "request"
    assert len(children) == 2
    for child in children:
        assert child["missing_cost"] is False
        assert child["cost_scope"] == "bundled_parent"
        assert child["supplier_units"] is None
        assert child["attempts"] is None
    other_child = next(t for t in (await reports.tasks(session, window, other.id, 0, 20))["items"]
                       if t["request_id"].startswith(request_id))
    assert other_child["missing_cost"] is True
    assert other_child["cost_scope"] == "unknown"
    totals = (await reports.overview(session, window))["totals"]
    assert totals["supplier_units"] == 302350
    assert totals["untracked_cost_tasks"] == 1


@pytest.mark.asyncio
async def test_audio_cost_breakdown_uses_ledger_and_configured_model_identity(db, window, monkeypatch):
    from app.api.audio import _usage_row

    _, session = db
    user, _, _, _ = await seed(session, window)
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_MODEL", "current-audio-model")
    request_id = str(uuid.uuid4())
    session.add(RequestLedger(
        user_id=user.id, request_id=request_id, model_name="historical-audio-model",
        feature="transcription", cost=2, state=State.consumed, created_at=window.start,
    ))
    usage = _usage_row(
        user_id=user.id, request_id=request_id, model="historical-audio-model",
        result=None, duration_seconds=120, status_value="success",
    )
    usage.created_at = window.start
    session.add_all([
        usage,
        TokenUsage(
            user_id=user.id, request_id="audio-without-ledger", model_name="current-audio-model",
            total_cost=Decimal(".001"), created_at=window.start,
        ),
    ])
    await session.commit()
    result = await reports.overview(session, window)
    audio = [r for r in result["breakdown"] if r["model"] in
             {"historical-audio-model", "current-audio-model"}]
    assert len(audio) == 2
    assert {r["feature"] for r in audio} == {"transcription"}
    detail = await reports.user_detail(session, window, user.id)
    assert all(r["feature"] == "transcription" for r in detail["breakdown"]
               if r["model"] in {"historical-audio-model", "current-audio-model"})
