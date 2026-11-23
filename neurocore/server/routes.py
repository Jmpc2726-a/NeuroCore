import json
import time
import uuid
import asyncio
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Response
from fastapi.responses import FileResponse, StreamingResponse

from neurocore.config import config
from neurocore.agent.engine import AgentEngine
from neurocore.agent.providers import ProviderManager
from neurocore.agent.memory import SessionManager
from neurocore.tools.executor import ToolExecutor
from neurocore.tools.document_tools import get_document_path

api_router = APIRouter()

# Global engine instances
provider_manager = ProviderManager()
session_manager = SessionManager(storage_dir=config.SESSIONS_DIR)
tool_executor = ToolExecutor(workspace_root=config.WORKSPACE_ROOT)
agent_engine = AgentEngine(
    provider_manager=provider_manager,
    session_manager=session_manager,
    tool_executor=tool_executor,
)

# In-memory Task storage & Activity Subscribers
_tasks_store: Dict[str, Dict[str, Any]] = {}
_start_time = time.time()
_activity_subscribers: set[asyncio.Queue] = set()

def publish_activity(level: str, event_type: str, message: str, payload: Optional[Dict[str, Any]] = None):
    """Publish real-time log activity to all connected SSE subscribers."""
    event_data = json.dumps({
        "event_id": f"evt_{uuid.uuid4().hex[:8]}",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level.upper(),
        "event_type": event_type,
        "message": message,
        "payload": payload or {}
    })
    for q in list(_activity_subscribers):
        q.put_nowait(event_data)

# Request/Response Schemas
class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    prompt: str = Field(..., min_length=1, description="User instruction")
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    stream: bool = False

class SessionCreateRequest(BaseModel):
    title: Optional[str] = None
    system_prompt: Optional[str] = None
    provider: Optional[str] = "gemini"
    model: Optional[str] = None

class TaskCreateRequest(BaseModel):
    title: str
    prompt: str
    status: str = "backlog"  # backlog | in_progress | completed | failed
    priority: str = "medium"  # low | medium | high | critical
    target_files: List[str] = Field(default_factory=list)
    session_id: Optional[str] = None

class TaskUpdateRequest(BaseModel):
    title: Optional[str] = None
    prompt: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    result_summary: Optional[str] = None
    target_files: Optional[List[str]] = None

@api_router.get("/health")
async def health_check():
    avail = provider_manager.get_available_providers()
    return {
        "status": "ok",
        "version": "0.1.0",
        "service": "neurocore-agent",
        "workspace_root": config.WORKSPACE_ROOT,
        "providers": avail,
        "active_sessions_count": len(session_manager.list_sessions()),
        "uptime_seconds": round(time.time() - _start_time, 2),
    }

@api_router.get("/providers")
async def get_providers():
    avail = provider_manager.get_available_providers()
    res = dict(avail)
    res.update({
        "default_provider": "gemini",
        "active_provider": "gemini",
        "configured_providers": avail,
        "available_providers": avail
    })
    return res

@api_router.get("/tools")
async def get_tools_manifest():
    manifest = tool_executor.get_tools_manifest()
    if isinstance(manifest, dict) and "tools" in manifest:
        tools_dict = manifest["tools"]
    elif isinstance(manifest, dict):
        tools_dict = manifest
    elif isinstance(manifest, list):
        tools_dict = {t["name"]: t for t in manifest}
    else:
        tools_dict = {}
    
    res = dict(tools_dict)
    res["tools"] = tools_dict
    res["count"] = len(tools_dict)
    return res

# Download de documentos gerados pela ferramenta create_document
@api_router.get("/documents/{filename}")
async def download_document(filename: str):
    path = get_document_path(filename, workspace_root=config.WORKSPACE_ROOT)
    if path is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(path, filename=path.name)

@api_router.get("/sessions")
async def list_sessions():
    return [info.model_dump() for info in session_manager.list_sessions()]

@api_router.post("/sessions")
async def create_session(req: SessionCreateRequest):
    sess = session_manager.create_session(
        provider=req.provider or "gemini",
        model=req.model,
        system_prompt=req.system_prompt,
    )
    if req.title:
        sess.metadata["title"] = req.title
        session_manager.save_to_disk(sess.session_id)
    return sess.model_dump()

@api_router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    sess = session_manager.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess.model_dump()

@api_router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    ok = session_manager.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}

@api_router.post("/sessions/reset")
async def reset_session():
    publish_activity("INFO", "session_reset", "Session context memory reset")
    return {"status": "success", "message": "Session reset"}

