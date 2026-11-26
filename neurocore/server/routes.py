import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import time
import uuid
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import unquote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from neurocore.agent.engine import AgentEngine, PromptOptions
from neurocore.agent.memory import SESSION_ID_RE, Session, SessionManager, session_title
from neurocore.agent.prompts import RESPONSE_STYLES, valid_timezone
from neurocore.agent.providers import ProviderManager
from neurocore.attachments import (
    ATTACHMENT_ID_RE,
    Attachment,
    AttachmentError,
    delete_owned_attachments,
    get_attachment,
    get_attachment_file,
    save_upload,
)
from neurocore.config import config
from neurocore.tools.context import ToolContext
from neurocore.tools.document_tools import get_document_path
from neurocore.tools.executor import DANGEROUS_TOOL_NAMES, ToolExecutor
from neurocore.tools.image_tools import resolve_image_backend

logger = logging.getLogger("neurocore.routes")
api_router = APIRouter()

VERSION = "1.2.0"

# Instâncias globais do motor
provider_manager = ProviderManager()
session_manager = SessionManager(
    storage_dir=config.SESSIONS_DIR,
    max_context_tokens=config.MAX_CONTEXT_TOKENS,
    max_sessions=config.MAX_SESSIONS,
)
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
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
MESSAGE_ID_RE = re.compile(r"^[a-f0-9]{8,32}$")
TaskStatus = Literal["backlog", "in_progress", "completed", "failed"]
TaskPriority = Literal["low", "medium", "high", "critical"]
ResponseStyle = Literal["balanced", "concise", "detailed", "didactic"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Dono (cada navegador tem um identificador aleatório próprio)
# ---------------------------------------------------------------------------

def request_owner(request: Request) -> Optional[str]:
    """Hash do cabeçalho X-Client-Id. Conversas e tarefas ficam isoladas por dono."""
    client_id = (request.headers.get("x-client-id") or "").strip()
    if not client_id:
        return None
    if not CLIENT_ID_RE.fullmatch(client_id):
        raise HTTPException(status_code=400, detail="Cabeçalho X-Client-Id inválido.")
    return hashlib.sha256(f"neurocore-client:{client_id}".encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Tarefas: persistência
# ---------------------------------------------------------------------------

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

    O stream é visto por todos: não inclua texto dos usuários nem identificadores.
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
    prompt: str = Field(default="", max_length=config.MAX_PROMPT_CHARS, description="User instruction")
    attachments: List[str] = Field(default_factory=list, max_length=config.MAX_ATTACHMENTS_PER_MESSAGE)
    provider: Optional[str] = Field(default=None, max_length=40)
    model: Optional[str] = Field(default=None, max_length=200)
    api_key: Optional[str] = Field(default=None, max_length=400)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=128000)
    stream: bool = False
    user_name: Optional[str] = Field(default=None, max_length=80)
    response_style: Optional[ResponseStyle] = None
    custom_instructions: Optional[str] = Field(default=None, max_length=4000)
    timezone: Optional[str] = Field(default=None, max_length=64)

    @field_validator("attachments")
    @classmethod
    def _valid_ids(cls, value: List[str]) -> List[str]:
        for item in value:
            if not ATTACHMENT_ID_RE.fullmatch(item or ""):
                raise ValueError("Identificador de anexo inválido.")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _needs_content(self) -> "ChatRequest":
        if not self.prompt.strip() and not self.attachments:
            raise ValueError("Envie uma mensagem ou um anexo.")
        return self


class SessionCreateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    system_prompt: Optional[str] = Field(default=None, max_length=20000)
    provider: Optional[str] = Field(default="local", max_length=40)
    model: Optional[str] = Field(default=None, max_length=200)


class SessionUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class RewindRequest(BaseModel):
    message_id: str = Field(..., pattern=MESSAGE_ID_RE.pattern)


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
    tools = _enabled_tools()
    return {
        "status": "ok",
        "version": VERSION,
        "service": "neurocore-agent",
        "workspace_root": os.path.basename(config.WORKSPACE_ROOT.rstrip(os.sep)) or config.WORKSPACE_ROOT,
        "providers": avail,
        "active_sessions_count": len(session_manager.list_sessions()),
        "uptime_seconds": round(time.time() - _start_time, 2),
        "auth_required": bool(config.ACCESS_TOKEN),
        "public_mode": config.PUBLIC_MODE,
        "dangerous_tools_enabled": any(name in DANGEROUS_TOOL_NAMES for name in tools),
        "default_provider": config.DEFAULT_PROVIDER,
        "features": {
            "uploads": True,
            "image_generation": "generate_image" in tools,
            "server_image_generation": "generate_image" in tools and resolve_image_backend(ToolContext()) is not None,
            "charts": "create_chart" in tools,
        },
        "limits": {
            "max_upload_bytes": config.MAX_UPLOAD_BYTES,
            "max_attachments_per_message": config.MAX_ATTACHMENTS_PER_MESSAGE,
            "max_prompt_chars": config.MAX_PROMPT_CHARS,
        },
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
# Anexos
# ---------------------------------------------------------------------------

@api_router.post("/uploads")
async def upload_file(request: Request):
    """Recebe o arquivo no corpo da requisição (bytes crus).

    Cabeçalhos: Content-Type (informativo) e X-Filename (nome, codificado
    com encodeURIComponent). O tipo real é detectado pelo conteúdo.
    """
    owner = request_owner(request)
    limit = config.MAX_UPLOAD_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=f"Arquivo grande demais (máximo {limit // (1024 * 1024)} MB).")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail=f"Arquivo grande demais (máximo {limit // (1024 * 1024)} MB).")
    if not body:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")
    filename = unquote(request.headers.get("x-filename") or "")[:255]
    content_type = request.headers.get("content-type") or ""
    try:
        att = await asyncio.to_thread(save_upload, bytes(body), filename, content_type, owner)
    except AttachmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return att.to_public()


