from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .utils import HOME, human_bytes, move_to_trash, parse_human_size, path_size, resolve_binary, tool_env

MOLE_CONFIG_DIR = HOME / ".config" / "mole"
MOLE_CLEAN_LIST = MOLE_CONFIG_DIR / "clean-list.txt"
MOLE_WHITELIST = MOLE_CONFIG_DIR / "whitelist"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SECTION_RE = re.compile(r"^===\s*(.+?)\s*===$")
_ITEM_RE = re.compile(r"^(.+?)\s+#\s*([0-9.]+[KMGT]?B)(?:,\s*(.+))?$")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _load_whitelist() -> set[str]:
    if not MOLE_WHITELIST.exists():
        return set()
    lines = set()
    for line in MOLE_WHITELIST.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            lines.add(s)
    return lines


def add_to_whitelist(paths: list[str]) -> dict[str, Any]:
    MOLE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = _load_whitelist()
    added: list[str] = []
    for p in paths:
        s = str(Path(p).expanduser())
        if s and s not in existing:
            existing.add(s)
            added.append(s)
    if added:
        with MOLE_WHITELIST.open("a", encoding="utf-8") as fh:
            for s in added:
                fh.write(s + "\n")
    return {"ok": True, "added": added, "whitelistPath": str(MOLE_WHITELIST)}


def parse_clean_list() -> list[dict[str, Any]]:
    if not MOLE_CLEAN_LIST.exists():
        return []
    whitelist = _load_whitelist()
    items: list[dict[str, Any]] = []
    current_section = "未分组"
    seen_paths: set[str] = set()

    for raw in MOLE_CLEAN_LIST.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sec = _SECTION_RE.match(line)
        if sec:
            current_section = sec.group(1)
            continue
        m = _ITEM_RE.match(line)
        if not m:
            continue
        path_str = m.group(1).strip()
        size_str = m.group(2).strip()
        extra = (m.group(3) or "").strip()
        # Avoid double-counting sub-items already counted under a parent directory
        if "counted under " in extra:
            continue
        if path_str in seen_paths:
            continue
        seen_paths.add(path_str)
        p = Path(path_str)
        exists = p.exists() or p.is_symlink()
        size_bytes = parse_human_size(size_str)
        if exists and size_bytes == 0 and p.is_file():
            try:
                size_bytes = p.stat().st_size
            except OSError:
                pass
        items.append(
            {
                "id": f"mole:{len(items)}",
                "engine": "mole",
                "category": current_section,
                "path": path_str,
                "name": p.name,
                "sizeBytes": size_bytes,
                "sizeHuman": human_bytes(size_bytes) if size_bytes > 0 else size_str,
                "note": extra,
                "exists": exists,
                "whitelisted": path_str in whitelist,
            }
        )
    items.sort(key=lambda x: x["sizeBytes"], reverse=True)
    return items


def run_mole_dry_run() -> dict[str, Any]:
    mo_bin = resolve_binary("mo")
    if not mo_bin:
        return {
            "ok": False,
            "error": "未检测到 mo (Mole) 可执行文件",
            "items": [],
            "totalBytes": 0,
            "totalHuman": "0 B",
            "notices": [],
        }

    proc = subprocess.run(
        [mo_bin, "clean", "--dry-run"],
        capture_output=True,
        text=True,
        timeout=180,
        env=tool_env(),
    )
    output = _strip_ansi((proc.stdout or "") + "\n" + (proc.stderr or ""))
    notices: list[str] = []
    for line in output.splitlines():
        s = line.strip()
        if any(k in s for k in ("skipped", "Full Disk Access", "need sudo", "Potential space")):
            if s not in notices:
                notices.append(s)

    items = parse_clean_list()
    total_bytes = sum(x["sizeBytes"] for x in items if not x["whitelisted"])

    by_category: dict[str, dict[str, Any]] = {}
    for item in items:
        cat = item["category"]
        slot = by_category.setdefault(cat, {"category": cat, "count": 0, "sizeBytes": 0})
        slot["count"] += 1
        slot["sizeBytes"] += item["sizeBytes"]
    for slot in by_category.values():
        slot["sizeHuman"] = human_bytes(slot["sizeBytes"])

    return {
        "ok": proc.returncode == 0 or bool(items),
        "returncode": proc.returncode,
        "listFile": str(MOLE_CLEAN_LIST),
        "count": len(items),
        "totalBytes": total_bytes,
        "totalHuman": human_bytes(total_bytes),
        "categories": sorted(by_category.values(), key=lambda c: c["sizeBytes"], reverse=True),
        "items": items,
        "notices": notices,
    }


def apply_mole_trash(paths: list[str]) -> dict[str, Any]:
    """Move selected Mole cache/leftover entries to macOS Trash (reversible)."""
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


def run_mole_analyze(target_path: str | None = None) -> dict[str, Any]:
    mo_bin = resolve_binary("mo")
    if not mo_bin:
        return {"ok": False, "error": "未检测到 mo"}
    cmd = [mo_bin, "analyze", "--json"]
    if target_path:
        cmd.append(str(Path(target_path).expanduser()))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        env=tool_env(),
    )
    raw = (proc.stdout or "").strip()
    if not raw:
        return {"ok": False, "error": (proc.stderr or "mo analyze 未返回 JSON").strip()}
    try:
        data = json.loads(raw)
        return {"ok": True, "data": data}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"解析 mo analyze JSON 失败: {exc}", "rawPreview": raw[:500]}
