from __future__ import annotations

import os
import plistlib
import re
from pathlib import Path
from typing import Any

from .orphans_adapter import BUNDLE_ALIASES, pear_format
from .utils import HOME, fast_sizes_batch, human_bytes, move_to_trash

# Built-in knowledge base of common macOS apps + system utilities (bundle_id -> (zh_name, category, purpose))
APP_CATALOG: dict[str, tuple[str, str, str]] = {
    "com.tencent.xinWeChat": ("微信", "通讯协作", "微信 Mac 桌面客户端，聊天记录与缓存常存于沙盒容器，体积通常较大。"),
    "com.tencent.WeWorkMac": ("企业微信", "通讯协作", "面向企业的沟通与协作客户端，本地文件库可能占用较多空间。"),
    "com.tencent.qq": ("QQ", "通讯协作", "腾讯 QQ 桌面客户端，聊天图片与视频缓存易累积。"),
    "com.tencent.meeting": ("腾讯会议", "通讯协作", "线上音视频会议与屏幕共享客户端。"),
    "com.tencent.QQMusicMac": ("QQ音乐", "影音娱乐", "桌面音乐播放器，音频缓存与曲库缓存会随收听增长。"),
    "com.tencent.workbuddy.mac": ("WorkBuddy", "效率与AI", "本地桌面工作助手与 AI 工具客户端。"),
    "ru.keepcoder.Telegram": ("Telegram", "通讯协作", "加密即时通讯客户端，群组媒体与缓存体积较大。"),
    "net.whatsapp.WhatsApp": ("WhatsApp", "通讯协作", "WhatsApp 桌面即时通讯客户端。"),
    "com.todesktop.230313mzl4w4u92": ("Cursor", "开发工具", "AI 代码编辑器，支持代码库上下文理解与智能改写。"),
    "com.google.antigravity": ("Antigravity", "开发工具", "Google DeepMind Agentic AI 开发与协作工作台。"),
    "local.antigravity.zh-hans.launcher": ("Antigravity 简体中文", "开发工具", "Antigravity 中文启动器封装。"),
    "com.openai.codex": ("ChatGPT", "效率与AI", "OpenAI ChatGPT / Codex 桌面客户端与本地助手。"),
    "com.google.GeminiMacOS": ("Gemini", "效率与AI", "Google Gemini 桌面客户端。"),
    "com.moonshot.kimichat": ("Kimi", "效率与AI", "月之暗面 Kimi 桌面助手客户端。"),
    "com.deepseek.dsh": ("DeepSeek Harness", "效率与AI", "DeepSeek 桌面实验与任务工作区客户端。"),
    "com.anthropic.claudefordesktop": ("Claude", "效率与AI", "Anthropic Claude 桌面客户端。"),
    "com.ccswitch.desktop": ("CC Switch", "开发工具", "Claude / Codex / Gemini API 配置切换管理工具。"),
    "com.hypergryph.arknights": ("明日方舟", "影音娱乐", "明日方舟桌面/移动适配客户端，主程序本体较大。"),
    "com.hguandl.meoasstmac": ("MAA", "实用工具", "明日方舟自动辅助工具（Mac 版）。"),
    "com.vivo.pcsuite": ("vivo 办公套件", "办公效率", "vivo 手机与 Mac 跨屏互联、文件互传与备份套件。"),
    "im.refiningcolors.petwithyou": ("Pet With You", "影音娱乐", "桌面互动宠物与陪伴小组件应用。"),
    "com.mehdi.duobook.host": ("DuoBook Host", "实用工具", "DuoBook 本地宿主辅助服务应用。"),
    "com.cocoduck.macos": ("COCODUCK", "实用工具", "桌面工具应用。"),
    "com.waifux.app": ("WaifuX", "影音娱乐", "动态壁纸引擎与桌面视觉美化应用。"),
    "com.hyperartflow.Never-Sleep": ("Never Sleep", "实用工具", "防止 Mac 自动休眠与亮屏保持工具。"),
    "com.pilotmoon.scroll-reverser": ("Scroll Reverser", "实用工具", "独立反转鼠标滚轮与触控板滚动方向的工具。"),
    "com.microsoft.VSCode": ("Visual Studio Code", "开发工具", "通用代码编辑器与扩展生态工作台。"),
    "com.microsoft.Word": ("Microsoft Word", "办公效率", "撰写、排版与审阅文档。"),
    "com.microsoft.Excel": ("Microsoft Excel", "办公效率", "编辑电子表格、工作簿与数据图表。"),
    "com.microsoft.Powerpoint": ("Microsoft PowerPoint", "办公效率", "创建与播放幻灯片演示文稿。"),
    "com.microsoft.PowerPoint": ("Microsoft PowerPoint", "办公效率", "创建与播放幻灯片演示文稿。"),
    "com.microsoft.onenote.mac": ("Microsoft OneNote", "办公效率", "数字笔记本与多设备同步笔记。"),
    "com.microsoft.Outlook": ("Microsoft Outlook", "办公效率", "邮件、日历与联系人客户端。"),
    "com.microsoft.OneDrive-mac": ("OneDrive", "办公效率", "微软云盘同步客户端，本地缓存与占位同步目录关联密切。"),
    "com.kingsoft.wpsoffice.mac": ("WPS Office", "办公效率", "国产办公文档、表格与演示套件。"),
    "net.xmind.vana.app": ("Xmind", "办公效率", "思维导图与知识结构整理工具。"),
    "com.superace.updf.mac": ("UPDF", "办公效率", "PDF 阅读、标注与格式转换工具。"),
    "com.quark.desktop": ("夸克桌面端", "效率与工具", "夸克网盘与桌面搜索工具，视频/下载缓存易增长。"),
    "com.tradingview.tradingviewapp.desktop": ("TradingView", "投资研究", "金融市场图表、技术指标与自选盯盘客户端。"),
    "cn.com.10jqka.macstockPro": ("同花顺", "投资研究", "证券行情、财务数据与交易看盘客户端。"),
    "io.github.clash-verge-rev.clash-verge-rev": ("Clash Verge", "网络工具", "基于 Mihomo 内核的网络代理规则与分流管理客户端。"),
    "com.youqu.todesk.mac": ("ToDesk", "实用工具", "跨平台远程桌面控制客户端。"),
    "com.netease.uuremote": ("UU远程", "实用工具", "网易低延迟远程桌面控制客户端。"),
    "com.valvesoftware.steam": ("Steam", "影音娱乐", "PC/Mac 游戏平台，着色器与游戏下载缓存占用较大。"),
    "com.bytedance.douyin.desktop": ("抖音", "影音娱乐", "抖音桌面客户端，离线视频与缓存占用空间较大。"),
    "com.soda.music": ("汽水音乐", "影音娱乐", "桌面在线音乐播放器与歌词客户端。"),
    "com.alienator88.Pearcleaner": ("Pearcleaner", "系统清理", "开源 macOS 应用卸载与残留清理工具。"),
    "net.freemacsoft.AppCleaner": ("AppCleaner", "系统清理", "经典轻量化应用卸载与关联文件定位工具。"),
    "local.tidymac.workbench": ("TidyMac", "系统清理", "本轻量 Mac 清理与文档整理工作台自身。"),
    "local.filemind.desktop": ("FileMind V4", "文件工具", "旧版全盘索引测试应用。"),
    "com.apple.dt.Xcode": ("Xcode", "开发工具", "Apple 官方 IDE，DerivedData 与模拟器通常占用数 GB 空间。"),
    "com.apple.Safari": ("Safari 浏览器", "系统核心", "macOS 内置网页浏览器。"),
    "com.apple.Terminal": ("终端", "开发工具", "macOS 命令行 Shell 与开发者工具环境。"),
    "com.apple.ActivityMonitor": ("活动监视器", "系统管理", "监控 CPU、内存、能耗、磁盘与网络活动。"),
    "com.apple.DiskUtility": ("磁盘工具", "系统管理", "管理磁盘分区、APFS 宗卷、急救与映像挂载。"),
    "com.apple.Notes": ("备忘录", "办公效率", "系统自带笔记、清单与附件记录工具。"),
    "com.apple.Preview": ("预览", "办公效率", "系统自带 PDF 阅读、标注与图片编辑工具。"),
    "com.apple.Photos": ("照片", "影音娱乐", "系统图库与相册管理工具。"),
    "com.apple.Music": ("音乐", "影音娱乐", "Apple 音乐播放器与资料库管理。"),
    "com.apple.garageband10": ("库乐队", "影音娱乐", "多轨录音、虚拟乐器与音乐创作工具。"),
}

