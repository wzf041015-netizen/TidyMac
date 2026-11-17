from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

HOME = Path.home()
WORKBENCH_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = WORKBENCH_ROOT / "bin"
CONFIG_DIR = WORKBENCH_ROOT / "config"
USER_STATE_DIR = HOME / ".config" / "tidymac"
HISTORY_DIR = USER_STATE_DIR / "history"


def tool_env() -> dict[str, str]:
    env = os.environ.copy()
    extra_bins = [
        str(BIN_DIR),
        str(HOME / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    current = env.get("PATH", "")
    parts = [p for p in extra_bins + current.split(":") if p]
    deduped = list(dict.fromkeys(parts))
    env["PATH"] = ":".join(deduped)
    env["NO_COLOR"] = "1"
    return env


def resolve_binary(name: str) -> str | None:
    env = tool_env()
    return shutil.which(name, path=env["PATH"])


def human_bytes(num: int | float) -> str:
    n = float(max(0, num))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{int(num)} B"


_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)\s*$", re.IGNORECASE)


def parse_human_size(text: str) -> int:
    m = _SIZE_RE.match(text or "")
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2).upper()
    # Both decimal (KB/MB/GB) and binary (KiB/MiB/GiB) units mapped cleanly
    if unit in ("B",):
        mult = 1
    elif unit in ("KB", "KIB"):
        mult = 1024 if "I" in unit else 1000
    elif unit in ("MB", "MIB"):
        mult = 1024**2 if "I" in unit else 1000**2
    elif unit in ("GB", "GIB"):
        mult = 1024**3 if "I" in unit else 1000**3
    elif unit in ("TB", "TIB"):
        mult = 1024**4 if "I" in unit else 1000**4
    else:
        mult = 1
    return int(val * mult)


def path_size(path: Path) -> int:
    """Compute logical + apparent disk size safely without following symlinks."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not os.path.isdir(path) or os.path.islink(path):
        # Use st_blocks * 512 when available on macOS for sparse-aware size, falling back to st_size
        blocks = getattr(st, "st_blocks", 0)
        return max(st.st_size, 0) if blocks == 0 else blocks * 512
    total = 0
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            est = entry.stat(follow_symlinks=False)
                            total += est.st_size
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            est = entry.stat(follow_symlinks=False)
                            blocks = getattr(est, "st_blocks", 0)
                            total += max(est.st_size, 0) if blocks == 0 else blocks * 512
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def fast_sizes_batch(paths: list[str | Path], workers: int = 4) -> dict[str, int]:
    """Compute disk sizes in parallel using native /usr/bin/du -sk (with Python fallback)."""
    from concurrent.futures import ThreadPoolExecutor

    str_paths = [str(p) for p in dict.fromkeys(paths) if Path(p).exists() or Path(p).is_symlink()]
    if not str_paths:
        return {}

    result: dict[str, int] = {}
    # Split files (instant lstat) from directories (native C du -sk)
    dirs: list[str] = []
    for sp in str_paths:
        p = Path(sp)
        if p.is_symlink() or not p.is_dir():
            result[sp] = path_size(p)
        else:
            dirs.append(sp)

    if dirs:
        chunk_size = max(1, (len(dirs) + workers - 1) // workers)
        chunks = [dirs[i : i + chunk_size] for i in range(0, len(dirs), chunk_size)]

        def _run_du(chunk: list[str]) -> dict[str, int]:
            out: dict[str, int] = {}
            try:
                proc = subprocess.run(
                    ["/usr/bin/du", "-sk"] + chunk,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                for line in (proc.stdout or "").splitlines():
                    tab = line.find("\t")
                    if tab > 0:
                        try:
                            kb = int(line[:tab].strip())
                            path_key = line[tab + 1 :]
                            out[path_key] = kb * 1024
                        except ValueError:
                            continue
            except Exception:
                pass
            return out

        with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as pool:
            for part in pool.map(_run_du, chunks):
                result.update(part)

        for d in dirs:
            if d not in result:
                result[d] = path_size(Path(d))

    return result


def is_protected_path(path: Path) -> str | None:
    """Block catastrophic targets even if selected accidentally."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    critical = {
        Path("/"),
        Path("/System"),
        Path("/Applications"),
        Path("/Library"),
        Path("/Users"),
        HOME.resolve(),
        (HOME / "Library").resolve(),
        (HOME / "Documents").resolve(),
        (HOME / "Downloads").resolve(),
        (HOME / "Desktop").resolve(),
        (HOME / ".Trash").resolve(),
        WORKBENCH_ROOT.resolve(),
    }
    if resolved in critical:
        return f"受保护的系统或根目录，禁止删除: {resolved}"
    return None


