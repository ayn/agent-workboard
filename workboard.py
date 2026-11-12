#!/usr/bin/env python3
"""A local, read-only work dashboard for people, agents and scripts. No dependencies."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
import urllib.request

__version__ = "0.1.1"
STATUSES = {"working", "waiting", "background", "queued", "done"}
DEMO = {
    "updated_at": "2030-01-01T12:00:00Z",
    "tasks": [
        {"id": "interface", "title": "Build the project dashboard", "status": "working",
         "detail": "Check the small-screen layout and keyboard navigation."},
        {"id": "review", "title": "Review the changes", "status": "queued",
         "detail": "Run checks and prepare a pull request."},
        {"id": "setup", "title": "Set up the workspace", "status": "done",
         "detail": "Example data only. Replace it with your own project notes."}],
    "team": {"updated_at": "2030-01-01T12:00:00Z", "members": [
        {"id": "coordinator", "name": "Coordinator", "role": "Agent or human",
         "status": "working", "task": "Review the interface and update the work list.",
         "updated_at": "2030-01-01T12:00:00Z"},
        {"id": "reviewer", "name": "Test reviewer", "role": "Example helper", "parent": "Coordinator",
         "status": "done", "task": "Finished the initial example checks.",
         "updated_at": "2030-01-01T11:55:00Z"}]},
    "cards": [{"label": "Checks passed", "value": 12,
               "note": "Synthetic example metric", "progress": {"value": 12, "max": 16}},
              {"label": "Files reviewed", "value": 8, "note": "Fictional project data"},
              {"label": "Open tasks", "value": 2, "note": "Working and queued"}],
}


def text(value, limit=4000):
    return value[:limit] if isinstance(value, str) else ""


def numeric(value):
    return type(value) in (int, float) and -1e18 <= value <= 1e18 and math.isfinite(value)


def safe_url(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value) or "\\" in value:
        return None
    if value.startswith("/") and not value.startswith("//"):
        return value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in {"https", "http"} and parsed.hostname
                and parsed.username is None and parsed.password is None):
            _ = parsed.port
            return value
    except ValueError:
        pass
    return None


def project(raw):
    """Explicit presentation fields only; arbitrary input metadata never reaches HTTP."""
    raw = raw if isinstance(raw, dict) else {}
    result = {"updated_at": text(raw.get("updated_at")), "tasks": [], "cards": [],
              "team": {"updated_at": "", "members": []}, "links": []}
    tasks = raw.get("tasks")
    for task in tasks[:5000] if isinstance(tasks, list) else []:
        if not isinstance(task, dict):
            continue
        row = {key: text(task.get(key)) for key in ("id", "title", "detail", "updated_at")}
        row["status"] = task.get("status") if isinstance(task.get("status"), str) and task.get("status") in STATUSES else "queued"
        if url := safe_url(task.get("url")):
            row["url"] = url
        result["tasks"].append(row)
    team = raw.get("team")
    if isinstance(team, dict):
        result["team"]["updated_at"] = text(team.get("updated_at"))
        members = team.get("members")
        for member in members[:500] if isinstance(members, list) else []:
            if not isinstance(member, dict):
                continue
            row = {key: text(member.get(key)) for key in
                   ("id", "name", "role", "parent", "task", "updated_at")}
            row["status"] = member.get("status") if isinstance(member.get("status"), str) and member.get("status") in STATUSES else "queued"
            result["team"]["members"].append(row)
    cards = raw.get("cards")
    for card in cards[:50] if isinstance(cards, list) else []:
        if not isinstance(card, dict):
            continue
        row = {key: text(card.get(key)) for key in ("id", "label", "note", "unit")}
        value = card.get("value")
        row["value"] = value if numeric(value) else text(value)
        precision = card.get("precision", 0)
        row["precision"] = precision if type(precision) is int and 0 <= precision <= 3 else 0
        progress = card.get("progress")
        if (isinstance(progress, dict) and numeric(progress.get("value"))
                and numeric(progress.get("max")) and 0 <= progress["value"] <= progress["max"]
                and progress["max"] > 0):
            row["progress"] = {"value": progress["value"], "max": progress["max"]}
        # Human-facing estimates belong to the producer, not the generic server.
        row["estimate"] = text(card.get("estimate"))
        result["cards"].append(row)
    links = raw.get("links")
    for link in links[:20] if isinstance(links, list) else []:
        if isinstance(link, dict) and (url := safe_url(link.get("url"))):
            result["links"].append({"label": text(link.get("label")), "url": url})
    return result


@dataclass
class Config:
    state_dir: Path = field(default_factory=lambda: Path(".workboard"))
    title: str = "Agent Workboard"
    demo: bool = False
    file_routes: dict[str, Path] = field(default_factory=dict)
    sources_file: Path | None = None
    include_local: bool = False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def fetch_source(url):
    """Only trusted local configuration chooses destinations; never a query argument."""
    if not safe_url(url) or url.startswith("/"):
        raise ValueError("Invalid source")
    parsed = urlsplit(url)
    if parsed.path != "/api/status" or parsed.query or parsed.fragment:
        raise ValueError("Expected a producer /api/status endpoint")
    opener = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Agent-Workboard/0.1"})
    with opener.open(request, timeout=2) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise ValueError("Source too large")
    value = json.loads(body)
    if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
        raise ValueError("Invalid producer snapshot")
    return project(value)


class Board:
    def __init__(self, config=None, *, snapshot_fn=None):
        self.config = config or Config()
        self.snapshot_fn = snapshot_fn
        self._files = {}
        self._unavailable = set()
        self._last = {}
        self._lock = threading.RLock()
        self._source_lock = threading.Lock()
        self._source_config = []
        self._remote = {}
        self._remote_urls = {}
        self._next_poll = 0

    def _read(self, name, default):
        path = self.config.state_dir / name
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Expected an object")
            self._files[name] = value
        except (OSError, ValueError):
            if name in self._files or path.exists():
                self._unavailable.add(name)
        return self._files.get(name, default)

    def local_snapshot(self):
        with self._lock:
            try:
                self._unavailable.clear()
                if self.snapshot_fn:
                    raw = self.snapshot_fn()
                    if not isinstance(raw, dict):
                        raise ValueError("Expected an object")
                elif self.config.demo:
                    raw = DEMO
                else:
                    raw = dict(self._read("tasks.json", {}))
                    raw["team"] = self._read("team.json", {})
                self._last = project(raw)
                if self._unavailable:
                    self._last["warning"] = "State file unavailable; retaining the last valid data."
            except Exception:
                # A producer failure must not expose exception text or drop the
                # last good state. A generic warning is safe to show to clients.
                self._last = dict(self._last or project({}))
                self._last["warning"] = "Source temporarily unavailable; showing the last snapshot."
            return {**self._last, "served_at": datetime.now(timezone.utc).isoformat()}

    def snapshot(self):
        local = self.local_snapshot()
        if self.config.sources_file is None:
            return local
        with self._source_lock:
            if time.monotonic() >= self._next_poll:
                config_ok = True
                try:
                    value = json.loads(self.config.sources_file.read_text(encoding="utf-8"))
                    entries = value.get("sources") if isinstance(value, dict) else None
                    if not isinstance(entries, list):
                        raise ValueError("Invalid sources")
                    sources = []
                    seen = {"local"}
                    for entry in entries[:20]:
                        if not isinstance(entry, dict):
                            raise ValueError("Invalid source")
                        identity = text(entry.get("id"), 100)
                        if not identity or identity in seen or not text(entry.get("title")):
                            raise ValueError("Source IDs must be unique; local is reserved")
                        seen.add(identity)
                        # Validate before scheduling any requests.
                        url = entry.get("url")
                        if (not safe_url(url) or url.startswith("/") or urlsplit(url).path != "/api/status"
                                or urlsplit(url).query or urlsplit(url).fragment):
                            raise ValueError("Invalid source URL")
                        sources.append({"id": identity, "title": text(entry["title"]), "url": url})
                    self._source_config = sources
                except (OSError, ValueError):
                    config_ok = False
                with ThreadPoolExecutor(max_workers=4) as pool:
                    jobs = [(s, pool.submit(fetch_source, s["url"])) for s in self._source_config]
                    current = {}
                    for source, future in jobs:
                        previous = (self._remote.get(source["id"], {})
                                    if self._remote_urls.get(source["id"]) == source["url"] else {})
                        try:
                            data = future.result()
                            available = True
                        except Exception:
                            # Never include URLs, error bodies or private exception details.
                            data = previous.get("data", project({}))
                            available = False
                        current[source["id"]] = {"id": source["id"], "title": source["title"],
                                                "available": available, "data": data}
                    self._remote = current
                    self._remote_urls = {s["id"]: s["url"] for s in self._source_config}
                self._sources_ok = config_ok
                self._next_poll = time.monotonic() + 2
            # The producer endpoint remains local-only. Hubs read that endpoint,
            # never another hub's combined view, preventing recursive aggregation.
            workspaces = []
            if self.config.include_local:
                workspaces.append({"id": "local", "title": self.config.title, "available": True,
                                   "data": {k: v for k, v in local.items() if k != "served_at"}})
            workspaces.extend(self._remote.values())
            state = {**local, "workspaces": workspaces}
            if not getattr(self, "_sources_ok", True):
                state["warning"] = "Workspace configuration unavailable; retaining the last configuration."
            return state


def event_frames(snapshot_fn, wait=time.sleep, clock=time.monotonic, heartbeat_seconds=15):
    previous = None
    last_sent = clock()
    while True:
        state = snapshot_fn()
        fingerprint = json.dumps({k: v for k, v in state.items() if k != "served_at"}, sort_keys=True)
        if fingerprint != previous:
            yield ("retry: 3000\nevent: status\ndata: " + json.dumps(state) + "\n\n").encode()
            previous = fingerprint
            last_sent = clock()
        elif clock() - last_sent >= heartbeat_seconds:
            yield b": heartbeat\n\n"
            last_sent = clock()
        wait(1)


def make_handler(board):
    class Handler(BaseHTTPRequestHandler):
        def send_board_headers(self, content_type, *, length=None, stream=False):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache, no-transform" if stream else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if stream:
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
            elif length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def do_HEAD(self):
            self._get(head=True)

        def do_GET(self):
            self._get()

        def _get(self, head=False):
            path = self.path.split("?", 1)[0]
            if path == "/api/events":
                self.send_board_headers("text/event-stream; charset=utf-8", stream=True)
                self.close_connection = True
                if head:
                    return
                self.connection.settimeout(10)
                try:
                    for frame in event_frames(board.snapshot):
                        self.wfile.write(frame); self.wfile.flush()
                except (OSError, TimeoutError):
                    pass
                return
            if path == "/":
                body = HTML.replace("@@TITLE@@", html.escape(board.config.title)).encode()
                kind = "text/html; charset=utf-8"
            elif path == "/api/status":
                body = json.dumps(board.local_snapshot()).encode(); kind = "application/json"
            elif path == "/api/workspaces":
                body = json.dumps(board.snapshot()).encode(); kind = "application/json"
            elif path in board.config.file_routes:
                try:
                    body = Path(board.config.file_routes[path]).read_bytes()
                except OSError:
                    self.send_error(404); return
                kind = "text/html; charset=utf-8"
            else:
                self.send_error(404); return
            self.send_board_headers(kind, length=len(body))
            if not head:
                self.wfile.write(body)

        def do_POST(self):
            self.send_error(405, "Read-only dashboard")

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def log_message(self, *_):
            pass
    return Handler


def make_server(board, *, host="127.0.0.1", port=8080):
    server = ThreadingHTTPServer((host, port), make_handler(board))
    server.daemon_threads = True
    return server


def serve(config=None, *, snapshot_fn=None, host="127.0.0.1", port=8080):
    board = Board(config, snapshot_fn=snapshot_fn)
    with make_server(board, host=host, port=port) as server:
        print(f"Agent Workboard at http://{host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def initialize(folder):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    for name, data in (("tasks.json", {k: v for k, v in DEMO.items() if k != "team"}),
                       ("team.json", DEMO["team"])):
        try:
            with (folder / name).open("x", encoding="utf-8") as f:
                json.dump(data, f, indent=2); f.write("\n")
        except FileExistsError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; default is local-only")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--title", default="Agent Workboard")
    parser.add_argument("--state-dir", type=Path, default=Path(".workboard"))
    parser.add_argument("--demo", action="store_true", help="Show built-in fictional data; no files needed")
    parser.add_argument("--init", action="store_true", help="Create example state files without overwriting existing files, then exit")
    parser.add_argument("--sources", type=Path, help="Private JSON configuration of producer endpoints for workspace tabs")
    parser.add_argument("--include-local", action="store_true", help="Include this board's own state as a tab when using --sources")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if args.init:
        initialize(args.state_dir)
        print(f"State files ready in {args.state_dir}. Run again without --init to serve.")
        return
    serve(Config(state_dir=args.state_dir, title=args.title, demo=args.demo,
                 sources_file=args.sources, include_local=args.include_local), host=args.host, port=args.port)


HTML = r"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>@@TITLE@@</title>
<style>body{margin:0;background:#f3f0e8;color:#233b35;font:15px/1.55 'Avenir Next',sans-serif}main{max-width:1050px;margin:55px auto;padding:0 24px}header{display:flex;align-items:baseline;justify-content:space-between;gap:20px;border-bottom:1px solid;padding-bottom:20px}h1{font:normal 55px Georgia,serif;letter-spacing:-2px;margin:0}header a{color:inherit;font-size:13px}.sub{color:#747e74;font-size:13px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#d5dbcf;border:1px solid #d5dbcf;margin:30px 0}.metric{background:#faf8f2;padding:20px}.metric strong{display:block;font:35px Georgia,serif;letter-spacing:-1px}.metric span{font-size:12px;color:#747e74}.metric-eta{font-size:13px;font-weight:600;line-height:1.4;margin:12px 0 5px}.metric-note{font-size:11px;line-height:1.5;color:#747e74;margin:0;max-width:32em}progress{width:100%;height:5px;accent-color:#bd5536;margin-top:12px}.task{display:grid;grid-template-columns:115px 1fr;gap:20px;padding:23px 0;border-bottom:1px solid #d5dbcf}.badge{text-transform:uppercase;font-size:10px;letter-spacing:1px;padding:5px 8px;display:inline-block;background:#e4e8dd}.working{background:#233b35;color:white}.waiting{background:#f0d6b9;color:#754b16}.background{background:#dbe9e2;color:#27513e}.queued{color:#797e72}.done{color:#4c6a50}h2{font-size:17px;font-weight:500;margin:0 0 5px}.task p{margin:0;color:#747e74;font-size:13px;max-width:740px}a{color:#a34b30;text-underline-offset:3px}.task a{display:inline-block;margin-top:8px;font-size:12px}footer{font-size:11px;color:#747e74;margin-top:30px}@media(max-width:600px){main{margin:25px auto;padding:0 18px}h1{font-size:38px;letter-spacing:-1px}header{align-items:center}.metrics{grid-template-columns:1fr}.metric{padding:15px}.metric strong{font-size:30px}.task{grid-template-columns:90px 1fr;gap:10px;padding:20px 0}.task h2{font-size:15px}}.team-heading{display:flex;align-items:baseline;justify-content:space-between;gap:15px;margin-top:36px}.team-heading h2{font:28px Georgia,serif}.team-heading span{font-size:11px;color:#747e74}.team-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:16px 0 36px}.agent{background:#faf8f2;border:1px solid #d5dbcf;padding:18px;overflow-wrap:anywhere}.agent h3{font-size:16px;margin:12px 0 5px;font-weight:500}.agent p{font-size:13px;color:#747e74;margin:0}.agent small{display:block;margin-top:12px;color:#747e74;font-size:11px}.team-empty{font-size:13px;color:#747e74}@media(max-width:600px){.team-grid{grid-template-columns:1fr}.team-heading{display:block}.team-heading span{display:block;margin-top:6px}}#connection::before{content:"";display:inline-block;width:8px;height:8px;margin-right:0;border-radius:50%;background:#b88a39;flex-shrink:0}#connection[data-state="connected"]::before{background:#39834e;animation:live-pulse 2s ease-out infinite}@keyframes live-pulse{0%{box-shadow:0 0 0 0 #39834e55}80%,100%{box-shadow:0 0 0 7px #39834e00}}@media(prefers-reduced-motion:reduce){#connection[data-state="connected"]::before{animation:none}}.work-heading{display:flex;align-items:baseline;justify-content:space-between;gap:15px;margin-top:36px}.work-heading h2{font:28px Georgia,serif}.filters{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0 4px}.filters button{appearance:none;font:inherit;font-size:13px;min-height:44px;padding:8px 17px;border:1px solid #c6cec0;border-radius:5px;background:#faf8f2;color:#233b35;cursor:pointer}.filters button:hover{background:#e4e8dd}.filters button[aria-pressed="true"]{background:#233b35;border-color:#233b35;color:#faf8f2}.filters button:focus-visible{outline:2px solid #a34b30;outline-offset:3px}.tasks-empty{padding:24px 0;color:#747e74;font-size:14px}header h1{font-size:clamp(32px,5vw,55px);line-height:1.12;overflow-wrap:anywhere}header>a{flex-shrink:0}@media(max-width:600px){header{align-items:flex-start;flex-direction:column;gap:14px}}.connection-panel{display:flex;align-items:baseline;gap:7px;margin:13px 0}.connection-panel p{margin:0}#connection{display:flex;align-items:center;gap:8px}#connection-detail::before{content:'· '}@media(max-width:600px){.connection-panel{flex-wrap:wrap;gap:0 7px}}
[hidden]{display:none!important}.task{grid-template-columns:115px minmax(0,1fr);overflow-wrap:anywhere}.metric{min-width:0;overflow-wrap:anywhere}@media(max-width:600px){.task{grid-template-columns:90px minmax(0,1fr)}}.metrics{grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr))}.header-links{display:flex;gap:16px;flex-wrap:wrap}.header-links a{font-size:13px}.agent .stale{color:#925c21}.team-controls button{font:inherit;font-size:12px;min-height:36px;padding:6px 12px;background:transparent;border:1px solid #c6cec0;border-radius:4px;color:inherit;cursor:pointer}.warning{padding:12px;background:#f0d6b9;color:#754b16}.connection-panel{margin-bottom:24px}.metric strong{overflow-wrap:anywhere}.task p{white-space:pre-wrap}.metrics:empty{display:none}.team-grid{align-items:start}button:focus-visible,a:focus-visible{outline:2px solid #a34b30;outline-offset:3px}footer{line-height:1.7}.team-controls{margin-top:12px}</style>
<main><header><h1>@@TITLE@@</h1><nav id="links" class="header-links" aria-label="Project links"></nav></header>
<p class="sub">Working, waiting, and what comes next. Updated by your agents, scripts, or you.</p>
<div class="connection-panel" role="status" aria-live="polite"><p id="connection" class="sub">Connecting</p><p id="connection-detail" class="sub">Waiting for the local server</p></div>
<nav id="workspace-tabs" class="filters" role="tablist" aria-label="Workspaces" hidden></nav>
<div id="workspace-content" role="tabpanel"><h2 id="workspace-title" hidden></h2>
<p id="warning" class="warning" hidden></p><section id="metrics" class="metrics" aria-label="Project metrics"></section>
<section aria-labelledby="team-title"><div class="team-heading"><h2 id="team-title">The team.</h2><span id="team-note"></span></div><div class="team-controls"><button id="completed-agents" type="button" aria-pressed="false" hidden>Show completed agents</button></div><div id="team" class="team-grid"></div></section>
<section aria-labelledby="work-title"><div class="work-heading"><h2 id="work-title">The work.</h2><span id="task-count" class="sub" role="status" aria-live="polite"></span></div><div class="filters" role="group" aria-label="Filter tasks"><button type="button" data-filter="all" aria-pressed="true">All work</button><button type="button" data-filter="open" aria-pressed="false">Open</button><button type="button" data-filter="done" aria-pressed="false">Done</button></div><div id="tasks"></div></section>
<footer id="footer"></footer></div></main>
<script>
'use strict';
let selectedFilter='all',latestTasks=[],latestTeam={},showCompletedAgents=false;
let selectedWorkspace=null,latestEnvelope=null;
const $=selector=>document.querySelector(selector);
function element(tag,className,value){const e=document.createElement(tag);if(className)e.className=className;if(value!==undefined)e.textContent=value;return e;}
function safeLink(raw){try{if(typeof raw!=='string'||raw.startsWith('//')||raw.includes('\\'))return null;const u=new URL(raw,location.href);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:null;}catch{return null;}}
function renderTasks(){
 const visible=latestTasks.filter(t=>selectedFilter==='all'||(selectedFilter==='done'?t.status==='done':t.status!=='done'));
 $('#task-count').textContent=visible.length.toLocaleString()+' of '+latestTasks.length.toLocaleString()+' tasks';
 const container=$('#tasks');container.replaceChildren();
 for(const t of visible){const row=element('article','task'),left=element('div'),body=element('div');left.append(element('span','badge '+t.status,t.status));body.append(element('h2','',t.title),element('p','',t.detail));const url=safeLink(t.url);if(url){const a=element('a','','Details ↗');a.href=url;body.append(a);}row.append(left,body);container.append(row);}
 if(!visible.length)container.append(element('p','tasks-empty',selectedFilter==='done'?'No completed work yet.':selectedFilter==='open'?'No open work right now.':'No work listed yet.'));
}
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{selectedFilter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));renderTasks();}));
function renderTeam(team){
 const members=team.members||[],active=members.filter(m=>m.status!=='done'),done=members.filter(m=>m.status==='done');
 $('#team-note').textContent=active.length+' active · '+done.length+' completed · coordinator maintained';
 const toggle=$('#completed-agents');toggle.hidden=!done.length;toggle.textContent=(showCompletedAgents?'Hide':'Show')+' completed agents ('+done.length+')';toggle.setAttribute('aria-pressed',String(showCompletedAgents));
 const container=$('#team');container.replaceChildren();
 for(const member of [...active,...(showCompletedAgents?done:[])]){
  const card=element('article','agent');card.append(element('span','badge '+member.status,member.status),element('h3','',member.name||member.id||'Agent'),element('p','',member.task));
  card.append(element('small','',member.role+(member.parent?' · Reports to '+member.parent:'')));
  const at=Date.parse(member.updated_at),valid=Number.isFinite(at),stale=valid&&Date.now()-at>15*60*1000;
  card.append(element('small',stale&&member.status!=='done'?'stale':'',valid?'Last report '+new Date(at).toLocaleString()+(stale&&member.status!=='done'?' · may be stale':''):'Last report unknown'));
  container.append(card);
 }
 if(!active.length&&!showCompletedAgents)container.append(element('p','team-empty','No active agents are listed. Background processes belong in the work list.'));
}
$('#completed-agents').addEventListener('click',()=>{showCompletedAgents=!showCompletedAgents;renderTeam(latestTeam);});
function renderCards(cards){
 const container=$('#metrics');container.replaceChildren();
 for(const card of cards){const article=element('article','metric');const value=typeof card.value==='number'?card.value.toLocaleString(undefined,{maximumFractionDigits:card.precision}):card.value;article.append(element('strong','',value+(card.unit?' '+card.unit:'')),element('span','',card.label));if(card.progress){const progress=element('progress');progress.max=card.progress.max;progress.value=card.progress.value;progress.setAttribute('aria-label',card.label);article.append(progress);}if(card.estimate)article.append(element('p','metric-eta',card.estimate));if(card.note)article.append(element('p','metric-note',card.note));container.append(article);}
}
function renderStatus(s){
 latestTasks=s.tasks;latestTeam=s.team;renderTasks();renderTeam(latestTeam);renderCards(s.cards);
 const links=$('#links');links.replaceChildren();for(const link of s.links){const url=safeLink(link.url);if(url){const a=element('a','',link.label);a.href=url;links.append(a);}}
 $('#warning').hidden=!s.warning;$('#warning').textContent=s.warning||'';
 const updated=Date.parse(s.updated_at);$('#footer').textContent=(Number.isFinite(updated)?'Task notes updated '+new Date(updated).toLocaleString()+'. ':'')+'Live connection; agent reports are producer-maintained. Agent Workboard.';
}
function renderEnvelope(s){
 latestEnvelope=s;
 const tabs=$('#workspace-tabs'),heading=$('#workspace-title'),workspaces=s.workspaces||[];
 tabs.hidden=!workspaces.length;heading.hidden=!workspaces.length;
 if(!workspaces.length){tabs.replaceChildren();renderStatus(s);return;}
 let selected=workspaces.find(w=>w.id===selectedWorkspace)||workspaces[0];selectedWorkspace=selected.id;
 const focused=document.activeElement?.dataset?.workspaceId;tabs.replaceChildren();
 workspaces.forEach((workspace,index)=>{const b=element('button','',workspace.title+(workspace.available?'':' · Offline'));b.type='button';b.id='workspace-tab-'+index;b.dataset.workspaceId=workspace.id;b.setAttribute('role','tab');b.setAttribute('aria-selected',String(workspace.id===selectedWorkspace));b.setAttribute('aria-controls','workspace-content');b.setAttribute('aria-pressed',String(workspace.id===selectedWorkspace));b.tabIndex=workspace.id===selectedWorkspace?0:-1;b.addEventListener('click',()=>{selectedWorkspace=workspace.id;renderEnvelope(latestEnvelope);});b.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?workspaces.length-1:(index+(event.key==='ArrowRight'?1:-1)+workspaces.length)%workspaces.length;selectedWorkspace=workspaces[next].id;renderEnvelope(latestEnvelope);$('#workspace-tab-'+next).focus();}});tabs.append(b);if(workspace.id===focused)b.focus();});
 heading.textContent=selected.title;
 $('#workspace-content').setAttribute('aria-labelledby','workspace-tab-'+workspaces.indexOf(selected));
 renderStatus(selected.data);
 const message=[s.warning,selected.data.warning,!selected.available?'Workspace unavailable; showing its last known notes. Other workspaces remain available.':''].filter(Boolean).join(' ');
 $('#warning').hidden=!message;$('#warning').textContent=message;
}
const connection=$('#connection');function setConnection(state,label,detail){connection.dataset.state=state;connection.textContent=label;$('#connection-detail').textContent=detail;}
const events=new EventSource('/api/events');events.onopen=()=>setConnection('connected','Live','Connected');
events.addEventListener('status',event=>{try{renderEnvelope(JSON.parse(event.data));setConnection('connected','Live','Connected');}catch{setConnection('reconnecting','Reconnecting','Update could not be displayed; keeping the previous snapshot.');}});
events.onerror=()=>setConnection('reconnecting','Reconnecting','Keeping the last received snapshot.');
const ageTimer=setInterval(()=>{if(latestEnvelope)renderTeam(latestTeam);},60000);
window.addEventListener('pagehide',()=>{events.close();clearInterval(ageTimer);});window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
</script></html>
"""

if __name__ == "__main__":
    main()
