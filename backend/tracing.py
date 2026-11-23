"""
tracing.py — Optional LangSmith observability for Codonova
===========================================================
Everything here is a no-op unless LangSmith is configured, so the rest of the
codebase can use these helpers unconditionally.

Enable with these environment variables (see .env.example):
    LANGSMITH_TRACING=true
    LANGSMITH_API_KEY=<your key>
    LANGSMITH_PROJECT=codonova

What gets traced:
    * every LLM call (prompt, response, tokens, latency) via wrap_client()
    * the pipeline hierarchy via @traceable on the scheduler, correction
      engine, each agent and the Jest test run
"""

import os
import logging
from typing import Any, Callable

logger = logging.getLogger("tracing")

_TRUE = {"1", "true", "yes", "on"}


def tracing_enabled() -> bool:
    """True when LangSmith tracing is switched on and an API key is present."""
    if os.getenv("LANGSMITH_TRACING", "").strip().lower() not in _TRUE:
        return False
    key = os.getenv("LANGSMITH_API_KEY", "").strip()
    return bool(key) and not key.startswith("your_")


_ENABLED = tracing_enabled()

try:  # langsmith is optional at runtime
    if _ENABLED:
        from langsmith import traceable as _ls_traceable
        from langsmith.wrappers import wrap_openai as _ls_wrap_openai
    else:
        _ls_traceable = None
        _ls_wrap_openai = None
except Exception as exc:  # pragma: no cover - missing/incompatible package
    logger.warning(f"LangSmith unavailable, tracing disabled: {exc}")
    _ENABLED = False
    _ls_traceable = None
    _ls_wrap_openai = None

if _ENABLED:
    # Make sure the SDK sees the tracing flag regardless of which env name it reads.
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_PROJECT", "codonova")
    logger.info(f"LangSmith tracing ON (project={os.environ['LANGSMITH_PROJECT']})")
else:
    logger.info("LangSmith tracing OFF")


def is_active() -> bool:
    return _ENABLED


def _drop_self(inputs: dict) -> dict:
    """Keep traces readable: don't log the agent object itself as an input."""
    return {k: v for k, v in inputs.items() if k != "self"}


def traceable(name: str | None = None, run_type: str = "chain", **kwargs) -> Callable:
    """Decorator that traces a function, or does nothing when tracing is off."""
    def decorator(func: Callable) -> Callable:
        if not _ENABLED or _ls_traceable is None:
            return func
        try:
            return _ls_traceable(
                name=name or func.__name__,
                run_type=run_type,
                process_inputs=_drop_self,
                **kwargs,
            )(func)
        except Exception as exc:  # never let tracing break the pipeline
            logger.warning(f"Could not trace {func.__name__}: {exc}")
            return func
    return decorator


def wrap_client(client: Any) -> Any:
    """Wrap an OpenAI-compatible client so every call is traced."""
    if not _ENABLED or _ls_wrap_openai is None:
        return client
    try:
        return _ls_wrap_openai(client)
    except Exception as exc:
        logger.warning(f"Could not wrap LLM client: {exc}")
        return client


def call_extra(name: str, metadata: dict, tags: list[str] | None = None) -> dict:
    """
    Extra kwargs to pass to a wrapped client's create() call so each LLM call is
    named and tagged. Returns {} when tracing is off (a plain client would
    reject the unknown keyword).
    """
    if not _ENABLED:
        return {}
    return {"langsmith_extra": {"name": name, "metadata": metadata, "tags": tags or []}}


_project_url_cache: str | None = None


def project_url() -> str | None:
    """Best-effort link to the LangSmith project page."""
    global _project_url_cache
    if not _ENABLED:
        return None
    if _project_url_cache:
        return _project_url_cache
    try:
        from langsmith import Client
        project = Client().read_project(project_name=os.environ["LANGSMITH_PROJECT"])
        _project_url_cache = getattr(project, "url", None)
    except Exception as exc:
        logger.debug(f"Could not resolve LangSmith project URL: {exc}")
    return _project_url_cache or "https://smith.langchain.com/"


def status() -> dict:
    return {
        "enabled": _ENABLED,
        "project": os.getenv("LANGSMITH_PROJECT", "codonova") if _ENABLED else None,
        "url": project_url() if _ENABLED else None,
    }


def annotate(metadata: dict | None = None, tags: list[str] | None = None) -> None:
    """Attach metadata/tags to the trace that is currently running (if any)."""
    if not _ENABLED:
        return
    try:
        from langsmith.run_helpers import get_current_run_tree
        run = get_current_run_tree()
        if run is None:
            return
        if metadata:
            run.metadata.update(metadata)
        if tags:
            run.tags.extend(t for t in tags if t not in run.tags)
    except Exception as exc:
        logger.debug(f"annotate failed: {exc}")
