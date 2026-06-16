import uuid
from datetime import datetime, timedelta, timezone
import re

from dateutil.relativedelta import relativedelta
from fastapi import BackgroundTasks, HTTPException, Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.metrics import track_event, track_value
from app.db.models import (
    AppUser,
    BindingMethodType,
    BindingSessionStatus,
    Payment,
    PaymentBindingSession,
    PaymentMethod,
    PaymentMethodStatus,
    PaymentMethodType,
    PaymentProductType,
)
from app.db.subscription_tiers import (
    GeneralDiscount,
    SubscriptionStatus,
    SubscriptionTier,
    UsagePack,
    UsagePackSource,
    UserSubscription,
    UserUsagePack,
)
from app.schemas.subscriptions import (
    BoundSubscriptionChargeRequest,
    BoundSubscriptionChargeResponse,
    CurrentSubscriptionRefundResponse,
    CurrentSubscriptionRefundStatusResponse,
    InitPaymentRequest,
    InitUsagePackPaymentRequest,
    MockUsagePackPurchaseRequest,
    PaymentInitResponse,
    PaymentMethodResponse,
    PaymentMethodsResponse,
    PaymentStatusResponse,
    SubscriptionBindingInitRequest,
    SubscriptionBindingInitResponse,
    SubscriptionBindingStatusResponse,
    UserAgreementResponse,
)
from app.services.banking.tbank import tbank_service
from app.services.legal_documents import (
    PUBLIC_OFFER_TITLE_RU,
    PUBLIC_OFFER_VERSION,
    get_public_offer_text_ru,
)
from app.services.premium_samples import get_recent_premium_sample_kind

logger = settings.custom_logger

RENEWAL_GRACE_HOURS = 24
RENEWAL_RETRY_HOURS = 12
REFUND_WINDOW_HOURS = 24
FINAL_PAYMENT_STATES = {"CONFIRMED", "CANCELED", "REJECTED", "REFUNDED"}
TERMINAL_FAILURE_REASONS = {
    "missing_method",
    "detached_method",
    "expired_method",
    "declined",
    "insufficient_funds",
}


