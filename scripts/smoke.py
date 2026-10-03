#!/usr/bin/env python3
"""Exercise real HTTP, JWT, broker and worker. Creates an isolated smoke-test team."""

import json
import os
import time
import urllib.error
import urllib.request
import uuid

base = os.getenv("SMOKE_URL", "http://127.0.0.1:8010")


def call(path, body=None, token=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = "Bearer " + token
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


suffix = uuid.uuid4().hex[:12]
username = "smoke_" + suffix
password = uuid.uuid4().hex + "!Aa9"
team = call(
    "/api/auth/register/",
    {"username": username, "email": username + "@example.com", "password": password, "team_name": "Smoke " + suffix},
)
token = call("/api/auth/token/", {"username": username, "password": password})["access"]
prefix = "/api/teams/" + team["id"]
created = call(prefix + "/requests/", {"title": "Smoke request"}, token)
assert created["title"] == "Smoke request"
boundary = "Boundary" + suffix
body = (
    f'--{boundary}\r\nContent-Disposition: form-data; name="idempotency_key"\r\n\r\nsmoke-key\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="smoke.csv"\r\nContent-Type: text/csv\r\n\r\nexternal_id,title\nSMOKE-1,Background import\n\r\n--{boundary}--\r\n'
).encode()
job = call(prefix + "/imports/", body, token, "multipart/form-data; boundary=" + boundary)
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    state = call(prefix + "/imports/" + job["id"] + "/", token=token)
    if state["status"] in ("succeeded", "failed"):
        break
    time.sleep(1)
assert state["status"] == "succeeded", state
assert state["created_count"] == 1
repeat = call(prefix + "/imports/", body, token, "multipart/form-data; boundary=" + boundary)
assert repeat["id"] == job["id"]
assert call(prefix + "/requests/", token=token)["count"] == 2
assert call(prefix + "/audit/", token=token)["count"] >= 4
print("PASS: HTTP registration, JWT, request creation, real Celery import, idempotent upload, audit history.")
print("Smoke team:", team["id"])
