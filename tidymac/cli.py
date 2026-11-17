from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .apps_adapter import scan_installed_apps
from .dupes_adapter import scan_big_files, scan_duplicates, scan_similar_images
from .mole_adapter import run_mole_analyze, run_mole_dry_run
from .organize_adapter import analyze_unmatched_files, revert_organize_batch, simulate_organize
from .orphans_adapter import scan_orphaned_files
from .projects_adapter import scan_abandoned_projects, scan_kondo_artifacts
from .server import run_full_checkup, serve_workbench
from .utils import WORKBENCH_ROOT, check_permissions, engine_status, human_bytes


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tidymac",
        description="TidyMac — 集成 Mole / Pearcleaner规则 / kondo / czkawka_cli / organize 的轻量 macOS 清理与整理工作台",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("doctor", help="检查集成开源引擎与 macOS 权限状态")

    p_checkup = sub.add_parser("checkup", help="执行全项只读体检 (Dry-Run)，默认优先读取缓存")
    p_checkup.add_argument("--json", action="store_true", help="输出 JSON")
    p_checkup.add_argument("--refresh", action="store_true", help="强制重新扫描所有引擎")

    p_mole = sub.add_parser("mole", help="Mole 缓存/残留扫描与磁盘透视")
    p_mole.add_argument("action", nargs="?", default="scan", choices=["scan", "analyze"])
    p_mole.add_argument("--path", default=None)

    sub.add_parser("orphans", help="Pearcleaner 规则孤立配置文件扫描 (Dry-Run)")

    p_apps = sub.add_parser("apps", help="扫描已安装应用、占用空间、关联目录与用途说明")
    p_apps.add_argument("--system", action="store_true", help="包含系统内置应用")

    p_proj = sub.add_parser("projects", help="kondo 依赖与废弃 AI 试水项目扫描 (Dry-Run)")
    p_proj.add_argument("action", nargs="?", default="all", choices=["kondo", "abandoned", "all"])

    p_dupes = sub.add_parser("dupes", help="czkawka_cli 文档查重 / 相似图片 / 超大文件扫描")
    p_dupes.add_argument("mode", nargs="?", default="docs", choices=["docs", "all", "images", "big"])

    p_org = sub.add_parser("organize", help="organize 规则模拟预览与 OCR/AI 分拣")
    p_org.add_argument("action", nargs="?", default="sim", choices=["sim", "triage", "revert"])
    p_org.add_argument("--dir", default=None)
    p_org.add_argument("--batch-id", default="")

    p_serve = sub.add_parser("serve", help="启动本地 HTTP 控制台 (无后台常驻)")
    p_serve.add_argument("--port", type=int, default=18765)
    p_serve.add_argument("--open", action="store_true")
    p_serve.add_argument("--print-port", action="store_true")
    p_serve.add_argument("--watch-parent", type=int, default=None)

    sub.add_parser("ui", help="打开 TidyMac 原生桌面壳或浏览器控制台")

    args = parser.parse_args(argv)
    cmd = args.cmd or "ui"

    if cmd == "doctor":
        _print_json({"engines": engine_status(), "permissions": check_permissions()})
        return 0

    if cmd == "checkup":
        res = run_full_checkup(use_cache=not args.refresh)
        if args.json:
            _print_json(res)
            return 0
        tag = "来自缓存快照" if res.get("fromCache") else "实时重扫"
        print(f"=== TidyMac 只读体检报告 (Dry-Run · {tag}) ===")
        print(f"磁盘可用空间: {res['disk']['freeHuman']} / {res['disk']['totalHuman']}")
        print(f"预计总可释放空间 (不含超大文件与待人工确认的半成品工程整包): {res['reclaimableTotalHuman']}\n")
        s = res["summary"]
        print(f"1. [Mole 缓存与残留] {s['mole']['count']} 项 · {s['mole']['human']}")
        for n in s["mole"]["notices"]:
            print(f"   提示: {n}")
        print(f"2. [Pearcleaner 孤立配置] {s['orphans']['count']} 项 · {s['orphans']['human']}")
        print(f"3. [kondo 工程依赖产物] {s['kondo']['count']} 个工程 · {s['kondo']['human']} (支持只清依赖保留源码)")
        print(f"4. [废弃 AI 试水工程] {s['abandonedProjects']['count']} 个候选 · {s['abandonedProjects']['human']}")
        print(f"5. [czkawka 文档/压缩包查重] {s['duplicates']['groups']} 组 ({s['duplicates']['duplicateFiles']} 个副本) · {s['duplicates']['human']}")
        print(f"6. [czkawka 相似图片] {s['similarImages']['groups']} 组 · {s['similarImages']['human']}")
        print(f"7. [czkawka 超大文件 Top30] {s['bigFiles']['count']} 个 · 共 {s['bigFiles']['human']}")
        print(f"8. [organize 规则预览] 命中 {s['organize']['matchedCount']} 个文件，未命中待 OCR/AI 分拣 {s['organize']['unmatchedCount']} 个")
        apps_sum = s.get("apps", {})
        print(
            f"9. [已安装应用与关联空间] {apps_sum.get('count', 0)} 个第三方应用 · 共 {apps_sum.get('human', '0 B')}"
            f" (缓存/日志可清 {apps_sum.get('cleanableCacheHuman', '0 B')})"
        )
        return 0

    if cmd == "mole":
        if args.action == "analyze":
            _print_json(run_mole_analyze(args.path))
        else:
            _print_json(run_mole_dry_run())
        return 0

    if cmd == "orphans":
        _print_json(scan_orphaned_files())
        return 0

    if cmd == "apps":
        _print_json(scan_installed_apps(include_system=args.system))
        return 0

    if cmd == "projects":
        if args.action == "kondo":
            _print_json(scan_kondo_artifacts())
        elif args.action == "abandoned":
            _print_json(scan_abandoned_projects())
        else:
            _print_json({"kondo": scan_kondo_artifacts(), "abandoned": scan_abandoned_projects()})
        return 0

    if cmd == "dupes":
        if args.mode == "docs":
            _print_json(scan_duplicates(docs_only=True))
        elif args.mode == "all":
            _print_json(scan_duplicates(docs_only=False))
        elif args.mode == "images":
            _print_json(scan_similar_images())
        elif args.mode == "big":
            _print_json(scan_big_files())
        return 0

    if cmd == "organize":
        if args.action == "sim":
            _print_json(simulate_organize(args.dir))
        elif args.action == "triage":
            sim = simulate_organize(args.dir)
            unmatched = [u["path"] for u in sim.get("unmatchedFiles", [])]
            _print_json(analyze_unmatched_files(unmatched, base_dir=sim.get("targetDir")))
        elif args.action == "revert":
            _print_json(revert_organize_batch(args.batch_id))
        return 0

    if cmd == "serve":
        serve_workbench(
            port=args.port,
            open_browser=args.open,
            print_ready_port=args.print_port,
            watch_parent_pid=args.watch_parent,
        )
        return 0

    if cmd == "ui":
        app_bundle = WORKBENCH_ROOT.parent / "TidyMac.app"
        if app_bundle.exists():
            subprocess.Popen(["/usr/bin/open", str(app_bundle)])
            print(f"已启动桌面工作台: {app_bundle}")
        else:
            serve_workbench(port=18765, open_browser=True)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
