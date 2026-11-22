"""
plan_editor.py — Human-in-the-loop plan editing
================================================
After the PlanningAgent has produced a task graph (project status PLANNED) the
user can review and edit it before approving execution:

  * edit / add / delete tasks and features
  * add / remove task dependencies (cycles are rejected)
  * edit the REST API contract that frontend and backend code must follow

Rules:
  * Editing is only allowed while the project is PLANNED. Once execution starts
    the plan is locked.
  * Every change is recorded as a PlanRevision node in Neo4j (edit history).
"""

import json
import uuid
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from graph.neo4j_client import query_graph, store_api_schema, get_api_schema

logger = logging.getLogger("plan_editor")

router = APIRouter(prefix="/api", tags=["plan-editor"])

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


# ─────────────────────────────────────────
# Request models
# ─────────────────────────────────────────
class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    priority: Optional[int] = Field(None, ge=1, le=99)
    feature_id: Optional[str] = None  # move the task to another feature


class TaskCreate(BaseModel):
    feature_id: str
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    priority: int = Field(5, ge=1, le=99)
    depends_on: list[str] = []


class DependencyBody(BaseModel):
    depends_on: str


class FeatureCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    priority: int = Field(1, ge=1, le=99)


class FeatureUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    priority: Optional[int] = Field(None, ge=1, le=99)


class Endpoint(BaseModel):
    method: str
    path: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=500)
    request: str = Field("", max_length=1000)    # example JSON body
    response: str = Field("", max_length=1000)   # example JSON response


class ApiSchemaBody(BaseModel):
    endpoints: list[Endpoint] = []
    base_port: int = Field(4000, ge=1024, le=65535)


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────
def _now() -> str:
    return datetime.utcnow().isoformat()