def _tier_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "tier"


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _format_ts(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def _serialize_method_snapshot(method: PaymentMethod) -> dict:
    return {
        "id": str(method.id),
        "type": method.type,
        "pan": method.pan,
        "card_type": method.card_type,
        "phone": method.phone,
    }


def _method_display_pan(method: PaymentMethod) -> str:
    if method.type == PaymentMethodType.sbp.value:
        return method.phone or "SBP"
    return method.pan or "****"


def _binding_method_type(raw: str | None) -> str:
    value = (raw or BindingMethodType.auto.value).strip().lower()
    if value not in {
        BindingMethodType.auto.value,
        BindingMethodType.card.value,
        BindingMethodType.sbp.value,
    }:
        raise HTTPException(status_code=400, detail=f"Unsupported binding method type: {raw}")
    return value


def _renewal_reason_from_error(exc: Exception | str | None) -> str:
    text = str(exc or "").strip().lower()
    if not text:
        return "provider_error"
    if "insufficient" in text or "not enough funds" in text:
        return "insufficient_funds"
    if "expired" in text:
        return "expired_method"
    if "declin" in text or "reject" in text:
        return "declined"
    return "provider_error"


def _subscription_charge_operation_type(*, manual_retry: bool, flow_kind: str) -> str:
    if flow_kind == "renewal" and not manual_retry:
        return "R"
    return "2"


def _binding_failure_message(exc: Exception) -> str:
    message = str(exc).strip()
    if "Failed:" in message:
        message = message.split("Failed:", 1)[1].strip()
    return message[:1000] or "provider_error"


async def _mark_binding_session_failed(
    session: AsyncSession,
    binding_session: PaymentBindingSession,
    *,
    error_code: str,
    error_message: str,
) -> PaymentBindingSession:
    binding_session.status = BindingSessionStatus.failed.value
    binding_session.error_code = error_code
    binding_session.error_message = error_message[:1000] if error_message else error_code
    session.add(binding_session)
    await session.commit()
    await session.refresh(binding_session)
    return binding_session


async def _first_purchase_available(session: AsyncSession, user_id: uuid.UUID) -> bool:
    existing_paid_subscription = await session.exec(
        select(Payment.id).where(
            Payment.user_id == user_id,
            Payment.product_type == PaymentProductType.subscription,
            Payment.amount > 0,
            Payment.tbank_status == "CONFIRMED",
        )
    )
    return existing_paid_subscription.first() is None


async def _eligible_general_discounts_for_tier(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tier: SubscriptionTier,
) -> list[GeneralDiscount]:
    now = _utcnow_naive()
    rows = (await session.exec(
        select(GeneralDiscount).where(
            GeneralDiscount.is_active == True,  # noqa: E712
            (GeneralDiscount.starts_at.is_(None)) | (GeneralDiscount.starts_at <= now),
            (GeneralDiscount.expires_at.is_(None)) | (GeneralDiscount.expires_at > now),
        )
    )).all()

    tier_slug = _tier_slug(tier.name)
    first_purchase_available = await _first_purchase_available(session, user_id)
    eligible: list[GeneralDiscount] = []
    for gd in rows:
        applies_to = gd.applies_to_tiers or ["all"]
        applies_norm = {str(item).strip().lower() for item in applies_to}
        if "all" not in applies_norm and tier_slug.lower() not in applies_norm:
            continue

        conditions = gd.conditions or {}
        if conditions.get("no_prior_paid_sub") and not first_purchase_available:
            continue

        eligible.append(gd)
    return eligible


def _apply_discount_stack(base_amount_cents: int, discounts: list[GeneralDiscount]) -> int:
    amount = float(base_amount_cents)
    if not discounts:
        return int(round(amount))

    any_non_stackable = any(not d.stackable for d in discounts)
    if any_non_stackable:
        best = max(discounts, key=lambda d: int(d.percent_off or 0))
        pct = max(0, min(100, int(best.percent_off or 0)))
        return int(round(amount * (1 - pct / 100)))

    for d in discounts:
        pct = max(0, min(100, int(d.percent_off or 0)))
        amount *= (1 - pct / 100)
    return int(round(amount))


def _build_receipt(*, description: str, email: str | None, amount_cents: int) -> dict | None:
    if not email:
        return None
    return {
        "Taxation": settings.TBANK_TAXATION,
        "Email": email,
        "Items": [
            {
                "Name": description,
                "Price": amount_cents,
                "Quantity": 1,
                "Amount": amount_cents,
                "Tax": "none",
                "PaymentMethod": "full_prepayment",
                "PaymentObject": "service",
            }
        ],
    }


async def _get_current_paid_subscription(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> UserSubscription | None:
    now = _utcnow_naive()
    return (await session.exec(
        select(UserSubscription).where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.active,
            UserSubscription.auto_renew_enabled == True,  # noqa: E712
            (UserSubscription.expires_at.is_(None))
            | (UserSubscription.expires_at > now)
            | (
                UserSubscription.renewal_grace_until.is_not(None)
                & (UserSubscription.renewal_grace_until > now)
            ),
        ).order_by(UserSubscription.started_at.desc())
    )).first()


async def _get_subscription_payment_context(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tier_name: str,
    started_at: datetime,
) -> tuple[Payment | None, str | None]:
    payment = (await session.exec(
        select(Payment).where(
            Payment.user_id == user_id,
            Payment.product_type == PaymentProductType.subscription,
            Payment.tier_name == tier_name,
            (
                Payment.created_at >= started_at
            ) | (
                Payment.updated_at >= started_at
            ),
        ).order_by(Payment.created_at.desc())
    )).first()
    if not payment:
        return None, "no_subscription_payment"
    if payment.tbank_status == "REFUNDED":
        return payment, "already_refunded"
    if payment.tbank_status != "CONFIRMED":
        return payment, "payment_not_confirmed"
    return payment, None


def _refund_deadline(payment: Payment) -> datetime:
    return payment.created_at + timedelta(hours=REFUND_WINDOW_HOURS)


async def _apply_subscription_refund_effect(
    session: AsyncSession,
    *,
    payment: Payment,
    subscription: UserSubscription,
) -> None:
    now = _utcnow_naive()
    subscription.auto_renew_enabled = False
    subscription.renewal_grace_until = None
    subscription.last_renewal_failure_reason = None
    subscription.last_renewal_attempt_at = now

    if payment.flow_kind == "renewal" and subscription.expires_at:
        reversed_expiry = subscription.expires_at - relativedelta(months=1)
        subscription.expires_at = reversed_expiry
        if reversed_expiry <= now:
            subscription.status = SubscriptionStatus.expired
        session.add(subscription)
        return

    subscription.status = SubscriptionStatus.cancelled
    subscription.expires_at = now
    session.add(subscription)


async def _unset_default_payment_methods(session: AsyncSession, user_id: uuid.UUID) -> None:
    methods = (await session.exec(
        select(PaymentMethod).where(
            PaymentMethod.user_id == user_id,
            PaymentMethod.is_default == True,
        )
    )).all()
    for method in methods:
        method.is_default = False
        session.add(method)


async def _upsert_payment_method(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    method_type: str,
    rebill_id: str | None = None,
    account_token: str | None = None,
    pan: str = "****",
    card_type: str = "Unknown",
    exp_date: str = "",
    phone: str | None = None,
    binding_request_key: str | None = None,
    mark_default: bool = True,
) -> PaymentMethod:
    statement = None
    if rebill_id:
        statement = select(PaymentMethod).where(PaymentMethod.rebill_id == rebill_id)
    elif account_token:
        statement = select(PaymentMethod).where(PaymentMethod.account_token == account_token)

    existing = (await session.exec(statement)).first() if statement is not None else None
    if existing:
        method = existing
        method.status = PaymentMethodStatus.active.value
        method.detached_at = None
        method.bound_at = method.bound_at or _utcnow_naive()
    else:
        method = PaymentMethod(
            user_id=user_id,
            rebill_id=rebill_id,
            account_token=account_token,
            type=method_type,
            pan=pan,
            card_type=card_type,
            exp_date=exp_date,
            phone=phone,
            status=PaymentMethodStatus.active.value,
            bound_at=_utcnow_naive(),
            binding_request_key=binding_request_key,
        )
        session.add(method)

    if binding_request_key:
        method.binding_request_key = binding_request_key
    if pan:
        method.pan = pan
    if card_type:
        method.card_type = card_type
    if exp_date:
        method.exp_date = exp_date
    if phone:
        method.phone = phone

    if mark_default:
        await _unset_default_payment_methods(session, user_id)
        method.is_default = True

    session.add(method)
    await session.flush()
    return method


async def save_payment_method(session: AsyncSession, user_id: uuid.UUID, webhook_data: dict | None = None) -> PaymentMethod | None:
    if webhook_data and "AccountToken" in webhook_data:
        return await _upsert_payment_method(
            session,
            user_id=user_id,
            method_type=PaymentMethodType.sbp.value,
            account_token=webhook_data["AccountToken"],
            phone=webhook_data.get("Phone"),
            binding_request_key=webhook_data.get("RequestKey"),
        )

    if webhook_data and "RebillId" in webhook_data:
        return await _upsert_payment_method(
            session,
            user_id=user_id,
            method_type=PaymentMethodType.card.value,
            rebill_id=str(webhook_data["RebillId"]),
            pan=webhook_data.get("Pan", "****"),
            card_type=webhook_data.get("CardType", "Card"),
            exp_date=webhook_data.get("ExpDate", ""),
            binding_request_key=webhook_data.get("RequestKey"),
        )

    cards = await tbank_service.get_card_list(str(user_id))
    if not cards:
        return None
    latest_card = cards[-1]
    return await _upsert_payment_method(
        session,
        user_id=user_id,
        method_type=PaymentMethodType.card.value,
        rebill_id=str(latest_card.get("RebillId")),
        pan=latest_card.get("Pan", "****"),
        card_type=str(latest_card.get("CardType", "Unknown")),
        exp_date=latest_card.get("ExpDate", ""),
    )


async def _get_subscription_tier(session: AsyncSession, tier_name: str) -> SubscriptionTier:
    tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == tier_name))).first()
    if not tier:
        raise HTTPException(status_code=404, detail="Tier not found")
    return tier


