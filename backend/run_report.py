"""
run_report.py — Run report for a project
=========================================
Every number comes from data the pipeline stores in Neo4j and on disk:
  * CodeModule versions        -> number of attempts per task
  * EvaluationResult scores    -> quality of each attempt
  * TestResult counts          -> tests passed / failed per attempt
  * Task started_at/updated_at -> time per task
  * LLMCallLog nodes           -> LLM calls, tokens, latency and the API errors
                                  (429 quota / 503 overload) absorbed by key and
                                  model rotation

Definitions
  first-pass rate  tasks whose very first attempt scored >= the pass mark
  final-pass rate  tasks that ended DONE
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from graph.neo4j_client import query_graph
from project_runtime import project_dir, walk_files

logger = logging.getLogger("run_report")

router = APIRouter(prefix="/api/report", tags=["report"])


def _ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except ValueError:
        return None


def _mean(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def _pct(part: int, whole: int) -> Optional[float]:
    return round(100 * part / whole, 1) if whole else None


def _code_stats(project_id: str) -> dict:
    """Files and lines of code the pipeline generated (installed packages excluded)."""
    directory = project_dir(project_id)
    if not directory.exists():
        return {"files": 0, "lines": 0}
    files = lines = 0
    for path in walk_files(directory):
        if (path.name in ("package-lock.json", "package.json")
                or path.name.startswith(".report_")):
            continue
        if path.suffix not in {".js", ".mjs", ".cjs", ".html", ".css", ".json", ".md"}:
            continue
        try:
            lines += len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
            files += 1
        except OSError:
            continue
    return {"files": files, "lines": lines}


def build_report(project_id: str) -> dict:
    from agents.correction_engine import PASS_SCORE  # imported lazily: heavy module

    proj = query_graph(
        """
        MATCH (p:Project {id: $pid})
        RETURN p.id AS id, p.title AS title, p.description AS description,
               p.requirement AS requirement, p.status AS status,
               p.created_at AS created_at, p.completed_at AS completed_at
        """,
        {"pid": project_id},
    )
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    project = proj[0]

    rows = query_graph(
        """
        MATCH (:Project {id: $pid})-[:HAS_FEATURE]->(f:Feature)-[:HAS_TASK]->(t:Task)
        OPTIONAL MATCH (cm:CodeModule)-[:PRODUCED_BY]->(t)
        OPTIONAL MATCH (er:EvaluationResult)-[:EVALUATES]->(cm)
        OPTIONAL MATCH (tr:TestResult)-[:VALIDATES]->(cm)
        RETURN t.id AS id, t.title AS title, t.status AS status, f.title AS feature,
               t.started_at AS started_at, t.updated_at AS finished_at,
               cm.id AS module_id, cm.version AS version, cm.filename AS filename,
               er.score AS score, tr.tests_passed AS tests_passed, tr.tests_failed AS tests_failed
        """,
        {"pid": project_id},
    )

    # ── group rows: task -> module(attempt) ──
    tasks: dict[str, dict] = {}
    for r in rows:
        t = tasks.setdefault(r["id"], {
            "id": r["id"], "title": r["title"], "feature": r["feature"], "status": r["status"],
            "started_at": r["started_at"], "finished_at": r["finished_at"],
            "filename": None, "modules": {},
        })
        if r["module_id"]:
            m = t["modules"].setdefault(r["module_id"], {
                "attempt": r["version"] or 1, "score": None,
                "tests_passed": None, "tests_failed": None,
            })
            if r["score"] is not None:
                m["score"] = r["score"]
            if r["tests_passed"] is not None:
                m["tests_passed"], m["tests_failed"] = r["tests_passed"], r["tests_failed"]
            t["filename"] = r["filename"] or t["filename"]

    task_list = []
    for t in tasks.values():
        attempts = sorted(t.pop("modules").values(), key=lambda a: a["attempt"])
        first = attempts[0] if attempts else None
        last = attempts[-1] if attempts else None
        started, finished = _ts(t["started_at"]), _ts(t["finished_at"])
        duration = None
        if started and finished and finished >= started and t["status"] in ("DONE", "FAILED"):
            duration = round((finished - started).total_seconds(), 1)
        t.update({
            "attempts": len(attempts),
            "attempt_log": attempts,
            "first_pass": bool(first and first["score"] is not None and first["score"] >= PASS_SCORE),
            "final_score": last["score"] if last else None,
            "tests_passed": last["tests_passed"] if last else None,
            "tests_failed": last["tests_failed"] if last else None,
            "duration_s": duration,
        })
        task_list.append(t)
    task_list.sort(key=lambda t: (t["feature"] or "", t["title"] or ""))

    total = len(task_list)
    count = lambda status: sum(1 for t in task_list if t["status"] == status)
    done = count("DONE")
    scores = [t["final_score"] for t in task_list if t["final_score"] is not None]
    retried = [t for t in task_list if t["attempts"] > 1]
    tests_pass = sum(t["tests_passed"] or 0 for t in task_list)
    tests_fail = sum(t["tests_failed"] or 0 for t in task_list)

    # wall clock: first task start -> last task finish (falls back to project timestamps)
    starts = [_ts(t["started_at"]) for t in task_list if _ts(t["started_at"])]
    ends = [_ts(t["finished_at"]) for t in task_list if _ts(t["finished_at"])]
    wall = None
    if starts and ends and max(ends) >= min(starts):
        wall = round((max(ends) - min(starts)).total_seconds(), 1)
    elif _ts(project["created_at"]) and _ts(project["completed_at"]):
        wall = round((_ts(project["completed_at"]) - _ts(project["created_at"])).total_seconds(), 1)

    # ── LLM usage and absorbed API errors ──
    calls = query_graph(
        """
        MATCH (l:LLMCallLog {project_id: $pid})
        RETURN l.agent_type AS agent, l.model AS model, l.success AS success,
               coalesce(l.tokens_used, 0) AS tokens, coalesce(l.latency_ms, 0) AS latency,
               coalesce(l.failed_attempts, 0) AS failed_attempts,
               coalesce(l.quota_errors, 0) AS quota_errors,
               coalesce(l.overload_errors, 0) AS overload_errors,
               coalesce(l.fallback_used, false) AS fallback_used
        """,
        {"pid": project_id},
    )
    per_agent: dict[str, dict] = {}
    per_model: dict[str, dict] = {}
    for c in calls:
        a = per_agent.setdefault(c["agent"] or "unknown", {"calls": 0, "tokens": 0, "latency": 0.0})
        a["calls"] += 1
        a["tokens"] += c["tokens"]
        a["latency"] += c["latency"]
        m = per_model.setdefault(c["model"] or "unknown", {"calls": 0, "tokens": 0})
        m["calls"] += 1
        m["tokens"] += c["tokens"]

    llm = {
        "calls": len(calls),
        "tokens": sum(c["tokens"] for c in calls),
        "failed_calls": sum(1 for c in calls if not c["success"]),
        "avg_latency_s": round(sum(c["latency"] for c in calls) / len(calls) / 1000, 1) if calls else None,
        "absorbed_errors": sum(c["failed_attempts"] for c in calls if c["success"]),
        "quota_errors": sum(c["quota_errors"] for c in calls if c["success"]),
        "overload_errors": sum(c["overload_errors"] for c in calls if c["success"]),
        "fallback_calls": sum(1 for c in calls if c["fallback_used"]),
    }

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "pass_mark": PASS_SCORE,
        "project": project,
        "summary": {
            "tasks_total": total,
            "tasks_done": done,
            "tasks_failed": count("FAILED"),
            "tasks_blocked": count("BLOCKED"),
            "first_pass_tasks": sum(1 for t in task_list if t["first_pass"]),
            "first_pass_rate_pct": _pct(sum(1 for t in task_list if t["first_pass"]), total),
            "final_pass_rate_pct": _pct(done, total),
            "avg_quality_score": _mean(scores),
            "total_attempts": sum(t["attempts"] for t in task_list),
            "retries": sum(t["attempts"] - 1 for t in retried),
            "tasks_retried": len(retried),
            "fixed_by_retry": sum(1 for t in retried if t["status"] == "DONE"),
            "tests_passed": tests_pass,
            "tests_failed": tests_fail,
            "duration_s": wall,
        },
        "code": _code_stats(project_id),
        "llm": llm,
        "per_agent": [
            {"agent": k, "calls": v["calls"], "tokens": v["tokens"],
             "avg_latency_s": round(v["latency"] / v["calls"] / 1000, 1)}
            for k, v in sorted(per_agent.items())
        ],
        "per_model": [{"model": k, **v} for k, v in sorted(per_model.items(), key=lambda kv: -kv[1]["calls"])],
        "tasks": task_list,
    }


# ─────────────────────────────────────────
# Markdown
# ─────────────────────────────────────────
def _fmt(value, suffix="", empty="-"):
    return empty if value is None else f"{value}{suffix}"


def _dur(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes} min {secs:02d} s" if minutes else f"{secs} s"


def render_markdown(report: dict) -> str:
    p, s, llm = report["project"], report["summary"], report["llm"]
    lines = [
        f"# Run report: {p.get('title') or 'Project'}",
        "",
        f"- **Project ID:** `{p['id']}`",
        f"- **Status:** {p.get('status')}",
        f"- **Generated:** {report['generated_at'][:19].replace('T', ' ')} UTC",
        f"- **Requirement:** {(p.get('requirement') or '').strip()}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Tasks completed | {s['tasks_done']} / {s['tasks_total']} "
        f"({s['tasks_failed']} failed, {s['tasks_blocked']} blocked) |",
        f"| First-pass rate (passed on attempt 1) | {_fmt(s['first_pass_rate_pct'], '%')} |",
        f"| Final pass rate (task ended DONE) | {_fmt(s['final_pass_rate_pct'], '%')} |",
        f"| Average quality score (evaluator, 0-10) | {_fmt(s['avg_quality_score'])} |",
        f"| Attempts / retries | {s['total_attempts']} / {s['retries']} "
        f"({s['fixed_by_retry']} of {s['tasks_retried']} retried tasks ended DONE) |",
        f"| Tests passed / failed | {s['tests_passed']} / {s['tests_failed']} |",
        f"| Wall-clock time | {_dur(s['duration_s'])} |",
        f"| Generated code | {report['code']['files']} files, {report['code']['lines']} lines |",
        "",
        f"A task passes an attempt when the evaluator scores it {report['pass_mark']} or higher.",
        "",
        "## Tasks",
        "",
        "| Task | File | Status | Attempts | Scores per attempt | Tests (last) | Time |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in report["tasks"]:
        scores = " → ".join(_fmt(a["score"]) for a in t["attempt_log"]) or "-"
        tests = "-" if t["tests_passed"] is None else f"{t['tests_passed']} passed / {t['tests_failed']} failed"
        lines.append(
            f"| {t['title']} | `{t['filename'] or '-'}` | {t['status']} | {t['attempts']} | "
            f"{scores} | {tests} | {_dur(t['duration_s'])} |"
        )

    lines += [
        "",
        "## LLM usage and resilience",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| LLM calls | {llm['calls']} ({llm['failed_calls']} ended in failure) |",
        f"| Tokens used | {llm['tokens']:,} |",
        f"| Average latency | {_fmt(llm['avg_latency_s'], ' s')} |",
        f"| API errors absorbed by key/model rotation | {llm['absorbed_errors']} "
        f"({llm['quota_errors']} quota 429, {llm['overload_errors']} overload 503) |",
        f"| Calls answered by a fallback model | {llm['fallback_calls']} |",
        "",
        "| Agent | Calls | Tokens | Avg latency |",
        "|---|---|---|---|",
    ]
    for a in report["per_agent"]:
        lines.append(f"| {a['agent']} | {a['calls']} | {a['tokens']:,} | {a['avg_latency_s']} s |")
    lines += ["", "| Model | Calls | Tokens |", "|---|---|---|"]
    for m in report["per_model"]:
        lines.append(f"| {m['model']} | {m['calls']} | {m['tokens']:,} |")
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────
# API
# ─────────────────────────────────────────
@router.get("/{project_id}")
def get_report(project_id: str):
    return build_report(project_id)


@router.get("/{project_id}/markdown", response_class=PlainTextResponse)
def get_report_markdown(project_id: str):
    text = render_markdown(build_report(project_id))
    return PlainTextResponse(
        text, media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="RUN_REPORT_{project_id[:8]}.md"'},
    )
