import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from app.db.models import AppUser
from app.db.allowance import AllowanceRequest, ProviderAttempt
from app.services.allowance import account
from app.services.allowance_history import history


async def create_history_tables(engine):
    # Minimal history relations in this test's own schema; no public-schema fixture.
    async with engine.begin() as connection:
        schema = (
            await connection.execute(text("select current_schema()"))
        ).scalar_one()
        assert schema.startswith("allowance_test_")
        for ddl in [
            "CREATE TABLE chat_folder (id UUID PRIMARY KEY, user_id UUID, name TEXT)",
            "CREATE TABLE conversation (id UUID PRIMARY KEY, user_id UUID, folder_id UUID, title TEXT)",
            "CREATE TABLE message (id UUID PRIMARY KEY, conversation_id UUID, role TEXT, created_at TIMESTAMP)",
            "CREATE TABLE messagecontent (message_id UUID, type TEXT, ordinal INTEGER, value TEXT)",
            "CREATE TABLE request_ledger (request_id TEXT, user_id UUID, conversation_id UUID, assistant_message_id UUID, feature TEXT)",
        ]:
            await connection.execute(text(ddl))


@pytest.mark.asyncio
async def test_history_filters_page_totals_owned_context_and_deleted_chats(db):
    engine, session, user = db
    await create_history_tables(engine)
    a = await account(session, user.id)
    other = AppUser(default_prompt="Other user")
    session.add(other)
    await session.commit()
    folder, chat, foreign_chat = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await session.execute(
        text("INSERT INTO chat_folder VALUES (:id,:user,'Design')"),
        {"id": folder, "user": user.id},
    )
    await session.execute(
        text("INSERT INTO conversation VALUES (:id,:user,:folder,'Cafe poster')"),
        {"id": chat, "user": user.id, "folder": folder},
    )
    await session.execute(
        text(
            "INSERT INTO conversation VALUES (:id,:user,NULL,'Private foreign title')"
        ),
        {"id": foreign_chat, "user": other.id},
    )
    requests = [
        AllowanceRequest(
            account_id=a.id,
            user_id=user.id,
            scope="local",
            request_id=str(i),
            model="claude-sonnet-5",
            ceiling=10000,
            charged=1000 * (i + 1),
            status="complete",
            conversation_id=chat if i < 2 else foreign_chat,
            created_at=a.period_start + timedelta(minutes=i),
        )
        for i in range(3)
    ]
    session.add_all(requests)
    await session.flush()
    session.add(
        ProviderAttempt(
            request_id=requests[0].id,
            step_key="1",
            model="gpt-image-2.5-flare",
            budget=1000,
            usage_details={"image_action": "edit"},
        )
    )
    um, am = uuid.uuid4(), uuid.uuid4()
    for mid, role, minute in [(um, "user", 0), (am, "assistant", 1)]:
        await session.execute(
            text("INSERT INTO message VALUES (:id,:chat,:role,:created)"),
            {
                "id": mid,
                "chat": chat,
                "role": role,
                "created": a.period_start + timedelta(seconds=minute),
            },
        )
    await session.execute(
        text("INSERT INTO messagecontent VALUES (:id,'text',0,'Make the cup red')"),
        {"id": um},
    )
    await session.execute(
        text("INSERT INTO request_ledger VALUES ('0',:user,:chat,:assistant,'text')"),
        {"user": user.id, "chat": chat, "assistant": am},
    )
    await session.commit()

    images = await history(session, user.id, kind="images", provider="anthropic")
    assert images["total"] == 1
    assert images["items"][0]["title"] == "Make the cup red"
    assert images["items"][0]["providers"] == ["anthropic", "openai"]
    assert images["items"][0]["activities"] == ["image_edit"]
    assert images["items"][0]["folder_name"] == "Design"
    assert (await history(session, user.id, provider="openai"))["total"] == 1
    assert (await history(session, user.id, kind="text"))["total"] == 2
    scoped = await history(session, user.id, folder_id=folder, limit=1)
    assert scoped["total_chats"] == 1
    assert scoped["models"] == [
        {
            "model": "claude-sonnet-5",
            "chats": 1,
            "charged_units": 3000,
            "percent": round(3000 * 100 / a.granted, 3),
        }
    ]
    assert scoped["total"] == 2 and scoped["next_offset"] == 1
    assert scoped["used_percent"] == round(3000 * 100 / a.granted, 3)
    page = await history(session, user.id, folder_id=folder, offset=1, limit=1)
    assert page["items"][0]["request_id"] != scoped["items"][0]["request_id"]
    assert page["next_offset"] is None
    assert (await history(session, user.id, model="claude-sonnet-5"))["total"] == 3
    assert (await history(session, user.id, model="gpt-5.6-sol"))["models"] == []
    all_tasks = await history(session, user.id)
    assert all_tasks["total_chats"] == 2
    assert all_tasks["models"][0]["percent"] == all_tasks["used_percent"]
    assert all_tasks["items"][0]["conversation_id"] is None
    assert all_tasks["items"][0]["conversation_title"] is None
    assert [c["id"] for c in all_tasks["chats"]] == [str(chat)]
    assert (await history(session, other.id))["total"] == 0
    await session.execute(text("DELETE FROM conversation WHERE id=:id"), {"id": chat})
    await session.commit()
    deleted = await history(session, user.id, kind="images")
    assert deleted["total_chats"] == 1
    assert deleted["models"][0]["chats"] == 1
    assert deleted["total"] == 1 and deleted["items"][0]["percent"] > 0
    assert (
        deleted["items"][0]["title"] is None
        and deleted["items"][0]["conversation_id"] is None
    )


