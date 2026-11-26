import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from neurocore.agent.engine import AgentEngine
from neurocore.agent.memory import SESSION_ID_RE, SessionManager
from neurocore.agent.providers import ProviderManager
from neurocore.config import config
from neurocore.tools.document_tools import get_document_path
from neurocore.tools.executor import DANGEROUS_TOOL_NAMES, ToolExecutor

logger = logging.getLogger("neurocore.routes")
api_router = APIRouter()

# Instâncias globais do motor
provider_manager = ProviderManager()
session_manager = SessionManager(storage_dir=config.SESSIONS_DIR, max_sessions=config.MAX_SESSIONS)
tool_executor = ToolExecutor(workspace_root=config.WORKSPACE_ROOT)
agent_engine = AgentEngine(
    provider_manager=provider_manager,
    session_manager=session_manager,
    tool_executor=tool_executor,
    max_steps=config.MAX_STEPS,
)

# Tarefas (persistidas em .tasks.json) e assinantes do stream de atividade
_tasks_store: Dict[str, Dict[str, Any]] = {}
_start_time = time.time()
_activity_subscribers: set = set()
_running_tasks: set = set()
SUBSCRIBER_QUEUE_SIZE = 200
MAX_SUBSCRIBERS = 200

SESSION_ID_PATTERN = SESSION_ID_RE.pattern
TaskStatus = Literal["backlog", "in_progress", "completed", "failed"]
TaskPriority = Literal["low", "medium", "high", "critical"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_tasks() -> None:
    try:
        with open(config.TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(data, list):
        return
    for task in data:
        if not isinstance(task, dict) or not isinstance(task.get("task_id"), str):
            continue
        if task.get("status") == "in_progress":
            task["status"] = "failed"
            task["result_summary"] = "Execução interrompida porque o servidor foi reiniciado."
        _tasks_store[task["task_id"]] = task


def _save_tasks() -> None:
    try:
        os.makedirs(os.path.dirname(config.TASKS_FILE) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(config.TASKS_FILE) or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(list(_tasks_store.values()), f, ensure_ascii=False, indent=2)
        os.replace(tmp, config.TASKS_FILE)
    except OSError:
        logger.warning("Não foi possível salvar as tarefas em %s", config.TASKS_FILE)


_load_tasks()


def publish_activity(level: str, event_type: str, message: str, payload: Optional[Dict[str, Any]] = None):
    """Publica um evento para todos os assinantes do SSE de atividade.

    Não inclua aqui texto digitado pelos usuários: o stream é visto por todos.
    """
    event_data = json.dumps({
        "event_id": f"evt_{uuid.uuid4().hex[:8]}",
        "timestamp": _now(),
        "level": level.upper(),
        "event_type": event_type,
        "message": message,
        "payload": payload or {},
    }, ensure_ascii=False)
    for q in list(_activity_subscribers):
        try:
            q.put_nowait(event_data)
        except asyncio.QueueFull:
            pass  # assinante lento: descarta o evento em vez de crescer sem limite


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, pattern=SESSION_ID_PATTERN)
    prompt: str = Field(..., min_length=1, max_length=config.MAX_PROMPT_CHARS, description="User instruction")
    provider: Optional[str] = Field(default=None, max_length=40)
    model: Optional[str] = Field(default=None, max_length=200)
    api_key: Optional[str] = Field(default=None, max_length=400)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=128000)
    stream: bool = False


class SessionCreateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    system_prompt: Optional[str] = Field(default=None, max_length=20000)
    provider: Optional[str] = Field(default="local", max_length=40)
    model: Optional[str] = Field(default=None, max_length=200)


class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    prompt: str = Field(..., min_length=1, max_length=config.MAX_PROMPT_CHARS)
    status: TaskStatus = "backlog"
    priority: TaskPriority = "medium"
    target_files: List[str] = Field(default_factory=list, max_length=50)
    session_id: Optional[str] = Field(default=None, pattern=SESSION_ID_PATTERN)


class TaskUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    prompt: Optional[str] = Field(default=None, min_length=1, max_length=config.MAX_PROMPT_CHARS)
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    result_summary: Optional[str] = Field(default=None, max_length=20000)
    target_files: Optional[List[str]] = Field(default=None, max_length=50)


class TaskRunRequest(BaseModel):
    provider: Optional[str] = Field(default=None, max_length=40)
    model: Optional[str] = Field(default=None, max_length=200)
    api_key: Optional[str] = Field(default=None, max_length=400)


# ---------------------------------------------------------------------------
# Sistema
# ---------------------------------------------------------------------------

def _enabled_tools() -> List[str]:
    return sorted(tool_executor.tools.keys())


@api_router.get("/health")
async def health_check():
    avail = provider_manager.get_available_providers()
    return {
        "status": "ok",
        "version": "1.1.0",
        "service": "neurocore-agent",
        "workspace_root": os.path.basename(config.WORKSPACE_ROOT.rstrip(os.sep)) or config.WORKSPACE_ROOT,
        "providers": avail,
        "active_sessions_count": len(session_manager.list_sessions()),
        "uptime_seconds": round(time.time() - _start_time, 2),
        "auth_required": bool(config.ACCESS_TOKEN),
        "public_mode": config.PUBLIC_MODE,
        "dangerous_tools_enabled": any(name in DANGEROUS_TOOL_NAMES for name in _enabled_tools()),
        "default_provider": config.DEFAULT_PROVIDER,
    }


@api_router.get("/providers")
async def get_providers():
    avail = provider_manager.get_available_providers()
    res: Dict[str, Any] = dict(avail)
    res.update({
        "default_provider": config.DEFAULT_PROVIDER,
        "active_provider": config.DEFAULT_PROVIDER,
        "configured_providers": avail,
        "available_providers": avail,
    })
    return res


@api_router.get("/tools")
async def get_tools_manifest():
    tools_dict = tool_executor.get_tools_manifest()
    res: Dict[str, Any] = dict(tools_dict)
    res["tools"] = tools_dict
    res["count"] = len(tools_dict)
    return res


@api_router.get("/documents/{filename}")
async def download_document(filename: str):
    """Download de documentos gerados pela ferramenta create_document."""
    path = get_document_path(filename, workspace_root=config.WORKSPACE_ROOT)
    if path is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(path, filename=path.name)


# ---------------------------------------------------------------------------
# Sessões
# ---------------------------------------------------------------------------

def _check_session_id(session_id: str) -> None:
    if not SESSION_ID_RE.fullmatch(session_id or ""):
        raise HTTPException(status_code=404, detail="Session not found")


@api_router.get("/sessions")
async def list_sessions():
    # Apenas metadados: o conteúdo das conversas exige o ID da sessão.
    return [info.model_dump() for info in session_manager.list_sessions()]


@api_router.post("/sessions")
async def create_session(req: SessionCreateRequest):
    sess = session_manager.create_session(
        provider=req.provider or "local",
        model=req.model,
        system_prompt=req.system_prompt,
    )
    if req.title:
        sess.metadata["title"] = req.title
        session_manager.save_to_disk(sess.session_id)
    return sess.model_dump(exclude={"messages": {"__all__": {"raw_content"}}})


@api_router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    _check_session_id(session_id)
    sess = session_manager.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess.model_dump(exclude={"messages": {"__all__": {"raw_content"}}})


@api_router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    _check_session_id(session_id)
    if not session_manager.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}


