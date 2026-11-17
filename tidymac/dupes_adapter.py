from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .utils import HOME, WORKBENCH_ROOT, human_bytes, move_to_trash, resolve_binary, tool_env

DEFAULT_DUPE_ROOTS = [
    HOME / "Documents",
    HOME / "Downloads",
    HOME / "Desktop",
]

DOC_ARCHIVE_EXTENSIONS = (
    "pdf,doc,docx,ppt,pptx,xls,xlsx,xmind,md,txt,csv,rtf,"
    "zip,rar,7z,tar,gz,tgz,bz2,xz,dmg,pkg,iso"
)

_DUP_COPY_SUFFIX_RE = re.compile(r"(?:\s+\d+|\(\d+\)|（\d+）|_副本|-副本|_copy|\s+copy)$", re.IGNORECASE)


def _collect_excluded_dirs(roots: list[Path]) -> list[str]:
    """Prune code dependencies, git dirs, app bundles, and FileMind snapshots fast."""
    prune_names = {
        ".git", "node_modules", ".venv", "venv", "target", "__pycache__",
        ".local", "site-packages", "qa-live-before-snapshot", ".swiftpm",
        ".tools", "DerivedData",
    }
    excludes: list[str] = [str(WORKBENCH_ROOT)]
    for root in roots:
        if not root.exists():
            continue
        stack = [(root, 0)]
        while stack:
            cur, depth = stack.pop()
            if depth > 6:
                continue
            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        name = entry.name
                        if name in prune_names or name.endswith(".app"):
                            excludes.append(entry.path)
                        else:
                            stack.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return excludes


def _pick_keep_index(entries: list[dict[str, Any]]) -> int:
    """Choose the cleanest canonical path as the recommended copy to keep."""
    def score(item: dict[str, Any]) -> tuple[int, int, int, int, int]:
        path_str = item["path"]
        p = Path(path_str)
        stem = p.stem
        has_copy_marker = 1 if _DUP_COPY_SUFFIX_RE.search(stem) else 0
        in_ai_sandbox = 1 if any(s in path_str for s in ("/Documents/Codex/", "/Documents/deepseek-harness/", "/Documents/kimi/")) else 0
        in_downloads_loose = 1 if ("/Downloads/" in path_str and "/Downloads/备考资料/" not in path_str and "/Downloads/上财真题/" not in path_str) else 0
        return (in_ai_sandbox, has_copy_marker, in_downloads_loose, len(path_str), int(item.get("modified_date", 0)))

    best_idx = 0
    best_score = score(entries[0])
    for i in range(1, len(entries)):
        s = score(entries[i])
        if s < best_score:
            best_score = s
            best_idx = i
    return best_idx


