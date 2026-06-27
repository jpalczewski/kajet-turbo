from fastapi import APIRouter

from .content import router as content_router
from .crud import router as crud_router
from .history import router as history_router

router = APIRouter()
router.include_router(crud_router)
router.include_router(content_router)
router.include_router(history_router)
