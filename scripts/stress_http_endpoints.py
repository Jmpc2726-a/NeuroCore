#!/usr/bin/env python3
"""
Empirical Stress Test Harness for NeuroCore FastAPI Endpoints.
Created by Challenger 2 (M1) to stress-test:
- GET /api/v1/health
- GET /api/v1/providers
- POST /api/v1/chat
- POST /api/v1/chat/stream
- GET/POST/DELETE /api/v1/sessions
"""

import os
import sys
import time
import json
import asyncio
import threading
import concurrent.futures
import httpx
import uvicorn

# Ensure repository root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from neurocore.server.app import app

PORT = 8098
BASE_URL = f"http://127.0.0.1:{PORT}"

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error")

async def test_health_endpoint(client):
    res = await client.get(f"{BASE_URL}/api/v1/health")
    assert res.status_code == 200, f"Health failed with {res.status_code}"
    data = res.json()
    assert data.get("status") == "ok"
    assert "providers" in data
    assert "uptime_seconds" in data
    return True

async def test_providers_endpoint(client):
    res = await client.get(f"{BASE_URL}/api/v1/providers")
    assert res.status_code == 200, f"Providers failed with {res.status_code}"
    data = res.json()
    assert "default_provider" in data
    assert "configured_providers" in data
    assert "available_providers" in data
    return True

async def test_sessions_crud(client):
    # 1. Create session
    create_res = await client.post(
        f"{BASE_URL}/api/v1/sessions",
        json={"title": "Stress Session", "provider": "local"}
    )
    assert create_res.status_code == 200, f"Session create failed with {create_res.status_code}"
    sess_data = create_res.json()
    session_id = sess_data.get("session_id")
    assert session_id, "Missing session_id in response"

    # 2. List sessions
    list_res = await client.get(f"{BASE_URL}/api/v1/sessions")
    assert list_res.status_code == 200, f"Sessions list failed with {list_res.status_code}"
    sessions = list_res.json()
    assert any(s.get("session_id") == session_id for s in sessions), "Created session not found in list"

    # 3. Get session details
    get_res = await client.get(f"{BASE_URL}/api/v1/sessions/{session_id}")
    assert get_res.status_code == 200, f"Get session failed with {get_res.status_code}"

    # 4. Delete session
    del_res = await client.delete(f"{BASE_URL}/api/v1/sessions/{session_id}")
    assert del_res.status_code == 200, f"Delete session failed with {del_res.status_code}"

    # 5. Verify deleted session returns 404
    get_after_del = await client.get(f"{BASE_URL}/api/v1/sessions/{session_id}")
    assert get_after_del.status_code == 404, f"Deleted session expected 404, got {get_after_del.status_code}"

    return True

async def test_sync_chat_endpoint(client):
    payload = {
        "prompt": "Explain Quantum Mechanics in one sentence.",
        "provider": "local",
        "temperature": 0.5
    }
    res = await client.post(f"{BASE_URL}/api/v1/chat", json=payload)
    assert res.status_code == 200, f"Sync chat failed with status {res.status_code}"
    data = res.json()
    assert "response" in data or "content" in data, "No content/response field in chat reply"
    assert data.get("provider_used") == "local"
    return True

async def test_stream_chat_endpoint(client):
    payload = {
        "prompt": "List prime numbers under 10",
        "provider": "local",
        "stream": True
    }
    async with client.stream("POST", f"{BASE_URL}/api/v1/chat/stream", json=payload) as res:
        assert res.status_code == 200, f"Stream chat failed with status {res.status_code}"
        assert "text/event-stream" in res.headers.get("content-type", ""), "Invalid content-type for streaming"

        events = []
        async for line in res.aiter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
        
        assert "connected" in events, "Streaming missing 'connected' event"
        assert "done" in events, "Streaming missing 'done' event"
    return True