def scan_duplicates(
    roots: list[str] | None = None,
    docs_only: bool = True,
    min_size_bytes: int = 8192,
) -> dict[str, Any]:
    cz_bin = resolve_binary("czkawka_cli")
    if not cz_bin:
        return {"ok": False, "error": "未检测到 czkawka_cli 可执行文件"}

    scan_roots = [Path(r).expanduser() for r in roots] if roots else [p for p in DEFAULT_DUPE_ROOTS if p.exists()]
    scan_roots = [p for p in scan_roots if p.exists()]
    if not scan_roots:
        return {"ok": True, "groups": [], "reclaimableBytes": 0, "reclaimableHuman": "0 B"}

    excludes = _collect_excluded_dirs(scan_roots)

    with tempfile.TemporaryDirectory(prefix="tidymac-cz-") as tmp:
        out_json = Path(tmp) / "dup.json"
        cmd = [cz_bin, "dup"]
        for d in scan_roots:
            cmd.extend(["-d", str(d)])
        for ex in excludes:
            cmd.extend(["-e", ex])
        cmd.extend(["-E", "DEFAULT", "-E", "*/site-packages/*", "-E", "*/.venv*/*", "-E", "*/node_modules/*"])
        if docs_only:
            cmd.extend(["-x", DOC_ARCHIVE_EXTENSIONS])
        cmd.extend(["-m", str(min_size_bytes), "-s", "hash", "-t", "BLAKE3", "-W", "-N", "-M", "-C", str(out_json)])

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=tool_env())
        if not out_json.exists():
            return {"ok": False, "error": (proc.stderr or proc.stdout or "czkawka_cli 未生成结果").strip()}
        raw = json.loads(out_json.read_text(encoding="utf-8"))

    groups: list[dict[str, Any]] = []
    reclaimable_total = 0

    if isinstance(raw, dict):
        for size_key, group_list in raw.items():
            if not isinstance(group_list, list):
                continue
            for entry_list in group_list:
                if not isinstance(entry_list, list) or len(entry_list) < 2:
                    continue
                keep_idx = _pick_keep_index(entry_list)
                file_size = int(entry_list[0].get("size", int(size_key) if str(size_key).isdigit() else 0))
                reclaim_group = file_size * (len(entry_list) - 1)
                reclaimable_total += reclaim_group
                formatted_files = []
                for idx, f in enumerate(entry_list):
                    formatted_files.append(
                        {
                            "path": f["path"],
                            "name": Path(f["path"]).name,
                            "dir": str(Path(f["path"]).parent),
                            "sizeBytes": int(f.get("size", file_size)),
                            "sizeHuman": human_bytes(int(f.get("size", file_size))),
                            "modifiedTs": int(f.get("modified_date", 0)),
                            "hash": f.get("hash", ""),
                            "recommendedKeep": idx == keep_idx,
                        }
                    )
                groups.append(
                    {
                        "id": f"dup:{len(groups)}",
                        "fileSizeBytes": file_size,
                        "fileSizeHuman": human_bytes(file_size),
                        "reclaimableBytes": reclaim_group,
                        "reclaimableHuman": human_bytes(reclaim_group),
                        "count": len(formatted_files),
                        "ext": Path(formatted_files[0]["path"]).suffix.lower().lstrip("."),
                        "files": formatted_files,
                    }
                )

    groups.sort(key=lambda g: g["reclaimableBytes"], reverse=True)
    return {
        "ok": True,
        "docsOnly": docs_only,
        "roots": [str(p) for p in scan_roots],
        "groupCount": len(groups),
        "duplicateFilesCount": sum(g["count"] - 1 for g in groups),
        "reclaimableBytes": reclaimable_total,
        "reclaimableHuman": human_bytes(reclaimable_total),
        "groups": groups,
    }


