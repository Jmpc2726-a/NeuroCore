"""Configuração comum da suíte de testes (carregada antes dos módulos de teste)."""
import os

# O TestClient do Starlette só devolve uma resposta em streaming quando ela
# termina; encurtamos a vida da conexão SSE de atividade para os testes.
os.environ.setdefault("NEUROCORE_SSE_MAX_SECONDS", "1")
# Evita que o limite de requisições por minuto interfira na suíte.
os.environ.setdefault("NEUROCORE_RATE_LIMIT_PER_MINUTE", "0")

# Sessões, tarefas e documentos dos testes ficam num diretório temporário,
# fora do repositório.
if "NEUROCORE_WORKSPACE" not in os.environ:
    import tempfile

    os.environ["NEUROCORE_WORKSPACE"] = tempfile.mkdtemp(prefix="neurocore-tests-")