async def init_subscription_binding(
    session: AsyncSession,
    user: AppUser,
    payload: SubscriptionBindingInitRequest,
) -> SubscriptionBindingInitResponse:
    tier = await _get_subscription_tier(session, payload.tier_name)
    method_type = _binding_method_type(payload.method_type)

    if method_type == BindingMethodType.auto.value:
        method_type = BindingMethodType.card.value

    if method_type == BindingMethodType.card.value:
        result = await tbank_service.add_card(customer_key=str(user.id), check_type="3DSHOLD")
        request_key = str(result.get("RequestKey"))
        session_obj = PaymentBindingSession(
            user_id=user.id,
            tier_id=tier.id,
            method_type=method_type,
            status=BindingSessionStatus.pending.value,
            request_key=request_key,
            payment_url=result.get("PaymentURL"),
        )
        session.add(session_obj)
        await session.commit()
        await session.refresh(session_obj)
        return SubscriptionBindingInitResponse(
            binding_id=str(session_obj.id),
            status=session_obj.status,
            method_type=method_type,
            payment_url=session_obj.payment_url,
        )

    result = await tbank_service.add_account_qr(
        description=f"Bind method for subscription: {tier.name}",
        data_type="PAYLOAD",
        data={"user_id": str(user.id), "tier_name": tier.name},
        bank_id=payload.bank_id,
    )
    request_key = str(result.get("RequestKey"))
    qr_data = result.get("Data")
    session_obj = PaymentBindingSession(
        user_id=user.id,
        tier_id=tier.id,
        method_type=method_type,
        status=BindingSessionStatus.pending.value,
        request_key=request_key,
        qr_payload=qr_data if isinstance(qr_data, str) else None,
        qr_image_svg=result.get("Data") if str(result.get("DataType", "")).upper() == "IMAGE" else None,
        bank_member_id=result.get("BankMemberId") or payload.bank_id,
    )
    session.add(session_obj)
    await session.commit()
    await session.refresh(session_obj)
    return SubscriptionBindingInitResponse(
        binding_id=str(session_obj.id),
        status=session_obj.status,
        method_type=method_type,
        qr_payload=session_obj.qr_payload,
        qr_image_svg=session_obj.qr_image_svg,
    )


async def _sync_card_binding_session(
    session: AsyncSession,
    binding_session: PaymentBindingSession,
) -> PaymentBindingSession:
    try:
        result = await tbank_service.get_add_card_state(binding_session.request_key)
    except Exception as exc:
        return await _mark_binding_session_failed(
            session,
            binding_session,
            error_code="binding_failed",
            error_message=_binding_failure_message(exc),
        )
    status = str(result.get("Status", "")).upper()
    if result.get("Success") and result.get("RebillId"):
        method = await _upsert_payment_method(
            session,
            user_id=binding_session.user_id,
            method_type=PaymentMethodType.card.value,
            rebill_id=str(result.get("RebillId")),
            pan=result.get("Pan", "****"),
            card_type=result.get("CardType", "Card"),
            exp_date=result.get("ExpDate", ""),
            binding_request_key=binding_session.request_key,
        )
        binding_session.status = BindingSessionStatus.active.value
        binding_session.linked_payment_method_id = method.id
        binding_session.bound_at = method.bound_at or _utcnow_naive()
    elif status in {"REJECTED", "CANCELED", "FAILED"}:
        binding_session.status = BindingSessionStatus.failed.value
        binding_session.error_code = "binding_failed"
        binding_session.error_message = status.lower()
    else:
        binding_session.status = BindingSessionStatus.pending.value
    session.add(binding_session)
    await session.commit()
    await session.refresh(binding_session)
    return binding_session