LS_CATEGORY_MAP = {
    "public.app-category.developer-tools": "开发工具",
    "public.app-category.productivity": "办公效率",
    "public.app-category.business": "办公效率",
    "public.app-category.finance": "投资研究",
    "public.app-category.social-networking": "通讯协作",
    "public.app-category.music": "影音娱乐",
    "public.app-category.video": "影音娱乐",
    "public.app-category.entertainment": "影音娱乐",
    "public.app-category.games": "影音娱乐",
    "public.app-category.graphics-design": "照片与设计",
    "public.app-category.photography": "照片与设计",
    "public.app-category.utilities": "实用工具",
    "public.app-category.education": "教育学习",
    "public.app-category.reference": "教育学习",
    "public.app-category.news": "资讯阅读",
    "public.app-category.books": "资讯阅读",
}

_EXTRA_DATA_LINKS: dict[str, tuple[str, ...]] = {
    "com.tencent.weworkmac": ("wecom-local-vault",),
    "com.kingsoft.wpsoffice.mac": ("kingsoft",),
    "com.vivo.pcsuite": ("vivoconnservice", "pcsuite"),
    "com.hguandl.meoasstmac": ("meoasst",),
    "com.openai.codex": ("com.openai.chat", "com.openai.sky", "chatgpt"),
}