def move_to_trash(paths: list[str | Path]) -> list[dict[str, Any]]:
    """Move files/directories safely to macOS Trash using /usr/bin/trash (never rm -rf)."""
    trash_bin = resolve_binary("trash") or "/usr/bin/trash"
    results: list[dict[str, Any]] = []

    for raw in paths:
        p = Path(raw).expanduser()
        if not p.exists() and not p.is_symlink():
            results.append({"path": str(p), "ok": False, "error": "路径不存在或已清理"})
            continue
        reason = is_protected_path(p)
        if reason:
            results.append({"path": str(p), "ok": False, "error": reason})
            continue
        size_before = path_size(p)
        try:
            proc = subprocess.run(
                [trash_bin, str(p)],
                capture_output=True,
                text=True,
                timeout=60,
                env=tool_env(),
            )
            if proc.returncode == 0 and not p.exists():
                results.append(
                    {
                        "path": str(p),
                        "ok": True,
                        "bytes": size_before,
                        "method": "trash",
                    }
                )
                continue
            err_msg = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        except Exception as exc:
            err_msg = str(exc)

        # Fallback: macOS Finder AppleScript Trash (handles certain protected metadata gracefully)
        try:
            escaped = str(p).replace("\\", "\\\\").replace('"', '\\"')
            osa = subprocess.run(
                [
                    "/usr/bin/osascript",
                    "-e",
                    f'tell application "Finder" to delete POSIX file "{escaped}"',
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if osa.returncode == 0 and not p.exists():
                results.append(
                    {
                        "path": str(p),
                        "ok": True,
                        "bytes": size_before,
                        "method": "finder_trash",
                    }
                )
                continue
            err_msg = f"{err_msg}; finder: {(osa.stderr or '').strip()}"
        except Exception as exc2:
            err_msg = f"{err_msg}; finder: {exc2}"

        results.append({"path": str(p), "ok": False, "bytes": 0, "error": err_msg})

    # Append audit log
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        log_file = HISTORY_DIR / "trash_audit.jsonl"
        import time

        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {"ts": int(time.time()), "action": "trash", "results": results},
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    return results


def check_permissions() -> dict[str, Any]:
    """Probe TCC Full Disk Access and standard user folders without triggering noise."""
    fda_probes = [HOME / "Library" / "Safari", HOME / "Library" / "Mail", HOME / ".Trash"]
    fda_granted = False
    probe_details: dict[str, str] = {}
    for probe in fda_probes:
        if probe.exists():
            try:
                with os.scandir(probe) as it:
                    next(it, None)
                fda_granted = True
                probe_details[probe.name] = "ok"
            except PermissionError as exc:
                probe_details[probe.name] = f"denied:{exc}"
            except OSError as exc:
                probe_details[probe.name] = f"oserr:{exc}"
        else:
            probe_details[probe.name] = "missing"

    swift_fda = os.environ.get("TIDYMAC_SWIFT_FDA")
    swift_detail = os.environ.get("TIDYMAC_SWIFT_FDA_DETAIL", "")
    fda_note = (
        "已授予完全磁盘访问权限"
        if fda_granted
        else "未授予完全磁盘访问权限（若刚在系统设置开启，请完全退出 TidyMac 并重新打开，且确认列表中无重复旧条目）"
    )

    folders = {}
    for label, p in [
        ("Downloads", HOME / "Downloads"),
        ("Documents", HOME / "Documents"),
        ("Desktop", HOME / "Desktop"),
        ("Library/Preferences", HOME / "Library" / "Preferences"),
        ("Library/LaunchAgents", HOME / "Library" / "LaunchAgents"),
        ("Library/Application Support", HOME / "Library" / "Application Support"),
        ("Library/Containers", HOME / "Library" / "Containers"),
    ]:
        ok = False
        err = ""
        if p.exists():
            try:
                with os.scandir(p) as it:
                    next(it, None)
                ok = True
            except OSError as exc:
                err = str(exc)
        else:
            err = "目录不存在"
        folders[label] = {"path": str(p), "readable": ok, "error": err}

    return {
        "fullDiskAccess": fda_granted,
        "fullDiskAccessNote": fda_note,
        "probeDetails": probe_details,
        "swiftFda": swift_fda,
        "swiftDetail": swift_detail,
        "settingsUrl": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
        "folders": folders,
    }


def _cmd_version(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=tool_env())
        out = (proc.stdout or proc.stderr or "").strip().splitlines()
        for line in out:
            if line.strip():
                return line.strip()
        return f"exit {proc.returncode}"
    except Exception as exc:
        return f"error: {exc}"


_ENGINE_CACHE: dict[str, Any] | None = None


def engine_status(refresh: bool = False) -> dict[str, Any]:
    global _ENGINE_CACHE
    if _ENGINE_CACHE is not None and not refresh:
        return _ENGINE_CACHE
    from concurrent.futures import ThreadPoolExecutor

    mo_bin = resolve_binary("mo")
    kondo_bin = resolve_binary("kondo")
    cz_bin = resolve_binary("czkawka_cli")
    org_bin = resolve_binary("organize")
    ocr_bin = str(BIN_DIR / "mac-ocr-extract") if (BIN_DIR / "mac-ocr-extract").exists() else None
    pear_app = Path("/Applications/Pearcleaner.app")
    trash_bin = resolve_binary("trash") or ("/usr/bin/trash" if Path("/usr/bin/trash").exists() else None)

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_mo = pool.submit(_cmd_version, [mo_bin, "--version"]) if mo_bin else None
        f_cz = pool.submit(_cmd_version, [cz_bin, "--version"]) if cz_bin else None
        f_org = pool.submit(_cmd_version, [org_bin, "--version"]) if org_bin else None
        v_mo = f_mo.result() if f_mo else None
        v_cz = f_cz.result() if f_cz else None
        v_org = f_org.result() if f_org else None

    _ENGINE_CACHE = {
        "mole": {
            "installed": bool(mo_bin),
            "path": mo_bin,
            "version": v_mo,
            "repo": "tw93/Mole",
        },
        "pearcleaner": {
            "installed": pear_app.exists(),
            "path": str(pear_app) if pear_app.exists() else None,
            "version": "5.4.3 (app + 内置规则引擎)",
            "repo": "alienator88/Pearcleaner",
        },
        "kondo": {
            "installed": bool(kondo_bin),
            "path": kondo_bin,
            "version": "0.9" if kondo_bin else None,
            "repo": "tbillington/kondo",
        },
        "czkawka": {
            "installed": bool(cz_bin),
            "path": cz_bin,
            "version": v_cz,
            "repo": "qarmin/czkawka",
        },
        "organize": {
            "installed": bool(org_bin),
            "path": org_bin,
            "version": v_org,
            "repo": "tfeldmann/organize",
        },
        "ocr": {
            "installed": bool(ocr_bin),
            "path": ocr_bin,
            "version": "Apple PDFKit + Vision OCR",
            "repo": "macOS Native",
        },
        "trash": {
            "installed": bool(trash_bin),
            "path": trash_bin,
            "version": "macOS Trash",
        },
    }
    return _ENGINE_CACHE