async def _sync_sbp_binding_session(
    session: AsyncSession,
    binding_session: PaymentBindingSession,
) -> PaymentBindingSession:
    try:
        result = await tbank_service.get_add_account_qr_state(binding_session.request_key)
    except Exception as exc:
        return await _mark_binding_session_failed(
            session,
            binding_session,
            error_code="binding_failed",
            error_message=_binding_failure_message(exc),
        )
    status = str(result.get("Status", "")).upper()
    account_token = result.get("AccountToken")
    if status == "ACTIVE" and account_token:
        method = await _upsert_payment_method(
            session,
            user_id=binding_session.user_id,
            method_type=PaymentMethodType.sbp.value,
            account_token=str(account_token),
            phone=result.get("Phone"),
            binding_request_key=binding_session.request_key,
        )
        binding_session.status = BindingSessionStatus.active.value
        binding_session.linked_payment_method_id = method.id
        binding_session.bound_at = method.bound_at or _utcnow_naive()
    elif status in {"INACTIVE", "FAILED", "CANCELED"}:
        binding_session.status = BindingSessionStatus.failed.value
        binding_session.error_code = "binding_failed"
        binding_session.error_message = status.lower()
    else:
        binding_session.status = BindingSessionStatus.pending.value
    session.add(binding_session)
    await session.commit()
    await session.refresh(binding_session)
    return binding_session


async def get_subscription_binding_status(
    session: AsyncSession,
    user: AppUser,
    binding_id: uuid.UUID,
) -> SubscriptionBindingStatusResponse:
    binding_session = await session.get(PaymentBindingSession, binding_id)
    if not binding_session or binding_session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Binding session not found")

    if binding_session.status == BindingSessionStatus.pending.value:
        if binding_session.method_type == BindingMethodType.card.value:
            binding_session = await _sync_card_binding_session(session, binding_session)
        elif binding_session.method_type == BindingMethodType.sbp.value:
            binding_session = await _sync_sbp_binding_session(session, binding_session)

    return SubscriptionBindingStatusResponse(
        binding_id=str(binding_session.id),
        status=binding_session.status,
        method_type=binding_session.method_type,  # type: ignore[arg-type]
        payment_method_id=str(binding_session.linked_payment_method_id) if binding_session.linked_payment_method_id else None,
        error_code=binding_session.error_code,
        error_message=binding_session.error_message,
    )


async def init_subscription_payment(
    session: AsyncSession,
    user,
    payload: InitPaymentRequest,
) -> PaymentInitResponse:
    binding = await init_subscription_binding(
        session,
        user,
        SubscriptionBindingInitRequest(
            tier_name=payload.tier_name,
            email=payload.email,
            method_type=BindingMethodType.card.value,
        ),
    )
    return PaymentInitResponse(payment_url=binding.payment_url or binding.qr_payload or "", payment_id=binding.binding_id)


async def _select_payment_method(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    payment_method_id: uuid.UUID | None = None,
) -> PaymentMethod:
    query = select(PaymentMethod).where(PaymentMethod.user_id == user_id)
    if payment_method_id:
        query = query.where(PaymentMethod.id == payment_method_id)
    else:
        query = query.where(PaymentMethod.is_default == True)  # noqa: E712
    method = (await session.exec(query)).first()
    if not method:
        raise HTTPException(status_code=409, detail={"error": "missing_payment_method"})
    if method.status == PaymentMethodStatus.detached.value:
        raise HTTPException(status_code=409, detail={"error": "detached_payment_method"})
    if method.status != PaymentMethodStatus.active.value:
        raise HTTPException(status_code=409, detail={"error": "inactive_payment_method"})
    return method