@api_router.post("/sessions/reset")
async def reset_session(session_id: Optional[str] = None):
    if session_id:
        _check_session_id(session_id)
        session_manager.clear_session(session_id)
    publish_activity("INFO", "session_reset", "Memória de contexto da sessão reiniciada")
    return {"status": "success", "message": "Session reset"}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@api_router.post("/chat")
async def sync_chat(req: ChatRequest):
    publish_activity("INFO", "user_prompt", "Nova mensagem recebida no chat")
    res = await agent_engine.run(
        user_prompt=req.prompt,
        session_id=req.session_id,
        provider=req.provider,
        model=req.model,
        temperature=req.temperature,
        api_key=req.api_key,
    )
    dump = res.model_dump()
    dump["response"] = dump["content"]
    return dump


@api_router.post("/chat/stream")
async def stream_chat(req: ChatRequest):
    publish_activity("INFO", "user_prompt", "Nova mensagem recebida no chat (stream)")

    async def event_generator():
        async for event in agent_engine.run_stream(
            user_prompt=req.prompt,
            session_id=req.session_id,
            provider=req.provider,
            model=req.model,
            temperature=req.temperature,
            api_key=req.api_key,
        ):
            yield _sse(event.event_type, event.data)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Tarefas (/api/v1/tasks)
# ---------------------------------------------------------------------------

def _get_task_or_404(task_id: str) -> Dict[str, Any]:
    task = _tasks_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@api_router.get("/tasks")
async def list_tasks():
    return sorted(_tasks_store.values(), key=lambda t: t["created_at"])


@api_router.post("/tasks", status_code=200)
async def create_task(req: TaskCreateRequest):
    if len(_tasks_store) >= config.MAX_TASKS:
        # Remove a tarefa finalizada mais antiga para abrir espaço.
        finished = sorted(
            (t for t in _tasks_store.values() if t["status"] in ("completed", "failed")),
            key=lambda t: t["updated_at"],
        )
        if not finished:
            raise HTTPException(status_code=409, detail="Limite de tarefas atingido. Exclua alguma tarefa.")
        _tasks_store.pop(finished[0]["task_id"], None)

    tid = f"task_{uuid.uuid4().hex[:12]}"
    now = _now()
    task_obj = {
        "task_id": tid,
        "id": tid,
        "title": req.title,
        "prompt": req.prompt,
        "status": req.status,
        "priority": req.priority,
        "target_files": req.target_files,
        "session_id": req.session_id,
        "created_at": now,
        "updated_at": now,
        "result_summary": None,
    }
    _tasks_store[tid] = task_obj
    _save_tasks()
    publish_activity("INFO", "task_created", "Tarefa criada", {"task_id": tid, "status": req.status})
    return task_obj


