from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any

from .utils import USER_STATE_DIR, human_bytes

CACHE_DIR = USER_STATE_DIR / "cache"
META_FILE = CACHE_DIR / "meta.json"

MODULE_KEYS = (
    "mole",
    "orphans",
    "apps",
    "kondo",
    "abandoned",
    "dupes_docs",
    "dupes_all",
    "sim_images",
    "big_files",
    "organize_sim",
)


def _module_file(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _atomic_write_json(path: Path, payload: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# In-process memo of parsed module envelopes keyed by file mtime/size, so that
# progress polling and repeated /api/cache calls don't re-read & re-parse JSON.
_MEM_LOCK = threading.Lock()
_MEM: dict[str, tuple[int, int, dict[str, Any]]] = {}


def _stat_sig(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def module_version(key: str) -> int:
    """Monotonic-ish version for a module (file mtime in ns, 0 if absent)."""
    sig = _stat_sig(_module_file(key))
    return sig[0] if sig else 0


def save_module(key: str, data: dict[str, Any], scanned_at: int | None = None) -> dict[str, Any]:
    ts = scanned_at or int(time.time())
    envelope = {"scannedAt": ts, "data": data}
    p = _module_file(key)
    try:
        _atomic_write_json(p, envelope)
        sig = _stat_sig(p)
        if sig:
            with _MEM_LOCK:
                _MEM[key] = (sig[0], sig[1], envelope)
    except OSError:
        pass
    return envelope


def load_module(key: str) -> dict[str, Any] | None:
    p = _module_file(key)
    sig = _stat_sig(p)
    if sig is None:
        with _MEM_LOCK:
            _MEM.pop(key, None)
        return None
    with _MEM_LOCK:
        hit = _MEM.get(key)
    if hit and hit[0] == sig[0] and hit[1] == sig[1]:
        return hit[2]
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "data" in raw:
            with _MEM_LOCK:
                _MEM[key] = (sig[0], sig[1], raw)
            return raw
    except Exception:
        return None
    return None


def update_meta(**kwargs: Any) -> dict[str, Any]:
    meta = load_meta()
    meta.update(kwargs)
    try:
        _atomic_write_json(META_FILE, meta)
    except OSError:
        pass
    return meta


def load_meta() -> dict[str, Any]:
    if not META_FILE.exists():
        return {}
    try:
        data = json.loads(META_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_all_cached(known_versions: dict[str, int] | None = None) -> dict[str, Any]:
    """Load cached modules.

    When ``known_versions`` is given, modules whose on-disk version equals the
    client's known version are omitted from ``modules`` (delta payload), while
    ``timestamps`` / ``versions`` always describe every cached module.
    """
    modules: dict[str, Any] = {}
    timestamps: dict[str, int] = {}
    versions: dict[str, int] = {}
    for key in MODULE_KEYS:
        ver = module_version(key)
        if not ver:
            continue
        if known_versions is not None and known_versions.get(key) == ver:
            # Client already has this exact payload; only report metadata.
            env = load_module(key)
            if env:
                timestamps[key] = int(env.get("scannedAt", 0))
                versions[key] = ver
            continue
        env = load_module(key)
        if env and isinstance(env.get("data"), dict):
            modules[key] = env["data"]
            timestamps[key] = int(env.get("scannedAt", 0))
            versions[key] = ver
    meta = load_meta()
    return {
        "ok": True,
        "hasCache": bool(timestamps),
        "lastCheckupAt": meta.get("lastCheckupAt") or (max(timestamps.values()) if timestamps else 0),
        "timestamps": timestamps,
        "versions": versions,
        "modules": modules,
        "delta": known_versions is not None,
    }


def _load_for_update(key: str) -> dict[str, Any] | None:
    """Deep-copied envelope so in-place pruning never races readers of the memo."""
    env = load_module(key)
    return copy.deepcopy(env) if env else None


def prune_mole_cache(removed_paths: list[str] | None = None, whitelisted_paths: list[str] | None = None) -> dict[str, Any] | None:
    env = _load_for_update("mole")
    if not env:
        return None
    data = env["data"]
    removed = set(removed_paths or [])
    wl = set(whitelisted_paths or [])
    items = [x for x in data.get("items", []) if x.get("path") not in removed]
    for x in items:
        if x.get("path") in wl:
            x["whitelisted"] = True
    total_bytes = sum(x.get("sizeBytes", 0) for x in items if not x.get("whitelisted"))
    by_cat: dict[str, dict[str, Any]] = {}
    for x in items:
        c = x.get("category", "未分组")
        slot = by_cat.setdefault(c, {"category": c, "count": 0, "sizeBytes": 0})
        slot["count"] += 1
        slot["sizeBytes"] += x.get("sizeBytes", 0)
    for slot in by_cat.values():
        slot["sizeHuman"] = human_bytes(slot["sizeBytes"])
    data["items"] = items
    data["count"] = len(items)
    data["totalBytes"] = total_bytes
    data["totalHuman"] = human_bytes(total_bytes)
    data["categories"] = sorted(by_cat.values(), key=lambda c: c["sizeBytes"], reverse=True)
    save_module("mole", data, scanned_at=env.get("scannedAt"))
    return data


def prune_orphans_cache(removed_paths: list[str]) -> dict[str, Any] | None:
    env = _load_for_update("orphans")
    if not env:
        return None
    data = env["data"]
    removed = set(removed_paths)
    items = [x for x in data.get("items", []) if x.get("path") not in removed]
    total_bytes = sum(x.get("sizeBytes", 0) for x in items)
    by_cat: dict[str, dict[str, Any]] = {}
    for x in items:
        c = x.get("category", "其他")
        slot = by_cat.setdefault(c, {"category": c, "count": 0, "sizeBytes": 0})
        slot["count"] += 1
        slot["sizeBytes"] += x.get("sizeBytes", 0)
    for slot in by_cat.values():
        slot["sizeHuman"] = human_bytes(slot["sizeBytes"])
    data["items"] = items
    data["count"] = len(items)
    data["totalBytes"] = total_bytes
    data["totalHuman"] = human_bytes(total_bytes)
    data["categories"] = sorted(by_cat.values(), key=lambda c: c["sizeBytes"], reverse=True)
    save_module("orphans", data, scanned_at=env.get("scannedAt"))
    return data


def prune_projects_cache(removed_paths: list[str]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    removed = set(removed_paths)

    def _is_under_removed(p: str) -> bool:
        return any(p == r or p.startswith(r.rstrip("/") + "/") for r in removed)

    kondo_out = None
    k_env = _load_for_update("kondo")
    if k_env:
        kd = k_env["data"]
        new_projs = []
        for proj in kd.get("projects", []):
            if _is_under_removed(proj.get("projectPath", "")):
                continue
            kept_arts = [a for a in proj.get("artifacts", []) if not _is_under_removed(a.get("path", ""))]
            if not kept_arts:
                continue
            proj["artifacts"] = kept_arts
            proj["artifactPaths"] = [a["path"] for a in kept_arts]
            proj["sizeBytes"] = sum(a.get("sizeBytes", 0) for a in kept_arts)
            proj["sizeHuman"] = human_bytes(proj["sizeBytes"])
            new_projs.append(proj)
        kd["projects"] = new_projs
        kd["count"] = len(new_projs)
        kd["totalBytes"] = sum(p.get("sizeBytes", 0) for p in new_projs)
        kd["totalHuman"] = human_bytes(kd["totalBytes"])
        save_module("kondo", kd, scanned_at=k_env.get("scannedAt"))
        kondo_out = kd

    ab_out = None
    a_env = _load_for_update("abandoned")
    if a_env:
        ad = a_env["data"]
        projs = [p for p in ad.get("projects", []) if not _is_under_removed(p.get("path", ""))]
        ab_only = [p for p in projs if p.get("isAbandonedCandidate")]
        ad["projects"] = projs
        ad["count"] = len(projs)
        ad["abandonedCount"] = len(ab_only)
        ad["abandonedBytes"] = sum(p.get("sizeBytes", 0) for p in ab_only)
        ad["abandonedHuman"] = human_bytes(ad["abandonedBytes"])
        ad["totalBytes"] = sum(p.get("sizeBytes", 0) for p in projs)
        ad["totalHuman"] = human_bytes(ad["totalBytes"])
        save_module("abandoned", ad, scanned_at=a_env.get("scannedAt"))
        ab_out = ad

    return kondo_out, ab_out


def prune_dupes_cache(removed_paths: list[str]) -> dict[str, Any]:
    removed = set(removed_paths)
    updated: dict[str, Any] = {}

    for key in ("dupes_docs", "dupes_all"):
        env = _load_for_update(key)
        if not env:
            continue
        d = env["data"]
        groups = []
        reclaim = 0
        for g in d.get("groups", []):
            files = [f for f in g.get("files", []) if f.get("path") not in removed]
            if len(files) < 2:
                continue
            g["files"] = files
            g["count"] = len(files)
            g["reclaimableBytes"] = g.get("fileSizeBytes", 0) * (len(files) - 1)
            g["reclaimableHuman"] = human_bytes(g["reclaimableBytes"])
            reclaim += g["reclaimableBytes"]
            groups.append(g)
        d["groups"] = groups
        d["groupCount"] = len(groups)
        d["duplicateFilesCount"] = sum(g["count"] - 1 for g in groups)
        d["reclaimableBytes"] = reclaim
        d["reclaimableHuman"] = human_bytes(reclaim)
        save_module(key, d, scanned_at=env.get("scannedAt"))
        updated[key] = d

    img_env = _load_for_update("sim_images")
    if img_env:
        d = img_env["data"]
        groups = []
        reclaim = 0
        for g in d.get("groups", []):
            files = [f for f in g.get("files", []) if f.get("path") not in removed]
            if len(files) < 2:
                continue
            keep = next((f for f in files if f.get("recommendedKeep")), files[0])
            rec = max(0, sum(f.get("sizeBytes", 0) for f in files) - keep.get("sizeBytes", 0))
            g["files"] = files
            g["count"] = len(files)
            g["reclaimableBytes"] = rec
            g["reclaimableHuman"] = human_bytes(rec)
            reclaim += rec
            groups.append(g)
        d["groups"] = groups
        d["groupCount"] = len(groups)
        d["reclaimableBytes"] = reclaim
        d["reclaimableHuman"] = human_bytes(reclaim)
        save_module("sim_images", d, scanned_at=img_env.get("scannedAt"))
        updated["sim_images"] = d

    big_env = _load_for_update("big_files")
    if big_env:
        d = big_env["data"]
        files = [f for f in d.get("files", []) if f.get("path") not in removed]
        d["files"] = files
        d["count"] = len(files)
        d["totalBytes"] = sum(f.get("sizeBytes", 0) for f in files)
        d["totalHuman"] = human_bytes(d["totalBytes"])
        save_module("big_files", d, scanned_at=big_env.get("scannedAt"))
        updated["big_files"] = d

    return updated


def prune_apps_cache(removed_paths: list[str]) -> dict[str, Any] | None:
    env = _load_for_update("apps")
    if not env:
        return None
    removed = set(removed_paths)
    d = env["data"]
    new_apps = []
    for app in d.get("apps", []):
        if app.get("installPath") in removed:
            continue
        kept_paths = [p for p in app.get("paths", []) if p.get("path") not in removed]
        if not kept_paths:
            continue
        app_bytes = sum(p.get("sizeBytes", 0) for p in kept_paths if p.get("role") == "installation")
        cache_bytes = sum(p.get("sizeBytes", 0) for p in kept_paths if p.get("role") in ("cache", "logs"))
        container_paths = {p.get("path") for p in kept_paths if p.get("role") == "container"}
        total_bytes = sum(
            p.get("sizeBytes", 0)
            for p in kept_paths
            if p.get("area") != "容器内部缓存" or len(Path(p.get("path", "")).parents) < 3 or str(Path(p.get("path", "")).parents[2]) not in container_paths
        )
        data_bytes = max(0, total_bytes - app_bytes)
        app["paths"] = kept_paths
        app["pathCount"] = len(kept_paths)
        app["appBytes"] = app_bytes
        app["appHuman"] = human_bytes(app_bytes)
        app["cacheBytes"] = cache_bytes
        app["cacheHuman"] = human_bytes(cache_bytes)
        app["dataBytes"] = data_bytes
        app["dataHuman"] = human_bytes(data_bytes)
        app["totalBytes"] = total_bytes
        app["totalHuman"] = human_bytes(total_bytes)
        new_apps.append(app)
    d["apps"] = new_apps
    d["count"] = len(new_apps)
    d["totalBytes"] = sum(a.get("totalBytes", 0) for a in new_apps)
    d["totalHuman"] = human_bytes(d["totalBytes"])
    d["cleanableCacheBytes"] = sum(a.get("cacheBytes", 0) for a in new_apps)
    d["cleanableCacheHuman"] = human_bytes(d["cleanableCacheBytes"])
    save_module("apps", d, scanned_at=env.get("scannedAt"))
    return d
