from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .apps_adapter import scan_installed_apps, trash_app_paths
from .cache import (
    load_all_cached,
    prune_apps_cache,
    prune_dupes_cache,
    prune_mole_cache,
    prune_orphans_cache,
    prune_projects_cache,
    save_module,
    update_meta,
)
from .dupes_adapter import scan_big_files, scan_duplicates, scan_similar_images, trash_duplicate_paths
from .mole_adapter import add_to_whitelist, apply_mole_trash, run_mole_analyze, run_mole_dry_run
from .organize_adapter import (
    analyze_unmatched_files,
    apply_organize_moves,
    list_organize_history,
    revert_organize_batch,
    simulate_organize,
)
from .orphans_adapter import scan_orphaned_files, trash_orphans
from .projects_adapter import scan_abandoned_projects, scan_kondo_artifacts, trash_project_paths
from .utils import WORKBENCH_ROOT, check_permissions, engine_status, human_bytes

WEB_DIR = WORKBENCH_ROOT / "web"

_INDEX_CACHE: dict[str, Any] = {"sig": None, "raw": b""}


def _index_bytes() -> bytes:
    """Serve index.html from memory, re-reading only when the file changes."""
    p = WEB_DIR / "index.html"
    st = p.stat()
    sig = (st.st_mtime_ns, st.st_size)
    if _INDEX_CACHE["sig"] != sig:
        _INDEX_CACHE["raw"] = p.read_bytes()
        _INDEX_CACHE["sig"] = sig
    return _INDEX_CACHE["raw"]


def _parse_known(query: str) -> dict[str, int] | None:
    """Parse ``known=key:version,key:version`` from a query string."""
    qs = parse_qs(query)
    if "known" not in qs:
        return None
    known: dict[str, int] = {}
    for part in (qs["known"][0] or "").split(","):
        k, _, v = part.partition(":")
        if k and v.isdigit():
            known[k] = int(v)
    return known

_CHECKUP_LOCK = threading.Lock()
_CHECKUP_STATE: dict[str, Any] = {
    "running": False,
    "startedAt": 0,
    "finishedAt": 0,
    "completed": [],
    "pending": [],
    "total": 9,
    "errors": {},
}

_CHECKUP_TASKS: list[tuple[str, str, Callable[[], dict[str, Any]]]] = [
    ("organize_sim", "organize 规则模拟", lambda: simulate_organize(None)),
    ("dupes_docs", "czkawka 文档查重", lambda: scan_duplicates(None, True)),
    ("big_files", "czkawka 超大文件", lambda: scan_big_files(None, 30)),
    ("orphans", "Pearcleaner 孤立配置", scan_orphaned_files),
    ("apps", "已安装应用与关联空间", lambda: scan_installed_apps(False)),
    ("kondo", "kondo 工程依赖", scan_kondo_artifacts),
    ("abandoned", "废弃 AI 试水工程", scan_abandoned_projects),
    ("sim_images", "czkawka 相似图片", scan_similar_images),
    ("mole", "Mole 安全缓存", run_mole_dry_run),
]


def _disk_info() -> dict[str, Any]:
    usage = shutil.disk_usage("/")
    return {
        "totalBytes": usage.total,
        "usedBytes": usage.used,
        "freeBytes": usage.free,
        "totalHuman": human_bytes(usage.total),
        "freeHuman": human_bytes(usage.free),
    }