async def _create_subscription_payment(
    session: AsyncSession,
    *,
    user: AppUser,
    tier: SubscriptionTier,
    method: PaymentMethod,
    email: str,
    flow_kind: str,
    manual_retry: bool = False,
) -> Payment:
    base_amount_cents = int(tier.price_cents * 100)
    selected_discounts: list[GeneralDiscount] = []
    if flow_kind == "binding_activation":
        selected_discounts = await _eligible_general_discounts_for_tier(
            session,
            user_id=user.id,
            tier=tier,
        )
    amount_cents = _apply_discount_stack(base_amount_cents, selected_discounts)
    payment = Payment(
        user_id=user.id,
        tier_name=tier.name,
        amount=amount_cents,
        tbank_status="NEW",
        product_type=PaymentProductType.subscription,
        payment_method_id=method.id,
        flow_kind=flow_kind,
        bound_method_snapshot=_serialize_method_snapshot(method),
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    receipt_data = _build_receipt(
        description=f"Subscription: {tier.name}",
        email=email,
        amount_cents=amount_cents,
    )

    is_sbp_method = method.type == PaymentMethodType.sbp.value
    init_data = {"QR": "true"} if is_sbp_method else None
    operation_initiator_type = None if is_sbp_method else _subscription_charge_operation_type(
        manual_retry=manual_retry,
        flow_kind=flow_kind,
    )

    try:
        payment_url, tbank_id = await tbank_service.init_payment(
            order_id=str(payment.id),
            amount_cents=amount_cents,
            description=f"Subscription: {tier.name}",
            user_id=str(user.id),
            recurrent=is_sbp_method,
            receipt=receipt_data,
            data=init_data,
            operation_initiator_type=operation_initiator_type,
        )
        payment.tbank_payment_id = tbank_id
        session.add(payment)
        await session.commit()

        token = method.rebill_id or method.account_token
        payment_type = "sbp" if is_sbp_method else "card"
        await tbank_service.charge(tbank_id, token, payment_type=payment_type)
        method.last_charge_at = _utcnow_naive()
        method.last_charge_status = "processing"
        method.last_charge_error = None
        session.add(method)
        await session.commit()
        return payment
    except Exception as exc:
        reason = _renewal_reason_from_error(exc)
        payment.tbank_status = "ERROR"
        payment.renewal_failure_reason = reason
        method.last_charge_at = _utcnow_naive()
        method.last_charge_status = "failed"
        method.last_charge_error = reason
        session.add(payment)
        session.add(method)
        await session.commit()
        raise HTTPException(status_code=500, detail={"error": reason, "message": str(exc).strip() or reason})


async def charge_bound_subscription(
    session: AsyncSession,
    user: AppUser,
    payload: BoundSubscriptionChargeRequest,
) -> BoundSubscriptionChargeResponse:
    tier = await _get_subscription_tier(session, payload.tier_name)

    if payload.binding_id:
        binding_status = await get_subscription_binding_status(session, user, uuid.UUID(payload.binding_id))
        if binding_status.status != BindingSessionStatus.active.value:
            raise HTTPException(status_code=409, detail={"error": "binding_not_ready"})
        payment_method_id = uuid.UUID(binding_status.payment_method_id)
    elif payload.payment_method_id:
        payment_method_id = uuid.UUID(payload.payment_method_id)
    else:
        payment_method_id = None

    method = await _select_payment_method(session, user_id=user.id, payment_method_id=payment_method_id)
    payment = await _create_subscription_payment(
        session,
        user=user,
        tier=tier,
        method=method,
        email=payload.email,
        flow_kind="binding_activation",
        manual_retry=payload.manual_retry,
    )
    return BoundSubscriptionChargeResponse(
        payment_id=str(payment.id),
        status=payment.tbank_status,
        subscription_status="pending_confirmation",
    )


async def list_payment_methods(session: AsyncSession, user: AppUser) -> PaymentMethodsResponse:
    methods = (await session.exec(
        select(PaymentMethod)
        .where(PaymentMethod.user_id == user.id)
        .order_by(PaymentMethod.is_default.desc(), PaymentMethod.created_at.desc())
    )).all()
    return PaymentMethodsResponse(
        methods=[
            PaymentMethodResponse(
                id=str(method.id),
                type=method.type,  # type: ignore[arg-type]
                status=method.status,
                is_default=bool(method.is_default),
                card_type=method.card_type,
                pan=_method_display_pan(method),
                exp_date=method.exp_date,
                phone=method.phone,
                bound_at=_format_ts(method.bound_at),
                detached_at=_format_ts(method.detached_at),
                last_charge_at=_format_ts(method.last_charge_at),
                last_charge_status=method.last_charge_status,
                last_charge_error=method.last_charge_error,
            )
            for method in methods
        ]
    )


async def set_default_payment_method(
    session: AsyncSession,
    user: AppUser,
    payment_method_id: uuid.UUID,
) -> PaymentMethodResponse:
    method = await _select_payment_method(session, user_id=user.id, payment_method_id=payment_method_id)
    await _unset_default_payment_methods(session, user.id)
    method.is_default = True
    session.add(method)
    await session.commit()
    await session.refresh(method)
    return (await list_payment_methods(session, user)).methods[0]


async def detach_payment_method(
    session: AsyncSession,
    user: AppUser,
    payment_method_id: uuid.UUID,
) -> Response:
    method = await session.get(PaymentMethod, payment_method_id)
    if not method or method.user_id != user.id:
        raise HTTPException(status_code=404, detail="Payment method not found")
    method.status = PaymentMethodStatus.detached.value
    method.is_default = False
    method.detached_at = _utcnow_naive()
    session.add(method)
    await session.commit()
    return Response(status_code=204)


async def get_payment_status(
    session: AsyncSession,
    payment_id: uuid.UUID,
    user: AppUser,
) -> PaymentStatusResponse:
    payment = await session.get(Payment, payment_id)
    if not payment or payment.user_id != user.id:
        raise HTTPException(status_code=404, detail="Payment not found")
    return PaymentStatusResponse(
        id=str(payment.id),
        status=payment.tbank_status,
        is_confirmed=payment.tbank_status == "CONFIRMED",
        tier_name=payment.tier_name,
        product_type=payment.product_type.value,
        product_name=payment.tier_name,
        pack_id=str(payment.pack_id) if payment.pack_id else None,
    )


async def get_current_subscription_refund_status(
    session: AsyncSession,
    user: AppUser,
) -> CurrentSubscriptionRefundStatusResponse:
    subscription = await _get_current_paid_subscription(session, user.id)
    if not subscription:
        return CurrentSubscriptionRefundStatusResponse(
            refundable=False,
            reason="no_active_subscription",
            window_hours=REFUND_WINDOW_HOURS,
        )

    tier = await session.get(SubscriptionTier, subscription.tier_id)
    payment, reason = await _get_subscription_payment_context(
        session,
        user_id=user.id,
        tier_name=tier.name,
        started_at=subscription.started_at,
    )
    if not payment:
        return CurrentSubscriptionRefundStatusResponse(
            refundable=False,
            reason=reason,
            window_hours=REFUND_WINDOW_HOURS,
            tier_name=tier.name,
        )

    deadline = _refund_deadline(payment)
    if deadline <= _utcnow_naive():
        return CurrentSubscriptionRefundStatusResponse(
            refundable=False,
            reason="window_expired",
            window_hours=REFUND_WINDOW_HOURS,
            payment_id=str(payment.id),
            tier_name=tier.name,
            amount_cents=payment.amount,
            purchased_at=_format_ts(payment.created_at),
            refund_deadline_at=_format_ts(deadline),
        )

    return CurrentSubscriptionRefundStatusResponse(
        refundable=reason is None,
        reason=reason,
        window_hours=REFUND_WINDOW_HOURS,
        payment_id=str(payment.id),
        tier_name=tier.name,
        amount_cents=payment.amount,
        purchased_at=_format_ts(payment.created_at),
        refund_deadline_at=_format_ts(deadline),
    )


async def refund_current_subscription(
    session: AsyncSession,
    user: AppUser,
) -> CurrentSubscriptionRefundResponse:
    subscription = await _get_current_paid_subscription(session, user.id)
    if not subscription:
        raise HTTPException(status_code=409, detail={"error": "no_active_subscription"})

    tier = await session.get(SubscriptionTier, subscription.tier_id)
    payment, reason = await _get_subscription_payment_context(
        session,
        user_id=user.id,
        tier_name=tier.name,
        started_at=subscription.started_at,
    )
    if not payment:
        raise HTTPException(status_code=409, detail={"error": reason or "no_subscription_payment"})
    if reason is not None:
        raise HTTPException(status_code=409, detail={"error": reason})
    if _refund_deadline(payment) <= _utcnow_naive():
        raise HTTPException(status_code=409, detail={"error": "window_expired"})
    if not payment.tbank_payment_id:
        raise HTTPException(status_code=409, detail={"error": "missing_provider_payment_id"})

    try:
        await tbank_service.cancel_payment(payment.tbank_payment_id, amount=payment.amount)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "refund_failed", "message": str(exc).strip() or "refund_failed"},
        )

    refunded_at = _utcnow_naive()
    payment.tbank_status = "REFUNDED"
    payment.updated_at = refunded_at
    session.add(payment)
    await _apply_subscription_refund_effect(
        session,
        payment=payment,
        subscription=subscription,
    )
    await session.commit()

    return CurrentSubscriptionRefundResponse(
        payment_id=str(payment.id),
        status=payment.tbank_status,
        subscription_status=subscription.status,
        refunded_at=_format_ts(refunded_at) or refunded_at.isoformat(timespec="seconds"),
    )