_PATH_ROLE_PURPOSE: dict[str, tuple[str, str, bool]] = {
    "installation": ("安装本体", "应用程序主程序（.app）本体，卸载应用时移除。", False),
    "cache": ("缓存", "临时缓存、图片缓存或更新包，删后软件会自动重建，可安全清理。", True),
    "logs": ("日志与状态", "运行日志、崩溃记录或窗口恢复状态，通常可安全清理。", True),
    "container": ("应用容器", "macOS 沙盒隔离目录，包含该应用内部设置、缓存与本地文件。", False),
    "shared": ("共享容器", "同厂商多个应用或组件共享的登录态与数据容器。", False),
    "data": ("应用数据", "Application Support 下的本地数据、插件或用户资料库，正常使用请勿误删。", False),
    "configuration": ("偏好配置", "应用偏好设置（.plist），保存界面布局与选项开关。", False),
}


def _scan_library_buckets() -> dict[str, list[tuple[str, str, Path]]]:
    lib = HOME / "Library"
    buckets: list[tuple[str, str, Path]] = [
        ("data", "Application Support", lib / "Application Support"),
        ("cache", "Caches", lib / "Caches"),
        ("container", "Containers", lib / "Containers"),
        ("shared", "Group Containers", lib / "Group Containers"),
        ("configuration", "Preferences", lib / "Preferences"),
        ("logs", "Logs", lib / "Logs"),
        ("logs", "Saved Application State", lib / "Saved Application State"),
        ("cache", "HTTPStorages", lib / "HTTPStorages"),
        ("cache", "WebKit", lib / "WebKit"),
    ]
    index: dict[str, list[tuple[str, str, Path]]] = {}
    for role, area_name, root in buckets:
        if not root.exists():
            continue
        items: list[tuple[str, str, Path]] = []
        try:
            with os.scandir(root) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue
                    items.append((entry.name, area_name, Path(entry.path)))
        except OSError:
            continue
        index[f"{role}:{area_name}"] = items
    return index