def _project_status(project_id: str) -> str:
    rows = query_graph(
        "MATCH (p:Project {id: $pid}) RETURN p.status AS status", {"pid": project_id}
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Project not found")
    return rows[0]["status"]


def _require_editable(project_id: str) -> None:
    status = _project_status(project_id)
    if status != "PLANNED":
        raise HTTPException(
            status_code=409,
            detail=f"Plan is locked (project status is {status}). Only PLANNED projects can be edited.",
        )


def _task_info(task_id: str) -> dict:
    rows = query_graph(
        """
        MATCH (t:Task {id: $tid})
        OPTIONAL MATCH (f:Feature)-[:HAS_TASK]->(t)
        RETURN t.id AS id, t.project_id AS project_id, t.title AS title,
               t.description AS description, t.priority AS priority,
               f.id AS feature_id
        """,
        {"tid": task_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Task not found")
    return rows[0]


def _feature_info(feature_id: str) -> dict:
    rows = query_graph(
        """
        MATCH (p:Project)-[:HAS_FEATURE]->(f:Feature {id: $fid})
        RETURN f.id AS id, p.id AS project_id, f.title AS title,
               f.description AS description, f.priority AS priority,
               f.logical_id AS logical_id
        """,
        {"fid": feature_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Feature not found")
    return rows[0]


def _record_revision(
    project_id: str, action: str, entity_type: str, entity_id: str,
    before: Optional[dict], after: Optional[dict],
) -> None:
    """Store an edit-history entry. Never lets a history failure break an edit."""
    try:
        seq = query_graph(
            "MATCH (p:Project {id: $pid})-[:HAS_REVISION]->(r:PlanRevision) RETURN count(r) AS c",
            {"pid": project_id},
        )[0]["c"] + 1
        query_graph(
            """
            MATCH (p:Project {id: $pid})
            CREATE (r:PlanRevision {
                id: $id, seq: $seq, action: $action, entity_type: $etype,
                entity_id: $eid, before: $before, after: $after, created_at: $at
            })
            CREATE (p)-[:HAS_REVISION]->(r)
            """,
            {
                "pid": project_id, "id": str(uuid.uuid4()), "seq": seq,
                "action": action, "etype": entity_type, "eid": entity_id,
                "before": json.dumps(before) if before is not None else "",
                "after": json.dumps(after) if after is not None else "",
                "at": _now(),
            },
        )
    except Exception as exc:
        logger.warning(f"Could not record plan revision: {exc}")


def _refresh_task_count(project_id: str) -> None:
    query_graph(
        """
        MATCH (p:Project {id: $pid})
        OPTIONAL MATCH (p)-[:HAS_FEATURE]->(:Feature)-[:HAS_TASK]->(t:Task)
        WITH p, count(t) AS n
        SET p.task_count = n
        """,
        {"pid": project_id},
    )


def _same_project_task(task_id: str, project_id: str) -> dict:
    info = _task_info(task_id)
    if info["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="Task belongs to a different project")
    return info


def _would_create_cycle(task_id: str, dep_id: str) -> bool:
    """task -> dep creates a cycle if dep can already reach task (or they are the same)."""
    rows = query_graph(
        """
        MATCH path = (d:Task {id: $dep})-[:DEPENDS_ON*0..]->(t:Task {id: $tid})
        RETURN count(path) AS c
        """,
        {"dep": dep_id, "tid": task_id},
    )
    return bool(rows and rows[0]["c"] > 0)


def ensure_executable(project_id: str) -> None:
    """Raise 400 if the plan has no tasks to run (called before execution starts)."""
    rows = query_graph(
        """
        MATCH (p:Project {id: $pid})
        OPTIONAL MATCH (p)-[:HAS_FEATURE]->(:Feature)-[:HAS_TASK]->(t:Task)
        RETURN count(t) AS n
        """,
        {"pid": project_id},
    )
    if not rows or rows[0]["n"] == 0:
        raise HTTPException(status_code=400, detail="The plan has no tasks. Add at least one task first.")


# ─────────────────────────────────────────
# Read
# ─────────────────────────────────────────
@router.get("/projects/{project_id}/plan")
def get_plan(project_id: str):
    """Full editable plan: features with their tasks and dependencies, plus API contract."""
    proj = query_graph(
        """
        MATCH (p:Project {id: $pid})
        RETURN p.id AS id, p.title AS title, p.description AS description,
               p.requirement AS requirement, p.status AS status
        """,
        {"pid": project_id},
    )
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    project = proj[0]
    project["editable"] = project["status"] == "PLANNED"

    features = query_graph(
        """
        MATCH (:Project {id: $pid})-[:HAS_FEATURE]->(f:Feature)
        RETURN f.id AS id, f.title AS title, f.description AS description,
               coalesce(f.priority, 1) AS priority
        ORDER BY priority, title
        """,
        {"pid": project_id},
    )
    tasks = query_graph(
        """
        MATCH (:Project {id: $pid})-[:HAS_FEATURE]->(f:Feature)-[:HAS_TASK]->(t:Task)
        OPTIONAL MATCH (t)-[:DEPENDS_ON]->(d:Task)
        RETURN t.id AS id, f.id AS feature_id, t.title AS title,
               t.description AS description, coalesce(t.type, 'CODE') AS type,
               coalesce(t.priority, 5) AS priority, t.status AS status,
               collect(d.id) AS depends_on
        ORDER BY priority, title
        """,
        {"pid": project_id},
    )

    by_feature: dict[str, list] = {f["id"]: [] for f in features}
    for t in tasks:
        by_feature.setdefault(t["feature_id"], []).append(t)
    for f in features:
        f["tasks"] = by_feature.get(f["id"], [])

    warnings = []
    if not tasks:
        warnings.append("The plan has no tasks.")
    for f in features:
        if not f["tasks"]:
            warnings.append(f"Feature '{f['title']}' has no tasks.")

    return {
        "project": project,
        "features": features,
        "api_schema": get_api_schema(project_id),
        "warnings": warnings,
    }


@router.get("/projects/{project_id}/revisions")
def list_revisions(project_id: str):
    """Edit history of the plan (newest first)."""
    _project_status(project_id)
    return query_graph(
        """
        MATCH (:Project {id: $pid})-[:HAS_REVISION]->(r:PlanRevision)
        RETURN r.seq AS seq, r.action AS action, r.entity_type AS entity_type,
               r.entity_id AS entity_id, r.before AS before, r.after AS after,
               r.created_at AS created_at
        ORDER BY r.seq DESC LIMIT 100
        """,
        {"pid": project_id},
    )


# ─────────────────────────────────────────
# Tasks
# ─────────────────────────────────────────
@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdate):
    info = _task_info(task_id)
    pid = info["project_id"]
    _require_editable(pid)

    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to update")

    new_feature = None
    if "feature_id" in changes:
        new_feature = _feature_info(changes.pop("feature_id"))
        if new_feature["project_id"] != pid:
            raise HTTPException(status_code=400, detail="Feature belongs to a different project")

    sets, params = [], {"tid": task_id, "at": _now()}
    for key in ("title", "description", "priority"):
        if key in changes:
            sets.append(f"t.{key} = ${key}")
            params[key] = changes[key]
    if new_feature:
        sets.append("t.feature_logical_id = $flid")
        params["flid"] = new_feature["logical_id"] or ""
    sets.append("t.updated_at = $at")
    query_graph(f"MATCH (t:Task {{id: $tid}}) SET {', '.join(sets)}", params)

    if new_feature and new_feature["id"] != info["feature_id"]:
        query_graph(
            """
            MATCH (t:Task {id: $tid})
            OPTIONAL MATCH (:Feature)-[old:HAS_TASK]->(t)
            DELETE old
            WITH t
            MATCH (f:Feature {id: $fid})
            MERGE (f)-[:HAS_TASK]->(t)
            """,
            {"tid": task_id, "fid": new_feature["id"]},
        )

    before = {k: info.get(k) for k in ("title", "description", "priority", "feature_id")}
    after = dict(before)
    after.update({k: v for k, v in changes.items()})
    if new_feature:
        after["feature_id"] = new_feature["id"]
    _record_revision(pid, "update", "task", task_id, before, after)
    return {"ok": True, "task_id": task_id}


@router.post("/projects/{project_id}/tasks")
def create_task(project_id: str, body: TaskCreate):
    _require_editable(project_id)
    feature = _feature_info(body.feature_id)
    if feature["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="Feature belongs to a different project")

    deps = list(dict.fromkeys(body.depends_on))
    for dep_id in deps:
        _same_project_task(dep_id, project_id)

    task_id = str(uuid.uuid4())
    query_graph(
        """
        MATCH (f:Feature {id: $fid})
        CREATE (t:Task {
            id: $tid, logical_id: $lid, project_id: $pid, title: $title,
            description: $desc, type: 'CODE', status: 'PENDING', priority: $prio,
            feature_logical_id: $flid, created_at: $at
        })
        CREATE (f)-[:HAS_TASK]->(t)
        """,
        {
            "fid": feature["id"], "tid": task_id, "lid": f"t_{task_id[:6]}",
            "pid": project_id, "title": body.title, "desc": body.description,
            "prio": body.priority, "flid": feature["logical_id"] or "", "at": _now(),
        },
    )
    # A brand-new task has no dependents, so adding its dependencies can't create a cycle.
    for dep_id in deps:
        query_graph(
            "MATCH (a:Task {id: $a}), (b:Task {id: $b}) MERGE (a)-[:DEPENDS_ON]->(b)",
            {"a": task_id, "b": dep_id},
        )
    _refresh_task_count(project_id)
    _record_revision(project_id, "create", "task", task_id, None, {
        "title": body.title, "description": body.description, "priority": body.priority,
        "feature_id": feature["id"], "depends_on": deps,
    })
    return {"ok": True, "task_id": task_id}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    info = _task_info(task_id)
    pid = info["project_id"]
    _require_editable(pid)

    dependents = query_graph(
        "MATCH (x:Task)-[:DEPENDS_ON]->(:Task {id: $tid}) RETURN x.title AS title",
        {"tid": task_id},
    )
    query_graph(
        """
        MATCH (t:Task {id: $tid})
        OPTIONAL MATCH (t)-[:HAS_SUBTASK]->(s:SubTask)
        DETACH DELETE s, t
        """,
        {"tid": task_id},
    )
    _refresh_task_count(pid)
    _record_revision(pid, "delete", "task", task_id,
                     {"title": info["title"], "description": info["description"],
                      "feature_id": info["feature_id"]}, None)
    return {"ok": True, "unlinked_dependents": [d["title"] for d in dependents]}


@router.post("/tasks/{task_id}/dependencies")
def add_dependency(task_id: str, body: DependencyBody):
    info = _task_info(task_id)
    pid = info["project_id"]
    _require_editable(pid)
    _same_project_task(body.depends_on, pid)

    if task_id == body.depends_on:
        raise HTTPException(status_code=400, detail="A task cannot depend on itself")
    if _would_create_cycle(task_id, body.depends_on):
        raise HTTPException(
            status_code=409,
            detail="That dependency would create a cycle (the other task already depends on this one).",
        )
    query_graph(
        "MATCH (a:Task {id: $a}), (b:Task {id: $b}) MERGE (a)-[:DEPENDS_ON]->(b)",
        {"a": task_id, "b": body.depends_on},
    )
    _record_revision(pid, "add_dependency", "task", task_id, None, {"depends_on": body.depends_on})
    return {"ok": True}


@router.delete("/tasks/{task_id}/dependencies/{dep_id}")
def remove_dependency(task_id: str, dep_id: str):
    info = _task_info(task_id)
    pid = info["project_id"]
    _require_editable(pid)
    query_graph(
        "MATCH (:Task {id: $a})-[r:DEPENDS_ON]->(:Task {id: $b}) DELETE r",
        {"a": task_id, "b": dep_id},
    )
    _record_revision(pid, "remove_dependency", "task", task_id, {"depends_on": dep_id}, None)
    return {"ok": True}


# ─────────────────────────────────────────
# Features
# ─────────────────────────────────────────
@router.post("/projects/{project_id}/features")
def create_feature(project_id: str, body: FeatureCreate):
    _require_editable(project_id)
    feature_id = str(uuid.uuid4())
    query_graph(
        """
        MATCH (p:Project {id: $pid})
        CREATE (f:Feature {
            id: $fid, logical_id: $lid, title: $title, description: $desc,
            acceptance_criteria: '[]', priority: $prio, created_at: $at
        })
        CREATE (p)-[:HAS_FEATURE]->(f)
        """,
        {"pid": project_id, "fid": feature_id, "lid": f"f_{feature_id[:6]}",
         "title": body.title, "desc": body.description, "prio": body.priority, "at": _now()},
    )
    _record_revision(project_id, "create", "feature", feature_id, None, body.model_dump())
    return {"ok": True, "feature_id": feature_id}


@router.patch("/features/{feature_id}")
def update_feature(feature_id: str, body: FeatureUpdate):
    info = _feature_info(feature_id)
    pid = info["project_id"]
    _require_editable(pid)

    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to update")
    sets = [f"f.{k} = ${k}" for k in changes]
    query_graph(
        f"MATCH (f:Feature {{id: $fid}}) SET {', '.join(sets)}, f.updated_at = $at",
        {"fid": feature_id, "at": _now(), **changes},
    )
    before = {k: info.get(k) for k in changes}
    _record_revision(pid, "update", "feature", feature_id, before, changes)
    return {"ok": True}


@router.delete("/features/{feature_id}")
def delete_feature(feature_id: str):
    """Delete a feature together with all of its tasks."""
    info = _feature_info(feature_id)
    pid = info["project_id"]
    _require_editable(pid)

    removed = query_graph(
        "MATCH (:Feature {id: $fid})-[:HAS_TASK]->(t:Task) RETURN count(t) AS n",
        {"fid": feature_id},
    )[0]["n"]
    query_graph(
        """
        MATCH (f:Feature {id: $fid})
        OPTIONAL MATCH (f)-[:HAS_TASK]->(t:Task)
        OPTIONAL MATCH (t)-[:HAS_SUBTASK]->(s:SubTask)
        DETACH DELETE s, t, f
        """,
        {"fid": feature_id},
    )
    _refresh_task_count(pid)
    _record_revision(pid, "delete", "feature", feature_id,
                     {"title": info["title"], "tasks_removed": removed}, None)
    return {"ok": True, "tasks_removed": removed}


# ─────────────────────────────────────────
# API contract
# ─────────────────────────────────────────
@router.get("/projects/{project_id}/api-schema")
def read_api_schema(project_id: str):
    _project_status(project_id)
    return get_api_schema(project_id)


@router.put("/projects/{project_id}/api-schema")
def write_api_schema(project_id: str, body: ApiSchemaBody):
    _require_editable(project_id)

    endpoints, seen = [], set()
    for ep in body.endpoints:
        method = ep.method.strip().upper()
        path = ep.path.strip()
        if method not in HTTP_METHODS:
            raise HTTPException(status_code=400, detail=f"Invalid HTTP method: {ep.method}")
        if not path.startswith("/"):
            raise HTTPException(status_code=400, detail=f"Path must start with '/': {path}")
        if (method, path) in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate endpoint: {method} {path}")
        seen.add((method, path))
        endpoints.append({
            "method": method, "path": path, "description": ep.description.strip(),
            "request": ep.request.strip(), "response": ep.response.strip(),
        })

    before = get_api_schema(project_id)
    schema = {"endpoints": endpoints, "base_port": body.base_port}
    store_api_schema(project_id, schema)
    _record_revision(project_id, "update", "api_schema", project_id,
                     {"endpoint_count": len(before.get("endpoints", []))},
                     {"endpoint_count": len(endpoints)})
    return schema
