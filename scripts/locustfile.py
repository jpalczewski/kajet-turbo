"""Locust stress test for the Kajet API.

Setup:
    pip install locust   (or: uv tool install locust)

    # Create a technical user first:
    uv run kajet-turbo create-user --email stress01@kajet.local

Usage (web UI):
    KAJET_STRESS_EMAIL=stress01@kajet.local \
    KAJET_STRESS_PASSWORD=<password> \
    locust -f scripts/locustfile.py --host http://localhost:8000

Usage (headless):
    KAJET_STRESS_EMAIL=stress01@kajet.local \
    KAJET_STRESS_PASSWORD=<password> \
    locust -f scripts/locustfile.py --host http://localhost:8000 \
        --headless -u 10 -r 2 --run-time 60s
"""

import os
import random
import string

from locust import HttpUser, between, task

_WS = os.environ.get("KAJET_STRESS_WS", "stress")


class KajetUser(HttpUser):
    wait_time = between(0.05, 0.3)

    def on_start(self) -> None:
        email = os.environ["KAJET_STRESS_EMAIL"]
        password = os.environ["KAJET_STRESS_PASSWORD"]
        resp = self.client.post("/api/login", json={"email": email, "password": password})
        resp.raise_for_status()
        # 409 = workspace already exists, which is fine
        self.client.post("/api/workspaces", json={"name": _WS})

    @task(3)
    def create_note(self) -> None:
        suffix = "".join(random.choices(string.ascii_lowercase, k=8))
        self.client.post(
            f"/api/workspaces/{_WS}/notes",
            json={
                "title": f"stress-{suffix}",
                "content": "Content line.\n" * 20,
                "tags": ["stress"],
            },
        )

    @task(1)
    def list_notes(self) -> None:
        self.client.get(f"/api/workspaces/{_WS}/notes")
