from __future__ import annotations
import json
import os
import time
import uuid
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from neurocore.agent.providers import Message

class SessionInfo(BaseModel):
    session_id: str
    created_at: str
    updated_at: str
    provider: str
    model: str
    message_count: int
    estimated_tokens: int

class Session(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    updated_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    provider: str = "gemini"
    model: str = "gemini-2.5-flash"
    system_prompt: str = "You are NeuroCore Code Agent, an autonomous AI coding assistant."
    messages: List[Message] = []
    metadata: Dict[str, Any] = Field(default_factory=dict)


def estimate_message_tokens(message: Message) -> int:
    """Heuristic token counter: 1 token ≈ 4 characters + 4 overhead tokens."""
    tokens = 4
    if message.content:
        tokens += len(message.content) // 4
    if message.tool_calls:
        for tc in message.tool_calls:
            tokens += len(tc.name) // 4 + len(json.dumps(tc.arguments)) // 4 + 8
    return max(1, tokens)


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
    def __init__(self, storage_dir: Optional[str] = ".sessions", max_context_tokens: int = 32000):
        self.storage_dir = storage_dir
        self.max_context_tokens = max_context_tokens
        self._sessions: Dict[str, Session] = {}

        if self.storage_dir:
            os.makedirs(self.storage_dir, exist_ok=True)
            self._load_all_from_disk()

    def create_session(
        self,
        session_id: Optional[str] = None,
        provider: str = "gemini",
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Session:
        sid = session_id or str(uuid.uuid4())
        session = Session(
            session_id=sid,
            provider=provider,
            model=model or "gemini-2.5-flash",
            system_prompt=system_prompt or "You are NeuroCore Code Agent, an autonomous AI coding assistant.",
        )
        self._sessions[sid] = session
        self.save_to_disk(sid)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def get_or_create_session(self, session_id: Optional[str] = None, provider: str = "gemini") -> Session:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self.create_session(session_id=session_id, provider=provider)

    def add_message(self, session_id: str, message: Message) -> Session:
        session = self.get_session(session_id)
        if not session:
            session = self.create_session(session_id=session_id)
        session.messages.append(message)
        session.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        session.messages = trim_messages(session.messages, self.max_context_tokens, session.system_prompt)
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
            if self.storage_dir:
                filepath = os.path.join(self.storage_dir, f"{session_id}.json")
                if os.path.exists(filepath):
                    os.remove(filepath)
            return True
        return False

    def list_sessions(self) -> List[SessionInfo]:
        infos = []
        for session in self._sessions.values():
            est_tokens = estimate_session_tokens(session.messages, session.system_prompt)
            infos.append(
                SessionInfo(
                    session_id=session.session_id,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                    provider=session.provider,
                    model=session.model,
                    message_count=len(session.messages),
                    estimated_tokens=est_tokens,
                )
            )
        return sorted(infos, key=lambda x: x.updated_at, reverse=True)

    def save_to_disk(self, session_id: str):
        if not self.storage_dir:
            return
        session = self.get_session(session_id)
        if session:
            filepath = os.path.join(self.storage_dir, f"{session_id}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(session.model_dump_json(indent=2))

    def _load_all_from_disk(self):
        if not self.storage_dir or not os.path.exists(self.storage_dir):
            return
        for fname in os.listdir(self.storage_dir):
            if fname.endswith(".json"):
                fpath = os.path.join(self.storage_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        session = Session(**data)
                        self._sessions[session.session_id] = session
                except Exception:
                    pass
