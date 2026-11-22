"""
developer_agent.py — Code Generation Agent
==========================================
Uses Gemini 2.5 Flash (reasoning) for high-quality code generation.
Reads full task context from Neo4j, generates code, writes to filesystem,
and records CodeModule node in Neo4j.
"""

import os
import json
import uuid
import logging
from datetime import datetime
from pathlib import Path
from llm_client import llm_call, parse_json_response, set_active_agent
from tracing import traceable
from graph.neo4j_client import (
    create_node, link_nodes, get_node, query_graph, upsert_agent, update_agent_profile,
    get_api_schema
)
from memory.context_retriever import ContextRetriever

logger = logging.getLogger("developer_agent")

GENERATED_CODE_DIR = Path(os.getenv("GENERATED_CODE_DIR", "./generated_code"))

CODE_GEN_SYSTEM = """You write ONE file of a small full-stack web app: a Node.js/Express backend plus a static HTML/CSS/vanilla-JS frontend. The finished app must start with `npm install && node server.js`.
Return JSON only:
{
  "filename": "path/to/file",
  "code": "complete file contents",
  "explanation": "one sentence",
  "module_type": "endpoint|schema|service|utility|component",
  "dependencies": ["npm-package"]
}
Project layout (use exactly these paths):
- server.js          backend entry point (Express). Keep routes and in-memory data here unless the task says otherwise.
- lib/<name>.js      any extra backend module, required from server.js
- public/index.html  page markup; link the other frontend files with relative paths (styles.css, app.js)
- public/styles.css  styles
- public/app.js      frontend logic: vanilla JS only, no frameworks, no JSX, no build step
Backend rules:
- const PORT = process.env.PORT || 4000; call app.listen(PORT) with NO host argument so it listens on all interfaces
- Start the server only when run directly: if (require.main === module) { app.listen(PORT, ...) } and end the file with module.exports = app so tests can import it
- app.use(express.json()); serve the frontend with app.use(express.static(path.join(__dirname, 'public')))
- Every API route lives under /api
Frontend rules:
- Call the API with relative URLs such as fetch('/api/items'); never hard-code localhost or a port
- Use only the endpoints of the project API contract when one is given
General rules:
- Complete, runnable code. No TODOs, no placeholders, no markdown fences inside the code field
- Handle errors. Use ES6+ and CommonJS require()
- "dependencies": only npm packages this file requires (never Node built-ins). Prefer express alone."""


