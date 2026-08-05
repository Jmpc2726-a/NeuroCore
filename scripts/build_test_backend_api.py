import os

content = r"""import pytest
from fastapi.testclient import TestClient
from neurocore.server.app import app

client = TestClient(app)

# ============================================================================
# 1. Health & System Manifest Endpoints (Tier 1 & Tier 2)
# ============================================================================

def test_health_check_endpoint():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "workspace_root" in data
    assert "providers" in data
    assert "active_sessions_count" in data
    assert "uptime_seconds" in data

def test_providers_endpoint():
    response = client.get("/api/v1/providers")
    assert response.status_code == 200
    data = response.json()
    assert "local" in data
    assert data["local"]["available"] is True
    assert data["default_provider"] == "gemini"
    assert "available_providers" in data

def test_tools_manifest_endpoint():
    response = client.get("/api/v1/tools")
    assert response.status_code == 200
    tools = response.json()
    assert "read_file" in tools
    assert "write_file" in tools
    assert "run_command" in tools
    assert "list_dir" in tools
    assert "grep_search" in tools
    assert "replace_file_content" in tools
    assert "tools" in tools
    assert "count" in tools

# ============================================================================
# 2. Session Management Endpoints (Tier 1 & Tier 2)
# ============================================================================

def test_sessions_lifecycle_endpoints():
    # Create session
    create_resp = client.post("/api/v1/sessions", json={"title": "Test Session", "provider": "local"})
    assert create_resp.status_code == 200
    sid = create_resp.json()["session_id"]

    # List sessions
    list_resp = client.get("/api/v1/sessions")
    assert list_resp.status_code == 200
    assert any(s["session_id"] == sid for s in list_resp.json())

    # Get session details
    detail_resp = client.get(f"/api/v1/sessions/{sid}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["session_id"] == sid

    # Delete session
    del_resp = client.delete(f"/api/v1/sessions/{sid}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

def test_session_get_non_existent():
    resp = client.get("/api/v1/sessions/sess_non_existent_xyz")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()

def test_session_delete_non_existent():
    resp = client.delete("/api/v1/sessions/sess_non_existent_xyz")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()

def test_session_reset_endpoint():
    resp = client.post("/api/v1/sessions/reset")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

# ============================================================================
# 3. Chat API Endpoints (Tier 1 & Tier 2)
# ============================================================================

def test_sync_chat_endpoint():
    payload = {
        "prompt": "Read file main.py",
        "provider": "local",
        "temperature": 0.7
    }
    response = client.post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert "content" in data or "response" in data
    assert "provider_used" in data
    assert data["provider_used"] == "local"

def test_streaming_chat_endpoint():
    payload = {
        "prompt": "Say hello",
        "provider": "local",
        "stream": True
    }
    with client.stream("POST", "/api/v1/chat/stream", json=payload) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        events = []
        for line in response.iter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
        assert "connected" in events
        assert "done" in events

def test_chat_invalid_payload_missing_prompt():
    resp = client.post("/api/v1/chat", json={"provider": "local"})
    assert resp.status_code == 422

def test_chat_empty_prompt_validation_error():
    resp = client.post("/api/v1/chat", json={"prompt": "", "provider": "local"})
    assert resp.status_code == 422

def test_chat_fallback_provider():
    payload = {
        "prompt": "Hello via non-existent provider",
        "provider": "unknown_provider_xyz"
    }
    response = client.post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "content" in data or "response" in data

# ============================================================================
# 4. Tasks API Endpoints (Tier 1 & Tier 2)
# ============================================================================

def test_tasks_crud_endpoints():
    # Create task
    task_payload = {
        "title": "Build Web Studio",
        "prompt": "Implement style.css",
        "priority": "high",
        "target_files": ["style.css"],
        "status": "backlog"
    }
    resp = client.post("/api/v1/tasks", json=task_payload)
    assert resp.status_code == 200
    task_data = resp.json()
    task_id = task_data["task_id"]
    assert task_data["title"] == "Build Web Studio"
    assert task_data["priority"] == "high"

    # Get single task
    single_resp = client.get(f"/api/v1/tasks/{task_id}")
    assert single_resp.status_code == 200
    assert single_resp.json()["task_id"] == task_id

    # List all tasks
    get_resp = client.get("/api/v1/tasks")
    assert get_resp.status_code == 200
    assert any(t["task_id"] == task_id for t in get_resp.json())

    # Update task
    update_payload = {"status": "in_progress", "priority": "critical"}
    patch_resp = client.patch(f"/api/v1/tasks/{task_id}", json=update_payload)
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "in_progress"
    assert patch_resp.json()["priority"] == "critical"

    # Delete task
    del_resp = client.delete(f"/api/v1/tasks/{task_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

def test_task_run_endpoint():
    # Create task to run
    create_resp = client.post("/api/v1/tasks", json={"title": "Run Task Test", "prompt": "Execute python code"})
    assert create_resp.status_code == 200
    tid = create_resp.json()["task_id"]

    # Run task
    run_resp = client.post(f"/api/v1/tasks/{tid}/run")
    assert run_resp.status_code == 200
    assert run_resp.json()["status"] == "initiated"
    assert run_resp.json()["task_id"] == tid

    # Clean up
    client.delete(f"/api/v1/tasks/{tid}")

def test_task_get_non_existent():
    resp = client.get("/api/v1/tasks/task_invalid_999")
    assert resp.status_code == 404
    assert "task not found" in resp.json()["detail"].lower()

def test_task_update_non_existent():
    resp = client.put("/api/v1/tasks/task_invalid_999", json={"status": "completed"})
    assert resp.status_code == 404
    assert "task not found" in resp.json()["detail"].lower()

def test_task_delete_non_existent():
    resp = client.delete("/api/v1/tasks/task_invalid_999")
    assert resp.status_code == 404
    assert "task not found" in resp.json()["detail"].lower()

def test_task_run_non_existent():
    resp = client.post("/api/v1/tasks/task_invalid_999/run")
    assert resp.status_code == 404
    assert "task not found" in resp.json()["detail"].lower()

# ============================================================================
# 5. Activity Stream Endpoint (Tier 1 & Tier 2)
# ============================================================================

def test_activity_stream_endpoint():
    with client.stream("GET", "/api/v1/stream/activity") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        for line in response.iter_lines():
            if line.startswith("data:"):
                assert "system_connect" in line or "NeuroCore" in line
                break

def test_web_studio_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
"""

with open("tests/test_backend_api.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Updated tests/test_backend_api.py")
