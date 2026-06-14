from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Iterable, Sequence

from sqlalchemy import delete, func
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.database import engine
from app.db.models import (
    AppUser,
    Conversation,
    ConversationSearchChunk,
    ConversationSearchJob,
    ConversationSearchProjection,
    Message,
    MessageContent,
)

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 384
CHUNK_TARGET_TOKENS = 350
CHUNK_MIN_TOKENS = 300
CHUNK_MAX_TOKENS = 400
CHUNK_OVERLAP_TOKENS = 50
PROJECTION_RECENT_TOKENS = 700
MAX_JOB_ATTEMPTS = 3
RETRY_BASE_SECONDS = 30

JOB_REINDEX_MESSAGE = "reindex_message"
JOB_REFRESH_PROJECTION = "refresh_projection"
JOB_REINDEX_CONVERSATION = "reindex_conversation"

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")
DEFAULT_FASTEMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def estimate_tokens_from_text(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().split())


def _hash_text(value: str) -> str:
    return hashlib.sha256(_normalize_text(value).encode("utf-8")).hexdigest()


def _message_sort_key(message: Message) -> tuple[datetime, uuid.UUID]:
    return (message.created_at or datetime.min, message.id)


def _normalize_vector(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(item * item for item in values))
    if norm <= 0:
        return values
    return [item / norm for item in values]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


class ConversationSearchEmbedder:
    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class HashingConversationSearchEmbedder(ConversationSearchEmbedder):
    def _embed_prefixed(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for token in _TOKEN_RE.findall(text.lower()):
            if not token.strip():
                continue
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:2], "big") % EMBEDDING_DIMENSIONS
            sign = 1.0 if digest[2] % 2 == 0 else -1.0
            weight = 1.0 / max(1, len(token))
            vector[index] += sign * weight
        return _normalize_vector(vector)

    def embed_query(self, text: str) -> list[float]:
        return self._embed_prefixed(f"query: {text}")

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_prefixed(f"passage: {text}") for text in texts]


class FastEmbedConversationSearchEmbedder(ConversationSearchEmbedder):
    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model_name = os.getenv("CONVERSATION_SEARCH_MODEL", DEFAULT_FASTEMBED_MODEL)
        self._model = TextEmbedding(model_name=self._model_name)

    def embed_query(self, text: str) -> list[float]:
        return [float(item) for item in next(self._model.embed([self._prepare_text(text, is_query=True)]))]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        prefixed = [self._prepare_text(text, is_query=False) for text in texts]
        return [[float(item) for item in row] for row in self._model.embed(prefixed)]

    def _prepare_text(self, text: str, *, is_query: bool) -> str:
        if "e5" in self._model_name.lower():
            prefix = "query" if is_query else "passage"
            return f"{prefix}: {text}"
        return text


@lru_cache(maxsize=1)
def get_conversation_search_embedder() -> ConversationSearchEmbedder:
    try:
        return FastEmbedConversationSearchEmbedder()
    except Exception as exc:
        logger.warning("Falling back to hashing embedder for conversation search: %s", exc)
        return HashingConversationSearchEmbedder()


def build_search_chunks(text: str) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []

    if estimate_tokens_from_text(normalized) <= CHUNK_MAX_TOKENS:
        return [normalized]

    parts = [part.strip() for part in _SENTENCE_BOUNDARY_RE.split(normalized) if part.strip()]
    if not parts:
        parts = [normalized]

    chunks: list[str] = []
    current_parts: list[str] = []
    current_tokens = 0

    for part in parts:
        part_tokens = estimate_tokens_from_text(part)
        if part_tokens > CHUNK_MAX_TOKENS:
            chars_per_chunk = CHUNK_TARGET_TOKENS * 4
            overlap_chars = CHUNK_OVERLAP_TOKENS * 4
            start = 0
            while start < len(part):
                end = min(len(part), start + chars_per_chunk)
                chunk = part[start:end].strip()
                if chunk:
                    chunks.append(chunk)
                if end >= len(part):
                    break
                start = max(start + 1, end - overlap_chars)
            continue

        if current_parts and current_tokens + part_tokens > CHUNK_MAX_TOKENS:
            chunks.append(" ".join(current_parts).strip())
            overlap_parts: list[str] = []
            overlap_tokens = 0
            for existing in reversed(current_parts):
                existing_tokens = estimate_tokens_from_text(existing)
                overlap_parts.insert(0, existing)
                overlap_tokens += existing_tokens
                if overlap_tokens >= CHUNK_OVERLAP_TOKENS:
                    break
            current_parts = overlap_parts.copy()
            current_tokens = sum(estimate_tokens_from_text(item) for item in current_parts)

        current_parts.append(part)
        current_tokens += part_tokens

        if current_tokens >= CHUNK_TARGET_TOKENS and current_tokens >= CHUNK_MIN_TOKENS:
            chunks.append(" ".join(current_parts).strip())
            overlap_parts = []
            overlap_tokens = 0
            for existing in reversed(current_parts):
                existing_tokens = estimate_tokens_from_text(existing)
                overlap_parts.insert(0, existing)
                overlap_tokens += existing_tokens
                if overlap_tokens >= CHUNK_OVERLAP_TOKENS:
                    break
            current_parts = overlap_parts.copy()
            current_tokens = sum(estimate_tokens_from_text(item) for item in current_parts)

    if current_parts:
        chunks.append(" ".join(current_parts).strip())

    deduped: list[str] = []
    for chunk in chunks:
        if chunk and (not deduped or deduped[-1] != chunk):
            deduped.append(chunk)
    return deduped