@api_router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    return _get_task_or_404(task_id)


@api_router.put("/tasks/{task_id}")
@api_router.patch("/tasks/{task_id}")
async def update_task(task_id: str, req: TaskUpdateRequest):
    t = _get_task_or_404(task_id)
    for field in ("title", "prompt", "status", "priority", "target_files", "result_summary"):
        value = getattr(req, field)
        if value is not None:
            t[field] = value
    t["updated_at"] = _now()
    _save_tasks()
    publish_activity("INFO", "task_updated", "Tarefa atualizada", {"task_id": task_id, "status": t["status"]})
    return t


@api_router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    _get_task_or_404(task_id)
    del _tasks_store[task_id]
    _save_tasks()
    publish_activity("WARN", "task_deleted", "Tarefa excluída", {"task_id": task_id})
    return {"status": "deleted", "task_id": task_id}


async def _execute_task(task_id: str, provider: Optional[str], model: Optional[str], api_key: Optional[str]) -> None:
    t = _tasks_store.get(task_id)
    if t is None:
        return
    _running_tasks.add(task_id)
    try:
        res = await agent_engine.run(
            user_prompt=t["prompt"],
            session_id=t.get("session_id") or f"task_{task_id[-12:]}",
            provider=provider,
            model=model,
            api_key=api_key,
        )
        tool_errors = [tc for tc in res.tool_calls if tc.get("status") != "success"]
        summary = res.content or "Tarefa concluída."
        if tool_errors:
            summary += f"\n\n({len(tool_errors)} chamada(s) de ferramenta com erro.)"
        t["result_summary"] = summary[:20000]
        t["status"] = "completed"
        t["provider_used"] = res.provider_used
        publish_activity("SUCCESS", "task_completed", "Tarefa concluída", {"task_id": task_id, "status": "completed"})
    except Exception as exc:
        logger.exception("Falha ao executar a tarefa %s", task_id)
        t["status"] = "failed"
        t["result_summary"] = f"Falha na execução: {type(exc).__name__}"
        publish_activity("ERROR", "task_failed", "Falha ao executar tarefa", {"task_id": task_id, "status": "failed"})
    finally:
        _running_tasks.discard(task_id)
        t["updated_at"] = _now()
        _save_tasks()


@api_router.post("/tasks/{task_id}/run")
async def run_task(task_id: str, background_tasks: BackgroundTasks, req: Optional[TaskRunRequest] = None):
    t = _get_task_or_404(task_id)
    if task_id in _running_tasks:
        raise HTTPException(status_code=409, detail="A tarefa já está em execução.")
    t["status"] = "in_progress"
    t["result_summary"] = None
    t["updated_at"] = _now()
    _save_tasks()
    publish_activity("INFO", "task_start", "Executando tarefa", {"task_id": task_id, "status": "in_progress"})

    req = req or TaskRunRequest()
    background_tasks.add_task(_execute_task, task_id, req.provider, req.model, req.api_key)
    return {"status": "initiated", "task_id": task_id}


# ---------------------------------------------------------------------------
# Stream de atividade (/api/v1/stream/activity)
# ---------------------------------------------------------------------------

@api_router.get("/stream/activity")
async def activity_stream(request: Request):
    if len(_activity_subscribers) >= MAX_SUBSCRIBERS:
        raise HTTPException(status_code=503, detail="Muitas conexões abertas.")

    async def activity_generator():
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        _activity_subscribers.add(q)
        welcome = json.dumps({
            "event_id": f"evt_{uuid.uuid4().hex[:8]}",
            "timestamp": _now(),
            "level": "INFO",
            "event_type": "system_connect",
            "message": "Connected to NeuroCore Activity Stream",
            "payload": {},
        })
        try:
            yield f"retry: 5000\ndata: {welcome}\n\n"
            idle = 0.0
            deadline = time.monotonic() + max(1, config.SSE_MAX_SECONDS)
            while time.monotonic() < deadline:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=min(1.0, max(0.05, deadline - time.monotonic())))
                except asyncio.TimeoutError:
                    idle += 1.0
                    if idle >= 15:
                        idle = 0.0
                        yield ": ping\n\n"  # mantém a conexão viva atrás de proxies
                    continue
                idle = 0.0
                yield f"data: {msg}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _activity_subscribers.discard(q)

    return StreamingResponse(
        activity_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
