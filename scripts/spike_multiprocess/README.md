# #310 spike — prometheus_client multiprocess lifecycle under uvicorn

Reproducible artifact behind the decisions recorded in
[`docs/specs/metrics-multiprocess.md`](../../docs/specs/metrics-multiprocess.md).

Not a pytest module: `prometheus_client` only becomes a project dependency in #311, so
this cannot run in CI. Its assertions move into `tests/stress/` once that lands.

Three files rather than one, mirroring kajet's own separation — `app.py` is the module
uvicorn's children import, `launch.py` is the supervisor that owns the multiproc
directory. Folding them together would put the metric definitions in the entrypoint's
own import, before the directory exists, which kajet never does.

## Running it

Linux only. uvicorn's supervisor hands the listening socket to its children, which does
not work where multiprocessing spawns rather than forks (macOS).

```bash
uv venv --python 3.14.7t /tmp/spike
uv pip install --python /tmp/spike/bin/python uvicorn starlette prometheus_client httpx
cd scripts/spike_multiprocess && /tmp/spike/bin/python harness.py
```

Or in a container, from the repository root:

```bash
docker run --rm -v "$PWD/scripts/spike_multiprocess:/spike" \
  ghcr.io/astral-sh/uv:0.12.7-trixie-slim sh -c '
    apt-get update -qq && apt-get install -y -qq procps
    export UV_PYTHON_INSTALL_DIR=/python UV_PYTHON_PREFERENCE=only-managed
    uv python install 3.14.7t && uv venv --python 3.14.7t /venv
    uv pip install -q --python /venv/bin/python uvicorn starlette prometheus_client httpx
    cd /spike && /venv/bin/python harness.py'
```

`procps` is needed for `pgrep`, which the harness uses to see the supervisor's children.
