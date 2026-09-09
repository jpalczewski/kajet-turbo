from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from kajet_turbo.db import Database
from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteLinkRepository,
    NoteRepository,
    NoteTagRepository,
)
from kajet_turbo.repositories.workspace_meta import WorkspaceMetaRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.indexing import Indexer, NoteIndexer
from kajet_turbo.services.notes import (
    NoteCreateService,
    NoteDeleteService,
    NoteEditService,
    NoteFolderService,
    NoteLinkService,
    NoteReadService,
    NoteSearchService,
    NoteTagService,
    NoteTemporalService,
    NoteVersionService,
)
from kajet_turbo.services.notes.persistence import NoteTeardown
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget
from kajet_turbo.services.workspaces import WorkspaceService
from tests.conftest import seed_user


@pytest.fixture
def _seed_default_owner(database: Database) -> None:
    """Seeds the default test owner ("u1") — several write paths here enqueue
    reindex_note/push_workspace/... jobs with a user_id FK to users.id. Opt in with
    ``pytestmark = pytest.mark.usefixtures("_seed_default_owner")`` rather than copying
    this fixture into a file (#178) — not ``autouse`` here, so files with no DB writes
    at all (test_staleness.py, test_notes_paths.py, ...) don't pay for a Database."""
    seed_user(database, "u1")


@dataclass(frozen=True)
class NoteWiring:
    """The concrete note write-service graph (#388: split into create/edit/delete), plus
    the shared repos/collaborators a test may need to address directly.

    A test drives writes through ``.create``/``.edit``/``.delete`` and asserts on, say,
    ``.link_service`` — the builder hands every one of these the same repo/collaborator
    instances, so patching ``wiring.crud_repo`` to count workspace snapshots still
    intercepts every one of the three write services alike."""

    create: NoteCreateService
    edit: NoteEditService
    delete: NoteDeleteService
    link_service: NoteLinkService
    tag_service: NoteTagService
    version_service: NoteVersionService
    crud_repo: NoteRepository
    tag_repo: NoteTagRepository
    chunk_repo: NoteChunkRepository
    link_repo: NoteLinkRepository
    share_link_repo: NoteShareLinkRepository
    teardown: NoteTeardown
    indexer: Indexer | None
    reconcile_repo: LinkReconcileRepository | None


def build_note_wiring(
    database: Database,
    indexer=None,
    link_validation_enabled=None,
    dangling_repo=None,
    chunk_repo: NoteChunkRepository | None = None,
    reconcile_repo: LinkReconcileRepository | None = None,
    jobs: JobRepository | None = None,
    link_service: NoteLinkService | None = None,
    share_link_repo: NoteShareLinkRepository | None = None,
) -> NoteWiring:
    """Construct the fully-wired note write services (create/edit/delete) from a Database
    for tests, plus the peer boundaries a test may need to address directly."""
    engine = database.engine
    crud_repo = NoteRepository(engine)
    link_repo = NoteLinkRepository(engine)
    tag_repo = NoteTagRepository(engine)
    if chunk_repo is None:
        chunk_repo = NoteChunkRepository(engine)
    if jobs is None:
        jobs = JobRepository(engine)
    if share_link_repo is None:
        share_link_repo = NoteShareLinkRepository(engine)

    tag_service = NoteTagService(crud_repo, tag_repo, indexer)
    if link_service is None:
        link_service = NoteLinkService(
            crud_repo, link_repo, tag_repo, dangling_repo, link_validation_enabled, jobs
        )
    version_service = NoteVersionService(crud_repo)
    teardown = NoteTeardown(
        tag_repo, chunk_repo, crud_repo, link_repo, link_service, share_link_repo
    )

    return NoteWiring(
        create=NoteCreateService(
            crud_repo, link_service, tag_service, indexer=indexer, reconcile_repo=reconcile_repo
        ),
        edit=NoteEditService(
            crud_repo,
            link_service,
            tag_service,
            version_service,
            indexer=indexer,
            reconcile_repo=reconcile_repo,
        ),
        delete=NoteDeleteService(
            crud_repo, tag_repo, link_service, teardown, reconcile_repo=reconcile_repo
        ),
        link_service=link_service,
        tag_service=tag_service,
        version_service=version_service,
        crud_repo=crud_repo,
        tag_repo=tag_repo,
        chunk_repo=chunk_repo,
        link_repo=link_repo,
        share_link_repo=share_link_repo,
        teardown=teardown,
        indexer=indexer,
        reconcile_repo=reconcile_repo,
    )


def build_note_search_service(
    database: Database,
    query_resolver=None,
    build_embedder=None,
    query_cache=None,
    chunk_repo: NoteChunkRepository | None = None,
    async_build_embedder=None,
) -> NoteSearchService:
    """Construct a NoteSearchService reading the same Database as build_note_wiring.

    Fresh repo instances on the same engine — stateless, so they see everything a
    NoteWiring built against the same Database has already written."""
    engine = database.engine
    crud_repo = NoteRepository(engine)
    tag_repo = NoteTagRepository(engine)
    if chunk_repo is None:
        chunk_repo = NoteChunkRepository(engine)
    return NoteSearchService(
        chunk_repo,
        query_resolver,
        build_embedder,
        query_cache,
        crud_repo,
        tag_repo,
        async_build_embedder=async_build_embedder,
    )


