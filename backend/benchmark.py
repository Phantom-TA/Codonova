#!/usr/bin/env python3
"""
benchmark.py - run several requirements through the pipeline and record real metrics
=====================================================================================
Runs each requirement end to end (plan + implement), waits for it to finish, reads
the run report and appends one row per run to a CSV file. Prints the averages at
the end. Uses only the Python standard library.

Usage (backend must be running):
    python backend/benchmark.py                 # 5 built-in sample requirements
    python backend/benchmark.py --count 2       # just the first two
    python backend/benchmark.py --requirements my_requirements.txt   # one per line
    python backend/benchmark.py --out results.csv --timeout 1800

Each run makes dozens of LLM calls, so mind the free-tier quota: start with --count 1 or 2.
"""

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

DEFAULT_REQUIREMENTS = [
    "Build a simple counter app: a Node.js Express API with endpoints to get, increment and reset a counter in memory, and a static HTML page with buttons to use it.",
    "Build a todo list: a Node.js Express REST API to add, list, complete and delete todos stored in memory, plus a static HTML page to manage them.",
    "Build a personal notes app: a Node.js Express REST API to create, list, edit and delete notes stored in memory, plus a static HTML page to manage them.",
    "Build a student grade tracker: a Node.js Express API to add students, record grades and compute each student's average, plus a static HTML page to use it.",
    "Build an expense tracker: a Node.js Express API to add, list and delete expenses and show the total spent, plus a static HTML page to use it.",
]

COLUMNS = [
    "timestamp", "requirement", "project_id", "status", "tasks_total", "tasks_done", "tasks_failed",
    "tasks_blocked", "first_pass_rate_pct", "final_pass_rate_pct", "avg_quality_score",
    "total_attempts", "retries", "tests_passed", "tests_failed", "duration_s", "llm_calls",
    "tokens", "absorbed_api_errors", "quota_errors", "overload_errors", "code_lines", "error",
]


def call(base, method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def run_one(base, requirement, timeout_s):
    row = {c: "" for c in COLUMNS}
    row.update(timestamp=datetime.utcnow().isoformat(timespec="seconds"), requirement=requirement)
    try:
        started = call(base, "POST", "/api/start", {"requirement": requirement}, timeout=900)
        pid = started["project_id"]
        row["project_id"] = pid
        deadline = time.time() + timeout_s
        status = "RUNNING"
        while time.time() < deadline:
            time.sleep(10)
            status = call(base, "GET", f"/api/status/{pid}").get("status", "")
            print(f"   ... {status}", end="\r", flush=True)
            if str(status).startswith("COMPLETED"):
                break
        else:
            row["error"] = f"timed out after {timeout_s}s (status {status})"

        rep = call(base, "GET", f"/api/report/{pid}")
        s, llm = rep["summary"], rep["llm"]
        row.update(
            status=rep["project"]["status"], tasks_total=s["tasks_total"], tasks_done=s["tasks_done"],
            tasks_failed=s["tasks_failed"], tasks_blocked=s["tasks_blocked"],
            first_pass_rate_pct=s["first_pass_rate_pct"], final_pass_rate_pct=s["final_pass_rate_pct"],
            avg_quality_score=s["avg_quality_score"], total_attempts=s["total_attempts"],
            retries=s["retries"], tests_passed=s["tests_passed"], tests_failed=s["tests_failed"],
            duration_s=s["duration_s"], llm_calls=llm["calls"], tokens=llm["tokens"],
            absorbed_api_errors=llm["absorbed_errors"], quota_errors=llm["quota_errors"],
            overload_errors=llm["overload_errors"], code_lines=rep["code"]["lines"],
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        row["error"] = f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # keep going with the next requirement
        row["error"] = str(exc)[:300]
    return row


def mean(rows, key):
    values = [float(r[key]) for r in rows if r.get(key) not in ("", None)]
    return round(sum(values) / len(values), 1) if values else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8000", help="backend URL")
    ap.add_argument("--count", type=int, default=len(DEFAULT_REQUIREMENTS), help="how many requirements to run")
    ap.add_argument("--requirements", help="text file with one requirement per line")
    ap.add_argument("--timeout", type=int, default=1500, help="seconds to wait for each run")
    ap.add_argument("--out", default="benchmark_results.csv", help="CSV file to append to")
    args = ap.parse_args()

    if args.requirements:
        with open(args.requirements, encoding="utf-8") as fh:
            reqs = [line.strip() for line in fh if line.strip()]
    else:
        reqs = DEFAULT_REQUIREMENTS
    reqs = reqs[: args.count]

    try:
        call(args.base, "GET", "/health", timeout=10)
    except Exception as exc:
        sys.exit(f"Backend not reachable at {args.base}: {exc}")

    new_file = True
    try:
        new_file = open(args.out, encoding="utf-8").read() == ""
    except FileNotFoundError:
        pass

    rows = []
    with open(args.out, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        if new_file:
            writer.writeheader()
        for i, requirement in enumerate(reqs, 1):
            print(f"[{i}/{len(reqs)}] {requirement[:80]}")
            row = run_one(args.base, requirement, args.timeout)
            rows.append(row)
            writer.writerow(row)
            fh.flush()
            print(f"   -> {row['status'] or 'ERROR'}: {row['tasks_done']}/{row['tasks_total']} tasks, "
                  f"first-pass {row['first_pass_rate_pct']}%, final {row['final_pass_rate_pct']}%, "
                  f"avg score {row['avg_quality_score']}, {row['duration_s']}s  {row['error']}")

    ok = [r for r in rows if not r["error"]]
    print("\n=== Averages over", len(ok), "successful run(s) ===")
    for label, key in [
        ("first-pass rate %", "first_pass_rate_pct"), ("final pass rate %", "final_pass_rate_pct"),
        ("avg quality score", "avg_quality_score"), ("retries per run", "retries"),
        ("duration (s)", "duration_s"), ("LLM calls", "llm_calls"), ("tokens", "tokens"),
        ("API errors absorbed", "absorbed_api_errors"),
    ]:
        print(f"  {label:<22} {mean(ok, key)}")
    print(f"\nResults appended to {args.out}")


if __name__ == "__main__":
    main()