def _build_job_dedupe_key(job_type: str, conversation_id: uuid.UUID, message_id: uuid.UUID | None = None) -> str:
    if message_id is None:
        return f"{job_type}:{conversation_id}"
    return f"{job_type}:{conversation_id}:{message_id}"


async def queue_search_job(
    session: AsyncSession,
    *,
    job_type: str,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID | None = None,
) -> None:
    dedupe_key = _build_job_dedupe_key(job_type, conversation_id, message_id)
    existing = (
        await session.exec(
            select(ConversationSearchJob).where(
                ConversationSearchJob.dedupe_key == dedupe_key,
                ConversationSearchJob.status.in_(("pending", "running")),
            )
        )
    ).first()
    if existing:
        return

    session.add(
        ConversationSearchJob(
            job_type=job_type,
            conversation_id=conversation_id,
            message_id=message_id,
            dedupe_key=dedupe_key,
        )
    )


async def queue_message_reindex(session: AsyncSession, *, conversation_id: uuid.UUID, message_id: uuid.UUID) -> None:
    await queue_search_job(
        session,
        job_type=JOB_REINDEX_MESSAGE,
        conversation_id=conversation_id,
        message_id=message_id,
    )


async def queue_projection_refresh(session: AsyncSession, *, conversation_id: uuid.UUID) -> None:
    await queue_search_job(
        session,
        job_type=JOB_REFRESH_PROJECTION,
        conversation_id=conversation_id,
    )


async def queue_conversation_reindex(session: AsyncSession, *, conversation_id: uuid.UUID) -> None:
    await queue_search_job(
        session,
        job_type=JOB_REINDEX_CONVERSATION,
        conversation_id=conversation_id,
    )


async def _load_message_with_content(session: AsyncSession, message_id: uuid.UUID) -> Message | None:
    return (
        await session.exec(
            select(Message)
            .where(Message.id == message_id)
            .options(selectinload(Message.content))
        )
    ).first()


async def _load_conversation_with_messages(session: AsyncSession, conversation_id: uuid.UUID) -> Conversation | None:
    return (
        await session.exec(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages).selectinload(Message.content))
        )
    ).first()


async def reindex_message(session: AsyncSession, *, message_id: uuid.UUID) -> None:
    message = await _load_message_with_content(session, message_id)
    if not message:
        return

    conversation = await session.get(Conversation, message.conversation_id)
    if not conversation:
        return

    await session.exec(delete(ConversationSearchChunk).where(ConversationSearchChunk.message_id == message_id))

    text_parts = [
        part for part in sorted(message.content, key=lambda item: (item.ordinal, item.id))
        if part.type == "text" and _normalize_text(part.value)
    ]
    rows_to_create: list[tuple[MessageContent, int, str]] = []
    passage_texts: list[str] = []
    for part in text_parts:
        for chunk_ordinal, chunk_text in enumerate(build_search_chunks(part.value)):
            rows_to_create.append((part, chunk_ordinal, chunk_text))
            passage_texts.append(chunk_text)

    embeddings = get_conversation_search_embedder().embed_passages(passage_texts) if passage_texts else []
    for (part, chunk_ordinal, chunk_text), embedding in zip(rows_to_create, embeddings):
        session.add(
            ConversationSearchChunk(
                user_id=conversation.user_id,
                conversation_id=conversation.id,
                message_id=message.id,
                message_content_id=part.id,
                message_role=message.role,
                chunk_ordinal=chunk_ordinal,
                chunk_text=chunk_text,
                text_hash=_hash_text(chunk_text),
                embedding=embedding,
            )
        )