def scan_similar_images(roots: list[str] | None = None, max_diff: int = 5) -> dict[str, Any]:
    cz_bin = resolve_binary("czkawka_cli")
    if not cz_bin:
        return {"ok": False, "error": "未检测到 czkawka_cli"}

    scan_roots = [Path(r).expanduser() for r in roots] if roots else [p for p in DEFAULT_DUPE_ROOTS if p.exists()]
    scan_roots = [p for p in scan_roots if p.exists()]
    excludes = _collect_excluded_dirs(scan_roots)

    with tempfile.TemporaryDirectory(prefix="tidymac-czimg-") as tmp:
        out_json = Path(tmp) / "img.json"
        cmd = [cz_bin, "image"]
        for d in scan_roots:
            cmd.extend(["-d", str(d)])
        for ex in excludes:
            cmd.extend(["-e", ex])
        cmd.extend(["-E", "DEFAULT", "-s", str(max_diff), "-W", "-N", "-M", "-C", str(out_json)])
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=tool_env())
        if not out_json.exists():
            return {"ok": False, "error": (proc.stderr or proc.stdout or "czkawka_cli image 未生成结果").strip()}
        raw = json.loads(out_json.read_text(encoding="utf-8"))

    groups: list[dict[str, Any]] = []
    reclaimable_total = 0
    if isinstance(raw, list):
        for entry_list in raw:
            if not isinstance(entry_list, list) or len(entry_list) < 2:
                continue
            keep_idx = _pick_keep_index(entry_list)
            sizes = [int(x.get("size", 0)) for x in entry_list]
            reclaim_group = sum(sizes) - sizes[keep_idx]
            reclaimable_total += max(0, reclaim_group)
            formatted_files = []
            for idx, f in enumerate(entry_list):
                formatted_files.append(
                    {
                        "path": f["path"],
                        "name": Path(f["path"]).name,
                        "dir": str(Path(f["path"]).parent),
                        "sizeBytes": int(f.get("size", 0)),
                        "sizeHuman": human_bytes(int(f.get("size", 0))),
                        "width": f.get("width", 0),
                        "height": f.get("height", 0),
                        "difference": f.get("difference", 0),
                        "recommendedKeep": idx == keep_idx,
                    }
                )
            groups.append(
                {
                    "id": f"img:{len(groups)}",
                    "count": len(formatted_files),
                    "reclaimableBytes": max(0, reclaim_group),
                    "reclaimableHuman": human_bytes(max(0, reclaim_group)),
                    "files": formatted_files,
                }
            )

    groups.sort(key=lambda g: g["reclaimableBytes"], reverse=True)
    return {
        "ok": True,
        "groupCount": len(groups),
        "reclaimableBytes": reclaimable_total,
        "reclaimableHuman": human_bytes(reclaimable_total),
        "groups": groups,
    }


def scan_big_files(roots: list[str] | None = None, limit: int = 40) -> dict[str, Any]:
    cz_bin = resolve_binary("czkawka_cli")
    if not cz_bin:
        return {"ok": False, "error": "未检测到 czkawka_cli"}

    scan_roots = [Path(r).expanduser() for r in roots] if roots else [p for p in DEFAULT_DUPE_ROOTS if p.exists()]
    scan_roots = [p for p in scan_roots if p.exists()]
    excludes = _collect_excluded_dirs(scan_roots)

    with tempfile.TemporaryDirectory(prefix="tidymac-czbig-") as tmp:
        out_json = Path(tmp) / "big.json"
        cmd = [cz_bin, "big"]
        for d in scan_roots:
            cmd.extend(["-d", str(d)])
        for ex in excludes:
            cmd.extend(["-e", ex])
        cmd.extend(["-E", "DEFAULT", "-n", str(limit), "-W", "-N", "-M", "-C", str(out_json)])
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=tool_env())
        if not out_json.exists():
            return {"ok": False, "error": (proc.stderr or proc.stdout or "czkawka_cli big 未生成结果").strip()}
        raw = json.loads(out_json.read_text(encoding="utf-8"))

    files = []
    if isinstance(raw, list):
        for idx, item in enumerate(raw):
            p = item["path"]
            sz = int(item.get("size", 0))
            files.append(
                {
                    "id": f"big:{idx}",
                    "path": p,
                    "name": Path(p).name,
                    "dir": str(Path(p).parent),
                    "ext": Path(p).suffix.lower().lstrip("."),
                    "sizeBytes": sz,
                    "sizeHuman": human_bytes(sz),
                    "modifiedTs": int(item.get("modified_date", 0)),
                }
            )
    total_bytes = sum(f["sizeBytes"] for f in files)
    return {
        "ok": True,
        "count": len(files),
        "totalBytes": total_bytes,
        "totalHuman": human_bytes(total_bytes),
        "files": files,
    }


def trash_duplicate_paths(paths: list[str]) -> dict[str, Any]:
    results = move_to_trash(paths)
    reclaimed = sum(r.get("bytes", 0) for r in results if r.get("ok"))
    ok_count = sum(1 for r in results if r.get("ok"))
    return {
        "ok": ok_count == len(results),
        "movedCount": ok_count,
        "failedCount": len(results) - ok_count,
        "reclaimedBytes": reclaimed,
        "reclaimedHuman": human_bytes(reclaimed),
        "results": results,
    }
