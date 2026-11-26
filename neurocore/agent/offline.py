"""
Provedor local (simulado, sem rede).

Não é um modelo de linguagem: reconhece pedidos simples, em português ou
inglês, e aciona as ferramentas correspondentes. Serve para usar a interface
sem chave de API e para os testes automatizados.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

from neurocore.agent.schema import (
    BaseLLMProvider,
    Message,
    ModelResponse,
    ProviderConfig,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    tool_dict,
)
from neurocore.attachments import Attachment, read_document_text, read_image_bytes
from neurocore.config import get_settings

_PATH_RE = r"['\"]?([\w\/\.\-]+)['\"]?"
_FILE_TOKEN_RE = re.compile(r"[\w\-\/\.]*[\w\-]\.[A-Za-z0-9]{1,8}\b")

# Palavras que podem aparecer dentro de uma expressão matemática.
_MATH_WORDS = {
    "x", "sin", "sen", "cos", "tan", "tg", "sqrt", "log", "ln", "exp", "abs", "pi",
    "asin", "acos", "atan", "arcsen", "arcsin", "arccos", "arctg", "arctan", "floor", "ceil",
    "round", "factorial", "comb", "perm", "mdc", "mmc", "gcd", "lcm", "root", "cbrt",
}
_CONNECTOR_AFTER_PERCENT = {"de", "do", "da", "of"}

_DISCIPLINES = [
    (r"matem[áa]tica|fun[çc][ãa]o|geometria|probabilidade|estat[íi]stica|porcentagem", "matematica"),
    (r"humanas|hist[óo]ria|geografia|filosofia|sociologia", "humanas"),
    (r"natureza|f[íi]sica|qu[íi]mica|biologia", "natureza"),
    (r"linguagens|portugu[êe]s|literatura|ingl[êe]s|espanhol|artes", "linguagens"),
]

_BLOCKED_TOOL_MESSAGES = {
    "generate_image": (
        "Para gerar imagens eu preciso de uma chave da OpenAI, do Google Gemini ou do OpenRouter. "
        "Configure em **Conexão** (canto superior) e peça de novo."
    ),
}

_COLOR_NAMES = [
    ((0, 0, 0), "preto"), ((255, 255, 255), "branco"), ((128, 128, 128), "cinza"),
    ((200, 30, 30), "vermelho"), ((240, 140, 20), "laranja"), ((240, 220, 40), "amarelo"),
    ((40, 160, 60), "verde"), ((40, 90, 200), "azul"), ((130, 60, 180), "roxo"),
    ((235, 120, 170), "rosa"), ((120, 80, 40), "marrom"), ((40, 180, 190), "ciano"),
]


def _take_math_prefix(text: str) -> str:
    """Pega o trecho inicial que parece matemática ("x² - 4x + 3 e mostre" -> "x² - 4x + 3")."""
    out: List[str] = []
    last_symbol = ""
    for match in re.finditer(r"[A-Za-zÀ-ÿ_]+|\s+|.", text):
        tok = match.group(0)
        if re.fullmatch(r"[A-Za-zÀ-ÿ_]+", tok):
            low = tok.lower()
            if not (low in _MATH_WORDS or (low in _CONNECTOR_AFTER_PERCENT and last_symbol == "%")):
                break
        elif tok in ";?!:\n" or (tok == "," and not re.search(r"\(\s*[^)]*$", "".join(out))):
            break
        out.append(tok)
        if tok.strip():
            last_symbol = tok
    return "".join(out).strip().rstrip(".,")


def _br_numbers(text: str) -> str:
    """1.234,56 -> 1234.56 ; 2,5 -> 2.5 (fora de chamadas de função)."""
    text = re.sub(r"R\$\s*", "", text)
    text = re.sub(r"\b\d{1,3}(?:\.\d{3})+(?:,\d+)?\b", lambda m: m.group(0).replace(".", "").replace(",", "."), text)
    if "(" not in text:
        text = re.sub(r"(\d),(\d)", r"\1.\2", text)
    return text


def _function_expressions(text: str) -> List[Tuple[str, str]]:
    """Encontra funções no pedido: [("f", "x² - 4x + 3"), ...]."""
    found: List[Tuple[str, str]] = []
    for match in re.finditer(r"(?:\b([fghpq])\s*\(\s*x\s*\)|\b(y))\s*=\s*", text):
        expr = _take_math_prefix(text[match.end():])
        if expr and "x" in expr:
            found.append((match.group(1) or "y", expr))
    if not found:
        after = re.search(r"\b(?:gr[áa]fico\s+(?:da\s+fun[çc][ãa]o\s+|de\s+)?|plote\s+|plot\s+|esboce\s+)(.+)", text, re.IGNORECASE)
        if after:
            expr = _take_math_prefix(after.group(1))
            if expr and "x" in expr:
                found.append(("f", expr))
    return found[:4]


def _nearest_color_name(rgb: tuple) -> str:
    return min(_COLOR_NAMES, key=lambda item: sum((a - b) ** 2 for a, b in zip(item[0], rgb)))[1]


def _image_summary(att: Attachment) -> str:
    size = f"{att.width}×{att.height} px" if att.width and att.height else "tamanho desconhecido"
    orientation = ""
    if att.width and att.height:
        orientation = "horizontal" if att.width > att.height * 1.1 else "vertical" if att.height > att.width * 1.1 else "quadrada"
    colors = ""
    data = read_image_bytes(att)
    if data:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as img:
                small = img.convert("RGB")
                small.thumbnail((64, 64))
                quant = small.quantize(colors=4)
                palette = quant.getpalette() or []
                counts = sorted(quant.getcolors() or [], reverse=True)
                names: List[str] = []
                for _, index in counts:
                    rgb = tuple(palette[index * 3:index * 3 + 3])
                    name = _nearest_color_name(rgb)
                    if name not in names:
                        names.append(name)
                colors = ", ".join(names[:3])
        except Exception:
            colors = ""
    parts = [size]
    if orientation:
        parts.append(orientation)
    if colors:
        parts.append(f"tons predominantes: {colors}")
    return f"- **{att.name}** (imagem): " + ", ".join(parts) + "."


def _document_summary(att: Attachment) -> str:
    details = []
    if att.pages:
        details.append(f"{att.pages} página(s)")
    if att.text_chars is not None:
        details.append(f"{att.text_chars} caracteres de texto")
    preview = read_document_text(att).strip()
    preview = re.sub(r"--- Página \d+ ---\s*", "", preview)
    preview = re.sub(r"\s+", " ", preview)[:280]
    line = f"- **{att.name}** (documento{': ' + ', '.join(details) if details else ''})."
    if preview:
        line += f"\n  > {preview}{'…' if len(preview) >= 280 else ''}"
    return line


class OfflineSimulatedProvider(BaseLLMProvider):
    name = "local"

    def __init__(self):
        super().__init__(api_key="local_simulated", default_model=get_settings().default_model_local)

    def is_available(self) -> bool:
        return True

    @staticmethod
    def _tool_names(tools: List[Union[ToolDefinition, Dict[str, Any]]]) -> Optional[set]:
        if not tools:
            return None  # sem lista = sem restrição
        return {tool_dict(t)["name"] for t in tools}

    # ------------------------------------------------------------------
    # Reconhecimento de intenção
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_intent(user_text: str) -> List[ToolCall]:
        text = user_text or ""
        user_lower = text.lower()

        # ENEM ---------------------------------------------------------------
        if re.search(r"\bquest(?:ão|ao|ões|oes)\b", user_lower) and "enem" in user_lower:
            args: Dict[str, Any] = {}
            for pattern, discipline in _DISCIPLINES:
                if re.search(pattern, user_lower):
                    args["discipline"] = discipline
                    break
            year = re.search(r"\b(20[0-3]\d)\b", user_lower)
            if year and 2009 <= int(year.group(1)) <= 2030:
                args["year"] = int(year.group(1))
            return [ToolCall(name="enem_question", arguments=args)]

        # Gráficos -------------------------------------------------------------
        if re.search(r"\b(gr[áa]fico|plot[ae]?r?|plote|esboce|esbo[çc]o)\b", user_lower):
            found = _function_expressions(text)
            if found:
                labels = [f"y = {e}" if name == "y" else f"{name}(x) = {e}" for name, e in found]
                return [ToolCall(name="create_chart", arguments={
                    "chart_type": "function",
                    "title": " e ".join(labels),
                    "functions": [{"expression": e, "label": l} for (_, e), l in zip(found, labels)],
                })]

        # Geração de imagem ----------------------------------------------------
        if re.search(r"\b(gere|gerar|crie|criar|desenhe|desenhar|fa[çc]a|ilustre|generate|draw)\b.{0,40}\b(imagem|ilustra[çc][ãa]o|desenho|figura|logo|logotipo|arte|image|picture)\b", user_lower):
            return [ToolCall(name="generate_image", arguments={"prompt": text.strip()})]

        # Calculadora ----------------------------------------------------------
        calc = re.search(r"\b(?:calcule|calcular|calcula|quanto\s+(?:é|e|dá|da|vale)|resolva\s+a\s+conta|compute|calculate)\b\s*:?\s*(.+)", text, re.IGNORECASE)
        if calc:
            expr = _take_math_prefix(_br_numbers(calc.group(1)))
            expr = re.sub(r"(\d+)\.0+(?!\d)", r"\1", expr)  # 2350.00 -> 2350
            if re.search(r"\d", expr) and re.search(r"[-+*/^%²³√]|\b(?:sqrt|log|ln|sin|sen|cos|tan|tg|factorial|comb|perm)\b", expr):
                return [ToolCall(name="calculator", arguments={"expression": expr})]

        # Português ------------------------------------------------------------
        if re.search(r"\b(leia|ler|leio|mostre|mostrar|abra|abrir|exiba|exibir)\b", user_lower) and \
                not re.search(r"\b(crie|criar|escreva|escrever)\b", user_lower):
            file_match = _FILE_TOKEN_RE.search(text)
            if file_match:
                return [ToolCall(name="read_file", arguments={"path": file_match.group(0)})]
        if re.search(r"\b(liste|listar|listagem)\b", user_lower):
            dir_match = re.search(r"\b(?:pasta|diret[óo]rio|em)\s+['\"]?([\w\/\.\-]+)['\"]?", text, re.IGNORECASE)
            return [ToolCall(name="list_dir", arguments={"path": dir_match.group(1) if dir_match else "."})]
        if re.search(r"\b(procure|procurar|busque|buscar|pesquise|pesquisar|encontre|encontrar)\b", user_lower):
            ident = re.search(r"\b(?:fun[çc][ãa]o|function|def|classe|class|vari[áa]vel|termo|palavra)\s+['\"`]?([A-Za-z_][\w\.]*)", text, re.IGNORECASE)
            if not ident:
                ident = re.search(r"['\"`]([^'\"`]+)['\"`]", text) or re.search(r"\b([A-Za-z]+_[\w]+)\b", text)
            if ident:
                return [ToolCall(name="grep_search", arguments={"pattern": ident.group(1), "path": "."})]
        if re.search(r"\b(crie|criar|escreva|escrever|gere|gerar)\b", user_lower):
            file_match = _FILE_TOKEN_RE.search(text)
            if file_match:
                content_match = re.search(r"(?:com o conte[úu]do|conte[úu]do)\s+['\"](.*?)['\"]", text, re.IGNORECASE)
                content = content_match.group(1) if content_match else "# Arquivo criado pelo NeuroCore (modo local)\n"
                return [ToolCall(name="write_file", arguments={"path": file_match.group(0), "content": content})]
        if "pytest" in user_lower and re.search(r"\b(execute|executar|rode|rodar|teste|testes)\b", user_lower):
            return [ToolCall(name="run_command", arguments={"command": "python -m pytest -q"})]
        pt_cmd = re.search(r"\b(?:execute|executar|rode|rodar)\s+(?:o\s+)?(?:comando\s+)?['\"`](.+?)['\"`]", text, re.IGNORECASE)
        if pt_cmd:
            return [ToolCall(name="run_command", arguments={"command": pt_cmd.group(1)})]

        # Inglês ---------------------------------------------------------------
        match_read = re.search(r"(?:read|view|show|check|cat)\s+(?:file\s+)?" + _PATH_RE, text, re.IGNORECASE)
        if match_read and "write" not in user_lower and "create" not in user_lower:
            return [ToolCall(name="read_file", arguments={"path": match_read.group(1).strip("'\"")})]

        if re.search(r"\b(?:list|ls|dir|directory)\b", text, re.IGNORECASE):
            match_dir = re.search(r"(?:in|path|dir|directory)\s+" + _PATH_RE, text, re.IGNORECASE)
            dir_path = match_dir.group(1).strip("'\"") if match_dir else "."
            return [ToolCall(name="list_dir", arguments={"path": dir_path})]

        match_grep = re.search(r"(?:grep|search|find)\s+(?:for\s+)?['\"]?([\w\.\-]+)['\"]?", text, re.IGNORECASE)
        if match_grep:
            return [ToolCall(name="grep_search", arguments={"pattern": match_grep.group(1).strip("'\""), "path": "."})]

        match_write = re.search(r"(?:create|write|make)\s+(?:file\s+)?" + _PATH_RE, text, re.IGNORECASE)
        if match_write:
            match_content = re.search(r"with\s+(?:content\s+)?['\"](.*?)['\"]", text, re.IGNORECASE)
            content = match_content.group(1) if match_content else "# Generated content\n"
            return [ToolCall(name="write_file", arguments={"path": match_write.group(1).strip("'\""), "content": content})]

        match_cmd = re.search(r"(?:run|exec|execute|pytest|command)\s+(.*)", text, re.IGNORECASE)
        if match_cmd:
            cmd = match_cmd.group(1).strip().strip("'\"")
            if cmd.lower().startswith("command "):
                cmd = cmd[8:].strip("'\"")
            if cmd:
                return [ToolCall(name="run_command", arguments={"command": cmd})]
        return []

    # ------------------------------------------------------------------
    # Respostas
    # ------------------------------------------------------------------

    @staticmethod
    def _summarize_tools(messages: List[Message], model: str) -> ModelResponse:
        tail: List[Message] = []
        for msg in reversed(messages):
            if msg.role != "tool":
                break
            tail.append(msg)
        tail.reverse()

        sections: List[str] = []
        for msg in tail:
            name = msg.name or "ferramenta"
            content = msg.content or ""
            if msg.is_error:
                sections.append(f"A ferramenta `{name}` falhou:\n{content[:600]}")
                continue
            try:
                data = json.loads(content)
            except (ValueError, TypeError):
                data = None

            if name == "calculator" and isinstance(data, dict) and "result" in data:
                line = f"**{data.get('expression', '')} = {data.get('result_br', data['result'])}**"
                if data.get("fraction"):
                    line += f"  (fração: {data['fraction']})"
                sections.append(line)
            elif name == "create_chart" and isinstance(data, dict):
                text = "Pronto! O gráfico está logo acima."
                analysis = data.get("analysis") or []
                if analysis:
                    text += "\n\n" + "\n".join(f"- {item}" for item in analysis)
                sections.append(text)
            elif name == "generate_image" and isinstance(data, dict):
                sections.append("Pronto! A imagem gerada está logo acima.")
            elif name == "enem_question" and isinstance(data, dict) and data.get("question_text"):
                question = data["question_text"]
                note = ""
                if data.get("images_total"):
                    note = "\n\n_As figuras da questão aparecem acima._"
                sections.append(f"{question}{note}\n\nQual alternativa você marcaria?")
            else:
                snippet = content[:1500]
                more = "\n..." if len(content) > 1500 else ""
                sections.append(
                    f"Operation completed successfully (`{name}`). Output snippet:\n```\n{snippet}{more}\n```"
                )
        return ModelResponse(content="\n\n".join(sections) or "Pronto.", provider="local", model=model)

    @staticmethod
    def _describe_attachments(attachments: List[Attachment]) -> str:
        lines = [_image_summary(a) if a.kind == "image" else _document_summary(a) for a in attachments]
        plural = "s" if len(attachments) > 1 else ""
        return (
            f"Recebi {len(attachments)} anexo{plural}:\n\n" + "\n".join(lines) + "\n\n"
            "Estou no **modo local (simulado)**, que não interpreta o conteúdo de imagens e documentos. "
            "Para resolver exercícios a partir de fotos, ler prints, analisar gráficos ou corrigir redações "
            "manuscritas, escolha em **Conexão** um provedor com visão (Google Gemini, OpenAI ou Anthropic Claude) "
            "e envie de novo."
        )

    async def generate(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> ModelResponse:
        model = self._model(config)

        if not messages:
            return ModelResponse(content="Olá! Sou o NeuroCore em modo local (simulado).", provider="local", model=model)

        if messages[-1].role == "tool":
            return self._summarize_tools(messages, model)

        user_msg: Optional[Message] = None
        for m in reversed(messages):
            if m.role == "user":
                user_msg = m
                break
        user_text = (user_msg.content or "") if user_msg else ""
        user_lower = user_text.lower()

        tool_calls = self._detect_intent(user_text)

        if user_msg is not None and user_msg.attachments and not tool_calls:
            return ModelResponse(content=self._describe_attachments(user_msg.attachments), provider="local", model=model)

        is_greeting = re.search(r"\b(hello|hi|hey|ol[áa]|oi|bom dia|boa tarde|boa noite)\b", user_lower)
        if not tool_calls and is_greeting and len(user_lower.split()) <= 6:
            return ModelResponse(
                content=(
                    "Hello! I am NeuroCore Code Agent in local simulated fallback mode.\n\n"
                    "Olá! Estou no modo local (simulado). Posso ler, listar e buscar arquivos do workspace, "
                    "fazer contas, desenhar gráficos de funções e buscar questões do ENEM. Para conversar com "
                    "uma IA de verdade (e analisar imagens), configure um provedor e uma chave de API em **Conexão**."
                ),
                provider="local",
                model=model,
            )

        allowed = self._tool_names(tools)
        if allowed is not None:
            blocked = [tc for tc in tool_calls if tc.name not in allowed]
            tool_calls = [tc for tc in tool_calls if tc.name in allowed]
            if blocked and not tool_calls:
                name = blocked[0].name
                message = _BLOCKED_TOOL_MESSAGES.get(name) or (
                    f"A ferramenta `{name}` está desativada neste servidor por segurança. "
                    "Ative-a com NEUROCORE_ENABLE_DANGEROUS_TOOLS=true em uma instalação local."
                )
                return ModelResponse(content=message, provider="local", model=model)

        if tool_calls:
            return ModelResponse(content=None, tool_calls=tool_calls, finish_reason="tool_calls", provider="local", model=model)

        default_reply = (
            "NeuroCore (modo local simulado): não entendi um pedido de ferramenta nesta mensagem.\n\n"
            "Neste modo eu reconheço pedidos como:\n"
            "- `Leia o arquivo README.md`\n"
            "- `Liste os arquivos da pasta neurocore`\n"
            "- `Procure a função run_stream`\n"
            "- `Calcule 18% de 2350`\n"
            "- `Plote f(x) = x² - 4x + 3`\n"
            "- `Me traga uma questão de matemática do ENEM`\n\n"
            "Para respostas de verdade, escolha um provedor (Gemini, OpenAI, Anthropic ou OpenRouter) "
            "e informe sua chave de API em **Conexão**."
        )
        return ModelResponse(content=default_reply, provider="local", model=model)

    async def generate_stream(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> AsyncIterator[StreamEvent]:
        async for event in super().generate_stream(messages, tools, config):
            yield event
            await asyncio.sleep(0)
