"""#310 criterion 2 + 4: hard-kill and respawn under a real uvicorn supervisor, and
concurrent-scrape correctness. Proves the mechanism, not kajet's integration."""

import contextlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start(workers, mpdir, clear="1", attempts=6):
    """Retry on bind races: free_port() releases the socket before uvicorn claims it,
    and the kernel can still hold it briefly."""
    last = ""
    for _ in range(attempts):
        port = free_port()
        time.sleep(0.3)
        env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": mpdir, "SPIKE_CLEAR": clear}
        p = subprocess.Popen(
            [sys.executable, "launch.py", str(port), str(workers)],
            cwd=HERE,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        ok = False
        for _ in range(200):
            if p.poll() is not None:
                last = p.stdout.read().decode()[-400:]
                break
            try:
                if httpx.get(f"http://127.0.0.1:{port}/readyz", timeout=0.5).status_code == 200:
                    ok = True
                    break
            except Exception:
                time.sleep(0.1)
        if ok:
            return p, port
        with contextlib.suppress(Exception):
            p.kill()
    raise RuntimeError("never became ready; last output:\n" + last)


def children(sup_pid):
    out = subprocess.run(
        ["pgrep", "-P", str(sup_pid)], capture_output=True, text=True, check=False
    ).stdout
    return sorted(int(x) for x in out.split())


def scrape(port):
    return httpx.get(f"http://127.0.0.1:{port}/metrics", timeout=10).text


def val(text, name, labels=""):
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        key = line.split(" ")[0]
        if key == name + labels or (not labels and key == name):
            return float(line.rsplit(" ", 1)[1])
    return None


def series_names(text):
    return sorted(
        {
            line.split("{")[0].split(" ")[0]
            for line in text.splitlines()
            if line and not line.startswith("#")
        }
    )


def drive(port, n, seen=None):
    for _ in range(n):
        pid = httpx.get(f"http://127.0.0.1:{port}/work", timeout=5).text
        if seen is not None:
            seen.add(int(pid))


R = {}


def check(label, ok, detail=""):
    R[label] = bool(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- cold start, empty dir
mp = tempfile.mkdtemp(prefix="mp-empty-")
print("\n== A. cold start, empty multiproc dir, workers=2 ==")
sup, port = start(2, mp)
seen = set()
drive(port, 60, seen)
for _ in range(40):
    if len(seen) >= 2:
        break
    drive(port, 20, seen)
kids = children(sup.pid)
check(
    "exactly 2 uvicorn workers serve traffic",
    len(seen) == 2,
    f"served={sorted(seen)}; supervisor children={kids} (3.14 forkserver adds a helper)",
)
check("both children served traffic", len(seen) >= 2, f"pids seen={sorted(seen)}")
t = scrape(port)
total_before = val(t, 'spike_requests_total{route="/work"}')
inflight_before = val(t, "spike_inflight")
hist_before = val(t, 'spike_duration_seconds_count{route="/work"}')
check(
    "livesum gauge == number of live children",
    inflight_before == 2.0,
    f"spike_inflight={inflight_before}",
)
check(
    "counter aggregates across children",
    total_before and total_before >= 60,
    f"total={total_before}",
)
check("no pid label anywhere in exposition", "pid=" not in t)

# ------------------------------------------------------- mark: make one child 'most recent'
print("\n== B. stamp snapshot gauges, then hard-kill the child that stamped last ==")
target = None
for v in range(1, 200):
    pid = int(httpx.get(f"http://127.0.0.1:{port}/mark?v={v * 10}", timeout=5).text)
    target, target_v = pid, v * 10
t = scrape(port)
mr_before = val(t, "spike_snapshot_mostrecent")
lmr_before = val(t, "spike_snapshot_livemostrecent")
check(
    "mostrecent shows last stamper's value",
    mr_before == target_v,
    f"{mr_before} (stamped by pid {target})",
)
check("livemostrecent shows last stamper's value", lmr_before == target_v, f"{lmr_before}")

os.kill(target, signal.SIGKILL)
for _ in range(200):
    time.sleep(0.1)
    kids2 = children(sup.pid)
    if target not in kids2 and len(kids2) == 2:
        break
print(f"  killed pid {target}; children now {kids2}")
seen_after = set()
for _ in range(60):
    drive(port, 10, seen_after)
    if seen_after - seen:
        break
check(
    "uvicorn respawned a worker that serves traffic",
    bool(seen_after - seen) and target not in seen_after,
    f"new pid(s)={sorted(seen_after - seen)}, dead pid {target} gone={target not in seen_after}",
)

# no reaper has run yet -- show the residue the reaper is supposed to remove
t_res = scrape(port)
check(
    "BEFORE reap: livesum still counts the dead child",
    val(t_res, "spike_inflight") == 3.0,
    f"spike_inflight={val(t_res, 'spike_inflight')} (2 live + 1 dead)",
)

# ---------------------------------------------------------------- the reaper
print("\n== C. reaper: scan *_<pid>.db, kill(0), mark_process_dead on ESRCH ==")
reap_src = """
import os, glob, sys
from prometheus_client import multiprocess
d = os.environ["PROMETHEUS_MULTIPROC_DIR"]
dead = []
for f in glob.glob(os.path.join(d, "*.db")):
    m = os.path.basename(f).rsplit("_", 1)[-1][:-3]
    if not m.isdigit(): continue
    pid = int(m)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        dead.append(pid)
    except PermissionError:
        pass
for pid in sorted(set(dead)):
    multiprocess.mark_process_dead(pid)
print(sorted(set(dead)))
"""
out = subprocess.run(
    [sys.executable, "-c", reap_src],
    env={**os.environ, "PROMETHEUS_MULTIPROC_DIR": mp},
    capture_output=True,
    text=True,
    check=False,
)
print("  reaper reported dead pids:", out.stdout.strip(), out.stderr.strip()[:200])
t2 = scrape(port)
check(
    "AFTER reap: livesum drops to live children only",
    val(t2, "spike_inflight") == 2.0,
    f"spike_inflight={val(t2, 'spike_inflight')}",
)
total_after = val(t2, 'spike_requests_total{route="/work"}')
hist_after = val(t2, 'spike_duration_seconds_count{route="/work"}')
check(
    "counter did NOT reset across kill+reap",
    total_after >= total_before,
    f"{total_before} -> {total_after}",
)
check(
    "histogram did NOT reset across kill+reap",
    hist_after >= hist_before,
    f"{hist_before} -> {hist_after}",
)
mr_after = val(t2, "spike_snapshot_mostrecent")
lmr_after = val(t2, "spike_snapshot_livemostrecent")
check(
    "mostrecent KEEPS the dead child's value (stale forever)",
    mr_after == target_v,
    f"{mr_after} — dead pid {target} still owns it",
)
check(
    "livemostrecent DROPS the dead child's value (fails clean)",
    lmr_after != target_v,
    f"{lmr_after} (was {target_v})",
)

# ---------------------------------------------------------------- concurrent scrapes
print("\n== D. concurrent scrapes ==")
with ThreadPoolExecutor(max_workers=12) as ex:
    texts = list(ex.map(lambda _: scrape(port), range(24)))
totals = {val(x, 'spike_requests_total{route="/work"}') for x in texts}
check("24 concurrent scrapes all succeeded", all(texts))
check(
    "no scrape duplicated the counter",
    max(totals) <= total_after + 5,
    f"range={min(totals)}..{max(totals)}",
)
check("no pid label under concurrency", not any("pid=" in x for x in texts))
names_multi = series_names(texts[0])
sup.terminate()
sup.wait(timeout=20)

# ---------------------------------------------------------------- stale dir + workers=1
print("\n== E. cold start over a STALE dir (files from the run above), workers=2 ==")
sup2, port2 = start(2, mp, clear="1")
t3 = scrape(port2)
check(
    "stale files cleared by supervisor: counter restarts from 0",
    (val(t3, 'spike_requests_total{route="/work"}') or 0) == 0,
    f"total={val(t3, 'spike_requests_total{route="/work"}')}",
)
sup2.terminate()
sup2.wait(timeout=20)

print("\n== F. workers=1 through the same code path ==")
mp0 = tempfile.mkdtemp(prefix="mp-unset-")
sup0, port0 = start(1, mp0)
drive(port0, 5)
t0 = scrape(port0)
check(
    "a gauge no process has set exposes NO series (not a zero)",
    val(t0, "spike_snapshot_mostrecent") is None,
    "absent rather than 0 -- dashboards must handle absence",
)
sup0.terminate()
sup0.wait(timeout=20)
mp1 = tempfile.mkdtemp(prefix="mp-one-")
sup3, port3 = start(1, mp1)
drive(port3, 10)
httpx.get(f"http://127.0.0.1:{port3}/mark?v=42", timeout=5)
t4 = scrape(port3)
names_one = series_names(t4)
check(
    "workers=1 exposes the same series names as workers=2",
    names_one == names_multi,
    f"only-in-1={sorted(set(names_one) - set(names_multi))} "
    f"only-in-2={sorted(set(names_multi) - set(names_one))}",
)
check("workers=1 emits no pid label", "pid=" not in t4)
sup3.terminate()
sup3.wait(timeout=20)

print("\n" + "=" * 66)
bad = [k for k, v in R.items() if not v]
print(f"{len(R) - len(bad)}/{len(R)} checks passed")
if bad:
    print("FAILED:")
    [print("  -", b) for b in bad]
