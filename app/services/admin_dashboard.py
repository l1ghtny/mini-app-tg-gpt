"""Read models; never initialize grants, settle tasks, or reprice historical use."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select

from app.api import user_usage_helpers
from app.core.config import settings
from app.core.version import APP_VERSION
from app.db.models import AiModelPricing, AppUser, ImageQualityPricing, TextModelCatalog
from app.db.subscription_tiers import SubscriptionTier, UsagePack
from app.services.allowance import accounting_scope
from app.services.allowance_policy import (
    MODELS,
    PLANS,
    grant_units,
    GRANT_VERSION_V2,
    RATE_VERSION,
)
from app.services.allowance_task_policy import limits


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    include_test: bool = False

    def params(self):
        return dict(
            start=self.start,
            end=self.end,
            now=datetime.now(UTC).replace(tzinfo=None),
            excluded=[]
            if self.include_test
            else list(settings.ADMIN_DASHBOARD_TEST_USER_IDS),
            test_ids=list(settings.ADMIN_DASHBOARD_TEST_USER_IDS),
            scope=accounting_scope(),
        )


# Each source aggregates independently before joining users. A logical task is
# not multiplied by provider attempts, token rows, overlapping tiers or payments.
CTES = """
WITH tasks AS (
    SELECT id::text AS id, user_id, request_id, created_at, model, 'shared' AS source,
           'text' AS feature, status, charged, luna_charged
    FROM allowance_request
    UNION ALL
    SELECT l.id::text, l.user_id, l.request_id, l.created_at, l.model_name, 'legacy', l.feature,
           CASE l.state::text WHEN 'consumed' THEN 'complete' WHEN 'reserved' THEN 'reserved'
                ELSE 'failed' END, 0, 0
    FROM request_ledger l
    WHERE NOT EXISTS (SELECT 1 FROM allowance_request r
                      WHERE r.user_id=l.user_id AND r.request_id=l.request_id)
), selected_tasks AS (
    SELECT t.*, (NOT EXISTS (SELECT 1 FROM allowance_provider_attempt p WHERE p.request_id::text=t.id)
                 AND NOT EXISTS (SELECT 1 FROM tokenusage u WHERE u.user_id=t.user_id AND u.request_id=t.request_id)) AS missing_cost
    FROM tasks t WHERE created_at >= :start AND created_at < :end
), task_totals AS (
    SELECT user_id, count(*) AS tasks, count(*) FILTER (WHERE status='complete') AS completed,
           count(*) FILTER (WHERE status='failed') AS failed,
           count(*) FILTER (WHERE status IN ('reserved','pending')) AS pending,
           count(*) FILTER (WHERE missing_cost) AS untracked_cost_tasks,
           sum(charged) AS charged_units, sum(luna_charged) AS luna_charged_units,
           max(created_at) AS last_active
    FROM selected_tasks GROUP BY user_id
), cost_rows AS (
    SELECT r.user_id, p.model, 'shared' AS source,
           CASE WHEN p.model LIKE 'gpt-image%' THEN 'image' ELSE 'text' END AS feature,
           p.supplier_units::numeric AS units, p.supplier_units IS NULL AS unknown,
           CASE WHEN p.supplier_units IS NULL THEN p.budget ELSE 0 END AS exposure_units,
           r.status='failed' AS failed, p.created_at
    FROM allowance_provider_attempt p JOIN allowance_request r ON r.id=p.request_id
    WHERE p.created_at >= :start AND p.created_at < :end
    UNION ALL
    SELECT t.user_id, t.model_name, 'legacy',
           CASE WHEN t.images_generated>0 THEN 'image' ELSE 'text' END,
           CASE WHEN t.currency='USD' THEN ceil(t.total_cost*1000000) ELSE NULL END,
           t.currency<>'USD' OR (t.total_cost=0 AND
               t.input_tokens+t.output_tokens+t.web_search_calls+t.images_generated>0),
           0, (EXISTS (SELECT 1 FROM request_ledger l WHERE l.user_id=t.user_id AND l.request_id=t.request_id
                     AND l.state::text IN ('failed','refunded')) OR t.status IN ('error','cancelled')), t.created_at
    FROM tokenusage t WHERE t.created_at >= :start AND t.created_at < :end
      AND NOT EXISTS (
          SELECT 1 FROM allowance_request r JOIN allowance_provider_attempt p ON p.request_id=r.id
          WHERE r.user_id=t.user_id AND ((r.request_id=t.request_id AND p.model=t.model_name) OR p.provider_id=t.request_id)
      )
), cost_totals AS (
    SELECT user_id, coalesce(sum(units),0) AS supplier_units,
           coalesce(sum(units) FILTER (WHERE failed),0) AS failed_supplier_units,
           count(*) AS provider_attempts, count(*) FILTER (WHERE unknown) AS unknown_cost_rows
    FROM cost_rows GROUP BY user_id
), unresolved AS (
    SELECT r.user_id, sum(p.budget) AS unresolved_exposure_units, count(*) AS unresolved_attempts
    FROM allowance_provider_attempt p JOIN allowance_request r ON r.id=p.request_id
    WHERE p.supplier_units IS NULL GROUP BY r.user_id
), current_account AS (
    SELECT DISTINCT ON (user_id) * FROM allowance_account
    WHERE period_start<=:now AND period_end>:now AND (scope=:scope OR plan='starter')
    ORDER BY user_id, (plan<>'starter') DESC, period_start DESC, id
), current_tier AS (
    SELECT DISTINCT ON (s.user_id) s.user_id, t.name AS tier_name, t.is_public, t.price_cents,
           s.expires_at, s.auto_renew_enabled, s.last_renewal_failure_reason
    FROM user_subscription s JOIN subscription_tier t ON t.id=s.tier_id
    WHERE s.status::text='active' AND s.started_at<=:now AND
          (s.expires_at IS NULL OR s.expires_at>:now OR s.renewal_grace_until>:now) AND t.is_active
    ORDER BY s.user_id, (t.price_cents>0) DESC, t.price_cents DESC, t.index DESC, s.started_at DESC
), payment_totals AS (
    SELECT user_id, count(*) FILTER (WHERE tbank_status='CONFIRMED') AS purchases
    FROM payment WHERE created_at>=:start AND created_at<:end GROUP BY user_id
), paid_users AS (
    SELECT DISTINCT user_id FROM payment WHERE tbank_status='CONFIRMED'
), user_rows AS (
    SELECT u.id, CASE WHEN u.deleted_at IS NULL THEN u.telegram_username ELSE NULL END AS username,
           CASE WHEN u.deleted_at IS NOT NULL THEN 'Deleted account'
                ELSE coalesce(nullif(concat_ws(' ',u.telegram_first_name,u.telegram_last_name),''),u.telegram_username,u.id::text) END AS name,
           u.deleted_at IS NOT NULL AS deleted,
           coalesce(t.tasks,0) AS tasks, coalesce(t.completed,0) AS completed,
           coalesce(t.failed,0) AS failed, coalesce(t.pending,0) AS pending,
           coalesce(t.untracked_cost_tasks,0) AS untracked_cost_tasks,
           coalesce(t.charged_units,0) AS charged_units, coalesce(t.luna_charged_units,0) AS luna_charged_units,
           t.last_active, coalesce(c.supplier_units,0) AS supplier_units,
           coalesce(c.failed_supplier_units,0) AS failed_supplier_units,
           coalesce(c.provider_attempts,0) AS provider_attempts, coalesce(c.unknown_cost_rows,0) AS unknown_cost_rows,
           coalesce(x.unresolved_exposure_units,0) AS unresolved_exposure_units,
           coalesce(x.unresolved_attempts,0) AS unresolved_attempts,
           a.plan AS allowance_plan, a.granted, a.spent, a.reserved,
           a.granted-a.spent-a.reserved AS remaining_units,
           a.luna_granted-a.luna_spent-a.luna_reserved AS luna_remaining_units,
           a.period_end AS allowance_ends_at, a.trial_started_at,
           a.scope AS allowance_scope, ct.tier_name, ct.expires_at AS subscription_ends_at,
           ct.auto_renew_enabled, ct.last_renewal_failure_reason,
           coalesce(pt.purchases,0) AS purchases, pu.user_id IS NOT NULL AS paying,
           EXISTS (SELECT 1 FROM user_subscription ps JOIN subscription_tier ptier ON ptier.id=ps.tier_id
                   WHERE ps.user_id=u.id AND ps.status::text='active' AND ps.started_at<=:now
                     AND (ps.expires_at IS NULL OR ps.expires_at>:now OR ps.renewal_grace_until>:now)
                     AND ptier.is_active AND NOT ptier.is_public AND ptier.price_cents=0) AS private,
           u.id::text=ANY(CAST(:test_ids AS text[])) AS test
    FROM app_user u LEFT JOIN task_totals t ON t.user_id=u.id
    LEFT JOIN cost_totals c ON c.user_id=u.id LEFT JOIN unresolved x ON x.user_id=u.id
    LEFT JOIN current_account a ON a.user_id=u.id LEFT JOIN current_tier ct ON ct.user_id=u.id
    LEFT JOIN payment_totals pt ON pt.user_id=u.id LEFT JOIN paid_users pu ON pu.user_id=u.id
    WHERE NOT (u.id::text=ANY(CAST(:excluded AS text[])))
)
"""


def utc(value):
    return (
        value.replace(tzinfo=UTC).isoformat() if isinstance(value, datetime) else value
    )


async def rows(session, sql, params=None):
    result = await session.execute(text(sql), params or {})
    return [{k: utc(v) for k, v in row.items()} for row in result.mappings()]


async def metadata(session, window=None):
    state = (
        await rows(
            session,
            "SELECT transaction_timestamp() AT TIME ZONE 'UTC' AS as_of, pg_is_in_recovery() AS replica, pg_last_xact_replay_timestamp() AT TIME ZONE 'UTC' AS replica_replay_at",
        )
    )[0]
    return dict(
        **state,
        environment=settings.DEPLOYMENT_CHANNEL,
        accounting_scope=accounting_scope(),
        database_scope="shared",
        start=utc(window.start) if window else None,
        end=utc(window.end) if window else None,
        include_test=window.include_test if window else None,
        configured_test_users=len(settings.ADMIN_DASHBOARD_TEST_USER_IDS),
    )


async def overview(session, window):
    params = window.params()
    totals = (
        await rows(
            session,
            CTES
            + """
        SELECT count(*) AS users, count(*) FILTER (WHERE tasks>0) AS active_users,
        coalesce(sum(tasks),0) AS tasks, coalesce(sum(completed),0) AS completed,
        coalesce(sum(failed),0) AS failed, coalesce(sum(pending),0) AS pending,
        coalesce(sum(supplier_units),0) AS supplier_units,
        coalesce(sum(failed_supplier_units),0) AS failed_supplier_units,
        coalesce(sum(charged_units),0) AS charged_units,
        coalesce(sum(luna_charged_units),0) AS luna_charged_units,
        coalesce(sum(unknown_cost_rows),0) AS unknown_cost_rows,
        coalesce(sum(untracked_cost_tasks),0) AS untracked_cost_tasks,
        coalesce(sum(unresolved_exposure_units),0) AS unresolved_exposure_units,
        count(*) FILTER (WHERE remaining_units IS NOT NULL AND granted>0 AND remaining_units<=granted/10) AS low_allowance_users,
        count(*) FILTER (WHERE last_renewal_failure_reason IS NOT NULL) AS renewal_failure_users
        FROM user_rows
    """,
            params,
        )
    )[0]
    breakdown = await rows(
        session,
        CTES
        + """
        SELECT c.model,c.feature,c.source,count(*) AS attempts,coalesce(sum(c.units),0) AS supplier_units,
        coalesce(sum(c.units) FILTER (WHERE c.failed),0) AS failed_supplier_units,
        count(*) FILTER (WHERE c.unknown) AS unknown_cost_rows
        FROM cost_rows c JOIN user_rows u ON u.id=c.user_id GROUP BY c.model,c.feature,c.source
        ORDER BY supplier_units DESC,c.model
    """,
        params,
    )
    payments = await payment_summary(session, window)
    month = params["now"].replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Reuse the exact internal guard computation, including older unresolved work;
    # never call admission/cleanup, and never filter test users out of real guards.
    from app.services.allowance_tasks import exposure

    committed, loss = await exposure(session)
    budget = max(0, settings.SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS)
    recovery_budget = budget * settings.SHARED_ALLOWANCE_RECOVERY_BUDGET_PERCENT // 100
    return dict(
        meta=await metadata(session, window),
        totals=totals,
        breakdown=breakdown,
        payments=payments,
        spending_guard=dict(
            period_start=utc(month),
            budget_units=budget,
            committed_units=committed,
            remaining_units=max(0, budget - committed),
            loss_committed_units=loss,
            loss_budget_units=recovery_budget,
            loss_remaining_units=max(0, recovery_budget - loss),
            includes_test=True,
            coverage="shared allowance provider attempts",
        ),
        coverage=dict(
            provider_bill_reconciled=False,
            legacy_costs="Stored historical USD estimates; zero-priced/non-USD rows are marked incomplete",
            tasks="Logical generation/image/transcription operations; HTTP calls are not tasks",
            payment_period="Purchases created in the selected period, shown at their current recorded status",
            unknown_exposure="All unresolved shared provider attempts, including older periods",
            balances="Recorded current allowance; absent rows are not initialized, not zero grants",
        ),
    )


async def users(
    session,
    window,
    *,
    search="",
    cohort="all",
    sort="cost",
    offset=0,
    limit=25,
    user_id=None,
):
    filters = dict(
        all="TRUE",
        paying="paying",
        private="private",
        trial="allowance_plan='starter'",
        test="test",
    )
    orders = dict(
        cost="supplier_units DESC",
        tasks="tasks DESC",
        charged="charged_units DESC",
        purchases="purchases DESC",
        remaining="remaining_units ASC NULLS LAST",
        last_active="last_active DESC NULLS LAST",
    )
    params = window.params() | dict(
        search=search, offset=offset, limit=limit, user_id=user_id
    )
    # Literal search: SQL wildcard characters are not special user input.
    where = (
        filters[cohort]
        + " AND (:search='' OR position(lower(:search) in lower(concat_ws(' ',name,username,id::text)))>0)"
    )
    if user_id is not None:
        where += " AND id=:user_id"
    total = (
        await rows(
            session, CTES + "SELECT count(*) AS n FROM user_rows WHERE " + where, params
        )
    )[0]["n"]
    items = await rows(
        session,
        CTES
        + "SELECT * FROM user_rows WHERE "
        + where
        + " ORDER BY "
        + orders[sort]
        + ",id LIMIT :limit OFFSET :offset",
        params,
    )
    return dict(
        meta=await metadata(session, window),
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


async def user_detail(session, window, user_id):
    # Selecting a known test user is explicit; exclusion still applies to reports.
    detail_window = Window(window.start, window.end, True)
    result = await users(session, detail_window, user_id=user_id)
    if not result["items"]:
        raise HTTPException(404, detail="admin_user_not_found")
    user = await session.get(AppUser, user_id)
    result["user"] = result.pop("items")[0]
    result.pop("total")
    result["text_usage"] = (
        await user_usage_helpers.get_text_usage(session, user)
    ).model_dump()
    result["image_usage"] = (
        await user_usage_helpers.get_image_usage(session, user)
    ).model_dump()
    result["image_energy"] = (
        await user_usage_helpers.get_image_energy_usage(session, user)
    ).model_dump()
    now = datetime.now(UTC).replace(tzinfo=None)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (
        month.replace(year=month.year + 1, month=1)
        if month.month == 12
        else month.replace(month=month.month + 1)
    )
    transcription = await rows(
        session,
        """
        SELECT t.monthly_transcription_minutes AS cap_minutes,
               coalesce((SELECT sum(l.cost) FROM request_ledger l WHERE l.user_id=s.user_id
                   AND l.tier_id=t.id AND l.feature='transcription' AND l.state::text IN ('reserved','consumed')
                   AND l.created_at>=:month),0) AS used_minutes
        FROM user_subscription s JOIN subscription_tier t ON t.id=s.tier_id
        WHERE s.user_id=:user_id AND s.status::text='active' AND
              (s.expires_at IS NULL OR s.expires_at>:now OR s.renewal_grace_until>:now)
              AND t.monthly_transcription_minutes>0
        ORDER BY t.monthly_transcription_minutes DESC,t.price_cents DESC,s.started_at DESC LIMIT 1
    """,
        dict(user_id=user_id, now=now, month=month),
    )
    tr = transcription[0] if transcription else dict(cap_minutes=0, used_minutes=0)
    result["transcription"] = dict(
        **tr,
        enabled=settings.VOICE_TRANSCRIPTION_ENABLED,
        remaining_minutes=max(0, tr["cap_minutes"] - tr["used_minutes"]),
        resets_at=utc(next_month),
    )
    params = detail_window.params() | dict(user_id=user_id)
    result["breakdown"] = await rows(
        session,
        CTES
        + """
        SELECT model,feature,source,count(*) AS attempts,coalesce(sum(units),0) AS supplier_units,
               count(*) FILTER (WHERE unknown) AS unknown_cost_rows
        FROM cost_rows WHERE user_id=:user_id GROUP BY model,feature,source ORDER BY supplier_units DESC
    """,
        params,
    )
    result["subscriptions"] = await rows(
        session,
        """
        SELECT t.name,s.status,s.started_at,s.expires_at,s.auto_renew_enabled,
               s.renewal_grace_until,s.last_renewal_failure_reason,t.is_public,t.price_cents
        FROM user_subscription s JOIN subscription_tier t ON t.id=s.tier_id
        WHERE s.user_id=:user_id ORDER BY s.started_at DESC LIMIT 100
    """,
        dict(user_id=user_id),
    )
    result["purchases"] = await purchases(
        session, detail_window, user_id=user_id, limit=10
    )
    return result


async def tasks(session, window, user_id, offset, limit):
    if await session.get(AppUser, user_id) is None:
        raise HTTPException(404, detail="admin_user_not_found")
    params = window.params() | dict(user_id=user_id, offset=offset, limit=limit)
    items = await rows(
        session,
        CTES
        + """
        SELECT t.*, (SELECT count(*) FROM allowance_provider_attempt p WHERE p.request_id::text=t.id) AS attempts,
        (SELECT sum(supplier_units) FROM allowance_provider_attempt p WHERE p.request_id::text=t.id) AS supplier_units
        FROM selected_tasks t WHERE t.user_id=:user_id ORDER BY t.created_at DESC,t.id LIMIT :limit OFFSET :offset
    """,
        params,
    )
    total = (
        await rows(
            session,
            CTES + "SELECT count(*) AS n FROM selected_tasks WHERE user_id=:user_id",
            params,
        )
    )[0]["n"]
    return dict(
        meta=await metadata(session, window),
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


async def attempts(session, task_id):
    # Deliberately omit usage_details, execution_plan, messages and provider payloads.
    items = await rows(
        session,
        """
        SELECT id,step_key,model,status,budget,supplier_units,customer_units,included,recovery,
               input_tokens,cached_tokens,cache_write_tokens,output_tokens,reasoning_tokens,
               search_calls,file_calls,created_at,completed_at
        FROM allowance_provider_attempt WHERE request_id=:task_id ORDER BY created_at,id LIMIT 100
    """,
        dict(task_id=task_id),
    )
    found = await rows(
        session,
        "SELECT id FROM allowance_request WHERE id=:task_id",
        dict(task_id=task_id),
    )
    if not found:
        raise HTTPException(404, detail="admin_task_not_found")
    return dict(meta=await metadata(session), items=items)


async def payment_summary(session, window):
    return await rows(
        session,
        """
        SELECT currency,count(*) FILTER (WHERE tbank_status='CONFIRMED') AS confirmed,
               coalesce(sum(amount) FILTER (WHERE tbank_status='CONFIRMED'),0) AS confirmed_amount_minor,
               count(*) FILTER (WHERE tbank_status='REFUNDED') AS refunded,
               coalesce(sum(amount) FILTER (WHERE tbank_status='REFUNDED'),0) AS refunded_amount_minor,
               count(*) FILTER (WHERE tbank_status IN ('NEW','FORM_SHOWED','AUTHORIZING','AUTHORIZED')) AS pending
        FROM payment WHERE created_at>=:start AND created_at<:end
          AND NOT (user_id::text=ANY(CAST(:excluded AS text[]))) GROUP BY currency ORDER BY currency
    """,
        window.params(),
    )


async def purchases(session, window, *, user_id=None, status="", offset=0, limit=25):
    params = window.params() | dict(
        user_id=user_id, status=status, offset=offset, limit=limit
    )
    where = "p.created_at>=:start AND p.created_at<:end AND NOT (p.user_id::text=ANY(CAST(:excluded AS text[]))) AND (:status='' OR p.tbank_status=:status)"
    if user_id is not None:
        where += " AND p.user_id=:user_id"
    total = (
        await rows(
            session, "SELECT count(*) AS n FROM payment p WHERE " + where, params
        )
    )[0]["n"]
    items = await rows(
        session,
        """
        SELECT p.id,p.user_id,p.tier_name,p.product_type,p.amount AS amount_minor,p.currency,
               p.tbank_status AS status,p.flow_kind,p.created_at,p.updated_at,p.renewal_failure_reason,
               CASE WHEN u.deleted_at IS NOT NULL THEN 'Deleted account'
                    ELSE coalesce(u.telegram_username,u.telegram_first_name,p.user_id::text) END AS user_name,
               CASE WHEN p.product_type::text='usage_pack' THEN EXISTS (
                    SELECT 1 FROM user_usage_pack up WHERE up.payment_id=p.id AND up.status::text='active'
                      AND (up.expires_at IS NULL OR up.expires_at>:now))
                    ELSE EXISTS (SELECT 1 FROM user_subscription s JOIN subscription_tier t ON t.id=s.tier_id
                         WHERE s.user_id=p.user_id AND t.name=p.tier_name AND s.status::text='active'
                         AND s.started_at<=:now AND (s.expires_at IS NULL OR s.expires_at>:now OR s.renewal_grace_until>:now))
               END AS current_entitlement_active
        FROM payment p LEFT JOIN app_user u ON u.id=p.user_id WHERE
    """
        + where
        + " ORDER BY p.created_at DESC,p.id LIMIT :limit OFFSET :offset",
        params,
    )
    return dict(
        meta=await metadata(session, window),
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


async def configuration(session):
    # Explicit whitelist. Never serialize Settings, connection URLs or auth data.
    keys = (
        "SHARED_ALLOWANCE_ENABLED",
        "SHARED_ALLOWANCE_TRIAL_ENABLED",
        "SHARED_ALLOWANCE_GENERATION_V2_ENABLED",
        "ANTHROPIC_ENABLED",
        "VOICE_TRANSCRIPTION_ENABLED",
        "BETA_ALLOW_PAYMENTS",
        "WEB_AUTH_ENABLED",
        "TELEGRAM_OIDC_ENABLED",
        "GOOGLE_DOCUMENTS_ENABLED",
        "DOCUMENT_PROVIDER_FALLBACK_ENABLED",
        "DOCUMENT_DUAL_INDEX_ENABLED",
        "OPENAI_CHAINING_ENABLED",
        "SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS",
        "SHARED_ALLOWANCE_RECOVERY_BUDGET_PERCENT",
        "SHARED_ALLOWANCE_TASK_MAX_UNITS",
        "SHARED_ALLOWANCE_TASK_GRACE_UNITS",
        "SHARED_ALLOWANCE_USER_LOSS_PERCENT",
        "SHARED_ALLOWANCE_USER_LOSS_MIN_UNITS",
        "SHARED_ALLOWANCE_USER_LOSS_MAX_UNITS",
        "SHARED_ALLOWANCE_HISTORY_TOKENS",
        "SHARED_ALLOWANCE_LOOP_CONTEXT_TOKENS",
        "SHARED_ALLOWANCE_REQUEST_SECONDS",
        "SHARED_ALLOWANCE_TOOL_EXECUTIONS",
        "SHARED_ALLOWANCE_TOOL_TURNS",
        "SHARED_ALLOWANCE_TOOL_PARALLELISM",
        "SHARED_ALLOWANCE_RESEARCH_SECONDS",
        "VOICE_TRANSCRIPTION_MAX_DURATION_SECONDS",
        "VOICE_TRANSCRIPTION_UPLOAD_MAX_DURATION_SECONDS",
    )
    tiers = (
        await session.exec(select(SubscriptionTier).order_by(SubscriptionTier.index))
    ).all()
    packs = (await session.exec(select(UsagePack).order_by(UsagePack.index))).all()
    pricing = (
        await session.exec(
            select(AiModelPricing).order_by(
                AiModelPricing.provider, AiModelPricing.model_name
            )
        )
    ).all()
    models = (
        await session.exec(
            select(TextModelCatalog).order_by(TextModelCatalog.model_name)
        )
    ).all()
    image_pricing = (await session.exec(select(ImageQualityPricing))).all()
    return dict(
        meta=await metadata(session),
        version=APP_VERSION,
        flags=[
            dict(
                key=k,
                value=getattr(settings, k),
                source="environment/default",
                change_requires="deployment",
            )
            for k in keys
        ],
        effective_generation_limits=limits(type("Account", (), dict(granted=0))()),
        plans=[
            dict(
                plan=k,
                **v,
                shared_grant_units=grant_units(
                    k,
                    version=GRANT_VERSION_V2
                    if settings.SHARED_ALLOWANCE_GENERATION_V2_ENABLED
                    else RATE_VERSION,
                ),
            )
            for k, v in PLANS.items()
        ],
        customer_rates=[asdict(p) for p in MODELS.values()],
        tiers=[
            dict(
                id=t.id,
                name=t.name,
                price_minor=t.price_cents,
                currency="RUB",
                active=t.is_active,
                public=t.is_public,
                recurring=t.is_recurring,
                transcription_minutes=t.monthly_transcription_minutes,
                daily_image_energy=t.daily_image_energy,
                storage_bytes=t.max_storage_bytes,
                retention_hours=t.doc_retention_hours,
            )
            for t in tiers
        ],
        packs=[
            dict(
                id=p.id,
                name=p.name,
                price_minor=p.price_cents,
                currency="RUB",
                active=p.is_active,
                public=p.is_public,
            )
            for p in packs
        ],
        provider_rates=[p.model_dump() for p in pricing],
        image_rates=[
            dict(
                model=p.image_model,
                quality=p.quality,
                credit_cost=p.credit_cost,
                active=p.is_active,
            )
            for p in image_pricing
        ],
        models=[
            dict(
                model=m.model_name,
                provider=m.provider,
                active=m.is_active,
                provider_enabled=m.provider != "anthropic"
                or settings.ANTHROPIC_ENABLED,
            )
            for m in models
        ],
    )