def _read_app_metadata(app_path: Path) -> dict[str, Any]:
    info_plist = app_path / "Contents" / "Info.plist"
    if not info_plist.exists():
        # Handle iOS wrapper apps on Apple Silicon
        wrapper = app_path / "Wrapper"
        if wrapper.exists():
            cands = list(wrapper.glob("*.app/Info.plist"))
            if cands:
                info_plist = cands[0]

    data: dict[str, Any] = {}
    if info_plist.exists():
        try:
            with info_plist.open("rb") as fh:
                loaded = plistlib.load(fh)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            pass

    bundle_id = str(data.get("CFBundleIdentifier") or "")
    raw_name = str(data.get("CFBundleDisplayName") or data.get("CFBundleName") or app_path.stem)
    version = str(data.get("CFBundleShortVersionString") or data.get("CFBundleVersion") or "")
    cat_type = str(data.get("LSApplicationCategoryType") or "")
    is_system = str(app_path).startswith("/System/") or bundle_id.startswith("com.apple.")

    cat_entry = APP_CATALOG.get(bundle_id)
    if cat_entry:
        disp_name, category, purpose = cat_entry
        basis = "内置应用百科与 Bundle ID 匹配"
    else:
        disp_name = raw_name
        category = LS_CATEGORY_MAP.get(cat_type, "系统组件" if is_system else "实用工具")
        if is_system:
            purpose = f"macOS 内置系统应用（{bundle_id or app_path.name}），建议保留以维持系统功能。"
            basis = "系统应用签名与位置推断"
        else:
            purpose = f"第三方桌面应用（类别：{category}，标识：{bundle_id or app_path.stem}）。"
            basis = "应用 Info.plist 声明推断"

    tokens = {pear_format(app_path.stem), pear_format(raw_name), pear_format(disp_name)}
    if bundle_id:
        tokens.add(pear_format(bundle_id))
        if "." in bundle_id:
            tail = pear_format(bundle_id.split(".")[-1])
            if len(tail) >= 5 and tail not in {"desktop", "macos", "helper", "client", "launcher"}:
                tokens.add(tail)
    for k, alias_list in BUNDLE_ALIASES.items():
        if k == bundle_id.lower() or (bundle_id and k in bundle_id.lower()):
            tokens.update(pear_format(a) for a in alias_list)
    for k, extra_list in _EXTRA_DATA_LINKS.items():
        if k == bundle_id.lower():
            tokens.update(pear_format(a) for a in extra_list)

    tokens = {t for t in tokens if len(t) >= 4}
    return {
        "name": disp_name,
        "originalName": raw_name,
        "bundleId": bundle_id or app_path.stem,
        "version": version,
        "category": category,
        "purpose": purpose,
        "basis": basis,
        "isSystem": is_system,
        "installPath": str(app_path),
        "tokens": tokens,
    }