def build_note_read_service(database: Database, indexer=None) -> NoteReadService:
    """Construct a NoteReadService reading the same Database as build_note_wiring.

    NoteLinkService.for_workspace takes a fresh DB snapshot on every call (no
    per-instance caching), so a separately-constructed NoteLinkService here sees
    everything a NoteWiring built against the same Database has already written."""
    engine = database.engine
    crud_repo = NoteRepository(engine)
    tag_repo = NoteTagRepository(engine)
    link_service = NoteLinkService(
        crud_repo, NoteLinkRepository(engine), tag_repo, None, None, JobRepository(engine)
    )
    return NoteReadService(crud_repo, tag_repo, link_service, indexer=indexer)


def build_note_reconcile_service(
    database: Database,
    link_service: NoteLinkService | None = None,
    dangling_repo: DanglingLinkRepository | None = None,
    link_validation_enabled=None,
    jobs: JobRepository | None = None,
    chunk_repo: NoteChunkRepository | None = None,
    indexer=None,
    reconcile_repo: LinkReconcileRepository | None = None,
    share_link_repo: NoteShareLinkRepository | None = None,
):
    """Construct a NoteReconcileService from a Database for tests, without building the
    full note write graph — the point of #225's split."""
    from kajet_turbo.services.notes import NoteReconcileService

    engine = database.engine
    crud_repo = NoteRepository(engine)
    link_repo = NoteLinkRepository(engine)
    tag_repo = NoteTagRepository(engine)
    if chunk_repo is None:
        chunk_repo = NoteChunkRepository(engine)
    if jobs is None:
        jobs = JobRepository(engine)
    if link_service is None:
        link_service = NoteLinkService(
            crud_repo, link_repo, tag_repo, dangling_repo, link_validation_enabled, jobs
        )
    if share_link_repo is None:
        share_link_repo = NoteShareLinkRepository(engine)
    teardown = NoteTeardown(
        tag_repo, chunk_repo, crud_repo, link_repo, link_service, share_link_repo
    )
    return NoteReconcileService(
        crud_repo,
        tag_repo,
        link_service,
        teardown,
        indexer=indexer,
        reconcile_repo=reconcile_repo,
    )


def build_workspace_service(database: Database) -> WorkspaceService:
    """Construct a fully-wired WorkspaceService from a Database for tests."""
    engine = database.engine
    jobs = JobRepository(engine)
    reconcile_repo = LinkReconcileRepository(engine, jobs)
    return WorkspaceService(
        WorkspaceRepository(engine),
        NoteRepository(engine),
        WorkspaceMetaRepository(engine),
        build_note_reconcile_service(database, jobs=jobs),
        DanglingLinkRepository(engine),
        FolderMetaRepository(engine),
        WorkspaceRemoteRepository(engine),
        jobs,
        reconcile_repo=reconcile_repo,
    )


@pytest.fixture
def workspace(git_workspace_factory: Callable[[str], Path]) -> Path:
    return git_workspace_factory("workspace")


@pytest.fixture
def service(database: Database) -> NoteWiring:
    chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: None,  # FTS-only in tests (no network)
        jobs=JobRepository(database.engine),
    )
    return build_note_wiring(database, indexer=indexer)


@pytest.fixture
def reconcile_service(service: NoteWiring):
    """A NoteReconcileService sharing every repo/collaborator instance the `service`
    fixture already holds (see build_note_reconcile_service_from) — reconcile_paths/
    reindex on this fixture and save()/update() on `service` operate on the same DB
    state, same as production wiring."""
    from tests.services.helpers import build_note_reconcile_service_from

    return build_note_reconcile_service_from(service)


@pytest.fixture
def tag_service(service: NoteWiring) -> NoteTagService:
    """The concrete tag boundary paired with the note writers in service tests."""
    return service.tag_service


@pytest.fixture
def folder_service(service: NoteWiring) -> NoteFolderService:
    """A NoteFolderService sharing every repo/collaborator instance the `service` fixture
    already holds (see build_note_folder_service_from) — so a test that patches a method on
    `service.crud_repo` also intercepts the folder move made here."""
    from tests.services.helpers import build_note_folder_service_from

    return build_note_folder_service_from(service)


@pytest.fixture
def temporal_service(service: NoteWiring) -> NoteTemporalService:
    """Shares `service`'s NoteRepository instance, not a fresh one, so a test that
    patches a method on `service.crud_repo` also affects backfill calls made here."""
    return NoteTemporalService(service.crud_repo)


@pytest.fixture
def read_service(database: Database) -> NoteReadService:
    return build_note_read_service(database)


@pytest.fixture
def search_service(database: Database) -> NoteSearchService:
    return build_note_search_service(database)


@pytest.fixture
def link_service(database: Database) -> NoteLinkService:
    """Direct link-service boundary for tests that inspect the link graph."""
    engine = database.engine
    return NoteLinkService(
        NoteRepository(engine),
        NoteLinkRepository(engine),
        NoteTagRepository(engine),
        None,
        None,
        JobRepository(engine),
    )


def workspace_target(owner_id: str, name: str, path) -> WorkspaceTarget:
    """Build a WorkspaceTarget by hand for tests that call note write entry points
    directly, bypassing the real TargetResolver (already covered by test_targets.py)."""
    return WorkspaceTarget(owner_id=owner_id, name=name, path=Path(path))


def note_target(owner_id: str, name: str, path, note_id: str) -> NoteTarget:
    return NoteTarget(note_id=note_id, workspace=workspace_target(owner_id, name, path))
