from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import yaml

from .utils import BIN_DIR, CONFIG_DIR, HISTORY_DIR, HOME, USER_STATE_DIR, human_bytes, resolve_binary, tool_env

DEFAULT_ORGANIZE_CONFIG = CONFIG_DIR / "organize_rules.yaml"
USER_ORGANIZE_CONFIG = USER_STATE_DIR / "organize_rules.yaml"


def active_organize_config() -> Path:
    """Prefer user-local rules in ~/.config/tidymac if present, else repo default."""
    if USER_ORGANIZE_CONFIG.exists():
        return USER_ORGANIZE_CONFIG
    return DEFAULT_ORGANIZE_CONFIG

USER_OCR_TOPICS = USER_STATE_DIR / "ocr_topics.json"

# General-purpose built-in taxonomy for on-demand OCR / content classification;
# users can prepend personal domain topics via ~/.config/tidymac/ocr_topics.json.
DEFAULT_DOMAIN_TOPICS: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    (
        "财务与票据",
        "归档_财务与商务",
        ("电子发票", "增值税", "发票代码", "发票号码", "报销单", "付款协议", "采购订单"),
        ("发票", "收据", "税率", "合计金额", "开票日期", "价税合计", "财务报表", "invoice", "receipt"),
    ),
    (
        "合同与协议",
        "归档_财务与商务",
        ("合同编号", "保密协议", "劳动合同", "服务协议", "合作协议", "授权委托书"),
        ("甲方", "乙方", "违约责任", "签字盖章", "有效期", "条款", "agreement", "contract"),
    ),
    (
        "研究与学术",
        "归档_研究报告",
        ("研究报告", "行业分析", "证券研究", "估值模型", "学术论文", "文献综述", "实验结果"),
        ("摘要", "引言", "参考文献", "实证分析", "投资策略", "研报", "abstract", "conclusion"),
    ),
    (
        "学习与讲义",
        "归档_学习资料",
        ("课程讲义", "复习笔记", "历年真题", "随堂测验", "教学大纲", "期末复习"),
        ("第1章", "第一章", "习题", "知识点", "考点", "课堂笔记", "讲义", "syllabus"),
    ),
    (
        "开发与技术文档",
        "归档_技术与数据",
        ("开发手册", "部署文档", "架构设计", "技术设计", "api文档", "architecture", "proposal"),
        ("接口文档", "操作手册", "项目说明", "技术方案", "release notes", "changelog", "readme"),
    ),
    (
        "简历与个人证件",
        "归档_个人与证件",
        ("个人简历", "求职意向", "工作经历", "教育背景", "身份证", "护照", "成绩单"),
        ("resume", "curriculum vitae", "学历", "证书", "资格证", "证明"),
    ),
]


def _load_domain_topics() -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    topics: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
    if USER_OCR_TOPICS.exists():
        try:
            custom = json.loads(USER_OCR_TOPICS.read_text(encoding="utf-8"))
            if isinstance(custom, list):
                for row in custom:
                    if isinstance(row, dict) and row.get("category") and row.get("relDir"):
                        topics.append(
                            (
                                str(row["category"]),
                                str(row["relDir"]),
                                tuple(str(x) for x in row.get("strong", [])),
                                tuple(str(x) for x in row.get("general", [])),
                            )
                        )
        except Exception:
            pass
    topics.extend(DEFAULT_DOMAIN_TOPICS)
    return topics


