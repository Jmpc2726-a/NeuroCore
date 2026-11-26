"""Cliente simples para a ENEM API (https://api.enem.dev/v1).

Uso rápido:
    python enem_client.py 2023 5      # mostra a questão 5 do ENEM 2023

Observação: o formato exato do JSON pode variar. Se algum campo vier vazio,
rode `python enem_client.py --raw 2023 5` para ver a resposta completa e
ajustar os nomes dos campos em `format_question`.
"""

import json
import os
import re
import sys
from pathlib import Path

import requests

BASE_URL = "https://api.enem.dev/v1"


def _cache_dir() -> Path:
    """Pasta de cache dentro do workspace (criada só quando necessária)."""
    root = os.environ.get("NEUROCORE_WORKSPACE") or os.environ.get("WORKSPACE_ROOT") or os.getcwd()
    path = Path(root) / ".cache_enem"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get(path, params=None):
    """GET com cache em disco, para não estourar o limite de requisições."""
    params = params or {}
    key = path.strip("/").replace("/", "_")
    if params:
        key += "_" + "_".join(f"{k}-{v}" for k, v in sorted(params.items()))
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:150]
    cache_file = _cache_dir() / f"{key}.json"

    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # cache corrompido: busca de novo

    resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    try:
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data


def list_exams():
    """Lista as provas (anos) disponíveis."""
    return _get("/exams")


def get_questions(year, limit=50, offset=0):
    """Lista questões de um ano, com paginação."""
    return _get(f"/exams/{int(year)}/questions", {"limit": int(limit), "offset": int(offset)})


def get_question(year, index):
    """Busca uma questão específica pelo número."""
    return _get(f"/exams/{int(year)}/questions/{int(index)}")


def format_question(q):
    """Converte uma questão em texto legível (também serve de contexto para a IA)."""
    lines = [
        f"ENEM {q.get('year', '?')} - Questão {q.get('index', '?')} "
        f"({q.get('discipline', 'sem disciplina')})",
        "",
        q.get("context") or "",
        "",
        q.get("alternativesIntroduction") or "",
    ]
    for alt in q.get("alternatives", []):
        lines.append(f"{alt.get('letter', '?')}) {alt.get('text') or '[imagem]'}")
    return "\n".join(line for line in lines if line is not None)


def check_answer(q, letter):
    """Confere a alternativa escolhida."""
    correct = q.get("correctAlternative")
    return letter.upper() == correct, correct


def build_study_prompt(q, user_answer=None):
    """Monta o texto que será enviado ao assistente (NeuroCore) para explicar a questão."""
    prompt = (
        "Explique esta questão do ENEM passo a passo, em português, "
        "indicando o conceito principal e por que as outras alternativas estão erradas.\n\n"
        + format_question(q)
    )
    if user_answer:
        prompt += f"\n\nEu marquei a alternativa {user_answer.upper()}."
    return prompt


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--raw"]
    raw = "--raw" in sys.argv
    if len(args) != 2:
        print("Uso: python enem_client.py [--raw] ANO NUMERO_DA_QUESTAO")
        sys.exit(1)

    question = get_question(int(args[0]), int(args[1]))
    if raw:
        print(json.dumps(question, indent=2, ensure_ascii=False))
    else:
        print(format_question(question))
