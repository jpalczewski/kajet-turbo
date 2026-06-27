from fastapi import APIRouter

from .folders import router as folders_router
from .notes import router as notes_router
from .reindex import router as reindex_router
from .tags import router as tags_router

router = APIRouter()
router.include_router(notes_router)
router.include_router(tags_router)
router.include_router(folders_router)
router.include_router(reindex_router)