def _build_projection_text(conversation: Conversation) -> tuple[str, str, uuid.UUID | None]:
    title = _normalize_text(conversation.title or "New Chat")
    if conversation.history_summary:
        summary = _normalize_text(conversation.history_summary)
        if title and summary:
            return f"{title}\n\n{summary}", "history_summary", conversation.history_summary_up_to_message_id
        return summary or title, "history_summary", conversation.history_summary_up_to_message_id

    selected_lines: list[str] = []
    selected_tokens = estimate_tokens_from_text(title) if title else 0
    last_message_id: uuid.UUID | None = None
    ordered_messages = sorted(conversation.messages, key=_message_sort_key)
    for message in reversed(ordered_messages):
        text_parts = [
            _normalize_text(part.value)
            for part in sorted(message.content, key=lambda item: (item.ordinal, item.id))
            if part.type == "text" and _normalize_text(part.value)
        ]
        if not text_parts:
            continue
        message_text = " ".join(text_parts).strip()
        label = "assistant" if message.role == "assistant" else "user"
        line = f"{label}: {message_text}"
        line_tokens = estimate_tokens_from_text(line)
        if selected_lines and selected_tokens + line_tokens > PROJECTION_RECENT_TOKENS:
            break
        selected_lines.insert(0, line)
        selected_tokens += line_tokens
        last_message_id = message.id

    projection_parts = [part for part in [title, "\n".join(selected_lines).strip()] if part]
    return "\n\n".join(projection_parts), "recent_visible_transcript", last_message_id


async def refresh_projection(session: AsyncSession, *, conversation_id: uuid.UUID) -> None:
    conversation = await _load_conversation_with_messages(session, conversation_id)
    if not conversation:
        return

    projection_text, summary_source, last_message_id = _build_projection_text(conversation)
    normalized_projection = _normalize_text(projection_text)
    existing = (
        await session.exec(
            select(ConversationSearchProjection).where(
                ConversationSearchProjection.conversation_id == conversation_id
            )
        )
    ).first()

    if not normalized_projection:
        if existing:
            await session.delete(existing)
        return

    embedding = get_conversation_search_embedder().embed_passages([normalized_projection])[0]
    if existing:
        existing.user_id = conversation.user_id
        existing.projection_text = normalized_projection
        existing.summary_source = summary_source
        existing.embedding = embedding
        existing.last_indexed_message_id = last_message_id
        existing.updated_at = utcnow_naive()
        session.add(existing)
        return

    session.add(
        ConversationSearchProjection(
            user_id=conversation.user_id,
            conversation_id=conversation.id,
            projection_text=normalized_projection,
            summary_source=summary_source,
            embedding=embedding,
            last_indexed_message_id=last_message_id,
        )
    )


async def reindex_conversation(session: AsyncSession, *, conversation_id: uuid.UUID) -> None:
    conversation = await _load_conversation_with_messages(session, conversation_id)
    if not conversation:
        return

    await session.exec(delete(ConversationSearchChunk).where(ConversationSearchChunk.conversation_id == conversation_id))
    for message in sorted(conversation.messages, key=_message_sort_key):
        if message.role not in {"user", "assistant"}:
            continue
        await reindex_message(session, message_id=message.id)
    await refresh_projection(session, conversation_id=conversation_id)


