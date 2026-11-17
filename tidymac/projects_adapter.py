from __future__ import annotations

import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .utils import HOME, WORKBENCH_ROOT, fast_sizes_batch, human_bytes, move_to_trash, parse_human_size, resolve_binary, tool_env

DEFAULT_SCAN_ROOTS = [
    HOME / "Downloads",
    HOME / "Documents",
    HOME / "cursor",
    HOME / "WorkBuddy",
    HOME / "terminals",
]

_KONDO_IGNORE_DIRS = {
    "临时照片库", "图片与视频", "图片视频", "备考资料", "上财真题",
    "分卷收集版", "奚君羊《国际金融》", "货币银行学资料", "归档_安装包",
    "归档_截图录屏", "归档_学习资料", "归档_压缩包", "归档_图片与媒体",
    "transcripts", "backups", "python_libs",
}

_KONDO_HEADER_RE = re.compile(
    r"^(.+?)\s+([A-Za-z0-9._+/ -]+?)\s+project\s+\((.+?)\)\s*$"
)
_KONDO_CHILD_RE = re.compile(r"^\s*[└├]─\s+(.+?)\s+\(([^)]+)\)\s*$")
_PROJECT_MARKERS = {
    ".git", "package.json", "Cargo.toml", "pyproject.toml", "requirements.txt",
    "go.mod", "Package.swift", "CMakeLists.txt", "pom.xml", "build.gradle",
}


def _find_ignored_bundles_and_media(roots: list[Path]) -> list[str]:
    ignored: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            with os.scandir(root) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if entry.name.endswith(".app") or entry.name in _KONDO_IGNORE_DIRS:
                        ignored.append(entry.path)
        except OSError:
            continue
    return ignored


def scan_kondo_artifacts(roots: list[str] | None = None) -> dict[str, Any]:
    kondo_bin = resolve_binary("kondo")
    if not kondo_bin:
        return {"ok": False, "error": "未检测到 kondo 可执行文件", "projects": [], "totalBytes": 0, "totalHuman": "0 B"}

    scan_roots = [Path(r).expanduser() for r in roots] if roots else [p for p in DEFAULT_SCAN_ROOTS if p.exists()]
    scan_roots = [p for p in scan_roots if p.exists()]
    if not scan_roots:
        return {"ok": True, "projects": [], "totalBytes": 0, "totalHuman": "0 B"}

    cmd = [kondo_bin, "-n"]
    for ign in _find_ignored_bundles_and_media(scan_roots):
        cmd.extend(["-I", ign])
    cmd.extend(str(p) for p in scan_roots)

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=tool_env())
    lines = (proc.stdout or "").splitlines()

    projects: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in lines:
        hm = _KONDO_HEADER_RE.match(line)
        if hm:
            proj_path = hm.group(1).strip()
            # Safety guard: skip anything inside a .app bundle
            if ".app/" in proj_path:
                current = None
                continue
            current = {
                "id": f"kondo:{len(projects)}",
                "projectPath": proj_path,
                "name": Path(proj_path).name,
                "projectType": hm.group(2).strip(),
                "lastActiveText": hm.group(3).strip(),
                "artifacts": [],
                "artifactPaths": [],
                "sizeBytes": 0,
            }
            projects.append(current)
            continue
        cm = _KONDO_CHILD_RE.match(line)
        if cm and current is not None:
            art_name = cm.group(1).strip()
            art_size_str = cm.group(2).strip()
            art_path = str(Path(current["projectPath"]) / art_name)
            art_bytes = parse_human_size(art_size_str)
            current["artifacts"].append(
                {
                    "name": art_name,
                    "path": art_path,
                    "sizeBytes": art_bytes,
                    "sizeHuman": human_bytes(art_bytes) if art_bytes > 0 else art_size_str,
                }
            )
            current["artifactPaths"].append(art_path)
            current["sizeBytes"] += art_bytes

    # Filter out projects with 0 artifacts and sort descending by artifact size
    projects = [p for p in projects if p["artifacts"]]
    for p in projects:
        p["sizeHuman"] = human_bytes(p["sizeBytes"])
    projects.sort(key=lambda x: x["sizeBytes"], reverse=True)

    total_bytes = sum(p["sizeBytes"] for p in projects)
    return {
        "ok": True,
        "roots": [str(p) for p in scan_roots],
        "count": len(projects),
        "totalBytes": total_bytes,
        "totalHuman": human_bytes(total_bytes),
        "projects": projects,
    }


