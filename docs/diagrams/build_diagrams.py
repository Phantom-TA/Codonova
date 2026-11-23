"""
Generates the Codonova architecture and flow diagrams as SVG files.
Run:  python docs/diagrams/build_diagrams.py
Green = new in this version, purple = existing, blue = data, amber = external service.
"""
import os
from xml.sax.saxutils import escape

OUT = os.path.dirname(os.path.abspath(__file__))

BG, CARD, BORDER = "#0b1020", "#141b34", "#2a3358"
TEXT, MUTED = "#e2e8f0", "#94a3b8"
OLD, NEW, DATA, EXT = "#8b5cf6", "#10b981", "#38bdf8", "#f59e0b"
FONT = "Segoe UI, Arial, sans-serif"


class Svg:
    def __init__(self, w, h):
        self.w, self.h, self.parts = w, h, []
        self.parts.append(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'font-family="{FONT}">'
            f'<defs>'
            f'<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker>'
            f'<marker id="arrowg" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{NEW}"/></marker>'
            f'</defs>'
            f'<rect width="{w}" height="{h}" fill="{BG}"/>'
        )

    def text(self, x, y, s, size=16, fill=TEXT, weight="400", anchor="start", italic=False):
        style = ' font-style="italic"' if italic else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
            f'text-anchor="{anchor}"{style}>{escape(s)}</text>'
        )

    def box(self, x, y, w, h, stroke=BORDER, fill=CARD, r=12, dash=False, sw=1.6):
        d = ' stroke-dasharray="7 5"' if dash else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw}"{d}/>'
        )

    def card(self, x, y, w, h, title, sub=None, color=OLD, new=False, size=16):
        """A labelled card; `new` adds a green outline and a NEW tag."""
        stroke = NEW if new else color
        self.box(x, y, w, h, stroke=stroke, sw=2.2 if new else 1.6)
        cy = y + h / 2 + (0 if sub else 6)
        self.text(x + w / 2, cy - (6 if sub else 0), title, size=size, weight="600", anchor="middle")
        if sub:
            self.text(x + w / 2, cy + 17, sub, size=12.5, fill=MUTED, anchor="middle")
        if new:
            self.parts.append(
                f'<rect x="{x + w - 44}" y="{y - 9}" width="40" height="18" rx="9" fill="{NEW}"/>'
            )
            self.text(x + w - 24, y + 4, "NEW", size=10.5, fill="#04261c", weight="700", anchor="middle")

    def line(self, x1, y1, x2, y2, color=MUTED, dash=False, both=False, green=False):
        d = ' stroke-dasharray="6 5"' if dash else ""
        mark = "arrowg" if green else "arrow"
        start = f' marker-start="url(#{mark})"' if both else ""
        self.parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{NEW if green else color}" '
            f'stroke-width="2"{d}{start} marker-end="url(#{mark})"/>'
        )

    def path(self, d, green=False, dash=False):
        dd = ' stroke-dasharray="6 5"' if dash else ""
        mark = "arrowg" if green else "arrow"
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{NEW if green else MUTED}" stroke-width="2"{dd} '
            f'marker-end="url(#{mark})"/>'
        )

    def diamond(self, cx, cy, w, h, label, sub=None):
        pts = f"{cx},{cy - h / 2} {cx + w / 2},{cy} {cx},{cy + h / 2} {cx - w / 2},{cy}"
        self.parts.append(f'<polygon points="{pts}" fill="{CARD}" stroke="{EXT}" stroke-width="2"/>')
        self.text(cx, cy - (2 if sub else -5), label, size=15, weight="600", anchor="middle")
        if sub:
            self.text(cx, cy + 17, sub, size=12, fill=MUTED, anchor="middle")

    def legend(self, x, y):
        for i, (c, label) in enumerate([(NEW, "New in this version"), (OLD, "Existing"), (DATA, "Data store"), (EXT, "External service")]):
            self.box(x + i * 205, y, 16, 16, stroke=c, fill=c, r=4)
            self.text(x + i * 205 + 24, y + 13, label, size=13.5, fill=MUTED)

    def save(self, name):
        self.parts.append("</svg>")
        path = os.path.join(OUT, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(self.parts))
        print("wrote", path)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Architecture