def _extract_office_text(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    if ext not in {".docx", ".pptx", ".xlsx"}:
        return "", ""
    title = ""
    parts: list[str] = []
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "docProps/core.xml" in zf.namelist():
                core = ET.fromstring(zf.read("docProps/core.xml"))
                for elem in core.iter():
                    if elem.tag.endswith(("title", "subject")) and elem.text:
                        title = elem.text.strip()
                        break
            targets: list[str] = []
            if ext == ".docx" and "word/document.xml" in zf.namelist():
                targets = ["word/document.xml"]
            elif ext == ".pptx":
                targets = sorted(n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml"))[:5]
            elif ext == ".xlsx":
                targets = [n for n in ("xl/sharedStrings.xml", "xl/worksheets/sheet1.xml") if n in zf.namelist()]
            for t in targets:
                root = ET.fromstring(zf.read(t))
                for elem in root.iter():
                    if elem.text and elem.text.strip():
                        parts.append(elem.text.strip())
                    if len(" ".join(parts)) > 2500:
                        break
    except Exception:
        pass
    return title, " ".join(parts)[:3000]


def _extract_plain_text(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            data = fh.read(16384)
        if b"\x00" in data:
            return ""
        return data.decode("utf-8", errors="ignore")[:3000]
    except OSError:
        return ""


def _sanitize_filename(name: str, max_len: int = 56) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" ._")
    return cleaned


def _suggest_clean_name(path: Path, title: str, text: str, ocr_used: bool, category: str) -> str:
    stem = path.stem
    ext = path.suffix
    # Detect messy/generic stems like "2024___", "2024___ 2", "未命名", "IMG_1234", "截屏..."
    is_messy = bool(
        re.search(r"_{2,}|^未命名|^新建|^document\d*$|^img_\d+$|^screenshot|^截屏", stem, re.IGNORECASE)
        or re.fullmatch(r"[\d_\s\-]+", stem)
    )
    if not is_messy:
        return path.name

    candidate = ""
    if title and len(title) >= 4:
        candidate = title
    elif text:
        # Extract first meaningful heading/phrase
        first_chunk = re.split(r"[。！？\n|]", text.strip())[0]
        candidate = first_chunk[:48].strip()

    if candidate:
        safe = _sanitize_filename(candidate)
        if safe and len(safe) >= 4:
            if ocr_used and "OCR" not in safe:
                return f"{safe}_OCR{ext}"
            return f"{safe}{ext}"
    return path.name


def _score_content(name: str, title: str, text: str, ext: str, ocr_used: bool) -> tuple[str, str, int, str]:
    sample = f"{name} {title} {text}".lower()
    best_cat = ""
    best_rel = ""
    best_score = 0
    best_hits: list[str] = []
    runner_up = 0

    topics = _load_domain_topics()
    for cat, rel_dir, strong, general in topics:
        score = 0
        hits: list[str] = []
        for w in strong:
            if w.lower() in sample:
                score += 2
                hits.append(w)
        for w in general:
            if w.lower() in sample:
                score += 1
                if w not in hits:
                    hits.append(w)
        if score > best_score:
            runner_up = best_score
            best_score = score
            best_cat = cat
            best_rel = rel_dir
            best_hits = hits
        elif score > runner_up:
            runner_up = score

    if best_score >= 2 and (best_score > runner_up or len(best_hits) >= 2):
        ocr_tag = "（Vision OCR）" if ocr_used else ""
        conf = min(95, 65 + best_score * 3)
        basis = f"内容分析{ocr_tag}命中「{'、'.join(best_hits[:4])}」"
        return best_cat, best_rel, conf, basis

    # Extension-based gentle fallback for remaining unmatched files
    e = ext.lower().lstrip(".")
    if e in {"png", "jpg", "jpeg", "heic", "webp", "gif", "tiff"}:
        return "图片与截图", "归档_图片与媒体", 60, "图片格式归类" + ("（已做 OCR 识别）" if ocr_used else "")
    if e in {"mp4", "mov", "mkv", "avi", "mp3", "m4a", "wav"}:
        return "影音文件", "归档_图片与媒体", 70, "音视频格式归类"
    if e in {"csv", "xlsx", "xls", "json", "tsv"}:
        return "数据与表格", "归档_技术与数据", 65, "表格/数据格式归类"
    if e in {"pdf", "doc", "docx", "ppt", "pptx", "md", "txt", "xmind"}:
        return "其他文档", "归档_其他文档", 50, "暂未命中特定主题，建议核对预览后归档"
    return "待整理文件", "归档_其他文件", 40, "通用扩展名兜底"


def _maybe_llm_refine(items: list[dict[str, Any]], llm_config: dict[str, Any] | None) -> None:
    """Optional on-demand OpenAI-compatible / Ollama classification enhancement when explicitly configured."""
    if not llm_config or not llm_config.get("baseUrl") or not llm_config.get("model"):
        return
    base_url = llm_config["baseUrl"].rstrip("/")
    model = llm_config["model"]
    api_key = llm_config.get("apiKey", "")
    topics = _load_domain_topics()
    categories = [c[0] for c in topics] + ["图片与截图", "影音文件", "数据与表格", "其他文档"]

    for item in items[:20]:
        snippet = (item.get("excerptPreview") or "")[:600]
        if not snippet:
            continue
        prompt = (
            f"请为以下文件选择最合适的分类并建议简洁中文文件名（保留原扩展名）。\n"
            f"可选分类：{', '.join(categories)}\n"
            f"当前文件名：{item['currentName']}\n"
            f"内容摘要：{snippet}\n"
            f'严格返回 JSON：{{"category":"...", "filename":"..."}}'
        )
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(f"{base_url}/chat/completions", data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
                cat = parsed.get("category")
                fname = parsed.get("filename")
                if cat:
                    for c_name, rel_dir, _, _ in topics:
                        if c_name == cat:
                            item["suggestedCategory"] = cat
                            item["suggestedRelDir"] = rel_dir
                            break
                if fname and len(fname) >= 4:
                    item["suggestedName"] = _sanitize_filename(Path(fname).stem) + Path(item["currentName"]).suffix
                    item["basis"] += f" + AI 模型 ({model})"
        except Exception:
            continue


def simulate_organize(target_dir: str | None = None) -> dict[str, Any]:
    org_bin = resolve_binary("organize")
    if not org_bin:
        return {"ok": False, "error": "未检测到 organize 可执行文件"}

    config_file = active_organize_config()
    if not config_file.exists():
        return {"ok": False, "error": f"缺少规则文件: {config_file}"}

    scope_dir = Path(target_dir).expanduser() if target_dir else None
    if scope_dir and not scope_dir.exists():
        return {"ok": False, "error": f"目录不存在: {scope_dir}"}

    with tempfile.TemporaryDirectory(prefix="tidymac-org-") as tmp:
        cfg_to_use = config_file
        if scope_dir is not None:
            cfg = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            for rule in cfg.get("rules", []):
                rule["locations"] = [
                    {
                        "path": str(scope_dir),
                        "max_depth": 0,
                        "exclude_dirs": ["归档_*", "*.app"],
                    }
                ]
            scoped_yaml = Path(tmp) / "scoped_rules.yaml"
            scoped_yaml.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
            cfg_to_use = scoped_yaml

        proc = subprocess.run(
            [org_bin, "sim", "--format", "JSONL", str(cfg_to_use)],
            capture_output=True,
            text=True,
            timeout=60,
            env=tool_env(),
        )
        if proc.returncode != 0 and not proc.stdout:
            return {"ok": False, "error": (proc.stderr or "organize sim 执行失败").strip()}

    moves: list[dict[str, Any]] = []
    matched_paths: set[str] = set()
    errors: list[str] = []

    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "MSG" and ev.get("sender") == "move":
            src = ev.get("path", "")
            msg = ev.get("msg", "")
            dest = msg[len("Move to ") :] if msg.startswith("Move to ") else msg
            if not src or not dest:
                continue
            # Track first matching rule per source file so preview reflects real execution order
            if src in matched_paths:
                continue
            matched_paths.add(src)
            src_p = Path(src)
            try:
                sz = src_p.stat().st_size if src_p.is_file() else 0
            except OSError:
                sz = 0
            moves.append(
                {
                    "id": f"org:{len(moves)}",
                    "srcPath": src,
                    "srcName": src_p.name,
                    "destPath": dest,
                    "destDir": str(Path(dest).parent),
                    "destName": Path(dest).name,
                    "ruleName": ev.get("rule_name", ""),
                    "sizeBytes": sz,
                    "sizeHuman": human_bytes(sz),
                }
            )
        elif ev.get("level") == "error":
            errors.append(ev.get("msg", ""))

    # Find unmatched top-level files in scope_dir (or ~/Downloads by default) for on-demand AI/OCR triage
    inspect_dir = scope_dir or (HOME / "Downloads")
    unmatched: list[dict[str, Any]] = []
    if inspect_dir.exists():
        try:
            for entry in sorted(os.scandir(inspect_dir), key=lambda e: e.name):
                if entry.name.startswith(".") or entry.name.endswith(".app"):
                    continue
                if entry.is_file(follow_symlinks=False) and entry.path not in matched_paths:
                    try:
                        sz = entry.stat().st_size
                    except OSError:
                        sz = 0
                    unmatched.append(
                        {
                            "path": entry.path,
                            "name": entry.name,
                            "ext": Path(entry.name).suffix.lower().lstrip("."),
                            "sizeBytes": sz,
                            "sizeHuman": human_bytes(sz),
                        }
                    )
        except OSError:
            pass

    return {
        "ok": True,
        "configPath": str(config_file),
        "targetDir": str(inspect_dir),
        "matchedCount": len(moves),
        "moves": moves,
        "unmatchedCount": len(unmatched),
        "unmatchedFiles": unmatched,
        "errors": errors,
    }


def analyze_unmatched_files(
    paths: list[str],
    base_dir: str | None = None,
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """On-demand macOS Vision OCR + content analysis for unmatched files (no persistent index)."""
    if not paths:
        return {"ok": True, "suggestions": []}

    ocr_bin = BIN_DIR / "mac-ocr-extract"
    ocr_targets = [
        p for p in paths
        if Path(p).suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".heic", ".webp", ".tif", ".tiff"}
    ]
    ocr_map: dict[str, dict[str, Any]] = {}

    if ocr_bin.exists() and ocr_targets:
        # Process in chunks of 16 so command line stays compact and responsive
        for i in range(0, len(ocr_targets), 16):
            chunk = ocr_targets[i : i + 16]
            try:
                proc = subprocess.run(
                    [str(ocr_bin)] + chunk,
                    capture_output=True,
                    text=True,
                    timeout=90,
                    env=tool_env(),
                )
                if proc.stdout:
                    arr = json.loads(proc.stdout)
                    for item in arr:
                        ocr_map[item["path"]] = item
            except Exception:
                continue

    suggestions: list[dict[str, Any]] = []
    for idx, raw_path in enumerate(paths):
        p = Path(raw_path)
        if not p.exists() or not p.is_file():
            continue
        ext = p.suffix.lower()
        title = ""
        text = ""
        ocr_used = False

        if raw_path in ocr_map:
            info = ocr_map[raw_path]
            title = info.get("title", "") or ""
            text = info.get("text", "") or ""
            ocr_used = bool(info.get("ocrUsed", False))
        elif ext in {".docx", ".pptx", ".xlsx"}:
            title, text = _extract_office_text(p)
        elif ext in {".md", ".txt", ".csv", ".tsv", ".json", ".log"}:
            text = _extract_plain_text(p)

        cat, rel_dir, conf, basis = _score_content(p.name, title, text, ext, ocr_used)
        suggested_name = _suggest_clean_name(p, title, text, ocr_used, cat)
        parent_root = Path(base_dir).expanduser() if base_dir else p.parent
        dest_dir = parent_root / rel_dir

        try:
            sz = p.stat().st_size
        except OSError:
            sz = 0

        suggestions.append(
            {
                "id": f"ai:{idx}",
                "srcPath": str(p),
                "currentName": p.name,
                "sizeBytes": sz,
                "sizeHuman": human_bytes(sz),
                "suggestedCategory": cat,
                "suggestedRelDir": rel_dir,
                "suggestedDestDir": str(dest_dir),
                "suggestedName": suggested_name,
                "destPath": str(dest_dir / suggested_name),
                "confidence": conf,
                "basis": basis,
                "ocrUsed": ocr_used,
                "title": title,
                "excerptPreview": (text or "")[:240],
            }
        )

    if llm_config:
        _maybe_llm_refine(suggestions, llm_config)

    seen_dests: set[str] = set()
    for s in suggestions:
        parent_root = Path(base_dir).expanduser() if base_dir else Path(s["srcPath"]).parent
        dest_dir = parent_root / s["suggestedRelDir"]
        s["suggestedDestDir"] = str(dest_dir)
        cand = dest_dir / s["suggestedName"]
        if str(cand) in seen_dests or cand.exists():
            stem = cand.stem
            suffix = cand.suffix
            counter = 2
            while True:
                next_cand = dest_dir / f"{stem}_{counter}{suffix}"
                if str(next_cand) not in seen_dests and not next_cand.exists():
                    cand = next_cand
                    break
                counter += 1
        seen_dests.add(str(cand))
        s["suggestedName"] = cand.name
        s["destPath"] = str(cand)

    return {"ok": True, "count": len(suggestions), "suggestions": suggestions}


def _collision_safe_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    counter = 2
    while True:
        cand = parent / f"{stem}_{counter}{suffix}"
        if not cand.exists():
            return cand
        counter += 1


def apply_organize_moves(moves: list[dict[str, str]]) -> dict[str, Any]:
    """Execute user-confirmed file moves/renames safely without overwrite and save undo history."""
    executed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for m in moves:
        src = Path(m["srcPath"]).expanduser()
        dst_raw = Path(m["destPath"]).expanduser()
        if not src.exists():
            failed.append({"srcPath": str(src), "error": "源文件不存在"})
            continue
        dst_raw.parent.mkdir(parents=True, exist_ok=True)
        dst = _collision_safe_dest(dst_raw)
        try:
            shutil.move(str(src), str(dst))
            executed.append({"srcPath": str(src), "destPath": str(dst), "ruleName": m.get("ruleName", "")})
        except Exception as exc:
            failed.append({"srcPath": str(src), "destPath": str(dst), "error": str(exc)})

    batch_id = f"batch_{int(time.time())}"
    if executed:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        batch_file = HISTORY_DIR / f"{batch_id}.json"
        batch_file.write_text(
            json.dumps({"batchId": batch_id, "ts": int(time.time()), "moves": executed}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "ok": len(failed) == 0,
        "batchId": batch_id if executed else None,
        "movedCount": len(executed),
        "failedCount": len(failed),
        "executed": executed,
        "failed": failed,
    }


def list_organize_history() -> list[dict[str, Any]]:
    if not HISTORY_DIR.exists():
        return []
    batches: list[dict[str, Any]] = []
    for p in sorted(HISTORY_DIR.glob("batch_*.json"), reverse=True)[:20]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            batches.append(
                {
                    "batchId": data.get("batchId", p.stem),
                    "ts": data.get("ts", 0),
                    "count": len(data.get("moves", [])),
                    "reverted": bool(data.get("reverted", False)),
                    "moves": data.get("moves", [])[:10],
                }
            )
        except Exception:
            continue
    return batches


def revert_organize_batch(batch_id: str) -> dict[str, Any]:
    batch_file = HISTORY_DIR / f"{batch_id}.json"
    if not batch_file.exists():
        return {"ok": False, "error": f"找不到整理记录: {batch_id}"}
    data = json.loads(batch_file.read_text(encoding="utf-8"))
    reverted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for m in reversed(data.get("moves", [])):
        cur = Path(m["destPath"])
        orig = Path(m["srcPath"])
        if not cur.exists():
            failed.append({"path": str(cur), "error": "目标文件已不在原处"})
            continue
        target = _collision_safe_dest(orig)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(cur), str(target))
            reverted.append({"from": str(cur), "to": str(target)})
        except Exception as exc:
            failed.append({"path": str(cur), "error": str(exc)})
    data["reverted"] = True
    batch_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": len(failed) == 0, "revertedCount": len(reverted), "failed": failed}