class DeveloperAgent:
    """
    Generates code for a given task by reading full context from Neo4j.
    Writes output to filesystem and records CodeModule in Neo4j.
    """

    AGENT_NAME = "DeveloperAgent"

    def __init__(self):
        upsert_agent(self.AGENT_NAME)
        self.retriever = ContextRetriever()
        GENERATED_CODE_DIR.mkdir(parents=True, exist_ok=True)

    @traceable(name="DeveloperAgent.run")
    def run(self, task_node: dict) -> dict:
        """
        Generate code for a task.

        Args:
            task_node: Task dict from Neo4j (must have 'id', 'title', 'description')

        Returns:
            dict with filename, code_module_id, and success status
        """
        task_id = task_node["id"]
        logger.info(f"DeveloperAgent processing task: {task_id} — {task_node.get('title')}")
        set_active_agent(self.AGENT_NAME)

        # Fetch full context from Neo4j
        context = self._build_context(task_node)

        # Fetch project-level API schema
        api_schema = get_api_schema(task_node.get("project_id", ""))

        # Get similar past solutions from memory
        similar_examples = self.retriever.get_similar_context(
            task_node.get("description", "") + " " + task_node.get("title", "")
        )

        # Build prompt with context injection
        messages = self._build_messages(task_node, context, similar_examples, api_schema)

        # Generate code via Gemini
        raw = llm_call("reasoning", messages, json_mode=True)
        result = parse_json_response(raw)

        # Write code to filesystem
        filepath = self._write_code(task_node, result)

        # Store CodeModule in Neo4j
        module_id = self._store_code_module(task_id, result, filepath)

        update_agent_profile(self.AGENT_NAME, score=7.5, task_type="CODE", retries=0)

        return {
            "success": True,
            "task_id": task_id,
            "code_module_id": module_id,
            "filename": str(filepath),
            "explanation": result.get("explanation", ""),
        }

    @traceable(name="DeveloperAgent.run_with_critique")
    def run_with_critique(self, task_node: dict, critique: str, version: int) -> dict:
        """
        Re-generate code incorporating a critic's feedback.
        Used by the correction engine.
        """
        task_id = task_node["id"]
        logger.info(f"DeveloperAgent retry (v{version}) for task: {task_id}")
        set_active_agent(self.AGENT_NAME)

        context = self._build_context(task_node)
        similar_examples = self.retriever.get_similar_context(task_node.get("description", ""))
        api_schema = get_api_schema(task_node.get("project_id", ""))

        messages = self._build_messages(task_node, context, similar_examples, api_schema)
        # Append the critique as a follow-up
        messages.append({
            "role": "user",
            "content": (
                f"Your previous implementation was evaluated and received this critique:\n\n"
                f"{critique}\n\n"
                f"Please fix the issues and return improved code following the same JSON format."
            ),
        })

        raw = llm_call("reasoning", messages, json_mode=True)
        result = parse_json_response(raw)

        filepath = self._write_code(task_node, result, version=version)
        module_id = self._store_code_module(task_id, result, filepath, version=version)

        # Record this retry in the graph for analytics
        update_agent_profile(self.AGENT_NAME, score=6.5, task_type="CODE", retries=1)

        return {
            "success": True,
            "task_id": task_id,
            "code_module_id": module_id,
            "filename": str(filepath),
            "version": version,
        }

    def _build_context(self, task_node: dict) -> dict:
        """Fetch parent feature, sibling tasks, and acceptance criteria from Neo4j."""
        cypher = """
        MATCH (t:Task {id: $task_id})
        OPTIONAL MATCH (f:Feature)-[:HAS_TASK]->(t)
        OPTIONAL MATCH (f)-[:HAS_TASK]->(sibling:Task)
        WHERE sibling.id <> $task_id
        RETURN
          f.title AS feature_title,
          f.description AS feature_description,
          f.acceptance_criteria AS acceptance_criteria,
          collect(sibling.title) AS sibling_tasks
        """
        results = query_graph(cypher, {"task_id": task_node["id"]})
        if results:
            row = results[0]
            criteria = row.get("acceptance_criteria", "[]")
            if isinstance(criteria, str):
                try:
                    criteria = json.loads(criteria)
                except Exception:
                    criteria = [criteria]
            return {
                "feature_title": row.get("feature_title", ""),
                "feature_description": row.get("feature_description", ""),
                "acceptance_criteria": criteria,
                "sibling_tasks": row.get("sibling_tasks", []),
            }
        return {}

    def _build_messages(
        self, task_node: dict, context: dict, similar_examples: list, api_schema: dict = None
    ) -> list[dict]:
        """Construct the full message list for the LLM."""
        user_content = (
            f"Task: {task_node.get('title', '')}\n"
            f"Description: {task_node.get('description', '')}\n"
            f"Feature: {context.get('feature_title', 'N/A')}"
        )

        if similar_examples:
            eg = similar_examples[0]
            user_content += f"\n\nSimilar pattern (use as reference):\n{eg['code'][:200]}"

        # Inject API contract so frontend/backend are always consistent
        if api_schema and api_schema.get("endpoints"):
            def _fmt(e):
                line = f"  {e['method']} {e['path']} — {e.get('description', '')}"
                if e.get("request"):
                    line += f"\n      request body: {e['request']}"
                if e.get("response"):
                    line += f"\n      response JSON: {e['response']}"
                return line

            endpoint_lines = "\n".join(_fmt(e) for e in api_schema["endpoints"])
            user_content += (
                "\n\n=== PROJECT API CONTRACT (must follow exactly) ===\n"
                f"Endpoints:\n{endpoint_lines}\n"
                "The backend serves the frontend from the same origin, so the frontend must call "
                "these paths with relative URLs (e.g. fetch('/api/...')), never with localhost or a port.\n"
                "If generating backend code, implement ALL of these endpoints and return EXACTLY the "
                "response JSON shown (same field names). If generating frontend code, read EXACTLY "
                "those field names from responses."
            )

        return [
            {"role": "system", "content": CODE_GEN_SYSTEM},
            {"role": "user",   "content": user_content},
        ]

    def _write_code(self, task_node: dict, result: dict, version: int = 1) -> Path:
        """Write generated code to the project-specific filesystem folder."""
        filename = result.get("filename") or f"module_{task_node['id'][:8]}.js"
        project_id = task_node.get("project_id", "default_project")

        # Sanitize filename
        filename = filename.replace("..", "").lstrip("/").lstrip("\\")

        filepath = GENERATED_CODE_DIR / project_id / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # On a retry keep the same path (other files import it by name) and archive the
        # previous attempt under .versions/ instead of creating server_v2.js next to it.
        if version > 1 and filepath.exists():
            archive = GENERATED_CODE_DIR / project_id / ".versions" / Path(filename).parent
            archive.mkdir(parents=True, exist_ok=True)
            archived = archive / f"{filepath.stem}_v{version - 1}{filepath.suffix}"
            archived.write_text(filepath.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

        code = result.get("code", "# No code generated")
        filepath.write_text(code, encoding="utf-8")
        logger.info(f"Code written to: {filepath}")
        return filepath

    def _store_code_module(
        self, task_id: str, result: dict, filepath: Path, version: int = 1
    ) -> str:
        """Create a CodeModule node in Neo4j linked to the Task."""
        module_id = str(uuid.uuid4())
        create_node("CodeModule", {
            "id": module_id,
            "filename": result.get("filename", ""),
            "filepath": str(filepath),
            "module_type": result.get("module_type", "utility"),
            "explanation": result.get("explanation", ""),
            "version": version,
            "status": "GENERATED",
            "dependencies": json.dumps(result.get("dependencies", [])),
        })
        link_nodes(module_id, task_id, "PRODUCED_BY")
        logger.info(f"CodeModule {module_id} linked to Task {task_id}")
        return module_id
