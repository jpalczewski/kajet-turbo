from fastapi import APIRouter

from .notes import router as notes_router
from .workspace_meta import router as workspace_meta_router
from .workspace_settings import router as workspace_settings_router

router = APIRouter()
router.include_router(workspace_meta_router)
router.include_router(workspace_settings_router)
router.include_router(notes_router)