@api_router.get("/files/{attachment_id}")
async def get_file(attachment_id: str, download: bool = False):
    """Serve um anexo. Os ids são aleatórios (128 bits) e funcionam como link privado."""
    found = get_attachment_file(attachment_id)
    if not found:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    att, path = found
    cache = {"Cache-Control": "private, max-age=31536000, immutable"}
    if att.kind == "image":
        return FileResponse(
            path,
            media_type=att.mime_type,
            filename=att.name,
            content_disposition_type="attachment" if download else "inline",
            headers=cache,
        )
    text_name = os.path.splitext(att.name)[0] + ".txt"
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=text_name,
                        content_disposition_type="attachment", headers=cache)


# ---------------------------------------------------------------------------
# Sessões
# ---------------------------------------------------------------------------

def _check_session_id(session_id: str) -> None:
    if not SESSION_ID_RE.fullmatch(session_id or ""):
        raise HTTPException(status_code=404, detail="Session not found")


def _owned_session(session_id: str, owner: Optional[str]) -> Session:
    """Sessão do dono. Sessões sem dono (criadas sem X-Client-Id) valem como link privado."""
    _check_session_id(session_id)
    sess = session_manager.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    sess_owner = SessionManager.owner_of(sess)
    if sess_owner and sess_owner != owner:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


def _session_payload(sess: Session) -> Dict[str, Any]:
    data = sess.model_dump(exclude={"messages": {"__all__": {"raw_content"}}})
    data["title"] = session_title(sess)
    data["metadata"] = {k: v for k, v in data.get("metadata", {}).items() if k != "owner"}
    for msg in data.get("messages", []):
        for att in msg.get("attachments") or []:
            att["url"] = f"/api/v1/files/{att['id']}"
    return data


def _attachment_ids(sess: Session) -> List[str]:
    return [att.id for msg in sess.messages for att in (msg.attachments or [])]


@api_router.get("/sessions")
async def list_sessions(request: Request):
    # Só as conversas deste navegador (metadados; o conteúdo exige o ID).
    owner = request_owner(request)
    return [info.model_dump() for info in session_manager.list_sessions(owner=owner, filter_owner=True)]