async def queue_assistant_index_refresh(
    *,
    conversation_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await queue_message_reindex(
            session,
            conversation_id=conversation_id,
            message_id=assistant_message_id,
        )
        await queue_projection_refresh(session, conversation_id=conversation_id)
        await session.commit()


@dataclass
class _ConversationScore:
    conversation: Conversation
    score: float


async def search_conversations(
    session: AsyncSession,
    *,
    current_user: AppUser,
    query: str,
) -> list[Conversation]:
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return []

    conversations = (
        await session.exec(
            select(Conversation)
            .where(Conversation.user_id == current_user.id)
        )
    ).all()
    if not conversations:
        return []

    conversation_by_id = {conversation.id: conversation for conversation in conversations}
    query_embedding = get_conversation_search_embedder().embed_query(normalized_query)

    projection_rows = (
        await session.exec(
            select(ConversationSearchProjection).where(
                ConversationSearchProjection.user_id == current_user.id
            )
        )
    ).all()
    chunk_rows = (
        await session.exec(
            select(ConversationSearchChunk).where(
                ConversationSearchChunk.user_id == current_user.id
            )
        )
    ).all()
    title_scores = dict(
        (
            await session.exec(
                select(Conversation.id, func.similarity(Conversation.title, normalized_query)).where(
                    Conversation.user_id == current_user.id
                )
            )
        ).all()
    )

    best_projection: dict[uuid.UUID, float] = {}
    for row in projection_rows:
        best_projection[row.conversation_id] = max(
            best_projection.get(row.conversation_id, 0.0),
            cosine_similarity(query_embedding, row.embedding),
        )

    best_chunk: dict[uuid.UUID, float] = {}
    for row in chunk_rows:
        best_chunk[row.conversation_id] = max(
            best_chunk.get(row.conversation_id, 0.0),
            cosine_similarity(query_embedding, row.embedding),
        )

    ranked: list[_ConversationScore] = []
    lowered_query = normalized_query.casefold()
    for conversation in conversations:
        title = (conversation.title or "").strip()
        title_lower = title.casefold()
        title_similarity = float(title_scores.get(conversation.id, 0.0) or 0.0)
        lexical_boost = title_similarity * 0.2
        exact_title_match = title_lower == lowered_query
        prefix_title_match = title_lower.startswith(lowered_query)
        contains_title_match = lowered_query in title_lower
        if exact_title_match:
            lexical_boost += 1.0
        elif prefix_title_match:
            lexical_boost += 0.45
        elif contains_title_match:
            lexical_boost += 0.2

        projection_score = best_projection.get(conversation.id, 0.0)
        chunk_score = best_chunk.get(conversation.id, 0.0)
        semantic_score = max(chunk_score, projection_score * 0.9)
        score = semantic_score + lexical_boost
        if (
            semantic_score >= 0.15
            or exact_title_match
            or prefix_title_match
            or contains_title_match
            or title_similarity >= 0.08
        ):
            ranked.append(_ConversationScore(conversation=conversation, score=score))

    ranked.sort(
        key=lambda item: (
            item.score,
            item.conversation.updated_at or datetime.min,
        ),
        reverse=True,
    )
    return [conversation_by_id[item.conversation.id] for item in ranked]


async def claim_next_search_job(session: AsyncSession) -> ConversationSearchJob | None:
    job = (
        await session.exec(
            select(ConversationSearchJob)
            .where(
                ConversationSearchJob.status == "pending",
                ConversationSearchJob.run_after <= utcnow_naive(),
            )
            .order_by(ConversationSearchJob.run_after.asc(), ConversationSearchJob.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).first()
    if not job:
        return None

    job.status = "running"
    job.locked_at = utcnow_naive()
    job.updated_at = utcnow_naive()
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def _mark_job_complete(session: AsyncSession, job: ConversationSearchJob) -> None:
    job.status = "completed"
    job.locked_at = None
    job.error_message = None
    job.updated_at = utcnow_naive()
    session.add(job)
    await session.commit()


async def _mark_job_failed(session: AsyncSession, job: ConversationSearchJob, exc: Exception) -> None:
    job.attempt_count += 1
    job.locked_at = None
    job.error_message = str(exc)
    job.updated_at = utcnow_naive()
    if job.attempt_count >= MAX_JOB_ATTEMPTS:
        job.status = "failed"
    else:
        job.status = "pending"
        delay_seconds = RETRY_BASE_SECONDS * (2 ** max(0, job.attempt_count - 1))
        job.run_after = utcnow_naive() + timedelta(seconds=delay_seconds)
    session.add(job)
    await session.commit()


async def process_search_job(session: AsyncSession, job: ConversationSearchJob) -> None:
    if job.job_type == JOB_REINDEX_MESSAGE and job.message_id:
        await reindex_message(session, message_id=job.message_id)
        await refresh_projection(session, conversation_id=job.conversation_id)
        await session.commit()
        return

    if job.job_type == JOB_REFRESH_PROJECTION:
        await refresh_projection(session, conversation_id=job.conversation_id)
        await session.commit()
        return

    if job.job_type == JOB_REINDEX_CONVERSATION:
        await reindex_conversation(session, conversation_id=job.conversation_id)
        await session.commit()
        return

    logger.warning("Skipping unknown conversation search job type: %s", job.job_type)


async def run_search_job_once() -> bool:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        job = await claim_next_search_job(session)
        if not job:
            return False

        try:
            await process_search_job(session, job)
        except Exception as exc:
            logger.exception("Conversation search job failed: %s", job.id)
            await _mark_job_failed(session, job, exc)
        else:
            await _mark_job_complete(session, job)
        return True