def _match_app_paths(app_meta: dict[str, Any], lib_index: dict[str, list[tuple[str, str, Path]]]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    def add_path(role: str, area: str, p: Path, extra_purpose: str = "") -> None:
        sp = str(p)
        if sp in seen_paths:
            return
        seen_paths.add(sp)
        role_label, default_purpose, safe_clean = _PATH_ROLE_PURPOSE.get(role, ("关联目录", "应用关联文件。", False))
        matched.append(
            {
                "path": sp,
                "name": p.name,
                "role": role,
                "roleLabel": role_label,
                "area": area,
                "purpose": extra_purpose or default_purpose,
                "safeToClean": safe_clean,
                "sizeBytes": 0,
                "sizeHuman": "0 B",
            }
        )

    add_path("installation", "应用程序", Path(app_meta["installPath"]))

    bundle_fmt = pear_format(app_meta["bundleId"])
    tokens = app_meta["tokens"]

    for bucket_key, entries in lib_index.items():
        role, area = bucket_key.split(":", 1)
        for name, _, full_path in entries:
            fmt = pear_format(name)
            hit = False
            if bundle_fmt and len(bundle_fmt) >= 5 and bundle_fmt in fmt:
                hit = True
            elif any(len(t) >= 5 and t in fmt for t in tokens):
                hit = True
            elif fmt in tokens:
                hit = True

            if not hit:
                continue

            extra = ""
            if name == "wecom-local-vault":
                extra = "企业微信本地聊天记录、缓存与文件库。"
            elif role == "container" and (full_path / "Data" / "Library" / "Caches").exists():
                extra = "沙盒容器，内含应用设置与本地文档/缓存。"
            add_path(role, area, full_path, extra_purpose=extra)

            # Expose inner Caches inside Sandbox Container so users can clean ONLY app cache cleanly!
            if role == "container":
                inner_cache = full_path / "Data" / "Library" / "Caches"
                if inner_cache.exists():
                    add_path("cache", "容器内部缓存", inner_cache, extra_purpose="沙盒容器内部缓存，清理后可安全释放空间。")

    return matched


def scan_installed_apps(include_system: bool = False) -> dict[str, Any]:
    """Discover installed macOS apps, map associated Library paths with purpose notes, and measure disk usage."""
    roots = [
        (Path("/Applications"), False),
        (Path("/Applications/Utilities"), False),
        (HOME / "Applications", False),
    ]
    if include_system:
        roots.extend([
            (Path("/System/Applications"), True),
            (Path("/System/Applications/Utilities"), True),
        ])

    app_bundles: list[Path] = []
    seen_app_paths: set[str] = set()
    for root, _ in roots:
        if not root.exists():
            continue
        try:
            with os.scandir(root) as it:
                for entry in it:
                    if entry.name.endswith(".app") and entry.is_dir(follow_symlinks=True):
                        if entry.path not in seen_app_paths:
                            seen_app_paths.add(entry.path)
                            app_bundles.append(Path(entry.path))
        except OSError:
            continue

    lib_index = _scan_library_buckets()
    apps_raw: list[dict[str, Any]] = []
    all_paths_to_measure: list[str] = []

    for app_p in app_bundles:
        meta = _read_app_metadata(app_p)
        if meta["isSystem"] and not include_system:
            continue
        linked = _match_app_paths(meta, lib_index)
        for item in linked:
            all_paths_to_measure.append(item["path"])
        apps_raw.append({**meta, "paths": linked})

    sizes = fast_sizes_batch(all_paths_to_measure, workers=4)

    apps_out: list[dict[str, Any]] = []
    for idx, app in enumerate(apps_raw):
        paths = app["paths"]
        app_bytes = 0
        cache_bytes = 0
        data_bytes = 0
        container_paths = {p["path"] for p in paths if p["role"] == "container"}

        for p_item in paths:
            sz = sizes.get(p_item["path"], 0)
            p_item["sizeBytes"] = sz
            p_item["sizeHuman"] = human_bytes(sz)
            if p_item["role"] == "installation":
                app_bytes += sz
            elif p_item["role"] in ("cache", "logs"):
                cache_bytes += sz
                # Avoid double-counting container inner cache against container total
                if p_item["area"] != "容器内部缓存":
                    data_bytes += sz
            else:
                data_bytes += sz

        # Total footprint avoids double counting container inner caches already inside container dir
        total_bytes = sum(
            p["sizeBytes"]
            for p in paths
            if p["area"] != "容器内部缓存" or len(Path(p["path"]).parents) < 3 or str(Path(p["path"]).parents[2]) not in container_paths
        )

        paths.sort(key=lambda x: (x["role"] != "installation", -x["sizeBytes"]))
        apps_out.append(
            {
                "id": f"app:{idx}",
                "name": app["name"],
                "originalName": app["originalName"],
                "bundleId": app["bundleId"],
                "version": app["version"],
                "category": app["category"],
                "purpose": app["purpose"],
                "basis": app["basis"],
                "isSystem": app["isSystem"],
                "installPath": app["installPath"],
                "appBytes": app_bytes,
                "appHuman": human_bytes(app_bytes),
                "dataBytes": data_bytes,
                "dataHuman": human_bytes(data_bytes),
                "cacheBytes": cache_bytes,
                "cacheHuman": human_bytes(cache_bytes),
                "totalBytes": total_bytes,
                "totalHuman": human_bytes(total_bytes),
                "pathCount": len(paths),
                "paths": paths,
            }
        )

    apps_out.sort(key=lambda a: a["totalBytes"], reverse=True)
    total_all = sum(a["totalBytes"] for a in apps_out)
    total_caches = sum(a["cacheBytes"] for a in apps_out)

    return {
        "ok": True,
        "includeSystem": include_system,
        "count": len(apps_out),
        "totalBytes": total_all,
        "totalHuman": human_bytes(total_all),
        "cleanableCacheBytes": total_caches,
        "cleanableCacheHuman": human_bytes(total_caches),
        "apps": apps_out,
    }


def trash_app_paths(paths: list[str]) -> dict[str, Any]:
    """Move selected app caches or full app bundle + leftovers safely to macOS Trash."""
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