@api_router.post("/sessions")
async def create_session(req: SessionCreateRequest, request: Request):
    owner = request_owner(request)
    sess = session_manager.create_session(
        provider=req.provider or "local",
        model=req.model,
        system_prompt=req.system_prompt,
    )
    if owner:
        sess.metadata["owner"] = owner
    if req.title:
        sess.metadata["title"] = req.title
    session_manager.save_to_disk(sess.session_id)
    return _session_payload(sess)


@api_router.delete("/sessions")
async def delete_all_sessions(request: Request):
    owner = request_owner(request)
    if not owner:
        raise HTTPException(status_code=400, detail="Envie o cabeçalho X-Client-Id.")
    removed = 0
    for info in session_manager.list_sessions(owner=owner, filter_owner=True):
        sess = session_manager.get_session(info.session_id)
        ids = _attachment_ids(sess) if sess else []
        if session_manager.delete_session(info.session_id):
            removed += 1
            delete_owned_attachments(ids, owner)
    return {"status": "deleted", "count": removed}


@api_router.post("/sessions/reset")
async def reset_session(request: Request, session_id: Optional[str] = None):
    if session_id:
        _owned_session(session_id, request_owner(request))
        session_manager.clear_session(session_id)
    publish_activity("INFO", "session_reset", "Memória de contexto da sessão reiniciada")
    return {"status": "success", "message": "Session reset"}


@api_router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    return _session_payload(_owned_session(session_id, request_owner(request)))


@api_router.patch("/sessions/{session_id}")
async def update_session(session_id: str, req: SessionUpdateRequest, request: Request):
    _owned_session(session_id, request_owner(request))
    session_manager.set_title(session_id, req.title)
    return {"status": "updated", "session_id": session_id, "title": session_manager.get_session(session_id).metadata.get("title")}


@api_router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    owner = request_owner(request)
    sess = _owned_session(session_id, owner)
    ids = _attachment_ids(sess)
    sess_owner = SessionManager.owner_of(sess)
    if not session_manager.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    delete_owned_attachments(ids, sess_owner)
    return {"status": "deleted", "session_id": session_id}


@api_router.post("/sessions/{session_id}/rewind")
async def rewind_session(session_id: str, req: RewindRequest, request: Request):
    """Apaga a mensagem indicada e as seguintes (usado para editar e regenerar)."""
    _owned_session(session_id, request_owner(request))
    removed = session_manager.rewind(session_id, req.message_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Mensagem não encontrada nesta conversa.")
    return {"status": "rewound", "removed": len(removed)}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _prepare_chat(req: ChatRequest, request: Request) -> Dict[str, Any]:
    owner = request_owner(request)
    if req.session_id and session_manager.get_session(req.session_id):
        _owned_session(req.session_id, owner)
    attachments: List[Attachment] = []
    for att_id in req.attachments:
        att = get_attachment(att_id, owner=owner)
        if att is None:
            raise HTTPException(status_code=400, detail="Anexo não encontrado. Envie o arquivo novamente.")
        attachments.append(att)
    style = req.response_style if req.response_style in RESPONSE_STYLES else None
    return {
        "user_prompt": req.prompt.strip(),
        "session_id": req.session_id,
        "provider": req.provider,
        "model": req.model,
        "temperature": req.temperature,
        "api_key": req.api_key,
        "attachments": attachments,
        "owner": owner,
        "prompt_options": PromptOptions(
            user_name=req.user_name,
            response_style=style,
            custom_instructions=req.custom_instructions,
            timezone=valid_timezone(req.timezone),
        ),
    }


@api_router.post("/chat")
async def sync_chat(req: ChatRequest, request: Request):
    params = _prepare_chat(req, request)
    publish_activity("INFO", "user_prompt", "Nova mensagem recebida no chat")
    res = await agent_engine.run(**params)
    dump = res.model_dump()
    dump["response"] = dump["content"]
    return dump


@api_router.post("/chat/stream")
async def stream_chat(req: ChatRequest, request: Request):
    params = _prepare_chat(req, request)
    publish_activity("INFO", "user_prompt", "Nova mensagem recebida no chat (stream)")

    async def event_generator():
        async for event in agent_engine.run_stream(**params):
            yield _sse(event.event_type, event.data)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Tarefas (/api/v1/tasks)
# ---------------------------------------------------------------------------

def _public_task(task: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in task.items() if k != "owner"}


def _get_task_or_404(task_id: str, owner: Optional[str]) -> Dict[str, Any]:
    task = _tasks_store.get(task_id)
    if task is None or task.get("owner") != owner:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@api_router.get("/tasks")
async def list_tasks(request: Request):
    owner = request_owner(request)
    tasks = [t for t in _tasks_store.values() if t.get("owner") == owner]
    return [_public_task(t) for t in sorted(tasks, key=lambda t: t["created_at"])]


@api_router.post("/tasks", status_code=200)
async def create_task(req: TaskCreateRequest, request: Request):
    owner = request_owner(request)
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
        "owner": owner,
    }
    _tasks_store[tid] = task_obj
    _save_tasks()
    publish_activity("INFO", "task_created", "Tarefa criada", {"status": req.status})
    return _public_task(task_obj)


