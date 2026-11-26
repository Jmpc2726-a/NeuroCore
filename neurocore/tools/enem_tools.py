"""
ENEM question tool for NeuroCore Agent.

Permite que o agente busque questões reais do ENEM (via ENEM API) para
o aluno estudar. O gabarito só é devolvido quando `reveal_answer=True`,
para o agente não entregar a resposta antes da hora.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Dict, List, Optional

from neurocore.attachments import save_remote_image
from neurocore.enem_client import (
    format_question,
    get_question,
    list_exams,
    question_image_urls,
)
from neurocore.tools.base import BaseTool, ToolResult

# Em provas com opção de idioma, as questões 1-5 existem em inglês e espanhol.
# Para sortear, usamos o intervalo geral de questões do ENEM.
MIN_INDEX = 1
MAX_INDEX = 180
MAX_RANDOM_TRIES = 10


def _available_years() -> List[int]:
    """Extrai os anos disponíveis da resposta de /exams (lista ou dicionário)."""
    data = list_exams()
    items = data.get("exams", data) if isinstance(data, dict) else data
    years: List[int] = []
    for item in items or []:
        if isinstance(item, dict) and isinstance(item.get("year"), int):
            years.append(item["year"])
        elif isinstance(item, int):
            years.append(item)
    return sorted(set(years))


class EnemQuestionTool(BaseTool):
    name = "enem_question"
    description = (
        "Busca uma questão real de uma prova anterior do ENEM para o aluno estudar. "
        "Informe 'year' e 'index' para uma questão específica, ou deixe 'index' vazio "
        "para sortear uma questão (opcionalmente filtrando por 'year' e 'discipline'). "
        "Se a questão tiver figuras (gráficos, tabelas, mapas, charges), elas são enviadas "
        "junto com o resultado e aparecem para o aluno; o texto as cita como [Figura N]. "
        "O gabarito só é devolvido se reveal_answer for true; use isso apenas depois "
        "que o aluno responder ou pedir a correção."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "year": {
                "type": "integer",
                "description": "Ano da prova do ENEM (ex.: 2023). Opcional ao sortear.",
            },
            "index": {
                "type": "integer",
                "description": "Número da questão na prova. Se omitido, sorteia uma questão.",
            },
            "discipline": {
                "type": "string",
                "description": (
                    "Filtro opcional ao sortear (ex.: 'matematica', 'natureza', "
                    "'humanas', 'linguagens'). Compara com o campo discipline da questão."
                ),
            },
            "reveal_answer": {
                "type": "boolean",
                "description": "Se true, inclui a alternativa correta na resposta.",
            },
        },
        "required": [],
    }

    def _run_sync(
        self,
        tool_call_id: str = "",
        year: Optional[int] = None,
        index: Optional[int] = None,
        discipline: Optional[str] = None,
        reveal_answer: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        question = self._fetch(year, index, discipline)
        q_year = question.get("year", year)
        q_index = question.get("index", index)

        # Figuras da questão: baixadas de servidores permitidos, mostradas ao
        # aluno e enviadas ao modelo (que consegue analisá-las).
        urls = question_image_urls(question)
        labels: Dict[str, str] = {}
        attachments: List[Dict[str, Any]] = []
        failed = 0
        for number, url in enumerate(urls, start=1):
            label = f"Figura {number}"
            labels[url] = label
            try:
                att = save_remote_image(url, source="enem", name=f"enem-{q_year}-q{q_index}-figura-{number}")
                attachments.append(att.to_public())
            except Exception:  # rede, formato inválido ou servidor não permitido
                failed += 1

        output: Dict[str, Any] = {
            "year": q_year,
            "index": q_index,
            "discipline": question.get("discipline"),
            "question_text": format_question(question, labels),
        }
        if urls:
            output["images_total"] = len(urls)
            output["images_loaded"] = len(attachments)
            note = "As figuras [Figura N] foram enviadas junto com este resultado e aparecem para o aluno."
            if failed:
                note += f" {failed} figura(s) não puderam ser carregadas; se forem essenciais, avise o aluno."
            output["images_note"] = note
        if reveal_answer:
            output["correct_alternative"] = question.get("correctAlternative")

        return ToolResult(
            tool_call_id=tool_call_id,
            name=self.name,
            status="success",
            output=output,
            attachments=attachments,
        )

    async def _run_async_impl(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        # A chamada HTTP é bloqueante; roda em outra thread para não travar o servidor.
        return await asyncio.to_thread(self._run_sync, tool_call_id, **kwargs)

    def _fetch(
        self,
        year: Optional[int],
        index: Optional[int],
        discipline: Optional[str],
    ) -> Dict[str, Any]:
        if year is not None and index is not None:
            return get_question(year, index)

        years = [year] if year is not None else _available_years()
        if not years:
            raise ValueError(
                "Não foi possível descobrir os anos disponíveis. Informe 'year' explicitamente."
            )

        wanted = discipline.lower() if discipline else None
        last_error: Optional[Exception] = None

        for _ in range(MAX_RANDOM_TRIES):
            try:
                q = get_question(random.choice(years), random.randint(MIN_INDEX, MAX_INDEX))
            except Exception as exc:  # questão inexistente, limite de requisições etc.
                last_error = exc
                continue
            if wanted and wanted not in str(q.get("discipline", "")).lower():
                continue
            return q

        raise ValueError(
            "Não consegui sortear uma questão com esses filtros. "
            f"Último erro: {last_error}" if last_error else
            "Nenhuma questão sorteada correspondeu ao filtro de disciplina. Tente novamente."
        )