async def get_user_agreement() -> UserAgreementResponse:
    return UserAgreementResponse(
        document_key="public_offer",
        version=PUBLIC_OFFER_VERSION,
        lang="ru",
        title=PUBLIC_OFFER_TITLE_RU,
        text=get_public_offer_text_ru(),
    )


async def init_usage_pack_payment(
    session: AsyncSession,
    user,
    payload: InitUsagePackPaymentRequest,
) -> PaymentInitResponse:
    try:
        pack_id = uuid.UUID(payload.pack_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pack_id")

    pack = (await session.exec(
        select(UsagePack).where(
            UsagePack.id == pack_id,
            UsagePack.is_active == True,
            UsagePack.is_public == True,
        )
    )).first()
    if not pack:
        raise HTTPException(status_code=404, detail="Usage pack not found")

    amount_cents = int(pack.price_cents * 100)
    payment = Payment(
        user_id=user.id,
        tier_name=pack.name,
        amount=amount_cents,
        tbank_status="NEW",
        product_type=PaymentProductType.usage_pack,
        pack_id=pack.id,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    receipt_data = _build_receipt(
        description=f"Usage pack: {pack.name}",
        email=payload.email,
        amount_cents=amount_cents,
    )

    try:
        payment_url, tbank_id = await tbank_service.init_payment(
            order_id=str(payment.id),
            amount_cents=amount_cents,
            description=f"Usage pack: {pack.name}",
            user_id=str(user.id),
            recurrent=False,
            receipt=receipt_data,
        )
        payment.tbank_payment_id = tbank_id
        session.add(payment)
        await session.commit()
        return PaymentInitResponse(payment_url=payment_url, payment_id=str(payment.id))
    except Exception as exc:
        payment.tbank_status = "ERROR"
        session.add(payment)
        await session.commit()
        detail = str(exc).strip() or exc.__class__.__name__
        raise HTTPException(status_code=500, detail=detail)


async def _activate_or_renew_subscription(
    session: AsyncSession,
    *,
    payment: Payment,
    existing_subscription: UserSubscription | None = None,
) -> None:
    tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == payment.tier_name))).first()
    if not tier:
        logger.info("Tier %s not found during activation", payment.tier_name)
        return

    now = _utcnow_naive()
    if existing_subscription is None:
        existing_subscription = (await session.exec(
            select(UserSubscription).where(
                UserSubscription.user_id == payment.user_id,
                UserSubscription.tier_id == tier.id,
                UserSubscription.status == SubscriptionStatus.active,
            ).order_by(UserSubscription.started_at.desc())
        )).first()

    if payment.flow_kind == "renewal" and existing_subscription is not None:
        base_expiry = existing_subscription.expires_at if existing_subscription.expires_at and existing_subscription.expires_at > now else now
        existing_subscription.expires_at = base_expiry + relativedelta(months=1)
        existing_subscription.renewal_grace_until = None
        existing_subscription.last_renewal_failure_reason = None
        existing_subscription.last_renewal_attempt_at = now
        existing_subscription.auto_renew_enabled = True
        session.add(existing_subscription)
        return

    active_subs = (await session.exec(
        select(UserSubscription).where(
            UserSubscription.user_id == payment.user_id,
            UserSubscription.status == SubscriptionStatus.active,
        )
    )).all()
    for sub in active_subs:
        sub.status = SubscriptionStatus.cancelled
        session.add(sub)

    new_sub = UserSubscription(
        user_id=payment.user_id,
        tier_id=tier.id,
        status=SubscriptionStatus.active,
        started_at=now,
        expires_at=now + relativedelta(months=1),
        auto_renew_enabled=True,
        renewal_grace_until=None,
        last_renewal_attempt_at=now,
        last_renewal_failure_reason=None,
    )
    session.add(new_sub)