async def test_concurrent_stress(num_requests=50):
    print(f"⚡ Starting Concurrent Stress Test with {num_requests} requests...")
    start_time = time.time()
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Mix of endpoint requests
        tasks = []
        for i in range(num_requests):
            if i % 5 == 0:
                tasks.append(test_health_endpoint(client))
            elif i % 5 == 1:
                tasks.append(test_providers_endpoint(client))
            elif i % 5 == 2:
                tasks.append(test_sessions_crud(client))
            elif i % 5 == 3:
                tasks.append(test_sync_chat_endpoint(client))
            else:
                tasks.append(test_stream_chat_endpoint(client))

        results = await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.time() - start_time
    errors = [r for r in results if isinstance(r, Exception)]

    if errors:
        print(f"❌ Stress Test Completed with {len(errors)} failures out of {num_requests} requests in {elapsed:.2f}s:")
        for err in errors[:5]:
            print(f"   - Exception: {err}")
        return False
    else:
        print(f"✅ Stress Test Passed! {num_requests} requests completed successfully in {elapsed:.2f}s ({num_requests/elapsed:.1f} req/sec).")
        return True

async def test_edge_cases():
    print("🧪 Running Edge Case & Robustness Validation...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Invalid prompt (empty prompt should return HTTP 422 Unprocessable Entity)
        res_empty = await client.post(f"{BASE_URL}/api/v1/chat", json={"prompt": ""})
        assert res_empty.status_code == 422, f"Expected 422 for empty prompt, got {res_empty.status_code}"
        print("  - Empty prompt validation: PASS (422)")

        # 2. Large prompt (10,000 chars)
        large_prompt = "A" * 10000
        res_large = await client.post(f"{BASE_URL}/api/v1/chat", json={"prompt": large_prompt, "provider": "local"})
        assert res_large.status_code == 200, f"Expected 200 for large prompt, got {res_large.status_code}"
        print("  - 10,000 char prompt: PASS (200)")

        # 3. Non-existent session GET
        res_404 = await client.get(f"{BASE_URL}/api/v1/sessions/non_existent_id_9999")
        assert res_404.status_code == 404, f"Expected 404 for non-existent session, got {res_404.status_code}"
        print("  - Non-existent session 404: PASS (404)")

        # 4. Unicode & Special Characters prompt
        res_unicode = await client.post(f"{BASE_URL}/api/v1/chat", json={"prompt": "🚀 Test UTF-8: ⚡ \u4f60\u597d \U0001F600", "provider": "local"})
        assert res_unicode.status_code == 200, f"Expected 200 for unicode prompt, got {res_unicode.status_code}"
        print("  - Unicode prompt: PASS (200)")

    return True

async def main():
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    await asyncio.sleep(1.5)  # Wait for uvicorn to bind

    print("\n=================== NEUROCORE HTTP STRESS TEST HARNESS ===================")
    
    # 1. Baseline Endpoint Verification
    async with httpx.AsyncClient(timeout=10.0) as client:
        print("🔹 Testing GET /api/v1/health...")
        await test_health_endpoint(client)
        print("  ✅ Status 200 OK")

        print("🔹 Testing GET /api/v1/providers...")
        await test_providers_endpoint(client)
        print("  ✅ Status 200 OK")

        print("🔹 Testing /api/v1/sessions (POST, GET, DELETE)...")
        await test_sessions_crud(client)
        print("  ✅ Status 200 OK across full session lifecycle")

        print("🔹 Testing POST /api/v1/chat...")
        await test_sync_chat_endpoint(client)
        print("  ✅ Status 200 OK")

        print("🔹 Testing POST /api/v1/chat/stream...")
        await test_stream_chat_endpoint(client)
        print("  ✅ Status 200 OK with valid SSE stream")

    # 2. Edge Case Testing
    edge_ok = await test_edge_cases()

    # 3. Concurrent Stress Testing
    stress_ok = await test_concurrent_stress(num_requests=50)

    print("==========================================================================")
    if edge_ok and stress_ok:
        print("🎉 ALL HTTP STRESS & VERIFICATION TESTS PASSED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("💥 SOME STRESS TESTS FAILED!")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
