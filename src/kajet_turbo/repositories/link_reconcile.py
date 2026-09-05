from sqlalchemy import Engine, and_, delete, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import col, select

from kajet_turbo.models import LinkReconcileDirty
from kajet_turbo.repositories import DbRepository
from kajet_turbo.repositories.jobs import JobRepository


class LinkReconcileRepository(DbRepository):
    """Durable, versioned dirty set plus its atomically-created workspace job."""

    repository_name = "link_reconcile"

    def __init__(self, engine: Engine, jobs: JobRepository):
        super().__init__(engine)
        self._jobs = jobs

    def mark_and_enqueue(
        self, owner_id: str, workspace: str, source_note_ids: set[str]
    ) -> str | None:
        source_note_ids = source_note_ids - {""}
        if not source_note_ids:
            return None
        timing = self.timed_session()
        with timing as session:
            for source_note_id in sorted(source_note_ids):
                stmt = (
                    sqlite_insert(LinkReconcileDirty)
                    .values(
                        owner_id=owner_id,
                        workspace=workspace,
                        source_note_id=source_note_id,
                        generation=1,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            LinkReconcileDirty.owner_id,
                            LinkReconcileDirty.workspace,
                            LinkReconcileDirty.source_note_id,
                        ],
                        set_={"generation": LinkReconcileDirty.generation + 1},
                    )
                )
                session.exec(stmt)
            job_id = self._jobs.enqueue_in_session(
                session,
                "reconcile_links",
                {"user_id": owner_id, "workspace": workspace, "mode": "targeted"},
                dedup_key=f"reconcile:{owner_id}:{workspace}",
                user_id=owner_id,
            )
            session.commit()
        self.log_operation(
            "mark_and_enqueue",
            timing.db_ms,
            owner_id=owner_id,
            workspace=workspace,
            sources=len(source_note_ids),
            job_id=job_id,
        )
        return job_id

    def list_dirty(self, owner_id: str, workspace: str) -> dict[str, int]:
        with self.timed_session() as session:
            rows = session.exec(
                select(
                    LinkReconcileDirty.source_note_id,
                    LinkReconcileDirty.generation,
                ).where(
                    LinkReconcileDirty.owner_id == owner_id,
                    LinkReconcileDirty.workspace == workspace,
                )
            ).all()
        return dict(rows)

    def acknowledge(self, owner_id: str, workspace: str, generations: dict[str, int]) -> None:
        """Delete only markers whose generation still matches the worker snapshot."""
        if not generations:
            return
        items = list(generations.items())
        with self.operation(
            "acknowledge", owner_id=owner_id, workspace=workspace, sources=len(generations)
        ) as operation:
            session = operation.session
            # Bound statement size for large folder moves (two bind params per marker).
            for start in range(0, len(items), 400):
                predicates = [
                    and_(
                        col(LinkReconcileDirty.source_note_id) == source_note_id,
                        col(LinkReconcileDirty.generation) == generation,
                    )
                    for source_note_id, generation in items[start : start + 400]
                ]
                session.exec(
                    delete(LinkReconcileDirty).where(
                        col(LinkReconcileDirty.owner_id) == owner_id,
                        col(LinkReconcileDirty.workspace) == workspace,
                        or_(*predicates),
                    )
                )
            session.commit()

    def delete_for_workspace(self, owner_id: str, workspace: str) -> None:
        timing = self.timed_session()
        with timing as session:
            result = session.exec(
                delete(LinkReconcileDirty).where(
                    col(LinkReconcileDirty.owner_id) == owner_id,
                    col(LinkReconcileDirty.workspace) == workspace,
                )
            )
            count = result.rowcount
            session.commit()
        self.log_operation(
            "delete_for_workspace",
            timing.db_ms,
            owner_id=owner_id,
            workspace=workspace,
            count=count,
        )
