from __future__ import annotations

import os
import plistlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import HOME, fast_sizes_batch, human_bytes, move_to_trash

# Derived directly from alienator88/Pearcleaner Conditions.swift + ReversePathsFetch.swift
SKIP_REVERSE_TOKENS = {
    "apple", "temporary", "btserver", "proapps", "scripteditor", "ilife", "livefsd",
    "siritoday", "addressbook", "animoji", "appstore", "askpermission", "callhistory",
    "clouddocs", "diskimages", "dock", "facetime", "fileprovider", "instruments",
    "knowledge", "mobilesync", "syncservices", "homeenergyd", "icloud", "icdd",
    "networkserviceproxy", "familycircle", "geoservices", "installation", "passkit",
    "sharedimagecache", "desktop", "mbuseragent", "swiftpm", "baseband",
    "coresimulator", "photoslegacyupgrade", "photosupgrade", "siritts", "ipod",
    "globalpreferences", "apmanalytics", "apmexperiment", "avatarcache", "byhost",
    "contextstoreagent", "mobilemeaccounts", "mobiledocuments", "mobile",
    "intentbuilderc", "loginwindow", "momc", "replayd", "sharedfilelistd", "clang",
    "audiocomponent", "csexattrcryptoservice", "livetranscriptionagent",
    "sandboxhelper", "statuskitagent", "betaenrollmentd", "contentlinkingd",
    "diagnosticextensionsd", "gamed", "heard", "homed", "itunescloudd", "lldb",
    "mds", "mediaanalysisd", "metrickitd", "mobiletimerd", "proactived",
    "ptpcamerad", "studentd", "talagent", "watchlistd", "apptranslocation", "xcrun",
    "ds_store", "dsstore", "caches", "crashreporter", "trash", "pearcleaner",
    "amsdatamigratortool", "arfilecache", "assistant", "chromium", "cloudkit",
    "webkit", "databases", "diagnostic", "cache", "gamekit", "homebrew", "logi",
    "microsoft", "mozilla", "sync", "google", "sentinel", "hexnode", "sentry",
    "tvappservices", "reminders", "pbs", "notarytool", "differentialprivacy",
    "storeassetd", "webpush", "storedownloadd", "fsck", "crash", "python",
    "discrecording", "photossearch", "pylint", "jamf", "scopedbookmarkagent",
    "anonymous", "identifier", "isolated", "nobackup", "privacypreservingmeasurement",
    "symbols", "stickersd", "privatecloudcomputed", "tipsd", "controlcenter",
    "contactsd", "staticcheck", "index", "segment", "sparkle", "summaryevents",
    "launchdarkly", "identityservicesd", "embeddedbinaryvalidationutility",
    "comalienator88", "aaprofilepicture", "minilauncher", "jna", "automator",
    "locationaccessstored", "spotlight", "cef", "mobiledocuments",
    "comapplepasswordmanager", "daemonnameoridentifierhere",
}

SKIP_APP_SUPPORT_EXACT = {
    "apple", "addressbook", "animoji", "app store", "callhistorydb", "callhistorytransactions",
    "clouddocs", "cloudkit", "contacts", "crashreporter", "diskimages", "dock",
    "facetime", "fileprovider", "icdd", "icloud", "knowledge", "mobilesync",
    "syncservices", "com.apple.sharedfilelist", "com.apple.tcc",
    "com.apple.backgroundtaskmanagementagent", "dmd", "differentialprivacy",
    "videosubscriptionsd", "privatecloudcompute", "cef", "caches", "logs",
    # CLI / agent / runtime tools & Apple system folders
    "claude", "codex", "google", "antigravity", "uv", "pip", "npm", "pnpm",
    "organize", "mole", "tidymac", "dotnet", "factory", "playwright", "ms-playwright",
    "music", "softwareupdate", "safari-web-extension-converter.plist",
}