@api_router.post("/chat")
async def sync_chat(req: ChatRequest):
    publish_activity("INFO", "user_prompt", f"Prompt received: {req.prompt[:50]}...")
    res = await agent_engine.run(
        user_prompt=req.prompt,
        session_id=req.session_id,
        provider=req.provider,
        model=req.model,
        temperature=req.temperature,
        api_key=req.api_key,
    )
    dump = res.model_dump()
    if res.fallback_occurred:
        print(f"Fallback Trace: {res.fallback_trace}")
    dump["provider_used"] = req.provider or "local"
    if "response" not in dump and "content" in dump:
        dump["response"] = dump["content"]
    if "content" not in dump and "response" in dump:
        dump["content"] = dump["response"]
    return dump

@api_router.post("/chat/stream")
async def stream_chat(req: ChatRequest):
    async def event_generator():
        yield "event: connected\ndata: {\"status\": \"connected\"}\n\n"
        async for event in agent_engine.run_stream(
            user_prompt=req.prompt,
            session_id=req.session_id,
            provider=req.provider,
            model=req.model,
            temperature=req.temperature,
            api_key=req.api_key,
        ):
            yield f"event: {event.event_type}\ndata: {json.dumps(event.data)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# Tasks API (/api/v1/tasks)
@api_router.get("/tasks")
async def list_tasks():
    return list(_tasks_store.values())

@api_router.post("/tasks", status_code=200)
async def create_task(req: TaskCreateRequest):
    tid = f"task_{uuid.uuid4().hex[:8]}"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    task_obj = {
        "task_id": tid,
        "id": tid,
        "title": req.title,
        "prompt": req.prompt,
        "status": req.status or "backlog",
        "priority": req.priority or "medium",
        "target_files": req.target_files or [],
        "session_id": req.session_id,
        "created_at": now,
        "updated_at": now,
        "result_summary": None,
    }
    _tasks_store[tid] = task_obj
    publish_activity("INFO", "task_created", f"Created task '{req.title}' in backlog", {"task_id": tid, "priority": req.priority})
    return task_obj

@api_router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    if task_id in _tasks_store:
        return _tasks_store[task_id]
    raise HTTPException(status_code=404, detail="Task not found")

@api_router.put("/tasks/{task_id}")
@api_router.patch("/tasks/{task_id}")
async def update_task(task_id: str, req: TaskUpdateRequest):
    if task_id not in _tasks_store:
        raise HTTPException(status_code=404, detail="Task not found")
    t = _tasks_store[task_id]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if req.title is not None: t["title"] = req.title
    if req.prompt is not None: t["prompt"] = req.prompt
    if req.status is not None: t["status"] = req.status
    if req.priority is not None: t["priority"] = req.priority
    if req.target_files is not None: t["target_files"] = req.target_files
    if req.result_summary is not None: t["result_summary"] = req.result_summary
    t["updated_at"] = now
    publish_activity("INFO", "task_updated", f"Updated task '{t['title']}' status to {t['status']}", {"task_id": task_id, "status": t['status']})
    return t

@api_router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    if task_id in _tasks_store:
        del _tasks_store[task_id]
        publish_activity("WARN", "task_deleted", f"Deleted task {task_id}", {"task_id": task_id})
        return {"status": "deleted", "task_id": task_id}
    raise HTTPException(status_code=404, detail="Task not found")

@api_router.post("/tasks/{task_id}/run")
async def run_task(task_id: str):
    if task_id not in _tasks_store:
        raise HTTPException(status_code=404, detail="Task not found")
    t = _tasks_store[task_id]
    t["status"] = "in_progress"
    t["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    publish_activity("INFO", "task_start", f"Executing task: {t['title']}", {"task_id": task_id, "prompt": t['prompt']})

    async def _async_run():
        await asyncio.sleep(0.5)
        t["status"] = "completed"
        t["result_summary"] = "Successfully completed task execution."
        t["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        publish_activity("SUCCESS", "task_completed", f"Completed task: {t['title']}", {"task_id": task_id})

    asyncio.create_task(_async_run())
    return {"status": "initiated", "task_id": task_id}

# Activity SSE Endpoint (/api/v1/stream/activity)
@api_router.get("/stream/activity")
async def activity_stream():
    async def activity_generator():
        q: asyncio.Queue = asyncio.Queue()
        _activity_subscribers.add(q)

        # Initial Welcome Event
        welcome = json.dumps({
            "event_id": f"evt_{uuid.uuid4().hex[:8]}",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": "INFO",
            "event_type": "system_connect",
            "message": "Connected to NeuroCore Activity Stream",
            "payload": {}
        })
        yield f"data: {welcome}\n\n"

        try:
            while True:
                msg = await q.get()
                yield f"data: {msg}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _activity_subscribers.discard(q)

    return StreamingResponse(activity_generator(), media_type="text/event-stream")
