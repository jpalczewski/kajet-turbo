from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import JobsResponse
from kajet_turbo.dependencies import get_job_service, get_required_user
from kajet_turbo.services.jobs import JobService

router = APIRouter()


@router.get("/api/me/jobs", response_model=JobsResponse)
def api_list_jobs(
    request: Request,
    user: dict = Depends(get_required_user),
    svc: JobService = Depends(get_job_service),
) -> JSONResponse:
    status = request.query_params.get("status") or None
    return JSONResponse({"jobs": svc.list(user["id"], status=status)})


@router.post("/api/me/jobs/{job_id}/retry")
def api_retry_job(
    job_id: str,
    user: dict = Depends(get_required_user),
    svc: JobService = Depends(get_job_service),
) -> JSONResponse:
    if not svc.retry(user["id"], job_id):
        return JSONResponse({"error": "Job not found or not retryable"}, status_code=404)
    return JSONResponse({"ok": True})


@router.delete("/api/me/jobs/{job_id}")
def api_dismiss_job(
    job_id: str,
    user: dict = Depends(get_required_user),
    svc: JobService = Depends(get_job_service),
) -> JSONResponse:
    if not svc.dismiss(user["id"], job_id):
        return JSONResponse({"error": "Job not found or not dismissable"}, status_code=404)
    return JSONResponse({"ok": True})