async def activate_usage_pack(session: AsyncSession, payment: Payment) -> None:
    if not payment.pack_id:
        return
    pack = await session.get(UsagePack, payment.pack_id)
    if not pack or not pack.is_active:
        return
    now = _utcnow_naive()
    pack_purchase = UserUsagePack(
        user_id=payment.user_id,
        pack_id=pack.id,
        source=UsagePackSource.paid,
        purchased_at=now,
        expires_at=None,
        payment_id=payment.id,
    )
    session.add(pack_purchase)


async def handle_tbank_webhook(
    session: AsyncSession,
    background_tasks: BackgroundTasks,
    data: dict,
) -> Response:
    if not tbank_service.verify_notification(data):
        logger.error("Webhook signature verification failed. Data: %s", data)
        return Response(content="OK", media_type="text/plain")

    order_id = data.get("OrderId")
    new_status = data.get("Status")
    success = data.get("Success", False)

    if order_id:
        try:
            payment_uuid = uuid.UUID(order_id)
        except ValueError:
            payment_uuid = None
        if payment_uuid:
            payment = (await session.exec(
                select(Payment).where(Payment.id == payment_uuid).with_for_update()
            )).first()
            if payment:
                if payment.tbank_status in FINAL_PAYMENT_STATES:
                    return Response(content="OK", media_type="text/plain")

                payment.tbank_status = new_status
                payment.updated_at = _utcnow_naive()
                payment.renewal_failure_reason = None
                session.add(payment)

                if payment.payment_method_id:
                    method = await session.get(PaymentMethod, payment.payment_method_id)
                    if method:
                        method.last_charge_at = _utcnow_naive()
                        if new_status == "CONFIRMED" and success:
                            method.last_charge_status = "confirmed"
                            method.last_charge_error = None
                        elif new_status in {"REJECTED", "CANCELED"}:
                            method.last_charge_status = "failed"
                            method.last_charge_error = _renewal_reason_from_error(new_status)
                        session.add(method)

                if new_status == "CONFIRMED" and success:
                    if payment.product_type == PaymentProductType.usage_pack:
                        await activate_usage_pack(session, payment)
                        background_tasks.add_task(
                            track_event,
                            "usage_pack_purchased",
                            str(payment.user_id),
                            {"pack": payment.tier_name},
                        )
                    else:
                        active_sub = None
                        if payment.flow_kind == "renewal":
                            active_sub = (await session.exec(
                                select(UserSubscription).where(
                                    UserSubscription.user_id == payment.user_id,
                                    UserSubscription.status == SubscriptionStatus.active,
                                    UserSubscription.auto_renew_enabled == True,  # noqa: E712
                                )
                            )).first()
                        await _activate_or_renew_subscription(session, payment=payment, existing_subscription=active_sub)
                        background_tasks.add_task(
                            track_event,
                            "subscription_purchased",
                            str(payment.user_id),
                            {"tier": payment.tier_name, "flow_kind": payment.flow_kind},
                        )
                        recent_premium_sample_kind = await get_recent_premium_sample_kind(
                            session,
                            user_id=payment.user_id,
                        )
                        if recent_premium_sample_kind:
                            background_tasks.add_task(
                                track_event,
                                "premium_sample_converted",
                                str(payment.user_id),
                                {
                                    "tier": payment.tier_name,
                                    "flow_kind": payment.flow_kind,
                                    "kind": recent_premium_sample_kind,
                                },
                            )

                    background_tasks.add_task(
                        track_value,
                        "revenue",
                        float(payment.amount),
                        str(payment.user_id),
                        {"tier": payment.tier_name},
                        unit="rub",
                    )
                elif new_status == "REFUNDED" and payment.product_type == PaymentProductType.subscription:
                    active_sub = (await session.exec(
                        select(UserSubscription).where(
                            UserSubscription.user_id == payment.user_id,
                            UserSubscription.status == SubscriptionStatus.active,
                        ).order_by(UserSubscription.started_at.desc())
                    )).first()
                    if active_sub:
                        await _apply_subscription_refund_effect(
                            session,
                            payment=payment,
                            subscription=active_sub,
                        )

                await session.commit()
                return Response(content="OK", media_type="text/plain")

    if data.get("RequestKey"):
        binding_session = (await session.exec(
            select(PaymentBindingSession).where(PaymentBindingSession.request_key == str(data["RequestKey"]))
        )).first()
        if binding_session:
            try:
                if data.get("AccountToken"):
                    method = await save_payment_method(session, binding_session.user_id, data)
                    if method:
                        binding_session.status = BindingSessionStatus.active.value
                        binding_session.linked_payment_method_id = method.id
                        binding_session.bound_at = method.bound_at or _utcnow_naive()
                elif data.get("RebillId"):
                    method = await save_payment_method(session, binding_session.user_id, data)
                    if method:
                        binding_session.status = BindingSessionStatus.active.value
                        binding_session.linked_payment_method_id = method.id
                        binding_session.bound_at = method.bound_at or _utcnow_naive()
                elif str(data.get("Status", "")).upper() in {"REJECTED", "FAILED", "INACTIVE"}:
                    binding_session.status = BindingSessionStatus.failed.value
                    binding_session.error_code = "binding_failed"
                    binding_session.error_message = str(data.get("Status")).lower()
                session.add(binding_session)
                await session.commit()
            except Exception:
                logger.exception("Failed to update binding session from webhook request_key=%s", data.get("RequestKey"))

    return Response(content="OK", media_type="text/plain")


