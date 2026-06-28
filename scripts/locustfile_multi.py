"""Locust stress test — multi-user, per-VU isolated workspace.

Każdy VU dostaje własnego usera i własny workspace (stress-00, stress-01, ...).
Zero contention na git locku — każdy repo jest niezależny.

Setup:
    ./ops/run-stress.sh --users 10 --headless -u 10 -r 2 --run-time 60s

Plik z pulą userów (ops/stress-users.json) tworzy run-stress.sh.
"""

import itertools
import json
import os
import threading

from locust import HttpUser, between, task

_lock = threading.Lock()
_counter = itertools.count()

_USERS_FILE = os.environ.get("KAJET_STRESS_USERS_FILE", "")
_user_pool: list[dict] = []

if _USERS_FILE:
    with open(_USERS_FILE) as _f:
        _user_pool = json.load(_f)


class KajetUser(HttpUser):
    wait_time = between(0.05, 0.3)

    def on_start(self) -> None:
        with _lock:
            idx = next(_counter)

        if not _user_pool:
            raise RuntimeError("KAJET_STRESS_USERS_FILE not set or empty")

        user = _user_pool[idx % len(_user_pool)]
        self._ws = f"stress-{idx:02d}"

        resp = self.client.post(
            "/api/login", json={"email": user["email"], "password": user["password"]}
        )
        resp.raise_for_status()

        with self.client.post(
            "/api/workspaces", json={"name": self._ws}, catch_response=True
        ) as r:
            if r.status_code in (201, 409):
                r.success()

    @task(3)
    def create_note(self) -> None:
        import random
        import string

        suffix = "".join(random.choices(string.ascii_lowercase, k=8))
        self.client.post(
            f"/api/workspaces/{self._ws}/notes",
            json={
                "title": f"stress-{suffix}",
                "content": "Content line.\n" * 20,
                "tags": ["stress"],
            },
        )

    @task(1)
    def list_notes(self) -> None:
        self.client.get(f"/api/workspaces/{self._ws}/notes")