@pytest.mark.asyncio
async def test_image_split_reconciles_caps_included_failed_pending_and_multiple_outputs(
    db,
):
    engine, session, user = db
    await create_history_tables(engine)
    a = await account(session, user.id)
    flare = "gpt-image-2.5-flare"
    sonnet = "claude-sonnet-5"
    # charge, status, chat cost/included, image (cost/status/edit), expected image charge/count
    cases = [
        (1500, "complete", 900, False, [(600, "complete", False)], 600, 1),
        (1001, "complete", 900, False, [(600, "complete", True)], 400, 1),
        (600, "complete", 300, True, [(600, "complete", False)], 600, 1),
        (900, "complete", 900, False, [(0, "failed", False)], 0, 0),
        (0, "pending", 900, False, [(600, "complete", False)], 0, 0),
        (0, "failed", 900, False, [(600, "complete", True)], 0, 0),
        (77, "complete", 0, False, [], 0, 0),
        (
            1100,
            "complete",
            100,
            False,
            [(600, "complete", False), (400, "complete", True)],
            1000,
            2,
        ),
    ]
    expected_total = expected_images = 0
    for i, (
        charge,
        status,
        text_cost,
        included,
        images,
        image_cost,
        count,
    ) in enumerate(cases):
        chat = uuid.uuid4()
        await session.execute(
            text("INSERT INTO conversation VALUES (:id,:user,NULL,'Image test')"),
            {"id": chat, "user": user.id},
        )
        request = AllowanceRequest(
            account_id=a.id,
            user_id=user.id,
            scope="local",
            request_id=f"split-{i}",
            model="gpt-5.6-luna" if included else sonnet,
            ceiling=charge,
            charged=charge,
            status=status,
            conversation_id=chat,
        )
        session.add(request)
        await session.flush()
        session.add(
            ProviderAttempt(
                request_id=request.id,
                step_key="text",
                model=request.model,
                budget=text_cost,
                supplier_units=text_cost,
                customer_units=text_cost,
                included=included,
                status="complete",
            )
        )
        for n, (cost, child_status, edit) in enumerate(images):
            session.add(
                ProviderAttempt(
                    request_id=request.id,
                    step_key=f"image-{n}",
                    model=flare,
                    budget=600,
                    supplier_units=cost,
                    customer_units=cost,
                    status=child_status,
                    usage_details={"image_action": "edit" if edit else "generate"},
                )
            )
        await session.commit()
        overall = await history(session, user.id, conversation_id=chat)
        assert overall["used_units"] == charge
        assert overall["models"][0]["charged_units"] == charge - image_cost
        image_rows = overall["image_models"]
        assert sum(row["charged_units"] for row in image_rows) == image_cost
        assert sum(row["images"] for row in image_rows) == count
        assert sum(row["edits"] for row in image_rows) == (
            sum(edit and child_status == "complete" for _, child_status, edit in images)
            if status == "complete"
            else 0
        )
        chat_view = await history(
            session,
            user.id,
            conversation_id=chat,
            model=request.model,
            cost_scope="chat",
        )
        assert (
            chat_view["used_units"]
            == chat_view["items"][0]["charged_units"]
            == charge - image_cost
        )
        if images:
            image_view = await history(
                session, user.id, conversation_id=chat, model=flare, cost_scope="images"
            )
            assert image_view["total"] == 1
            assert (
                image_view["used_units"]
                == image_view["items"][0]["charged_units"]
                == image_cost
            )
            assert image_view["items"][0]["included"] is False
        expected_total += charge
        expected_images += image_cost
    overall = await history(session, user.id, limit=1)
    assert overall["used_units"] == expected_total
    assert overall["image_models"][0]["charged_units"] == expected_images
    assert (
        sum(row["charged_units"] for row in overall["models"]) + expected_images
        == expected_total
    )
    assert (await history(session, user.id, model=flare, cost_scope="images", limit=1))[
        "used_units"
    ] == expected_images
