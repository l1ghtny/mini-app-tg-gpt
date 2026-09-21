"""Current-period activity browsing without copying deleted chat content into billing."""

from datetime import UTC
from sqlalchemy import BigInteger, Numeric, and_, case, cast, exists, func, or_
from sqlalchemy.orm import aliased
from sqlmodel import select

from app.db.allowance import AllowanceRequest, ProviderAttempt
from app.db.models import (
    ChatFolder,
    Conversation,
    Message,
    MessageContent,
    RequestLedger,
)
from app.services.allowance import account
from app.services.allowance_policy import FLARE, LUNA, MODELS


def provider_for(model):
    policy = MODELS.get(model)
    return policy.provider if policy else "openai" if model == FLARE else None


async def history(
    session,
    user_id,
    *,
    kind="all",
    cost_scope="all",
    provider=None,
    model=None,
    conversation_id=None,
    folder_id=None,
    offset=0,
    limit=20,
):
    a = await account(session, user_id)
    r, p = AllowanceRequest, ProviderAttempt
    owned_chat = and_(
        Conversation.id == r.conversation_id, Conversation.user_id == user_id
    )
    owned_folder = and_(
        ChatFolder.id == Conversation.folder_id, ChatFolder.user_id == user_id
    )
    # Attribute the actual customer charge, never supplier spend. If a request
    # hit its cap, share the discount proportionally; keep the rounding remainder
    # in chat so the two sections reconcile exactly in integer allowance units.
    image_call = p.model == FLARE
    completed_image = and_(image_call, p.status == "complete", r.status == "complete")
    image_edit = p.usage_details["image_action"].as_string() == "edit"
    breakdown = (
        select(
            p.request_id,
            func.sum(case((~p.included, p.customer_units), else_=0)).label("customer"),
            func.sum(
                case((and_(image_call, ~p.included), p.customer_units), else_=0)
            ).label("image_customer"),
            func.sum(case((completed_image, 1), else_=0)).label("images"),
            func.sum(case((and_(completed_image, image_edit), 1), else_=0)).label(
                "edits"
            ),
        )
        .join(r, r.id == p.request_id)
        .where(r.account_id == a.id, r.user_id == user_id)
        .group_by(p.request_id)
        .subquery()
    )
    image_units = cast(
        func.floor(
            cast(r.charged, Numeric)
            * func.coalesce(breakdown.c.image_customer, 0)
            / func.greatest(func.coalesce(breakdown.c.customer, 0), r.charged, 1)
        ),
        BigInteger,
    )
    chat_units = r.charged - image_units
    display_units = (
        image_units
        if cost_scope == "images"
        else chat_units
        if cost_scope == "chat"
        else r.charged
    )
    base = (
        select(
            r,
            Conversation.title,
            ChatFolder.name,
            ChatFolder.id,
            Conversation.id,
            image_units.label("image_units"),
            chat_units.label("chat_units"),
            display_units.label("display_units"),
            func.coalesce(breakdown.c.images, 0).label("images"),
            func.coalesce(breakdown.c.edits, 0).label("edits"),
        )
        .outerjoin(breakdown, breakdown.c.request_id == r.id)
        .outerjoin(Conversation, owned_chat)
        .outerjoin(ChatFolder, owned_folder)
        .where(r.account_id == a.id, r.user_id == user_id)
    )
    image = exists(select(p.id).where(p.request_id == r.id, p.model == FLARE))
    if cost_scope == "images":
        base = base.where(image)
    if kind == "images":
        base = base.where(image)
    elif kind == "text":
        base = base.where(~image)
    if model:
        base = base.where(image if model == FLARE else r.model == model)
    if provider:
        models = [name for name in [*MODELS, FLARE] if provider_for(name) == provider]
        base = base.where(
            or_(
                r.model.in_(models),
                exists(select(p.id).where(p.request_id == r.id, p.model.in_(models))),
            )
        )
    if conversation_id:
        base = base.where(Conversation.id == conversation_id)
    if folder_id:
        base = base.where(ChatFolder.id == folder_id)
    filtered = base.subquery()
    total, charged, total_chats = (
        await session.exec(
            select(
                func.count(),
                func.coalesce(func.sum(filtered.c.display_units), 0),
                func.count(func.distinct(filtered.c.conversation_id)),
            ).select_from(filtered)
        )
    ).one()
    model_rows = (
        await session.exec(
            select(
                filtered.c.model,
                func.count(func.distinct(filtered.c.conversation_id)),
                func.coalesce(func.sum(filtered.c.chat_units), 0),
            )
            .group_by(filtered.c.model)
            .order_by(func.sum(filtered.c.chat_units).desc(), filtered.c.model)
        )
    ).all()
    image_total, image_count, edit_count = (
        await session.exec(
            select(
                func.coalesce(func.sum(filtered.c.image_units), 0),
                func.coalesce(func.sum(filtered.c.images), 0),
                func.coalesce(func.sum(filtered.c.edits), 0),
            )
        )
    ).one()
    has_images = (
        await session.exec(
            select(func.count())
            .select_from(filtered)
            .where(
                exists(
                    select(p.id).where(p.request_id == filtered.c.id, p.model == FLARE)
                )
            )
        )
    ).one() > 0
    rows = (
        await session.exec(
            base.order_by(r.created_at.desc(), r.id.desc()).offset(offset).limit(limit)
        )
    ).all()
    attempts = (
        (
            await session.exec(
                select(p).where(p.request_id.in_([row[0].id for row in rows]))
            )
        ).all()
        if rows
        else []
    )
    by_request = {}
    for attempt in attempts:
        by_request.setdefault(attempt.request_id, []).append(attempt)

    # Only current owned content is read. Deleted chats/messages remain accounted
    # for, but their titles/snippets are not retained in the immutable ledger.
    assistant = aliased(Message)
    user_message = aliased(Message)
    latest_user = (
        select(user_message.id)
        .where(
            user_message.conversation_id == assistant.conversation_id,
            user_message.role == "user",
            user_message.created_at <= assistant.created_at,
        )
        .order_by(user_message.created_at.desc(), user_message.id.desc())
        .limit(1)
        .correlate(assistant)
        .scalar_subquery()
    )
    snippet = (
        select(func.substr(MessageContent.value, 1, 180))
        .where(
            MessageContent.message_id == latest_user,
            MessageContent.type == "text",
        )
        .order_by(MessageContent.ordinal)
        .limit(1)
        .correlate(assistant)
        .scalar_subquery()
    )
    descriptions = (
        dict(
            (
                await session.exec(
                    select(RequestLedger.request_id, snippet)
                    .join(
                        assistant,
                        and_(
                            assistant.id == RequestLedger.assistant_message_id,
                            assistant.conversation_id == RequestLedger.conversation_id,
                        ),
                    )
                    .join(
                        Conversation,
                        and_(
                            Conversation.id == assistant.conversation_id,
                            Conversation.user_id == user_id,
                        ),
                    )
                    .where(
                        RequestLedger.user_id == user_id,
                        RequestLedger.request_id.in_(
                            [row[0].request_id for row in rows]
                        ),
                        RequestLedger.feature == "text",
                    )
                )
            ).all()
        )
        if rows
        else {}
    )

    items = []
    for (
        request,
        title,
        folder,
        fid,
        cid,
        image_cost,
        chat_cost,
        cost,
        images,
        edits,
    ) in rows:
        children = by_request.get(request.id, [])
        activities = set()
        providers = {provider_for(request.model)}
        for child in children:
            providers.add(provider_for(child.model))
            if child.model == FLARE:
                activities.add(
                    "image_edit"
                    if (child.usage_details or {}).get("image_action") == "edit"
                    else "image_generation"
                )
            if child.search_calls:
                activities.add("web_search")
            if child.file_calls:
                activities.add("file_search")
        items.append(
            dict(
                request_id=request.request_id,
                model=request.model,
                status=request.status,
                activities=sorted(activities),
                providers=sorted(x for x in providers if x),
                percent=round(100 * cost / a.granted, 3) if a.granted else 0,
                charged_units=cost,
                image_count=images,
                image_edits=edits,
                included=cost_scope != "images"
                and request.model == LUNA
                and request.status == "complete"
                and cost == 0,
                created_at=request.created_at.replace(tzinfo=UTC).isoformat(),
                title=(descriptions.get(request.request_id) or "").strip() or None,
                conversation_id=str(cid) if cid else None,
                conversation_title=title,
                folder_id=str(fid) if fid else None,
                folder_name=folder,
            )
        )
    facets = (
        await session.exec(
            select(Conversation.id, Conversation.title, ChatFolder.id, ChatFolder.name)
            .join(
                r,
                and_(
                    r.conversation_id == Conversation.id,
                    r.account_id == a.id,
                    r.user_id == user_id,
                ),
            )
            .outerjoin(
                ChatFolder,
                and_(
                    ChatFolder.id == Conversation.folder_id,
                    ChatFolder.user_id == user_id,
                ),
            )
            .where(Conversation.user_id == user_id)
            .distinct()
            .order_by(Conversation.title, Conversation.id)
        )
    ).all()
    folders = {str(fid): name for _, _, fid, name in facets if fid}
    result = dict(
        items=items,
        total=total,
        total_chats=total_chats,
        models=[
            dict(
                model=name,
                chats=count,
                charged_units=int(cost),
                percent=round(100 * int(cost) / a.granted, 3) if a.granted else 0,
            )
            for name, count, cost in model_rows
        ],
        image_models=[
            dict(
                model=FLARE,
                images=int(image_count),
                edits=int(edit_count),
                charged_units=int(image_total),
                percent=round(100 * int(image_total) / a.granted, 3)
                if a.granted
                else 0,
            )
        ]
        if has_images
        else [],
        cost_scope=cost_scope,
        used_units=int(charged),
        used_percent=round(100 * int(charged) / a.granted, 3) if a.granted else 0,
        next_offset=offset + len(items) if offset + len(items) < total else None,
        period_start=a.period_start.replace(tzinfo=UTC).isoformat(),
        period_end=a.period_end.replace(tzinfo=UTC).isoformat(),
        chats=[
            dict(id=str(cid), title=title, folder_id=str(fid) if fid else None)
            for cid, title, fid, _ in facets
        ],
        folders=[
            dict(id=fid, name=name)
            for fid, name in sorted(folders.items(), key=lambda x: x[1])
        ],
    )
    await session.commit()
    return result
