import os

content = r"""import pytest
from fastapi.testclient import TestClient
from neurocore.server.app import app

client = TestClient(app)

# ============================================================================
# 1. Myers LCS Line-by-Line Diff Engine Tests (Tier 1 & Tier 2)
# ============================================================================

def myers_lcs_diff(old_text: str, new_text: str):
    """
    Python implementation of Myers LCS line diff matching DiffParser in app.js.
    Returns list of dicts: {"type": "added"|"deleted"|"unchanged", "oldLineNum": int|None, "newLineNum": int|None, "content": str}
    """
    old_lines = old_text.splitlines() if old_text else []
    new_lines = new_text.splitlines() if new_text else []
    m = len(old_lines)
    n = len(new_lines)

    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m):
        for j in range(n):
            if old_lines[i] == new_lines[j]:
                dp[i + 1][j + 1] = dp[i][j] + 1
            else:
                dp[i + 1][j + 1] = max(dp[i + 1][j], dp[i][j + 1])

    i, j = m, n
    diff = []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and old_lines[i - 1] == new_lines[j - 1]:
            diff.insert(0, {"type": "unchanged", "oldLineNum": i, "newLineNum": j, "content": old_lines[i - 1]})
            i -= 1
            j -= 1
        elif j > 0 and (i == 0 or dp[i][j - 1] >= dp[i - 1][j]):
            diff.insert(0, {"type": "added", "oldLineNum": None, "newLineNum": j, "content": new_lines[j - 1]})
            j -= 1
        elif i > 0 and (j == 0 or dp[i][j - 1] < dp[i - 1][j]):
            diff.insert(0, {"type": "deleted", "oldLineNum": i, "newLineNum": None, "content": old_lines[i - 1]})
            i -= 1

    return diff

def test_myers_lcs_diff_identical_texts():
    text = "line1\nline2\nline3"
    diff = myers_lcs_diff(text, text)
    assert len(diff) == 3
    assert all(d["type"] == "unchanged" for d in diff)
    assert diff[0]["oldLineNum"] == 1 and diff[0]["newLineNum"] == 1

def test_myers_lcs_diff_all_added():
    old = ""
    new = "line1\nline2"
    diff = myers_lcs_diff(old, new)
    assert len(diff) == 2
    assert all(d["type"] == "added" for d in diff)

def test_myers_lcs_diff_all_deleted():
    old = "line1\nline2"
    new = ""
    diff = myers_lcs_diff(old, new)
    assert len(diff) == 2
    assert all(d["type"] == "deleted" for d in diff)

def test_myers_lcs_diff_interleaved_changes():
    old = "def foo():\n    return 1\n"
    new = "def foo():\n    # docstring\n    return 42\n"
    diff = myers_lcs_diff(old, new)
    types = [d["type"] for d in diff]
    assert "unchanged" in types
    assert "added" in types or "deleted" in types

def test_myers_lcs_diff_single_line_replacement():
    old = "a\nb\nc"
    new = "a\nX\nc"
    diff = myers_lcs_diff(old, new)
    assert diff[0]["content"] == "a" and diff[0]["type"] == "unchanged"
    assert diff[-1]["content"] == "c" and diff[-1]["type"] == "unchanged"

# ============================================================================
# 2. Web Studio Root & Static File Endpoints (Tier 1 & Tier 2)
# ============================================================================

def test_web_studio_root_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "NeuroCore" in response.text

def test_web_studio_static_css_endpoint():
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"] or "text/plain" in response.headers["content-type"]

def test_web_studio_static_js_endpoint():
    response = client.get("/static/js/app.js")
    assert response.status_code == 200

# ============================================================================
# 3. Web Studio Task Management Board REST API (Tier 1 & Tier 2)
# ============================================================================

def test_web_studio_task_crud():
    # Create task via Web Studio API
    create_payload = {
        "title": "Fix CSS Layout",
        "prompt": "Align 3-column Web Studio dashboard",
        "priority": "high",
        "status": "backlog",
        "target_files": ["neurocore/web/static/css/style.css"]
    }
    res_create = client.post("/api/v1/tasks", json=create_payload)
    assert res_create.status_code == 200
    task_data = res_create.json()
    tid = task_data["task_id"]
    assert task_data["title"] == "Fix CSS Layout"
    assert task_data["priority"] == "high"

    # List tasks
    res_list = client.get("/api/v1/tasks")
    assert res_list.status_code == 200
    assert any(t["task_id"] == tid for t in res_list.json())

    # Get single task
    res_get = client.get(f"/api/v1/tasks/{tid}")
    assert res_get.status_code == 200
    assert res_get.json()["task_id"] == tid

    # Update task status to in_progress
    res_update = client.patch(f"/api/v1/tasks/{tid}", json={"status": "in_progress", "priority": "critical"})
    assert res_update.status_code == 200
    assert res_update.json()["status"] == "in_progress"
    assert res_update.json()["priority"] == "critical"

    # Run task
    res_run = client.post(f"/api/v1/tasks/{tid}/run")
    assert res_run.status_code == 200
    assert res_run.json()["status"] == "initiated"

    # Delete task
    res_del = client.delete(f"/api/v1/tasks/{tid}")
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "deleted"

def test_web_studio_task_404_errors():
    invalid_id = "task_non_existent_99"
    assert client.get(f"/api/v1/tasks/{invalid_id}").status_code == 404
    assert client.patch(f"/api/v1/tasks/{invalid_id}", json={"status": "completed"}).status_code == 404
    assert client.delete(f"/api/v1/tasks/{invalid_id}").status_code == 404
    assert client.post(f"/api/v1/tasks/{invalid_id}/run").status_code == 404

# ============================================================================
# 4. Activity Stream & Session Control (Tier 1 & Tier 2)
# ============================================================================

def test_web_studio_activity_stream_connect():
    with client.stream("GET", "/api/v1/stream/activity") as stream_res:
        assert stream_res.status_code == 200
        for line in stream_res.iter_lines():
            if line.startswith("data:"):
                assert "system_connect" in line or "NeuroCore" in line
                break

def test_web_studio_session_reset():
    response = client.post("/api/v1/sessions/reset")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
"""

with open("tests/test_web_studio.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Created tests/test_web_studio.py")
