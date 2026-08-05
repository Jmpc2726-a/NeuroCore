# Project: NeuroCore Code Agent

## Architecture
NeuroCore Code Agent is an autonomous AI coding & daily productivity assistant featuring a FastAPI backend, an interactive terminal CLI, and a dark-mode Web Studio interface.

```
[ Web Studio (HTML/CSS/JS) ] ── (HTTP / SSE) ──┐
                                               ▼
[ Terminal CLI (Rich/REPL) ] ── (Python API) ─► [ FastAPI Server (neurocore/server) ]
                                                       │
                                                       ▼
                                               [ Agent Engine (neurocore/agent) ]
                                                       │
                                       ┌───────────────┴───────────────┐
                                       ▼                               ▼
                             [ Provider Manager ]            [ Tools Engine ]
                         (Local/Gemini/OpenAI/Anthropic)  (ReadFile, WriteFile, Grep,
                                                           ListDir, ReplaceFile, RunCmd)
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Provider Availability API | `ProviderManager.get_available_providers()` for health & provider list endpoints | M1 | R1 |
| 2 | Backend API Pytest & HTTP Verification | 100% passing pytest suite and `verify_http.py` endpoint checks | M1 | R1 / Verification |
| 3 | Sandboxed Tools Engine | Read, Write, Grep, ListDir, Replace, RunCmd with workspace guards | M1 | R1 |
| 4 | Multi-provider Fallback & Cascade | Offline local simulated fallback and API key provider integration | M1 | R1 |
| 5 | Interactive CLI REPL & Slash Commands | Terminal chat, `/help`, `/provider`, `/status`, `/clear`, `/reset`, `/exit` | M2 | R2 |
| 6 | CLI Output Formatting & Tool Tracing | Cyberpunk theme, Markdown stream rendering, code diffs, command panels | M2 | R2 |
| 7 | CLI Packaging Entrypoint | `pyproject.toml` `[project.scripts]` executable entrypoint (`neurocore`) | M2 | R2 |
| 8 | Web Studio Cyberpunk UI & Visual Diff | 3-column layout, Myers LCS split/unified code diff viewer | M3 | R3 |
| 9 | Web Studio Task Manager & Live SSE | 4-column Kanban board, Task API `/api/v1/tasks/{task_id}/run`, SSE activity stream | M3 | R3 |
| 10 | Comprehensive E2E Verification Harness | Automated pytest, CLI smoke script (`verify_cli.py`), HTTP script (`verify_http.py`) | E2E | Verification |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Backend & Provider Engine Fix | Implement `get_available_providers()` in `neurocore/agent/providers.py`, verify FastAPI `/health` and `/providers` endpoints, achieve 100% pytest pass | none | PLANNED |
| M2 | CLI Entrypoint & Alignment | Add `[project.scripts]` to `pyproject.toml`, verify `execute_agent_turn` call alignment, run `scripts/verify_cli.py` | M1 | PLANNED |
| M3 | Web Studio Live Task Execution | Connect `/api/v1/tasks/{task_id}/run` to agent engine, verify SSE live stream activity and HTTP endpoint script | M1 | PLANNED |
| E2E | E2E Test Suite Track | Build 4-tier E2E test suite (Feature Coverage, BVA, Pairwise, Real-world), publish `TEST_READY.md` | none | IN_PROGRESS |
| M-FINAL | Final Verification & Hardening | Phase 1: 100% pass across E2E test suite (Tiers 1-4); Phase 2: Tier 5 white-box adversarial coverage hardening | M1, M2, M3, E2E | PLANNED |

## Interface Contracts
### Backend API ↔ ProviderManager
- `provider_manager.get_available_providers() -> Dict[str, Dict[str, Any]]`
  Returns dict mapping provider name to `{"available": bool, "model": str, "configured": bool}`.

### Web Studio Task API ↔ Agent Engine
- `POST /api/v1/tasks/{task_id}/run` -> invokes `agent_engine.run(user_prompt=task.prompt)`
  Streams tool events to `/api/v1/stream/activity`.

### CLI Package Entrypoint
- Command `neurocore` maps to `neurocore.cli.main:main`.

## Code Layout
```
neurocore/
├── agent/
│   ├── engine.py          # AgentEngine execution loop & SSE streaming
│   ├── memory.py          # SessionManager & sliding window message history
│   ├── prompts.py         # System prompts & instructions
│   └── providers.py       # ProviderManager & LLM providers (Local, Gemini, OpenAI, Anthropic)
├── cli/
│   ├── display.py         # Rich display, diff renderer, slash commands
│   └── main.py            # CLI argument parser, batch & REPL mode
├── server/
│   ├── app.py             # FastAPI app initialization & CORS
│   └── routes.py          # REST & SSE streaming endpoint handlers
├── tools/
│   ├── base.py            # Tool base class & registry
│   └── ...                # Tool handlers (read, write, grep, list, replace, run)
└── web/
    ├── static/            # CSS, JS (Myers LCS diff, Kanban, SSE client)
    └── templates/         # index.html (Web Studio interface)
```