# ─────────────────────────────────────────────────────────────────────────────
def architecture():
    s = Svg(1600, 930)
    s.text(40, 52, "Codonova architecture", size=30, weight="700")
    s.text(40, 80, "Graph-driven multi-agent system that plans, builds, tests, corrects and runs software", size=15.5, fill=MUTED)
    s.legend(700, 40)

    # Desktop shell + dashboard
    s.box(40, 105, 1520, 175, stroke=NEW, fill="#0e1830", dash=True, sw=2)
    s.text(62, 134, "Desktop app (Electron)", size=17, weight="700", fill=NEW)
    s.text(280, 134, "starts Docker + services, tray icon, native notifications", size=13.5, fill=MUTED)
    s.box(70, 150, 1460, 112, stroke=OLD, fill=CARD)
    s.text(90, 176, "React dashboard", size=16, weight="700")
    chips = [("Task Graph", False), ("Plan Review", True), ("Code Viewer", False), ("Live Preview", True),
             ("Run Report", True), ("Agent Analytics", False), ("Evaluations", False), ("Insights", False)]
    for i, (name, new) in enumerate(chips):
        x = 90 + i * 180
        stroke = NEW if new else OLD
        s.box(x, 192, 164, 44, stroke=stroke, sw=2 if new else 1.4, r=10)
        s.text(x + 82, 220, name, size=14.5, weight="600", anchor="middle")

    s.line(800, 280, 800, 332, both=True)
    s.text(815, 312, "HTTP + WebSocket (live events)", size=13, fill=MUTED)

    # Backend
    s.box(40, 335, 1520, 300, stroke=OLD, fill="#10172e")
    s.text(62, 364, "FastAPI backend", size=17, weight="700")
    mods = [("REST + WebSocket API", None, False), ("Plan editor API", "edit, add, delete, deps", True),
            ("Preview manager", "run generated apps", True), ("Run report", "real metrics", True),
            ("Scheduler", "dependency order", False), ("LLM client + router", "model fallback chain", False)]
    for i, (t, sub, new) in enumerate(mods):
        s.card(62 + i * 245, 380, 228, 56, t, sub, new=new, size=15)

    s.box(62, 456, 985, 160, stroke=BORDER, fill=BG, r=14)
    s.text(80, 482, "Agents", size=15, weight="700", fill=MUTED)
    agents = [("Planning", "tasks + files + API contract"), ("Developer", "writes one file per task"),
              ("Testing", "Jest, npm install"), ("Evaluator", "scores 0-10"), ("Debugging", "root cause + critique")]
    for i, (t, sub) in enumerate(agents):
        s.card(78 + i * 193, 496, 180, 66, t, sub, size=15.5)
    s.box(78, 574, 957, 32, stroke=OLD, fill="#1a1440", r=10)
    s.text(556, 596, "Self-correction engine: test, evaluate, debug, retry (up to 2 attempts), block dependents on failure",
           size=13.5, anchor="middle")

    s.box(1075, 456, 460, 160, stroke=NEW, fill=CARD, r=14, sw=2.2, dash=False)
    s.text(1095, 482, "Generated app sandbox", size=15, weight="700", fill=NEW)
    s.text(1095, 512, "one Node process per project", size=14, fill=TEXT)
    s.text(1095, 536, "ports 4100-4110, shown in an iframe", size=14, fill=TEXT)
    s.text(1095, 560, "minimal environment: no API keys,", size=14, fill=MUTED)
    s.text(1095, 580, "no database passwords", size=14, fill=MUTED)

    # Bottom layer
    s.card(40, 705, 360, 100, "Neo4j graph database", "plan graph, tasks, code versions,", color=DATA, size=16)
    s.text(220, 790, "results, plan edit history", size=12.5, fill=MUTED, anchor="middle")
    s.card(430, 705, 330, 100, "ChromaDB vector memory", "high-scoring code reused as context", color=DATA, size=16)
    s.card(790, 705, 400, 100, "Gemini model chain", "3.5 flash, 3.6 flash, preview, lite ...", color=EXT, size=16)
    s.text(990, 790, "OpenRouter as last resort", size=12.5, fill=MUTED, anchor="middle")
    s.card(1220, 705, 340, 100, "LangSmith", "traces every agent and LLM call", color=EXT, new=True, size=16)

    for x in (220, 595, 990, 1390):
        s.line(x, 635, x, 705, both=(x in (220, 595)), green=(x == 1390))
    s.text(235, 674, "tasks, results", size=12.5, fill=MUTED)
    s.text(610, 674, "similar context", size=12.5, fill=MUTED)
    s.text(1005, 674, "LLM calls, key + model rotation", size=12.5, fill=MUTED)
    s.text(1405, 674, "tracing", size=12.5, fill=NEW)

    s.text(40, 850, "Everything runs in Docker Compose: neo4j, chromadb, backend, frontend (nginx). "
                    "The desktop app only starts and wraps them.", size=14, fill=MUTED)
    s.text(40, 876, "The dashboard shows the pipeline live over WebSocket; the plan, code, tests and scores are stored as a graph.",
           size=14, fill=MUTED)
    s.save("architecture.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Pipeline flow
# ─────────────────────────────────────────────────────────────────────────────
def pipeline():
    s = Svg(1600, 860)
    s.text(40, 52, "Pipeline flow", size=30, weight="700")
    s.text(40, 80, "From a requirement to a running, tested app", size=15.5, fill=MUTED)
    s.legend(700, 40)

    # Row 1: plan
    y1 = 130
    s.card(40, y1, 300, 90, "1  Requirement", "plain-text description", size=17)
    s.card(400, y1, 330, 90, "2  Planning agent", "features, tasks, one file each, API contract", size=17)
    s.card(790, y1, 330, 90, "3  Plan review", "human edits and approves the plan", new=True, size=17)
    s.card(1180, y1, 380, 90, "4  Scheduler", "runs tasks in dependency order, in parallel", size=17)
    for x1, x2 in ((340, 400), (730, 790)):
        s.line(x1, y1 + 45, x2, y1 + 45)
    s.line(1120, y1 + 45, 1180, y1 + 45, green=True)
    s.text(760, y1 + 122, "edit tasks, dependencies, API shapes (cycles rejected)", size=12.5, fill=NEW, anchor="middle")

    # Row 2: task loop
    s.box(40, 270, 1520, 300, stroke=OLD, fill="#10172e", dash=True)
    s.text(420, 298, "Self-correction loop (per task)", size=16, weight="700")
    y2 = 340
    s.card(70, y2, 260, 90, "5  Developer", "writes the planned file", size=17)
    s.card(400, y2, 260, 90, "6  Testing", "npm install, Jest tests", size=17)
    s.card(730, y2, 260, 90, "7  Evaluator", "score 0-10 per attempt", size=17)
    s.diamond(1190, y2 + 45, 230, 120, "score >= 6?", "and tests ok")
    s.line(330, y2 + 45, 400, y2 + 45)
    s.line(660, y2 + 45, 730, y2 + 45)
    s.line(990, y2 + 45, 1075, y2 + 45)

    # yes branch
    s.card(1350, y2 - 5, 190, 100, "Task DONE", "saved to memory", size=16)
    s.line(1305, y2 + 45, 1350, y2 + 45)
    s.text(1327, y2 + 36, "yes", size=12.5, fill=NEW, anchor="middle", weight="700")

    # no branch -> debugger -> developer
    s.card(730, 475, 260, 70, "Debugging agent", "root cause + critique", size=16)
    s.path(f"M 1190 {y2 + 105} L 1190 510 L 990 510")
    s.text(1200, 470, "no", size=12.5, fill=EXT, weight="700")
    s.path(f"M 730 510 L 200 510 L 200 {y2 + 90}")
    s.text(470, 500, "retry with the critique (max 2 attempts)", size=12.5, fill=MUTED, anchor="middle")

    # failed
    s.card(1350, 475, 190, 70, "Task FAILED", "dependents blocked", color=EXT, size=15.5)
    s.line(1190, 510, 1350, 510, dash=True)
    s.text(1270, 502, "attempts used up", size=12.5, fill=EXT, anchor="middle")

    # entry arrow from scheduler into loop
    s.path("M 1370 220 L 1370 262 L 200 262 L 200 340")
    s.text(1100, 255, "each ready task is dispatched to the loop", size=12.5, fill=MUTED, anchor="middle")

    # Row 3: outputs
    y3 = 640
    s.card(40, y3, 360, 100, "8  Live preview", "install + run the app on a free port", new=True, size=17)
    s.card(470, y3, 360, 100, "9  Run report", "first-pass rate, retries, tokens, errors", new=True, size=17)
    s.card(900, y3, 330, 100, "10  ZIP export", "runnable project + reports", size=17)
    s.card(1300, y3, 260, 100, "LangSmith", "trace of the whole run", color=EXT, new=True, size=17)
    s.path("M 220 570 L 220 640", dash=True)
    s.text(236, 612, "when every task has finished", size=12.5, fill=MUTED)
    s.line(400, y3 + 50, 470, y3 + 50, green=True)
    s.line(830, y3 + 50, 900, y3 + 50)
    s.text(40, 790, "When every task has finished (done, failed or blocked) the project is marked COMPLETED and can be run, "
                    "inspected and exported.", size=14, fill=MUTED)
    s.save("pipeline-flow.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 3. LLM resilience
# ─────────────────────────────────────────────────────────────────────────────
def resilience():
    s = Svg(1600, 620)
    s.text(40, 52, "Surviving API limits", size=30, weight="700")
    s.text(40, 80, "A request that hits a quota (429) or overload (503) error moves on instead of failing the task", size=15.5, fill=MUTED)
    s.legend(700, 40)

    y = 150
    s.card(40, y, 250, 100, "LLM call", "from any agent", size=17)
    steps = [("Model 1", "gemini-3.5-flash"), ("Model 2", "gemini-3.6-flash"), ("Model 3", "gemini-3-flash-preview"),
             ("Model 4+", "lite models")]
    for i, (t, sub) in enumerate(steps):
        s.card(340 + i * 280, y, 240, 100, t, sub, color=EXT, new=(i == 0), size=17)
    for i in range(4):
        x1 = 290 if i == 0 else 340 + (i - 1) * 280 + 240
        s.line(x1, y + 50, 340 + i * 280, y + 50)
    for i in range(3):
        x = 340 + i * 280 + 120
        s.text(x + 150, y + 135, "429 / 503", size=12.5, fill=EXT, anchor="middle", weight="700")
    s.text(40, 330, "What makes this work", size=15, weight="700", fill=MUTED)
    s.card(40, 350, 330, 90, "Several API keys", "rotated per model", size=16, new=True)
    s.card(440, 350, 380, 90, "Cooldown memory", "exhausted models skipped for a while", size=16, new=True)
    s.card(890, 350, 330, 90, "OpenRouter", "last resort (needs a key)", color=EXT, size=16)
    s.card(1290, 350, 270, 90, "Clear error", "no hidden 401", size=16, new=True)
    for x1, x2 in ((370, 440), (820, 890), (1220, 1290)):
        s.line(x1, 395, x2, 395)
    s.text(40, 500, "Every call records how many errors it absorbed, so the run report can show them "
                    "(for example: 7 API errors absorbed, 0 failed calls).", size=14.5, fill=MUTED)
    s.text(40, 526, "Calls and tokens per model are also traced in LangSmith.", size=14.5, fill=MUTED)
    s.save("resilience.svg")


if __name__ == "__main__":
    architecture()
    pipeline()
    resilience()