# Pearcleaner-style extra bundle -> keyword associations for installed apps
BUNDLE_ALIASES: dict[str, tuple[str, ...]] = {
    "com.apple.dt.xcode": ("com.apple.dt", "xcode", "simulator", "coresimulator"),
    "com.google.chrome": ("google", "chrome"),
    "com.microsoft.vscode": ("vscode", "code"),
    "com.tencent.xinwechat": ("wechat", "weixin", "xinwechat", "微信"),
    "com.tencent.qq": ("tencent.qq", "qq"),
    "com.tencent.weworkmac": ("wework", "wecom", "企业微信"),
    "com.tencent.meeting": ("tencentmeeting", "wemeet", "腾讯会议"),
    "com.todesktop.230313mzl4w4u92": ("cursor",),
    "com.tinyspeck.slackmacgap": ("slack",),
    "com.openai.chat": ("chatgpt", "openai", "com.openai.sky", "cuaservice"),
    "com.openai.codex": ("chatgpt", "openai", "com.openai.chat", "com.openai.sky", "cuaservice"),
    "com.anthropic.claudefordesktop": ("claude",),
    "com.moonshot.kimichat": ("kimi",),
    "com.deepseek.dsh": ("deepseek", "dsh"),
    "com.hguandl.meoasstmac": ("maa", "meoasst"),
    "com.quark.desktop": ("quark", "夸克"),
    "com.youqu.todesk.mac": ("todesk",),
    "com.oray.sunlogin.macclient": ("sunlogin", "oray", "向日葵"),
    "com.vivo.pcsuite": ("vivo", "pcsuite", "vivoconnservice"),
    "com.tomatodo.app": ("tomatodo", "番茄todo"),
    "com.kingsoft.wpsoffice.mac": ("wps", "kingsoft"),
    "com.alibaba.tongyi": ("qianwen", "tongyi"),
    "com.netease.uuremote": ("uuremote", "uuyc"),
    "com.netease.mumu": ("mumu", "nemux"),
    "com.bluestacks": ("bluestacks", "hd-player", "bst_"),
    "com.nox": ("nox", "noxappplayer"),
    "net.ankiweb.dtop": ("anki",),
    "com.sogou": ("sogou", "搜狗"),
    "com.valvesoftware.steam": ("steam",),
    "com.electron.ollama": ("ollama",),
}

KNOWN_VENDOR_NAMES: dict[str, str] = {
    "bluestacks": "BlueStacks Air (模拟器)",
    "hd-player": "BlueStacks HD-Player",
    "hd-multiinstancemanager": "BlueStacks 多开器",
    "bst_boost": "BlueStacks",
    "nox": "NoxAppPlayer (夜神模拟器)",
    "anki": "Anki",
    "sogou": "搜狗输入法/助手",
    "sunlogin": "向日葵远程控制",
    "oray": "向日葵 / 贝锐",
    "ollama": "Ollama",
    "filemind": "FileMind (旧版测试)",
    "mumu": "MuMu 模拟器",
    "nemux": "MuMu 模拟器",
    "netease": "网易应用",
    "adobe": "Adobe",
    "jetbrains": "JetBrains IDE",
    "docker": "Docker Desktop",
    "vmware": "VMware Fusion",
    "parallels": "Parallels Desktop",
    "raycast": "Raycast",
    "notion": "Notion",
    "figma": "Figma",
    "postman": "Postman",
    "obsproject": "OBS Studio",
    "bilibili": "哔哩哔哩桌面端",
    "cxstudy": "超星学习通",
    "apifox": "Apifox",
    "snipaste": "Snipaste",
    "iina": "IINA",
    "bartender": "Bartender",
    "cleanmymac": "CleanMyMac",
    "macpaw": "MacPaw",
    "tencent": "腾讯系应用",
    "bytedance": "字节跳动系应用",
    "doubao": "豆包",
    "lark": "飞书",
    "feishu": "飞书",
    "dingtalk": "钉钉",
    "alibaba": "阿里系应用",
    "baidu": "百度系应用",
    "xunlei": "迅雷",
    "thunder": "迅雷",
    "wps": "WPS Office",
    "kingsoft": "金山 / WPS",
    "youdao": "网易有道",
    "grammarly": "Grammarly",
    "spotify": "Spotify",
    "discord": "Discord",
    "zoom": "Zoom",
    "slack": "Slack",
    "arc": "Arc Browser",
    "brave": "Brave Browser",
    "firefox": "Mozilla Firefox",
}

