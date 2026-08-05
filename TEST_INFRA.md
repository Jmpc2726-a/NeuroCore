# E2E Test Infra: NeuroCore Code Agent

## Test Philosophy
- Opaque-box, requirement-driven end-to-end and tier testing.
- Methodology: Category-Partitioning + Boundary Value Analysis (BVA) + Pairwise Combinatorial Testing + Real-World Workload Testing.
- 100% offline-capable, deterministic, zero network flakiness.

## Feature Inventory & Tier Target Matrix
| # | Feature | Source | Tier 1 (Feature) | Tier 2 (Boundary) | Tier 3 (Cross) | Tier 4 (Scenario) |
|---|---------|--------|:----------------:|:-----------------:|:--------------:|:-----------------:|
| 1 | Provider Availability API (`get_available_providers`) | R1 | ≥5 | ≥5 | ✓ | ✓ |
| 2 | Backend REST & SSE Endpoints (`/health`, `/providers`, `/api/v1/chat`) | R1 | ≥5 | ≥5 | ✓ | ✓ |
| 3 | Sandboxed Tools Engine (`read`, `write`, `grep`, `list`, `replace`, `run`) | R1 | ≥5 | ≥5 | ✓ | ✓ |
| 4 | Multi-provider Fallback & Cascade (`Gemini -> OpenAI -> Anthropic -> Local`) | R1 | ≥5 | ≥5 | ✓ | ✓ |
| 5 | Interactive CLI REPL & Slash Commands (`/help`, `/provider`, `/status`, etc.) | R2 | ≥5 | ≥5 | ✓ | ✓ |
| 6 | CLI Output Formatting & Tool Tracing (Cyberpunk theme, diffs, panels) | R2 | ≥5 | ≥5 | ✓ | ✓ |
| 7 | CLI Packaging Entrypoint (`pyproject.toml` `neurocore` binary) | R2 | ≥5 | ≥5 | ✓ | ✓ |
| 8 | Web Studio Cyberpunk UI & Visual Diff (Myers LCS diff viewer) | R3 | ≥5 | ≥5 | ✓ | ✓ |
| 9 | Web Studio Task Manager & Live SSE (Kanban board, Task API, SSE stream) | R3 | ≥5 | ≥5 | ✓ | ✓ |

## Test Architecture & Runner Setup
- **Test Framework**: `pytest` and standalone validation scripts.
- **Test File Location**: `tests/` directory (`test_tools.py`, `test_providers.py`, `test_provider_fallback.py`, `test_backend_api.py`, `test_cli_main.py`, `test_cli_display.py`, `test_web_studio.py`, `test_e2e_scenarios.py`).
- **Smoke Verification Scripts**: `scripts/verify_cli.py`, `scripts/verify_http.py`.
- **Pass/Fail Semantics**: Exit code 0, 100% test pass rate across all suites.

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Verification Method |
|---|----------|--------------------|---------------------|
| 1 | Code Search, Read, Replace & Execution | Grep, ReadFile, ReplaceFile, RunCmd | `pytest tests/test_e2e_scenarios.py` |
| 2 | Terminal CLI Interactive & REPL Session | CLI Main, Slash Commands, Display Tracing | `pytest tests/test_cli_main.py` & `scripts/verify_cli.py` |
| 3 | Web Studio Task & Live SSE Stream | Task API, SSE Activity, Web UI endpoints | `pytest tests/test_web_studio.py` & `scripts/verify_http.py` |
| 4 | Multi-Provider Offline Cascade & Fallback | ProviderManager, Memory, Fallback Cascade | `pytest tests/test_provider_fallback.py` |
| 5 | Sandboxed Security & Workspace Protection | Tools Base, Line Slices, Path Traversal Guards | `pytest tests/test_tools.py` |

## Coverage Thresholds
- Tier 1: ≥5 test cases per feature across Backend API, CLI, and Web Studio (Total ≥45 test cases).
- Tier 2: ≥5 boundary/corner cases per feature (Total ≥45 test cases).
- Tier 3: Pairwise feature interaction tests (Total ≥10 test cases).
- Tier 4: Real-World Application Scenarios (Total ≥5 E2E workflows).
- **Execution Threshold**: 100% pass rate, exit code 0.