def _inspect_git(project_dir: Path) -> dict[str, Any]:
    git_dir = project_dir / ".git"
    if not git_dir.exists():
        # Check immediate work/ subdir (common in Codex workspaces)
        work_git = project_dir / "work" / ".git"
        if work_git.exists():
            project_dir = project_dir / "work"
            git_dir = work_git
        else:
            return {"hasGit": False, "hasRemote": False, "remotes": [], "commitCount": 0}

    remotes: list[str] = []
    commit_count = 0
    try:
        r_proc = subprocess.run(
            ["/usr/bin/git", "-C", str(project_dir), "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r_proc.returncode == 0:
            for line in r_proc.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] not in remotes:
                    remotes.append(parts[1])
    except Exception:
        pass

    try:
        c_proc = subprocess.run(
            ["/usr/bin/git", "-C", str(project_dir), "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if c_proc.returncode == 0 and c_proc.stdout.strip().isdigit():
            commit_count = int(c_proc.stdout.strip())
    except Exception:
        pass

    return {
        "hasGit": True,
        "hasRemote": bool(remotes),
        "remotes": remotes,
        "commitCount": commit_count,
    }


def _latest_source_mtime(root: Path, max_entries: int = 300) -> int:
    """Estimate last active timestamp ignoring heavy dependency caches."""
    latest = 0
    try:
        latest = int(root.stat().st_mtime)
    except OSError:
        return 0
    ignore_dirs = {"node_modules", ".venv", "venv", "target", "__pycache__", ".git", "dist", "build", ".local"}
    seen = 0
    stack = [root]
    while stack and seen < max_entries:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    seen += 1
                    if seen >= max_entries:
                        break
                    name = entry.name
                    if name.startswith(".") and name not in {".gitignore", ".env.example"}:
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                        if st.st_mtime > latest:
                            latest = int(st.st_mtime)
                        if entry.is_dir(follow_symlinks=False) and name not in ignore_dirs:
                            stack.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return latest


def _collect_ai_candidate_dirs() -> list[tuple[Path, str]]:
    """Collect AI sandbox / code project directories without deep walking dependency trees."""
    candidates: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    def add(p: Path, source_label: str) -> None:
        try:
            rp = p.resolve()
        except OSError:
            rp = p
        if rp in seen or rp == WORKBENCH_ROOT.resolve():
            return
        if not p.is_dir() or p.name.startswith(".") or p.name.endswith(".app"):
            return
        seen.add(rp)
        candidates.append((p, source_label))

    # 1. Codex: ~/Documents/Codex/YYYY-MM-DD/<slug>
    codex_root = HOME / "Documents" / "Codex"
    if codex_root.exists():
        try:
            for day_dir in codex_root.iterdir():
                if day_dir.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", day_dir.name):
                    for sess in day_dir.iterdir():
                        if sess.is_dir():
                            add(sess, "Codex 试水工程")
        except OSError:
            pass

    # 2. DeepSeek Harness: ~/Documents/deepseek-harness/default-workspace/<dir>
    dsh_root = HOME / "Documents" / "deepseek-harness" / "default-workspace"
    if dsh_root.exists():
        try:
            for item in dsh_root.iterdir():
                if item.is_dir() and item.name not in {"transcripts", "backups", "browse"}:
                    add(item, "DeepSeek 试水工程")
        except OSError:
            pass

    # 3. Kimi tasks/workspace
    for kimi_sub in (HOME / "Documents" / "kimi" / "workspace", HOME / "Documents" / "kimi" / "tasks"):
        if kimi_sub.exists():
            try:
                for item in kimi_sub.iterdir():
                    if item.is_dir():
                        add(item, "Kimi 试水工程")
            except OSError:
                pass

    # 4. ~/cursor/<project>
    cursor_root = HOME / "cursor"
    if cursor_root.exists():
        try:
            for item in cursor_root.iterdir():
                if item.is_dir() and item.name not in {"tools"}:
                    add(item, "Cursor 工程")
        except OSError:
            pass

    # 5. Top-level code projects in ~/Documents and ~/Downloads
    for base, label in ((HOME / "Documents", "Documents 工程"), (HOME / "Downloads", "Downloads 工程")):
        if not base.exists():
            continue
        try:
            for item in base.iterdir():
                if not item.is_dir() or item.name in {"Codex", "deepseek-harness", "kimi"}:
                    continue
                if any((item / marker).exists() for marker in _PROJECT_MARKERS):
                    add(item, label)
        except OSError:
            pass

    return candidates


def scan_abandoned_projects(min_idle_days: int = 7) -> dict[str, Any]:
    now = int(time.time())
    candidates = _collect_ai_candidate_dirs()
    sizes = fast_sizes_batch([p for p, _ in candidates], workers=4)

    def _inspect_one(item: tuple[int, tuple[Path, str]]) -> dict[str, Any]:
        idx, (proj_dir, source_label) = item
        size_bytes = sizes.get(str(proj_dir), 0)
        last_ts = _latest_source_mtime(proj_dir)
        days_idle = max(0, (now - last_ts) // 86400) if last_ts > 0 else 0
        git_info = _inspect_git(proj_dir)

        reasons: list[str] = []
        if not git_info["hasGit"]:
            reasons.append("无 Git 仓库")
        elif not git_info["hasRemote"]:
            reasons.append("无 Git 远程仓库")
        if git_info["hasGit"] and git_info["commitCount"] <= 1:
            reasons.append(f"仅 {git_info['commitCount']} 次提交")
        if days_idle >= min_idle_days:
            reasons.append(f"{days_idle} 天未修改")

        is_abandoned = (
            (not git_info["hasRemote"] and days_idle >= min_idle_days)
            or (git_info["hasGit"] and not git_info["hasRemote"] and git_info["commitCount"] <= 1)
            or ("试水" in source_label and not git_info["hasRemote"] and size_bytes >= 10 * 1024 * 1024 and days_idle >= 3)
        )

        markers: list[str] = []
        for m in ("package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "Package.swift"):
            if (proj_dir / m).exists() or (proj_dir / "work" / m).exists():
                markers.append(m)

        return {
            "id": f"proj:{idx}",
            "path": str(proj_dir),
            "name": proj_dir.name,
            "sourceLabel": source_label,
            "sizeBytes": size_bytes,
            "sizeHuman": human_bytes(size_bytes),
            "lastModifiedTs": last_ts,
            "daysIdle": days_idle,
            "hasGit": git_info["hasGit"],
            "hasRemote": git_info["hasRemote"],
            "remotes": git_info["remotes"],
            "commitCount": git_info["commitCount"],
            "markers": markers,
            "isAbandonedCandidate": is_abandoned,
            "reason": " · ".join(reasons) if reasons else "活跃或已绑定远程工程",
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_inspect_one, enumerate(candidates)))

    results.sort(key=lambda x: (x["isAbandonedCandidate"], x["sizeBytes"]), reverse=True)
    abandoned_only = [r for r in results if r["isAbandonedCandidate"]]
    abandoned_bytes = sum(r["sizeBytes"] for r in abandoned_only)
    total_bytes = sum(r["sizeBytes"] for r in results)

    return {
        "ok": True,
        "count": len(results),
        "abandonedCount": len(abandoned_only),
        "abandonedBytes": abandoned_bytes,
        "abandonedHuman": human_bytes(abandoned_bytes),
        "totalBytes": total_bytes,
        "totalHuman": human_bytes(total_bytes),
        "projects": results,
    }


def trash_project_paths(paths: list[str]) -> dict[str, Any]:
    """Trash either dependency subdirs ('只清依赖保留源码') or whole abandoned project dirs."""
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