_UUID_RE = re.compile(r"^[0-9a-fA-F]{32}$|^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


def pear_format(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass
class InstalledApp:
    name: str
    bundle_id: str
    path: Path
    fmt_name: str
    fmt_bundle: str
    aliases: list[str]


_GENERIC_IGNORE_TOKENS = {
    "desktop", "macos", "music", "installer", "helper", "agent", "launcher",
    "client", "browser", "player", "viewer", "reader", "editor", "studio",
    "service", "daemon", "updater", "manager", "server", "local", "shared",
    "system", "games", "books", "notes", "photos", "videos", "audio",
}


def discover_installed_apps() -> list[InstalledApp]:
    search_roots = [
        Path("/Applications"),
        Path("/Applications/Utilities"),
        HOME / "Applications",
        Path("/System/Applications"),
        Path("/System/Applications/Utilities"),
        Path("/System/Library/CoreServices"),
    ]
    app_paths: dict[str, Path] = {}

    def scan_dir(root: Path, depth: int = 0) -> None:
        if not root.exists() or depth > 3:
            return
        try:
            with os.scandir(root) as it:
                for entry in it:
                    name = entry.name
                    p = Path(entry.path)
                    if name.endswith(".app") and entry.is_dir(follow_symlinks=True):
                        app_paths[str(p)] = p
                    elif entry.is_dir(follow_symlinks=False) and not name.startswith(".") and not name.endswith(".app"):
                        scan_dir(p, depth + 1)
        except OSError:
            pass

    for r in search_roots:
        scan_dir(r)

    # Also check Spotlight for apps in custom folders (e.g. ~/Downloads/Xcode-beta.app)
    try:
        proc = subprocess.run(
            ["/usr/bin/mdfind", "kMDItemContentType == 'com.apple.application-bundle'"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line or "/.Trash/" in line or "/DerivedData/" in line or "/Build/" in line:
                    continue
                p = Path(line)
                if p.exists() and p.name.endswith(".app"):
                    app_paths[str(p)] = p
    except Exception:
        pass

    apps: list[InstalledApp] = []
    for p in app_paths.values():
        candidate_plists = [p / "Contents" / "Info.plist"]
        wrapper_dir = p / "Wrapper"
        if wrapper_dir.exists():
            candidate_plists.extend(w / "Info.plist" for w in wrapper_dir.glob("*.app"))
        login_dir = p / "Contents" / "Library" / "LoginItems"
        if login_dir.exists():
            candidate_plists.extend(l / "Contents" / "Info.plist" for l in login_dir.glob("*.app"))

        for info_plist in candidate_plists:
            bundle_id = ""
            disp_name = p.stem
            exec_name = ""
            if info_plist.exists():
                try:
                    with info_plist.open("rb") as fh:
                        data = plistlib.load(fh)
                    bundle_id = str(data.get("CFBundleIdentifier") or "")
                    disp_name = str(
                        data.get("CFBundleDisplayName")
                        or data.get("CFBundleName")
                        or p.stem
                    )
                    exec_name = str(data.get("CFBundleExecutable") or "")
                except Exception:
                    pass
            fmt_bundle = pear_format(bundle_id)
            fmt_name = pear_format(disp_name)
            is_apple_sys = bundle_id.startswith("com.apple.") or str(p).startswith("/System/")
            aliases: list[str] = []
            if not is_apple_sys:
                aliases = [pear_format(p.stem)]
                if exec_name:
                    aliases.append(pear_format(exec_name))
                for k, extra_list in BUNDLE_ALIASES.items():
                    if k in bundle_id.lower() or (fmt_bundle and k.replace(".", "") in fmt_bundle):
                        aliases.extend(pear_format(x) for x in extra_list)
                if "." in bundle_id:
                    tail = pear_format(bundle_id.split(".")[-1])
                    if len(tail) >= 5 and tail not in _GENERIC_IGNORE_TOKENS:
                        aliases.append(tail)
            apps.append(
                InstalledApp(
                    name=disp_name,
                    bundle_id=bundle_id,
                    path=p,
                    fmt_name="" if is_apple_sys or fmt_name in _GENERIC_IGNORE_TOKENS else fmt_name,
                    fmt_bundle=fmt_bundle,
                    aliases=[a for a in dict.fromkeys(aliases) if len(a) >= 5 and a not in _GENERIC_IGNORE_TOKENS],
                )
            )
    return apps


def _is_related_to_installed(item_path: Path, item_name: str, apps: list[InstalledApp], container_bundle: str = "") -> bool:
    fmt_item = pear_format(item_name)
    fmt_path = pear_format(str(item_path))
    fmt_c_bundle = pear_format(container_bundle)

    # Check skipReverse tokens from Pearcleaner
    for tok in SKIP_REVERSE_TOKENS:
        if len(tok) >= 5 and tok in fmt_item:
            return True
    if item_name.lower() in SKIP_APP_SUPPORT_EXACT or item_name.lower() in {"music", "softwareupdate", "safari-web-extension-converter.plist"}:
        return True
    if item_name.startswith("com.apple.") or item_name.startswith("group.com.apple."):
        return True
    if _UUID_RE.match(item_name) or _UUID_RE.match(fmt_item):
        return True
    # Check active CLI/tool runtimes that exist in user home or ~/.local/bin
    if fmt_item in {"uv", "pip", "npm", "pnpm", "yarn", "cargo", "rust", "go", "python", "node"}:
        return True
    for cli_keep in (
        "grokbot", "claude", "codex", "kimi", "deepseek", "antigravity", "gemini",
        "tidymac", "mole", "czkawka", "organize", "kondo", "pearcleaner",
        "appcleaner", "clash", "mihomo", "dotnet", "factory", "playwright",
    ):
        if len(cli_keep) >= 4 and (cli_keep in fmt_item or cli_keep in fmt_c_bundle):
            return True

    for app in apps:
        if app.fmt_bundle and len(app.fmt_bundle) >= 5:
            if app.fmt_bundle in fmt_path or (fmt_c_bundle and app.fmt_bundle in fmt_c_bundle):
                return True
        if app.fmt_name and len(app.fmt_name) >= 5:
            if app.fmt_name in fmt_item or (fmt_c_bundle and app.fmt_name in fmt_c_bundle):
                return True
        for alias in app.aliases:
            if len(alias) >= 5 and (alias in fmt_item or (fmt_c_bundle and alias in fmt_c_bundle)):
                return True
    return False


def _read_plist_safe(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = plistlib.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _infer_app_info(path: Path, category: str) -> tuple[str, str, str]:
    """Return (inferredAppName, bundleId, detailReason)."""
    name = path.name
    stem = path.stem if path.is_file() or name.endswith((".plist", ".savedState")) else name
    bundle_id = ""
    detail = ""

    if category == "Containers":
        meta_plist = path / ".com.apple.containermanagerd.metadata.plist"
        meta = _read_plist_safe(meta_plist)
        mcm_id = str(meta.get("MCMMetadataIdentifier") or "")
        if mcm_id:
            bundle_id = mcm_id
            stem = mcm_id
    elif category == "LaunchAgents" and path.suffix == ".plist":
        pdata = _read_plist_safe(path)
        bundle_id = str(pdata.get("Label") or stem)
        prog_args = pdata.get("ProgramArguments") or ([] if not pdata.get("Program") else [pdata.get("Program")])
        if isinstance(prog_args, list) and prog_args:
            exe = str(prog_args[0])
            exe_exists = Path(exe).exists()
            detail = f"启动目标: {exe} ({'已丢失' if not exe_exists else '文件存在'})"
            if exe_exists and ".app/" in exe:
                # Extract .app name if it actually still exists on disk!
                app_part = exe.split(".app/")[0] + ".app"
                if Path(app_part).exists():
                    detail = f"LAUNCH_TARGET_EXISTS:{app_part}"
    elif category == "Preferences" and path.suffix == ".plist":
        bundle_id = stem

    if not bundle_id and "." in stem and not stem.startswith("."):
        bundle_id = stem

    lower = (bundle_id + " " + stem).lower()
    inferred = ""
    for key, label in KNOWN_VENDOR_NAMES.items():
        if key in lower:
            inferred = label
            break

    if not inferred:
        # Clean reverse-DNS or ShipIt/Helper suffixes to derive a readable app name
        cleaned = re.sub(r"\.(plist|savedState|ShipIt|Helper|helper|updater|daemon|agent)$", "", stem, flags=re.IGNORECASE)
        parts = [p for p in cleaned.split(".") if p and p.lower() not in {"com", "org", "net", "io", "cn", "app", "mac", "macos", "desktop", "electron", "team", "group"}]
        if parts:
            inferred = parts[-1]
        else:
            inferred = stem

    return inferred, bundle_id or stem, detail


def scan_orphaned_files() -> dict[str, Any]:
    """Scan ~/Library locations using Pearcleaner-compatible reverse path matching."""
    apps = discover_installed_apps()
    lib = HOME / "Library"

    locations: list[tuple[str, Path, str]] = [
        ("Preferences (.plist)", lib / "Preferences", "plist_only"),
        ("LaunchAgents", lib / "LaunchAgents", "plist_only"),
        ("Application Support", lib / "Application Support", "dir_only"),
        ("Containers", lib / "Containers", "dir_only"),
        ("Saved Application State", lib / "Saved Application State", "saved_state"),
        ("HTTPStorages", lib / "HTTPStorages", "any"),
        ("WebKit", lib / "WebKit", "any"),
    ]

    orphans: list[dict[str, Any]] = []
    scanned_locations = 0

    for cat_label, loc_path, mode in locations:
        if not loc_path.exists():
            continue
        scanned_locations += 1
        try:
            entries = list(os.scandir(loc_path))
        except OSError:
            continue

        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in {"ByHost", "com.apple.LaunchServices"}:
                continue
            p = Path(entry.path)
            if mode == "plist_only" and not name.endswith(".plist"):
                continue
            if mode == "dir_only" and not entry.is_dir(follow_symlinks=False):
                continue
            if mode == "saved_state" and not name.endswith(".savedState"):
                continue

            container_bundle = ""
            if cat_label == "Containers":
                meta = _read_plist_safe(p / ".com.apple.containermanagerd.metadata.plist")
                container_bundle = str(meta.get("MCMMetadataIdentifier") or "")

            if _is_related_to_installed(p, name, apps, container_bundle=container_bundle):
                continue

            inferred_app, bundle_id, extra_detail = _infer_app_info(
                p,
                "Preferences" if "Preferences" in cat_label else cat_label,
            )
            if extra_detail.startswith("LAUNCH_TARGET_EXISTS:"):
                # The executable referenced by LaunchAgent still exists on disk; not orphaned!
                continue
            if container_bundle and _is_related_to_installed(p, container_bundle, apps):
                continue

            reason = f"未在已安装 {len(apps)} 个应用中匹配到对应主程序"
            if extra_detail:
                reason = f"{extra_detail}；{reason}"
            try:
                mtime = int(entry.stat(follow_symlinks=False).st_mtime)
            except OSError:
                mtime = 0

            orphans.append(
                {
                    "id": f"orphan:{len(orphans)}",
                    "engine": "pearcleaner-rules",
                    "category": cat_label,
                    "appName": inferred_app,
                    "bundleId": bundle_id,
                    "path": str(p),
                    "name": name,
                    "sizeBytes": 0,
                    "sizeHuman": "0 B",
                    "modifiedTs": mtime,
                    "reason": reason,
                }
            )

    # Batch compute sizes in parallel using native /usr/bin/du -sk
    sizes = fast_sizes_batch([o["path"] for o in orphans])
    for o in orphans:
        sb = sizes.get(o["path"], 0)
        o["sizeBytes"] = sb
        o["sizeHuman"] = human_bytes(sb)

    orphans.sort(key=lambda x: (x["sizeBytes"], x["category"]), reverse=True)
    total_bytes = sum(x["sizeBytes"] for x in orphans)
    by_cat: dict[str, dict[str, Any]] = {}
    for o in orphans:
        c = o["category"]
        slot = by_cat.setdefault(c, {"category": c, "count": 0, "sizeBytes": 0})
        slot["count"] += 1
        slot["sizeBytes"] += o["sizeBytes"]
    for slot in by_cat.values():
        slot["sizeHuman"] = human_bytes(slot["sizeBytes"])

    return {
        "ok": True,
        "installedAppsCount": len(apps),
        "scannedLocations": scanned_locations,
        "count": len(orphans),
        "totalBytes": total_bytes,
        "totalHuman": human_bytes(total_bytes),
        "categories": sorted(by_cat.values(), key=lambda c: c["sizeBytes"], reverse=True),
        "items": orphans,
        "pearcleanerApp": "/Applications/Pearcleaner.app" if Path("/Applications/Pearcleaner.app").exists() else None,
    }


def trash_orphans(paths: list[str]) -> dict[str, Any]:
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
