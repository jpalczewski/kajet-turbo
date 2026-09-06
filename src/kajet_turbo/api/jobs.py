from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import ErrorResponse, JobsResponse, OkResponse
from kajet_turbo.dependencies import CurrentUser, get_job_service, get_required_user
from kajet_turbo.errors import JobError
from kajet_turbo.services.jobs import JobService

router = APIRouter()


@router.get("/api/me/jobs", response_model=JobsResponse)
def api_list_jobs(
    request: Request,
    user: CurrentUser = Depends(get_required_user),
    svc: JobService = Depends(get_job_service),
) -> JSONResponse:
    status = request.query_params.get("status") or None
    return JSONResponse({"jobs": svc.list(user.id, status=status)})


@router.post(
    "/api/me/jobs/{job_id}/retry",
    response_model=OkResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_retry_job(
    job_id: str,
    user: CurrentUser = Depends(get_required_user),
    svc: JobService = Depends(get_job_service),
) -> OkResponse:
    if not svc.retry(user.id, job_id):
        raise HTTPException(status_code=404, detail=JobError.NOT_FOUND)
    return OkResponse(ok=True)


@router.delete(
    "/api/me/jobs/{job_id}",
    response_model=OkResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_dismiss_job(
    job_id: str,
    user: CurrentUser = Depends(get_required_user),
    svc: JobService = Depends(get_job_service),
) -> OkResponse:
    if not svc.dismiss(user.id, job_id):
        raise HTTPException(status_code=404, detail=JobError.NOT_FOUND)
    return OkResponse(ok=True)
