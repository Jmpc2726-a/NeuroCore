from __future__ import annotations
import json
import os
import re
import tempfile
import time
import uuid
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from neurocore.agent.prompts import SYSTEM_PROMPT_BASE, is_default_prompt
from neurocore.agent.schema import Message

# Compatibilidade: o prompt padrão agora é montado a cada chamada em
# neurocore/agent/prompts.py (parte fixa + contexto dinâmico).
DEFAULT_SYSTEM_PROMPT = SYSTEM_PROMPT_BASE

IMAGE_TOKEN_ESTIMATE = 1200
TITLE_MAX_CHARS = 60

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_valid_session_id(session_id: Optional[str]) -> bool:
    """IDs viram nomes de arquivo: só aceitamos letras, números, '_' e '-'."""
    return bool(session_id) and bool(SESSION_ID_RE.fullmatch(session_id))


class SessionInfo(BaseModel):
    session_id: str
    created_at: str
    updated_at: str
    provider: str
    model: str
    message_count: int
    estimated_tokens: int
    title: str = ""

class Session(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    updated_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    provider: str = "local"
    model: str = ""
    # Vazio = prompt padrão do NeuroCore (montado dinamicamente a cada chamada).
    system_prompt: str = ""
    messages: List[Message] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


def estimate_message_tokens(message: Message) -> int:
    """Heuristic token counter: 1 token ≈ 4 characters + 4 overhead tokens."""
    tokens = 4
    if message.content:
        tokens += len(message.content) // 4
    if message.tool_calls:
        for tc in message.tool_calls:
            tokens += len(tc.name) // 4 + len(json.dumps(tc.arguments)) // 4 + 8
    for att in message.attachments or []:
        if att.kind == "image":
            tokens += IMAGE_TOKEN_ESTIMATE
        else:
            tokens += (att.text_chars or 0) // 4 + 20
    return max(1, tokens)


def effective_prompt_for_budget(system_prompt: str) -> str:
    """Texto usado para estimar o custo do prompt de sistema no orçamento de contexto."""
    return SYSTEM_PROMPT_BASE if is_default_prompt(system_prompt) else system_prompt


def make_title(text: Optional[str], attachments: Optional[list] = None) -> str:
    """Título curto a partir da primeira mensagem do usuário."""
    clean = re.sub(r"```.*?```", " ", text or "", flags=re.DOTALL)
    clean = re.sub(r"[#*_`>\[\]]+", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        if attachments:
            first = attachments[0]
            kind = "Imagem" if getattr(first, "kind", "") == "image" else "Arquivo"
            return f"{kind}: {getattr(first, 'name', '')}"[:TITLE_MAX_CHARS]
        return "Nova conversa"
    clean = clean[0].upper() + clean[1:]
    if len(clean) <= TITLE_MAX_CHARS:
        return clean
    cut = clean[:TITLE_MAX_CHARS].rsplit(" ", 1)[0]
    return (cut or clean[:TITLE_MAX_CHARS]).rstrip(",.;:") + "…"


def session_title(session: "Session") -> str:
    title = str(session.metadata.get("title") or "").strip()
    if title:
        return title
    for msg in session.messages:
        if msg.role == "user":
            return make_title(msg.content, msg.attachments)
    return "Nova conversa"


def estimate_session_tokens(messages: List[Message], system_prompt: str = "") -> int:
    total = len(system_prompt) // 4 + 4
    for msg in messages:
        total += estimate_message_tokens(msg)
    return total


def trim_messages(
    messages: List[Message],
    max_tokens: int = 32000,
    system_prompt: str = "",
) -> List[Message]:
    """
    Sliding window context truncation.
    - Preserves message order.
    - Ensures truncation boundary falls on a user turn (`role='user'`).
    - Avoids breaking tool execution turn sequences.
    """
    if not messages:
        return []

    total_tokens = estimate_session_tokens(messages, system_prompt)
    if total_tokens <= max_tokens:
        return messages

    trimmed = list(messages)
    while len(trimmed) > 1 and estimate_session_tokens(trimmed, system_prompt) > max_tokens:
        remove_count = 1
        while remove_count < len(trimmed) and trimmed[remove_count].role != "user":
            remove_count += 1
        trimmed = trimmed[remove_count:]

    return trimmed


class SessionManager:
    def __init__(
        self,
        storage_dir: Optional[str] = ".sessions",
        max_context_tokens: int = 96000,
        max_sessions: int = 500,
    ):
        self.storage_dir = str(storage_dir) if storage_dir else None
        self.max_context_tokens = max_context_tokens
        self.max_sessions = max_sessions
        self._sessions: Dict[str, Session] = {}

        if self.storage_dir:
            os.makedirs(self.storage_dir, exist_ok=True)
            self._load_all_from_disk()

    def _filepath(self, session_id: str) -> Optional[str]:
        if not self.storage_dir or not is_valid_session_id(session_id):
            return None
        return os.path.join(self.storage_dir, f"{session_id}.json")

    def _evict_if_needed(self) -> None:
        """Remove as sessões mais antigas quando o limite é ultrapassado."""
        if self.max_sessions <= 0 or len(self._sessions) <= self.max_sessions:
            return
        ordered = sorted(self._sessions.values(), key=lambda s: s.updated_at)
        for old in ordered[: len(self._sessions) - self.max_sessions]:
            self.delete_session(old.session_id)

    def create_session(
        self,
        session_id: Optional[str] = None,
        provider: str = "local",
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Session:
        if session_id is not None and not is_valid_session_id(session_id):
            raise ValueError("session_id inválido: use apenas letras, números, '_' ou '-' (máx. 64).")
        sid = session_id or str(uuid.uuid4())
        session = Session(
            session_id=sid,
            provider=provider or "local",
            model=model or "",
            system_prompt=system_prompt or "",
        )
        self._sessions[sid] = session
        self.save_to_disk(sid)
        self._evict_if_needed()
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def get_or_create_session(self, session_id: Optional[str] = None, provider: str = "local") -> Session:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self.create_session(session_id=session_id, provider=provider)

    def add_message(self, session_id: str, message: Message) -> Session:
        session = self.get_session(session_id)
        if not session:
            session = self.create_session(session_id=session_id)
        session.messages.append(message)
        session.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        session.messages = trim_messages(
            session.messages, self.max_context_tokens, effective_prompt_for_budget(session.system_prompt)
        )
        self.save_to_disk(session_id)
        return session

    def clear_session(self, session_id: str) -> bool:
        session = self.get_session(session_id)
        if not session:
            return False
        session.messages.clear()
        session.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.save_to_disk(session_id)
        return True

    def delete_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            filepath = self._filepath(session_id)
            if filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    pass
            return True
        return False

    @staticmethod
    def owner_of(session: Session) -> Optional[str]:
        owner = session.metadata.get("owner")
        return owner if isinstance(owner, str) and owner else None

    def list_sessions(self, owner: Optional[str] = None, filter_owner: bool = False) -> List[SessionInfo]:
        """Lista as sessões; com filter_owner, só as do dono (ou sem dono, se owner=None)."""
        infos = []
        for session in self._sessions.values():
            if filter_owner and self.owner_of(session) != owner:
                continue
            est_tokens = estimate_session_tokens(session.messages, effective_prompt_for_budget(session.system_prompt))
            infos.append(
                SessionInfo(
                    session_id=session.session_id,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                    provider=session.provider,
                    model=session.model,
                    message_count=len(session.messages),
                    estimated_tokens=est_tokens,
                    title=session_title(session),
                )
            )
        return sorted(infos, key=lambda x: x.updated_at, reverse=True)

    def set_title(self, session_id: str, title: str) -> bool:
        session = self.get_session(session_id)
        if not session:
            return False
        session.metadata["title"] = re.sub(r"\s+", " ", title).strip()[:120]
        self.save_to_disk(session_id)
        return True

    def rewind(self, session_id: str, message_id: str) -> Optional[List[Message]]:
        """Remove a mensagem indicada e todas as seguintes (para editar ou regenerar).

        Devolve as mensagens removidas, ou None se a mensagem não existir.
        """
        session = self.get_session(session_id)
        if not session:
            return None
        for index, msg in enumerate(session.messages):
            if msg.id == message_id:
                removed = session.messages[index:]
                session.messages = session.messages[:index]
                session.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self.save_to_disk(session_id)
                return removed
        return None

    def save_to_disk(self, session_id: str):
        filepath = self._filepath(session_id)
        session = self.get_session(session_id)
        if not filepath or not session:
            return
        # Escrita atômica: grava num arquivo temporário e renomeia.
        try:
            fd, tmp_path = tempfile.mkstemp(dir=self.storage_dir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(session.model_dump_json(indent=2))
            os.replace(tmp_path, filepath)
        except OSError:
            try:
                os.remove(tmp_path)  # type: ignore[possibly-undefined]
            except Exception:
                pass

    def _load_all_from_disk(self):
        if not self.storage_dir or not os.path.exists(self.storage_dir):
            return
        for fname in os.listdir(self.storage_dir):
            if not fname.endswith(".json") or not is_valid_session_id(fname[:-5]):
                continue
            fpath = os.path.join(self.storage_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    session = Session(**json.load(f))
                if is_valid_session_id(session.session_id):
                    self._sessions[session.session_id] = session
            except Exception:
                continue