async def _get_retryable_subscription(session: AsyncSession, user_id: uuid.UUID) -> UserSubscription:
    now = _utcnow_naive()
    sub = (await session.exec(
        select(UserSubscription).where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.active,
            UserSubscription.auto_renew_enabled == True,  # noqa: E712
            UserSubscription.renewal_grace_until.is_not(None),
            UserSubscription.renewal_grace_until > now,
        ).order_by(UserSubscription.renewal_grace_until.desc())
    )).first()
    if not sub:
        raise HTTPException(status_code=409, detail={"error": "no_retryable_subscription"})
    return sub


async def retry_subscription_renewal(
    session: AsyncSession,
    user: AppUser,
    payment_method_id: uuid.UUID | None = None,
) -> BoundSubscriptionChargeResponse:
    sub = await _get_retryable_subscription(session, user.id)
    tier = await session.get(SubscriptionTier, sub.tier_id)
    method = await _select_payment_method(session, user_id=user.id, payment_method_id=payment_method_id)
    payment = await _create_subscription_payment(
        session,
        user=user,
        tier=tier,
        method=method,
        email="",
        flow_kind="renewal",
        manual_retry=True,
    )
    sub.last_renewal_attempt_at = _utcnow_naive()
    sub.last_renewal_failure_reason = None
    session.add(sub)
    await session.commit()
    return BoundSubscriptionChargeResponse(
        payment_id=str(payment.id),
        status=payment.tbank_status,
        subscription_status="pending_confirmation",
    )


async def process_due_subscription_renewals(session: AsyncSession) -> dict[str, int]:
    now = _utcnow_naive()
    processed = 0
    expired = 0
    retried = 0

    subscriptions = (await session.exec(
        select(UserSubscription)
        .where(
            UserSubscription.status == SubscriptionStatus.active,
            UserSubscription.auto_renew_enabled == True,  # noqa: E712
            UserSubscription.expires_at.is_not(None),
        )
        .order_by(UserSubscription.expires_at.asc())
    )).all()

    for sub in subscriptions:
        if sub.expires_at and sub.expires_at > now and not (
            sub.renewal_grace_until and sub.renewal_grace_until <= now
        ):
            continue

        user = await session.get(AppUser, sub.user_id)
        tier = await session.get(SubscriptionTier, sub.tier_id)
        default_method = (await session.exec(
            select(PaymentMethod).where(
                PaymentMethod.user_id == sub.user_id,
                PaymentMethod.is_default == True,  # noqa: E712
            )
        )).first()

        if sub.renewal_grace_until and sub.renewal_grace_until <= now:
            sub.status = SubscriptionStatus.expired
            session.add(sub)
            expired += 1
            processed += 1
            continue

        if not default_method:
            sub.renewal_grace_until = sub.renewal_grace_until or (now + timedelta(hours=RENEWAL_GRACE_HOURS))
            sub.last_renewal_failure_reason = "missing_method"
            sub.last_renewal_attempt_at = now
            session.add(sub)
            processed += 1
            continue

        if default_method.status == PaymentMethodStatus.detached.value:
            sub.renewal_grace_until = sub.renewal_grace_until or (now + timedelta(hours=RENEWAL_GRACE_HOURS))
            sub.last_renewal_failure_reason = "detached_method"
            sub.last_renewal_attempt_at = now
            session.add(sub)
            processed += 1
            continue

        if sub.last_renewal_attempt_at and sub.last_renewal_attempt_at > (now - timedelta(hours=RENEWAL_RETRY_HOURS)):
            continue

        try:
            await _create_subscription_payment(
                session,
                user=user,
                tier=tier,
                method=default_method,
                email="",
                flow_kind="renewal",
                manual_retry=False,
            )
            sub.last_renewal_attempt_at = now
            sub.renewal_grace_until = None
            sub.last_renewal_failure_reason = None
            session.add(sub)
            retried += 1
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"error": "provider_error"}
            sub.renewal_grace_until = sub.renewal_grace_until or (now + timedelta(hours=RENEWAL_GRACE_HOURS))
            sub.last_renewal_failure_reason = detail.get("error", "provider_error")
            sub.last_renewal_attempt_at = now
            session.add(sub)
        processed += 1

    await session.commit()
    return {"processed": processed, "retried": retried, "expired": expired}


async def mock_usage_pack_purchase(
    session: AsyncSession,
    background_tasks: BackgroundTasks,
    payload: MockUsagePackPurchaseRequest,
) -> Response:
    try:
        user_id = uuid.UUID(payload.user_id)
        pack_id = uuid.UUID(payload.pack_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    user = await session.get(AppUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    pack = await session.get(UsagePack, pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="Usage pack not found")

    amount_cents = int(pack.price_cents * 100)
    payment = Payment(
        user_id=user.id,
        tier_name=pack.name,
        amount=amount_cents,
        tbank_status="NEW",
        product_type=PaymentProductType.usage_pack,
        pack_id=pack.id,
        tbank_payment_id=f"MOCK-{uuid.uuid4()}",
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    payment.tbank_status = "CONFIRMED"
    session.add(payment)
    await activate_usage_pack(session, payment)
    background_tasks.add_task(
        track_event,
        "usage_pack_purchased_mock",
        str(payment.user_id),
        {"pack": payment.tier_name},
    )
    await session.commit()
    return Response(content=f"Mock purchase successful. Payment ID: {payment.id}", media_type="text/plain")
