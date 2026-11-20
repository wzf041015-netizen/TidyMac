from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tidymac import cache
from tidymac.mole_adapter import run_mole_analyze
from tidymac.organize_adapter import apply_organize_moves, revert_organize_batch, simulate_organize
from tidymac.utils import check_permissions, engine_status, move_to_trash


def test_engines_and_permissions() -> None:
    engines = engine_status()
    for name in ("mole", "pearcleaner", "kondo", "czkawka", "organize", "ocr", "trash"):
        assert engines[name]["installed"], f"Engine missing: {name}"
    perms = check_permissions()
    assert perms["folders"]["Downloads"]["readable"], "Downloads must be readable"
    print("[PASS] engines & permissions")


def test_server_and_cache_endpoints() -> None:
    proc = subprocess.Popen(
        [str(ROOT / "bin" / "tidymac"), "serve", "--port", "0", "--print-port"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        port = None
        deadline = time.time() + 10
        while time.time() < deadline:
            line = proc.stdout.readline()
            if "TIDYMAC_PORT=" in line:
                port = int(line.split("TIDYMAC_PORT=")[1].strip())
                break
        assert port is not None, "Server did not print port"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as resp:
            status = json.loads(resp.read().decode("utf-8"))
            assert status["ok"] is True
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/cache", timeout=5) as resp:
            c = json.loads(resp.read().decode("utf-8"))
            assert c["ok"] is True
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
            assert "TidyMac" in html
            assert "floating-bar" in html
        req_apps = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/apps/scan",
            data=json.dumps({"includeSystem": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_apps, timeout=30) as resp:
            apps_res = json.loads(resp.read().decode("utf-8"))
            assert apps_res["ok"] is True and apps_res["count"] >= 1
            first_app = apps_res["apps"][0]
            assert first_app.get("purpose") and isinstance(first_app.get("paths"), list)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/shutdown",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert json.loads(resp.read().decode("utf-8"))["ok"] is True
        proc.wait(timeout=5)
        print(f"[PASS] HTTP server + /api/cache lifecycle on port {port}")
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


def test_cache_incremental_prune() -> None:
    orig_dir, orig_meta = cache.CACHE_DIR, cache.META_FILE
    with tempfile.TemporaryDirectory(prefix="tidymac-cache-test-") as tmp:
        cache.CACHE_DIR = Path(tmp)
        cache.META_FILE = Path(tmp) / "meta.json"
        try:
            cache.save_module(
                "mole",
                {
                    "ok": True,
                    "count": 2,
                    "totalBytes": 300,
                    "totalHuman": "300 B",
                    "items": [
                        {"path": "/a/1", "category": "Cat", "sizeBytes": 100, "whitelisted": False},
                        {"path": "/a/2", "category": "Cat", "sizeBytes": 200, "whitelisted": False},
                    ],
                },
            )
            upd = cache.prune_mole_cache(removed_paths=["/a/1"])
            assert upd is not None and upd["count"] == 1 and upd["totalBytes"] == 200

            cache.save_module(
                "dupes_docs",
                {
                    "ok": True,
                    "groupCount": 1,
                    "duplicateFilesCount": 1,
                    "reclaimableBytes": 500,
                    "reclaimableHuman": "500 B",
                    "groups": [
                        {
                            "fileSizeBytes": 500,
                            "count": 2,
                            "reclaimableBytes": 500,
                            "files": [{"path": "/d/1"}, {"path": "/d/2"}],
                        }
                    ],
                },
            )
            dupd = cache.prune_dupes_cache(["/d/2"])["dupes_docs"]
            assert dupd["groupCount"] == 0 and dupd["reclaimableBytes"] == 0

            cache.save_module(
                "apps",
                {
                    "ok": True,
                    "count": 1,
                    "totalBytes": 1200,
                    "totalHuman": "1.2 KB",
                    "cleanableCacheBytes": 200,
                    "cleanableCacheHuman": "200 B",
                    "apps": [
                        {
                            "name": "DemoApp",
                            "installPath": "/Applications/DemoApp.app",
                            "appBytes": 1000,
                            "dataBytes": 200,
                            "cacheBytes": 200,
                            "totalBytes": 1200,
                            "pathCount": 2,
                            "paths": [
                                {"path": "/Applications/DemoApp.app", "role": "installation", "area": "应用本体", "sizeBytes": 1000},
                                {"path": "/Users/demo/Library/Caches/com.demo.app", "role": "cache", "area": "应用缓存", "sizeBytes": 200},
                            ],
                        }
                    ],
                },
            )
            apps_pruned = cache.prune_apps_cache(["/Users/demo/Library/Caches/com.demo.app"])
            assert apps_pruned is not None and apps_pruned["cleanableCacheBytes"] == 0
            assert apps_pruned["apps"][0]["totalBytes"] == 1000
            apps_uninstalled = cache.prune_apps_cache(["/Applications/DemoApp.app"])
            assert apps_uninstalled is not None and apps_uninstalled["count"] == 0
            print("[PASS] persistent cache + incremental prune")
        finally:
            cache.CACHE_DIR = orig_dir
            cache.META_FILE = orig_meta


def test_organize_and_revert_on_tempdir() -> None:
    with tempfile.TemporaryDirectory(prefix="tidymac-smoke-") as tmp:
        d = Path(tmp)
        src_doc = d / "Sample_Architecture.md"
        src_dmg = d / "SampleInstaller.dmg"
        src_doc.write_text("# Sample Architecture\n", encoding="utf-8")
        src_dmg.write_bytes(b"dmg-bytes")

        sim = simulate_organize(str(d))
        assert sim["ok"] is True
        assert sim["matchedCount"] == 2, f"Expected 2 matches, got {sim['matchedCount']}"

        applied = apply_organize_moves(
            [{"srcPath": m["srcPath"], "destPath": m["destPath"], "ruleName": m["ruleName"]} for m in sim["moves"]]
        )
        assert applied["ok"] is True and applied["movedCount"] == 2
        assert not src_doc.exists() and not src_dmg.exists()

        reverted = revert_organize_batch(applied["batchId"])
        assert reverted["ok"] is True and reverted["revertedCount"] == 2
        assert src_doc.exists() and src_dmg.exists()
        print("[PASS] organize sim + apply + undo revert")


def test_trash_safety_guard() -> None:
    res = move_to_trash(["/Users", str(Path.home()), str(ROOT)])
    assert all(not r["ok"] for r in res), "Protected roots must never be trashed"
    tmp_file = Path(tempfile.gettempdir()) / f"tidymac_trash_smoke_{int(time.time())}.txt"
    tmp_file.write_text("smoke", encoding="utf-8")
    res2 = move_to_trash([str(tmp_file)])
    assert res2[0]["ok"] is True and not tmp_file.exists(), "Temp file should move to Trash"
    print("[PASS] trash safety guard + /usr/bin/trash")


def test_mole_analyze() -> None:
    res = run_mole_analyze(str(Path.home() / "Desktop"))
    assert res["ok"] is True, f"mo analyze failed: {res}"
    print("[PASS] mo analyze --json")


if __name__ == "__main__":
    test_engines_and_permissions()
    test_cache_incremental_prune()
    test_server_and_cache_endpoints()
    test_organize_and_revert_on_tempdir()
    test_trash_safety_guard()
    test_mole_analyze()
    print("ALL SMOKE TESTS PASSED")
