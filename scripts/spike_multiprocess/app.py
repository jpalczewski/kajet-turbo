"""Worker-side app. Imported by uvicorn children AFTER the launcher has put
PROMETHEUS_MULTIPROC_DIR in the environment, mirroring #280's decision to set it in
kajet_turbo/__init__.py before anything imports prometheus_client."""

import os

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

REQS = Counter("spike_requests_total", "requests", ["route"])
DUR = Histogram("spike_duration_seconds", "duration", ["route"], buckets=(0.01, 0.1, 1.0))
INFLIGHT = Gauge("spike_inflight", "live per-child state", multiprocess_mode="livesum")
SNAP_MR = Gauge("spike_snapshot_mostrecent", "shared snapshot", multiprocess_mode="mostrecent")
SNAP_LMR = Gauge(
    "spike_snapshot_livemostrecent", "shared snapshot", multiprocess_mode="livemostrecent"
)

INFLIGHT.set(1)  # each child contributes exactly 1 -> livesum == number of live children


async def work(request):
    REQS.labels("/work").inc()
    DUR.labels("/work").observe(0.02)
    return PlainTextResponse(str(os.getpid()))


async def mark(request):
    """Stamp both snapshot gauges with a caller-supplied value and report which child
    served it, so the test knows whose value is currently 'most recent'."""
    v = float(request.query_params["v"])
    SNAP_MR.set(v)
    SNAP_LMR.set(v)
    return PlainTextResponse(str(os.getpid()))


async def metrics(request):
    reg = CollectorRegistry()
    multiprocess.MultiProcessCollector(reg)
    return Response(generate_latest(reg), media_type="text/plain")


async def health(request):
    return PlainTextResponse("ok")


app = Starlette(
    routes=[
        Route("/work", work),
        Route("/mark", mark),
        Route("/metrics", metrics),
        Route("/readyz", health),
    ]
)
