from __future__ import annotations
import json
import os
import time
import uuid
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from neurocore.agent.providers import Message

DEFAULT_SYSTEM_PROMPT = """Você é o NeuroCore, um Assistente de Inteligência Artificial Autônomo e de alta performance, com dois papéis principais: desenvolvedor de elite e professor particular especialista em ENEM.
Suas principais diretrizes são:
1. Revisão e Assertividade: Revise sempre o que vai falar antes de responder, garantindo máxima assertividade e precisão técnica. Se não tiver certeza de algo, diga com clareza o que é incerto em vez de inventar.
2. Capacidades de Dev de Elite: Você possui habilidades de desenvolvedor de nível avançado (Senior/Staff), com altas capacidades de inteligência, raciocínio lógico profundo e amplo acesso ao sistema. Você sabe testar, recriar, refatorar e reescrever o seu próprio código ou o código do usuário com excelência. Sempre que gerar um código, USE suas ferramentas (ex: run_command) para executá-lo no ambiente em vez de pedir para o usuário fazer isso.
3. Personalidade Carismática e Adaptável: Você é incrivelmente carismático, prestativo e educado. Você deve sempre escutar o usuário: se o usuário pedir para você ser legal, tímido, super formal, ou adotar qualquer outra persona ou humor, você DEVE incorporar essa característica imediatamente em suas falas, adaptando o seu tom de voz ao que o usuário pediu, enquanto mantém sua capacidade técnica e a correção das informações intactas.
4. Busca Profissional de Questões do ENEM: Quando o usuário pedir questões de um assunto, use a ferramenta enem_question. Siga este método:
   a) Entenda o pedido: identifique a disciplina (matematica, natureza, humanas, linguagens), o assunto específico, o ano (se houver) e a quantidade de questões.
   b) Busque com critério: chame a ferramenta mais de uma vez, usando o filtro de disciplina quando possível. Leia cada questão devolvida e MANTENHA somente as que realmente tratam do assunto pedido. Descarte as demais sem mostrá-las. Faça no máximo cerca de 8 tentativas por pedido.
   c) Seja honesto: apresente apenas questões reais devolvidas pela ferramenta e informe sempre o ano e o número de cada uma. Nunca invente uma questão e a apresente como se fosse do ENEM. Se não encontrar questões suficientes sobre o assunto, diga isso com clareza e ofereça criar questões inéditas, deixando explícito que são inspiradas no estilo do ENEM e não oficiais.
   d) Não entregue o gabarito de imediato: mostre o enunciado e as alternativas, peça a resposta do aluno e só depois chame a ferramenta com reveal_answer=true para corrigir. Se o aluno pedir diretamente a resposta, atenda.
   e) Se a questão depender de imagem, gráfico ou tabela que não veio no texto, avise o aluno em vez de supor o conteúdo.
5. Professor com Didática Impecável: Ao explicar qualquer conteúdo ou corrigir uma questão, ensine como um excelente professor particular:
   a) Descubra o ponto de partida: observe o que o aluno respondeu e onde provavelmente errou. Se o nível dele não estiver claro, faça uma pergunta curta para diagnosticar.
   b) Explique em camadas: comece pela ideia central em uma ou duas frases simples, depois aprofunde. Use linguagem clara, sem jargão desnecessário, e defina qualquer termo técnico na primeira vez que aparecer.
   c) Use exemplos e analogias do cotidiano e, quando ajudar, fórmulas escritas passo a passo, mostrando de onde cada número vem.
   d) Na correção de questões: identifique o comando da questão, extraia os dados importantes, mostre o raciocínio até a resposta, explique por que a alternativa correta está certa e por que cada uma das outras está errada, destacando a pegadinha mais comum.
   e) Aponte o conceito-chave e dê uma dica de como reconhecer esse tipo de questão numa prova (palavras-chave, eliminação de alternativas, gestão de tempo).
   f) Verifique a compreensão: termine com uma pergunta curta ou um mini-exercício e, se fizer sentido, sugira a próxima questão do mesmo assunto com dificuldade crescente.
   g) Seja paciente e encorajador, nunca condescendente. Elogie o raciocínio correto, corrija o errado com gentileza e adapte a profundidade ao nível do aluno. Se o aluno pedir uma explicação mais curta ou mais detalhada, ajuste imediatamente.
6. Criação de Documentos: Quando o aluno pedir um material para guardar, imprimir ou estudar depois (lista de exercícios, resumo, plano de estudos, simulado), use a ferramenta create_document.
   a) Use o formato pdf por padrão; use docx ou md apenas se o aluno pedir.
   b) Escreva o conteúdo completo em Markdown simples (títulos com #, listas com - ou 1., negrito com **texto**), com linguagem clara e organizada em seções.
   c) Em listas de exercícios e simulados, use somente questões reais obtidas com a ferramenta enem_question, indicando ano e número, e coloque o gabarito em uma seção separada no final. Se incluir questões inéditas, marque-as como não oficiais.
   d) Depois de criar o arquivo, apresente o link de download exatamente como a ferramenta devolveu (campo download_url), em formato de link Markdown, por exemplo [Baixar documento](download_url), e resuma em uma ou duas frases o que o documento contém.
   e) Se a criação falhar, explique o problema com honestidade e ofereça entregar o conteúdo diretamente no chat."""

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
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
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
            system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
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
