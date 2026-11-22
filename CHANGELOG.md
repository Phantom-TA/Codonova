# Codonova Operation & Maintenance Log

This log traces modifications, bug fixes, operational adjustments, and testing milestones within the Codonova Autonomous System workspace.

## [2026-11-23] - Platform upgrades
**Plan review, live preview, run reports, desktop app and tracing**

### Added
- **Plan Review panel:** edit tasks, features, dependencies (cycles rejected) and the API contract before approving; edit history stored as `PlanRevision` nodes. Backend in `plan_editor.py`.
- **Live Preview:** `preview_manager.py` installs a generated app's npm packages, starts it on ports 4100-4110 with a minimal environment (no API keys) and shows it in the dashboard. `project_runtime.py` scans the code for dependencies and writes `package.json`.
- **Run Report:** `run_report.py` with first-pass and final pass rate, attempts, tests, time per task, tokens and absorbed API errors; Run Report panel, `RUN_REPORT.md` in the ZIP, and `backend/benchmark.py` for batch runs.
- **Electron desktop app (`desktop/`):** starts Docker and the services, tray, menu, native notifications and save dialog, Windows installer.
- **LangSmith tracing (optional):** every LLM call and the agent hierarchy, enabled by environment variables.
- **Model fallback chain:** `GEMINI_MODEL` accepts several models; exhausted models are skipped for a while; OpenRouter only when a key exists.
- API contract now carries request and response JSON shapes so frontend and backend agree on field names.

### Changed
- Developer prompt produces a runnable layout (`server.js`, `lib/`, `public/`), reads `PORT` from the environment, uses same-origin API calls and readable styling.
- The planner assigns each task one file and drops duplicates (two tasks writing the same file used to leave pages without their scripts).
- Retries overwrite the same file and archive earlier attempts in `.versions/` instead of creating `server_v2.js`.
- Agent scores are measured (Developer = evaluator score per attempt, Testing = share of tests passed); agents without an objective score show none.
- Export ZIP is the whole runnable project with an npm README.

### Fixed
- Agent calls ran inside the async event loop and froze the API and WebSocket during a run; they now run in worker threads.
- Jest runs that passed were recorded as failed (relative report path was resolved twice).
- Planner attached all tasks to the first feature and reused task ids across features.
- Task graph canvas used the window size and cut off nodes; it now fits its panel.
- `/api/start` did not mark the project RUNNING; `/api/execute` turned 404 and 400 into 500 and allowed a double start.
- Embedding model is cached in a Docker volume instead of being downloaded after every rebuild.
- Default model updated (`gemini-2.5-flash` is no longer served to new keys).

## [2026-04-04] - Phase 5 Operational Fixes
**System Stability & API Resilience Pass**

### Fixed
- **API Key Configuration:** Populated the original `.env` template with valid Gemini and OpenRouter tokens to activate agent responses.
- **Neo4j Cypher Arithmetic Bug:** Fixed a `ClientError.Statement.ArithmeticError` (/ by zero) in `backend/graph/neo4j_client.py` triggered when calculating the running average of an Agent's score on tasks. Modified the `SET` block to account for the execution order of operations for nodes with `0` initial tasks dynamically.
- **LLM Token Cutoffs:** Increased `max_tokens` default inside `llm_client.py` from `4096` to `32768`. The Planning Agent was previously generating massive JSON schemas that were structurally truncating and failing backend parsing. Now explicitly forces `json_mode` parameter injection for Gemini model compatibility natively as well.
- **Payload Too Large (Groq Migration):** The `TestingAgent` prompt inputs quickly eclipsed `30,000` context tokens, immediately triggering HTTP `413` rejections from Groq's 12K TPM free tier. Migrated all "fast" operations directly to Gemini via `.env` configuration adjustments.
- **Rate Limit Deadlock (429):** The `llm_client.py` handled 429 quota exceptions, but only retried 3 times with a maximum span of 3 seconds. Scaled retries to `5`, and explicitly hardcoded the wait time scalar to `30s`. The system will now gracefully hibernate for up to `120 seconds` to ensure the Gemini 15 RPM Free tier is effectively flushed before moving forward without crashing autonomous bursts.
- **Experimental Quota (Limit: 0):** Identified that `gemini-2.0-flash` and `gemini-2.5-flash` were hitting hard "Daily 0 Limit" blocks on the Free Tier. Resolved by switching the primary production model to **`gemini-1.5-flash`** (1,500 RPM / 1M TPM), which has significantly more reliable free-tier availability.
- **Nginx HTTP 504 Gateway Timeout:** The UI React application was consistently crashing while parsing asynchronous HTTP responses for the `/api/plan` route. Root cause was Nginx severing proxy payloads at exactly **60 seconds**, which was structurally incompatible with the extended rate-limit buffering implemented on the backend. Added `proxy_read_timeout 600;` inside `frontend/nginx.conf` making UI wait sequences 100% resilient.

### Added
- Created a comprehensive **`.gitignore`** file specifically tailored for this autonomous development workspace (ignoring `.env`, logs, generated code, and LLM-specific artifacts).
- Replaced the failing OpenRouter fallback models with **`google/gemma-2-9b-it:free`** to resolve 404 endpoint availability errors.
