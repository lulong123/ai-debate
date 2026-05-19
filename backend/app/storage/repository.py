import logging

from sqlalchemy import delete as sa_delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import DataPoolItem, DiscussionSession, Message, Position

logger = logging.getLogger(__name__)

MAX_POOL_DISPLAY = 20  # Max results to inject into agent prompts


class SessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_session(self, topic: str, max_rounds: int = 3) -> DiscussionSession:
        session = DiscussionSession(topic=topic, max_rounds=max_rounds)
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def get_session(self, session_id: str) -> DiscussionSession | None:
        result = await self.db.execute(
            select(DiscussionSession).where(DiscussionSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def update_session(self, session: DiscussionSession) -> DiscussionSession:
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def add_message(self, session_id: str, seq: int, role, content: str, **kwargs) -> Message:
        msg = Message(session_id=session_id, seq=seq, role=role, content=content, **kwargs)
        self.db.add(msg)
        await self.db.commit()
        return msg

    async def get_messages(self, session_id: str) -> list[Message]:
        result = await self.db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.seq)
        )
        return list(result.scalars().all())

    async def add_position(
        self, session_id: str, name: str, description: str,
        is_custom: bool = False, position_id: str | None = None,
    ) -> Position:
        # Delete any existing position with this ID (can happen when
        # LLM generates generic IDs like "excellent" across sessions).
        if position_id:
            existing = await self.db.get(Position, position_id)
            if existing:
                await self.db.delete(existing)
                await self.db.flush()
        position = Position(
            id=position_id, session_id=session_id,
            name=name, description=description, is_custom=is_custom,
        )
        self.db.add(position)
        await self.db.commit()
        return position

    async def get_active_positions(self, session_id: str) -> list[Position]:
        result = await self.db.execute(
            select(Position).where(Position.session_id == session_id)
        )
        return list(result.scalars().all())

    async def list_sessions(
        self,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        search: str | None = None,
    ) -> list[DiscussionSession]:
        stmt = select(DiscussionSession).order_by(DiscussionSession.created_at.desc())
        if status:
            stmt = stmt.where(DiscussionSession.status == status)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    DiscussionSession.topic.ilike(pattern),
                    DiscussionSession.refined_topic.ilike(pattern),
                )
            )
        stmt = stmt.offset(offset).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and all related data (messages, positions, pool items)."""
        session = await self.get_session(session_id)
        if not session:
            return False
        await self.db.execute(
            sa_delete(DataPoolItem).where(DataPoolItem.session_id == session_id)
        )
        await self.db.execute(
            sa_delete(Message).where(Message.session_id == session_id)
        )
        await self.db.execute(
            sa_delete(Position).where(Position.session_id == session_id)
        )
        await self.db.delete(session)
        await self.db.commit()
        return True

    async def update_session_topic(self, session_id: str, topic: str) -> DiscussionSession | None:
        session = await self.get_session(session_id)
        if not session:
            return None
        session.topic = topic
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def add_data_pool_item(
        self, session_id: str, source: str, title: str,
        snippet: str = "", url: str = "", round_number: int | None = None,
        key_facts: str | None = None, is_public: bool = False,
        publish_date: str = "",
    ) -> DataPoolItem:
        item = DataPoolItem(
            session_id=session_id, source=source, title=title,
            snippet=snippet, url=url, round_number=round_number,
            key_facts=key_facts, is_public=is_public,
            publish_date=publish_date,
        )
        self.db.add(item)
        await self.db.commit()
        return item

    async def get_data_pool(
        self, session_id: str, public_only: bool = False,
    ) -> list[DataPoolItem]:
        stmt = (
            select(DataPoolItem)
            .where(DataPoolItem.session_id == session_id)
            .order_by(DataPoolItem.created_at)
        )
        if public_only:
            stmt = stmt.where(DataPoolItem.is_public == True)  # noqa: E712
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def promote_pool_items(self, session_id: str, item_ids: list[str]) -> int:
        """Set is_public=True for specified pool items. Returns count promoted."""
        if not item_ids:
            return 0
        items = await self.get_data_pool(session_id)
        promoted = 0
        for item in items:
            if item.id in item_ids and not item.is_public:
                item.is_public = True
                promoted += 1
        if promoted:
            await self.db.commit()
        return promoted

    async def persist_research_results(
        self, session_id: str, results: list[dict], source: str = "data_clerk",
        round_number: int | None = None, public_urls: set[str] | None = None,
    ) -> list[DataPoolItem]:
        """Bulk-persist research results to data pool with public/private split.

        Deduplicates by URL — skips if a pool item with same URL already exists.
        public_urls: set of URLs that should be marked is_public=True.
                     If None, all items are public (backward compatible).
        Returns list of newly created DataPoolItem objects.
        """
        existing = await self.get_data_pool(session_id)
        existing_urls = {item.url for item in existing if item.url}

        new_items = []
        for r in results:
            url = r.get("url", "")
            if url and url in existing_urls:
                logger.info("Skipping duplicate pool item: %s", url[:80])
                continue

            # If public_urls not specified, mark all as public (backward compat)
            is_public = True if public_urls is None else (url in public_urls)
            item = await self.add_data_pool_item(
                session_id=session_id,
                source=source,
                title=r.get("title", ""),
                snippet=r.get("snippet", ""),
                url=url,
                round_number=round_number,
                key_facts=r.get("key_facts"),
                is_public=is_public,
                publish_date=r.get("publish_date", ""),
            )
            new_items.append(item)
            if url:
                existing_urls.add(url)

        return new_items

    async def get_pool_summary(self, session_id: str) -> str:
        """Get formatted public pool summary for agent context injection.

        Only includes public (validated) items. Citation [N] numbers
        map to the public pool order.
        """
        from app.agents.data_clerk import format_result_with_facts

        items = await self.get_data_pool(session_id, public_only=True)
        if not items:
            return ""
        display = items[-MAX_POOL_DISPLAY:]
        lines = []
        for i, item in enumerate(display, 1):
            d = item.to_dict()
            formatted = format_result_with_facts(d)
            if formatted.startswith("- "):
                formatted = f"[{i}] {formatted[2:]}"
            else:
                formatted = f"[{i}] {formatted}"
            lines.append(formatted)
        return "\n".join(lines)

    async def get_data_clerk_context(self, session_id: str) -> str:
        """Build full data clerk research context: public + private pool items.

        This is the data clerk's "research notebook" — it knows everything
        it has ever found, not just the validated public results.
        """
        from app.agents.data_clerk import format_result_with_facts

        all_items = await self.get_data_pool(session_id, public_only=False)
        if not all_items:
            return ""

        public_items = [it for it in all_items if it.is_public]
        private_items = [it for it in all_items if not it.is_public]

        lines: list[str] = []

        # Section 1: Public (validated) data
        if public_items:
            lines.append(f"【已公开数据（{len(public_items)}条，已验证）】")
            for i, item in enumerate(public_items, 1):
                d = item.to_dict()
                formatted = format_result_with_facts(d)
                if formatted.startswith("- "):
                    formatted = formatted[2:]
                round_tag = f"[R{item.round_number}]" if item.round_number else ""
                lines.append(f"  [{i}] {round_tag} {formatted}")

        # Section 2: Private (searched but not validated) data
        if private_items:
            lines.append(f"\n【已搜索但未验证数据（{len(private_items)}条，仅供参考）】")
            for item in private_items:
                title = item.title[:80] if item.title else "无标题"
                url = item.url[:60] if item.url else "无URL"
                round_tag = f"[R{item.round_number}]" if item.round_number else ""
                # Show extracted facts if any
                facts_note = ""
                if item.key_facts:
                    try:
                        import json
                        parsed = json.loads(item.key_facts)
                        if parsed.get("key_facts"):
                            facts_note = " → " + "; ".join(parsed["key_facts"][:2])
                    except (json.JSONDecodeError, AttributeError):
                        pass
                lines.append(f"  {round_tag} {title} | {url}{facts_note}")

        return "\n".join(lines)
