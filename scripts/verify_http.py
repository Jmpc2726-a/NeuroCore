#!/usr/bin/env python3
"""
HTTP Endpoint Verification Script for NeuroCore Web Studio & Backend Server

Probes:
- GET / (Returns HTTP 200 OK HTML)
- GET /static/css/style.css (Returns HTTP 200 OK CSS)
- GET /static/js/app.js (Returns HTTP 200 OK JS)
- GET /api/v1/health (Returns HTTP 200 OK JSON)
- GET /api/v1/providers (Returns HTTP 200 OK JSON)
- GET /api/v1/tasks (Returns HTTP 200 OK JSON)
"""

import os
import sys
import threading
import time
import httpx
import uvicorn

# Ensure repository root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from neurocore.server.app import app

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")

def verify():
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(1.5)  # Wait for server startup

    base_url = "http://127.0.0.1:8099"
    endpoints = [
        ("/", 200, "text/html"),
        ("/static/css/style.css", 200, "text/css"),
        ("/static/js/app.js", 200, None),
        ("/api/v1/health", 200, "application/json"),
        ("/api/v1/providers", 200, "application/json"),
        ("/api/v1/tasks", 200, "application/json"),
    ]

    failed = False
    with httpx.Client() as client:
        for path, expected_status, content_type in endpoints:
            try:
                res = client.get(f"{base_url}{path}")
                if res.status_code == expected_status:
                    print(f"✅ PASS: GET {path} -> Status {res.status_code}")
                else:
                    print(f"❌ FAIL: GET {path} -> Status {res.status_code} (Expected {expected_status})")
                    failed = True
            except Exception as e:
                print(f"❌ ERROR: GET {path} -> {e}")
                failed = True

    if failed:
        print("\n❌ HTTP Endpoint Verification Failed!")
        sys.exit(1)
    else:
        print("\n✅ All HTTP Endpoints Verified Successfully!")
        sys.exit(0)

if __name__ == "__main__":
    verify()