def _build_checkup_payload(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    mole = results.get("mole", {})
    orphans = results.get("orphans", {})
    apps = results.get("apps", {})
    kondo = results.get("kondo", {})
    abandoned = results.get("abandoned", {})
    dupes_docs = results.get("dupes_docs", {})
    sim_images = results.get("sim_images", {})
    big_files = results.get("big_files", {})
    org_sim = results.get("organize_sim", {})

    reclaimable = (
        mole.get("totalBytes", 0)
        + orphans.get("totalBytes", 0)
        + kondo.get("totalBytes", 0)
        + dupes_docs.get("reclaimableBytes", 0)
        + sim_images.get("reclaimableBytes", 0)
    )
    return {
        "ok": True,
        "disk": _disk_info(),
        "reclaimableTotalBytes": reclaimable,
        "reclaimableTotalHuman": human_bytes(reclaimable),
        "summary": {
            "mole": {
                "count": mole.get("count", 0),
                "bytes": mole.get("totalBytes", 0),
                "human": mole.get("totalHuman", "0 B"),
                "notices": mole.get("notices", []),
            },
            "orphans": {
                "count": orphans.get("count", 0),
                "bytes": orphans.get("totalBytes", 0),
                "human": orphans.get("totalHuman", "0 B"),
            },
            "apps": {
                "count": apps.get("count", 0),
                "bytes": apps.get("totalBytes", 0),
                "human": apps.get("totalHuman", "0 B"),
                "cleanableCacheBytes": apps.get("cleanableCacheBytes", 0),
                "cleanableCacheHuman": apps.get("cleanableCacheHuman", "0 B"),
            },
            "kondo": {
                "count": kondo.get("count", 0),
                "bytes": kondo.get("totalBytes", 0),
                "human": kondo.get("totalHuman", "0 B"),
            },
            "abandonedProjects": {
                "count": abandoned.get("abandonedCount", 0),
                "bytes": abandoned.get("abandonedBytes", 0),
                "human": abandoned.get("abandonedHuman", "0 B"),
            },
            "duplicates": {
                "groups": dupes_docs.get("groupCount", 0),
                "duplicateFiles": dupes_docs.get("duplicateFilesCount", 0),
                "bytes": dupes_docs.get("reclaimableBytes", 0),
                "human": dupes_docs.get("reclaimableHuman", "0 B"),
            },
            "similarImages": {
                "groups": sim_images.get("groupCount", 0),
                "bytes": sim_images.get("reclaimableBytes", 0),
                "human": sim_images.get("reclaimableHuman", "0 B"),
            },
            "bigFiles": {
                "count": big_files.get("count", 0),
                "bytes": big_files.get("totalBytes", 0),
                "human": big_files.get("totalHuman", "0 B"),
            },
            "organize": {
                "matchedCount": org_sim.get("matchedCount", 0),
                "unmatchedCount": org_sim.get("unmatchedCount", 0),
            },
        },
        "details": {
            "mole": mole,
            "orphans": orphans,
            "apps": apps,
            "kondo": kondo,
            "abandonedProjects": abandoned,
            "duplicates": dupes_docs,
            "similarImages": sim_images,
            "bigFiles": big_files,
            "organize": org_sim,
        },
    }


def run_full_checkup(use_cache: bool = False) -> dict[str, Any]:
    """Read-only dry-run checkup across all 4 engines, saving results to persistent cache."""
    if use_cache:
        cached = load_all_cached()
        mods = cached.get("modules", {})
        if all(k in mods for k, _, _ in _CHECKUP_TASKS):
            payload = _build_checkup_payload(mods)
            payload["fromCache"] = True
            payload["lastCheckupAt"] = cached.get("lastCheckupAt", 0)
            return payload

    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        future_map = {pool.submit(fn): key for key, _, fn in _CHECKUP_TASKS}
        for fut in as_completed(future_map):
            key = future_map[fut]
            res = fut.result()
            results[key] = res
            save_module(key, res)
    ts = int(time.time())
    update_meta(lastCheckupAt=ts)
    payload = _build_checkup_payload(results)
    payload["fromCache"] = False
    payload["lastCheckupAt"] = ts
    return payload


def start_progressive_checkup() -> dict[str, Any]:
    """Start an on-demand progressive checkup job so fast modules render within ~1s."""
    with _CHECKUP_LOCK:
        if _CHECKUP_STATE["running"]:
            return {"ok": True, "alreadyRunning": True, "state": _CHECKUP_STATE}
        _CHECKUP_STATE["running"] = True
        _CHECKUP_STATE["startedAt"] = int(time.time())
        _CHECKUP_STATE["finishedAt"] = 0
        _CHECKUP_STATE["completed"] = []
        _CHECKUP_STATE["pending"] = [label for _, label, _ in _CHECKUP_TASKS]
        _CHECKUP_STATE["total"] = len(_CHECKUP_TASKS)
        _CHECKUP_STATE["errors"] = {}

    def _runner() -> None:
        with ThreadPoolExecutor(max_workers=6) as pool:
            future_map = {pool.submit(fn): (key, label) for key, label, fn in _CHECKUP_TASKS}
            for fut in as_completed(future_map):
                key, label = future_map[fut]
                try:
                    res = fut.result()
                    save_module(key, res)
                except Exception as exc:
                    with _CHECKUP_LOCK:
                        _CHECKUP_STATE["errors"][key] = str(exc)
                with _CHECKUP_LOCK:
                    if label in _CHECKUP_STATE["pending"]:
                        _CHECKUP_STATE["pending"].remove(label)
                    _CHECKUP_STATE["completed"].append(key)
        ts = int(time.time())
        update_meta(lastCheckupAt=ts)
        with _CHECKUP_LOCK:
            _CHECKUP_STATE["running"] = False
            _CHECKUP_STATE["finishedAt"] = ts

    threading.Thread(target=_runner, daemon=True).start()
    return {"ok": True, "alreadyRunning": False, "state": dict(_CHECKUP_STATE)}


def _dispatch_post(path: str, body: dict[str, Any], server_ref: ThreadingHTTPServer) -> dict[str, Any]:
    if path == "/api/checkup":
        return run_full_checkup(use_cache=bool(body.get("useCache", False)))
    if path == "/api/checkup/start":
        return start_progressive_checkup()

    if path == "/api/mole/scan":
        res = run_mole_dry_run()
        save_module("mole", res)
        return res
    if path == "/api/mole/trash":
        res = apply_mole_trash(body.get("paths", []))
        removed = [r["path"] for r in res.get("results", []) if r.get("ok")]
        res["updatedCache"] = prune_mole_cache(removed_paths=removed)
        return res
    if path == "/api/mole/whitelist":
        paths = body.get("paths", [])
        res = add_to_whitelist(paths)
        res["updatedCache"] = prune_mole_cache(whitelisted_paths=res.get("added", paths))
        return res
    if path == "/api/mole/analyze":
        return run_mole_analyze(body.get("path"))

    if path == "/api/orphans/scan":
        res = scan_orphaned_files()
        save_module("orphans", res)
        return res
    if path == "/api/orphans/trash":
        res = trash_orphans(body.get("paths", []))
        removed = [r["path"] for r in res.get("results", []) if r.get("ok")]
        res["updatedCache"] = prune_orphans_cache(removed)
        return res
    if path == "/api/orphans/open-pearcleaner":
        subprocess.Popen(["/usr/bin/open", "-a", "/Applications/Pearcleaner.app"])
        return {"ok": True}

    if path == "/api/apps/scan":
        res = scan_installed_apps(include_system=bool(body.get("includeSystem", False)))
        if not res["includeSystem"]:
            save_module("apps", res)
        return res
    if path == "/api/apps/trash":
        res = trash_app_paths(body.get("paths", []))
        removed = [r["path"] for r in res.get("results", []) if r.get("ok")]
        res["updatedApps"] = prune_apps_cache(removed)
        res["updatedOrphans"] = prune_orphans_cache(removed)
        res["updatedMole"] = prune_mole_cache(removed_paths=removed)
        return res

    if path == "/api/projects/kondo":
        res = scan_kondo_artifacts(body.get("roots"))
        save_module("kondo", res)
        return res
    if path == "/api/projects/abandoned":
        res = scan_abandoned_projects(int(body.get("minIdleDays", 7)))
        save_module("abandoned", res)
        return res
    if path == "/api/projects/trash":
        res = trash_project_paths(body.get("paths", []))
        removed = [r["path"] for r in res.get("results", []) if r.get("ok")]
        kondo_upd, ab_upd = prune_projects_cache(removed)
        res["updatedKondo"] = kondo_upd
        res["updatedAbandoned"] = ab_upd
        return res

    if path == "/api/dupes/scan":
        docs_only = bool(body.get("docsOnly", True))
        res = scan_duplicates(body.get("roots"), docs_only=docs_only)
        save_module("dupes_docs" if docs_only else "dupes_all", res)
        return res
    if path == "/api/dupes/images":
        res = scan_similar_images(body.get("roots"))
        save_module("sim_images", res)
        return res
    if path == "/api/dupes/big":
        res = scan_big_files(body.get("roots"), limit=int(body.get("limit", 40)))
        save_module("big_files", res)
        return res
    if path == "/api/dupes/trash":
        res = trash_duplicate_paths(body.get("paths", []))
        removed = [r["path"] for r in res.get("results", []) if r.get("ok")]
        res["updatedCaches"] = prune_dupes_cache(removed)
        return res

    if path == "/api/organize/sim":
        target = body.get("targetDir")
        res = simulate_organize(target)
        if not target or Path(target).expanduser() == Path.home() / "Downloads":
            save_module("organize_sim", res)
        return res
    if path == "/api/organize/ai-triage":
        return analyze_unmatched_files(
            body.get("paths", []),
            base_dir=body.get("baseDir"),
            llm_config=body.get("llmConfig"),
        )
    if path == "/api/organize/apply":
        return apply_organize_moves(body.get("moves", []))
    if path == "/api/organize/revert":
        return revert_organize_batch(body.get("batchId", ""))

    if path == "/api/system/reveal":
        target = body.get("path", "")
        if target and Path(target).expanduser().exists():
            subprocess.Popen(["/usr/bin/open", "-R", str(Path(target).expanduser())])
            return {"ok": True}
        return {"ok": False, "error": "路径不存在"}
    if path == "/api/system/pick-folder":
        proc = subprocess.run(
            ["/usr/bin/osascript", "-e", 'POSIX path of (choose folder with prompt "选择要扫描或整理的文件夹")'],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return {"ok": True, "path": proc.stdout.strip().rstrip("/")}
        return {"ok": False, "cancelled": True}
    if path == "/api/system/open-fda":
        subprocess.Popen(["/usr/bin/open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"])
        return {"ok": True}
    if path == "/api/system/shutdown":
        t = threading.Timer(0.15, server_ref.shutdown)
        t.daemon = True
        t.start()
        return {"ok": True}
    raise KeyError(f"Unknown API endpoint: {path}")


class TidyMacHandler(BaseHTTPRequestHandler):
    server_version = "TidyMac/0.3"
    # Keep-alive: every response carries Content-Length, so the WebView can reuse
    # a single socket for progress polling instead of a new TCP handshake each tick.
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, data: Any) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            raw = _index_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return

        if parsed.path == "/api/status":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "engines": engine_status(),
                    "permissions": check_permissions(),
                    "disk": _disk_info(),
                    "pid": os.getpid(),
                },
            )
            return

        if parsed.path == "/api/cache":
            cached = load_all_cached(_parse_known(parsed.query))
            cached["disk"] = _disk_info()
            self._send_json(HTTPStatus.OK, cached)
            return

        if parsed.path == "/api/checkup/progress":
            with _CHECKUP_LOCK:
                state = dict(_CHECKUP_STATE)
                state["completed"] = list(_CHECKUP_STATE["completed"])
                state["pending"] = list(_CHECKUP_STATE["pending"])
                state["errors"] = dict(_CHECKUP_STATE["errors"])
            cached = load_all_cached(_parse_known(parsed.query))
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "state": state,
                    "cached": cached,
                },
            )
            return

        if parsed.path == "/api/organize/history":
            self._send_json(HTTPStatus.OK, {"ok": True, "batches": list_organize_history()})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or 0)
        body: dict[str, Any] = {}
        if length > 0:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON body"})
                return
        try:
            result = _dispatch_post(parsed.path, body, self.server)  # type: ignore[arg-type]
            self._send_json(HTTPStatus.OK, result)
        except KeyError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})


def serve_workbench(
    port: int = 18765,
    open_browser: bool = False,
    print_ready_port: bool = False,
    watch_parent_pid: int | None = None,
) -> None:
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer(("127.0.0.1", port), TidyMacHandler)
    actual_port = httpd.server_address[1]
    url = f"http://127.0.0.1:{actual_port}"

    if watch_parent_pid:
        def _watch_parent() -> None:
            while True:
                time.sleep(1.0)
                if os.getppid() != watch_parent_pid:
                    httpd.shutdown()
                    break
                try:
                    os.kill(watch_parent_pid, 0)
                except OSError:
                    httpd.shutdown()
                    break

        threading.Thread(target=_watch_parent, daemon=True).start()

    if print_ready_port:
        print(f"TIDYMAC_PORT={actual_port}", flush=True)
    else:
        print(f"TidyMac 本地工作台已启动: {url} (按 Ctrl+C 退出，无后台常驻)", flush=True)
    if open_browser:
        subprocess.Popen(["/usr/bin/open", url])
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