@api_router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    return _public_task(_get_task_or_404(task_id, request_owner(request)))


@api_router.put("/tasks/{task_id}")
@api_router.patch("/tasks/{task_id}")
async def update_task(task_id: str, req: TaskUpdateRequest, request: Request):
    t = _get_task_or_404(task_id, request_owner(request))
    for field in ("title", "prompt", "status", "priority", "target_files", "result_summary"):
        value = getattr(req, field)
        if value is not None:
            t[field] = value
    t["updated_at"] = _now()
    _save_tasks()
    publish_activity("INFO", "task_updated", "Tarefa atualizada", {"status": t["status"]})
    return _public_task(t)


@api_router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, request: Request):
    _get_task_or_404(task_id, request_owner(request))
    del _tasks_store[task_id]
    _save_tasks()
    publish_activity("WARN", "task_deleted", "Tarefa excluída")
    return {"status": "deleted", "task_id": task_id}


async def _execute_task(
    task_id: str,
    provider: Optional[str],
    model: Optional[str],
    api_key: Optional[str],
    owner: Optional[str] = None,
) -> None:
    t = _tasks_store.get(task_id)
    if t is None:
        return
    _running_tasks.add(task_id)
    try:
        session_id = t.get("session_id") or f"task_{task_id[-12:]}"
        existing = session_manager.get_session(session_id)
        if existing and SessionManager.owner_of(existing) not in (None, owner):
            session_id = f"task_{uuid.uuid4().hex[:12]}"
        res = await agent_engine.run(
            user_prompt=t["prompt"],
            session_id=session_id,
            provider=provider,
            model=model,
            api_key=api_key,
            owner=owner,
        )
        tool_errors = [tc for tc in res.tool_calls if tc.get("status") != "success"]
        summary = res.content or "Tarefa concluída."
        if tool_errors:
            summary += f"\n\n({len(tool_errors)} chamada(s) de ferramenta com erro.)"
        t["result_summary"] = summary[:20000]
        t["status"] = "completed"
        t["provider_used"] = res.provider_used
        t["result_attachments"] = res.attachments[:8]
        publish_activity("SUCCESS", "task_completed", "Tarefa concluída", {"status": "completed"})
    except Exception as exc:
        logger.exception("Falha ao executar a tarefa %s", task_id)
        t["status"] = "failed"
        t["result_summary"] = f"Falha na execução: {type(exc).__name__}"
        publish_activity("ERROR", "task_failed", "Falha ao executar tarefa", {"status": "failed"})
    finally:
        _running_tasks.discard(task_id)
        t["updated_at"] = _now()
        _save_tasks()


@api_router.post("/tasks/{task_id}/run")
async def run_task(task_id: str, background_tasks: BackgroundTasks, request: Request, req: Optional[TaskRunRequest] = None):
    owner = request_owner(request)
    t = _get_task_or_404(task_id, owner)
    if task_id in _running_tasks:
        raise HTTPException(status_code=409, detail="A tarefa já está em execução.")
    t["status"] = "in_progress"
    t["result_summary"] = None
    t["updated_at"] = _now()
    _save_tasks()
    publish_activity("INFO", "task_start", "Executando tarefa", {"status": "in_progress"})

    req = req or TaskRunRequest()
    background_tasks.add_task(_execute_task, task_id, req.provider, req.model, req.api_key, owner)
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
