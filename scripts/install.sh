#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_BIN="$HOME/.local/bin"

BUILD_APP=1
INSTALL_ENGINES=1
WITH_PEARCLEANER=0

for arg in "$@"; do
  case "$arg" in
    --no-app) BUILD_APP=0 ;;
    --no-engines) INSTALL_ENGINES=0 ;;
    --with-pearcleaner) WITH_PEARCLEANER=1 ;;
    -h|--help)
      cat <<'EOF'
TidyMac 安装脚本 (macOS)

用法:
  ./scripts/install.sh [选项]

选项:
  --with-pearcleaner  同时尝试安装 Pearcleaner.app (通过 Homebrew Cask)
  --no-engines        跳过开源 CLI 引擎安装，仅构建 Python 环境、OCR 工具与 TidyMac.app
  --no-app            跳过构建桌面壳 TidyMac.app（仅配置 CLI）
  -h, --help          显示帮助
EOF
      exit 0
      ;;
    *)
      echo "未知选项: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "错误: TidyMac 仅支持 macOS (Darwin)。" >&2
  exit 1
fi

if ! command -v swiftc >/dev/null 2>&1; then
  echo "错误: 未检测到 swiftc，请先运行: xcode-select --install" >&2
  exit 1
fi

mkdir -p "$LOCAL_BIN" "$ROOT_DIR/bin"
export PATH="$LOCAL_BIN:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "[1/5] 配置 Python 运行环境 (.venv)..."
if command -v uv >/dev/null 2>&1; then
  if [[ ! -d "$ROOT_DIR/.venv" ]]; then
    uv venv "$ROOT_DIR/.venv" --python ">=3.11"
  fi
  uv pip install --python "$ROOT_DIR/.venv/bin/python" -e "$ROOT_DIR"
else
  PY_BIN="$(command -v python3 || true)"
  if [[ -z "$PY_BIN" ]]; then
    echo "错误: 未找到 python3 (>=3.11) 或 uv。" >&2
    exit 1
  fi
  "$PY_BIN" -c 'import sys; assert sys.version_info >= (3, 11)' || {
    echo "错误: 需要 Python >= 3.11（当前 $("$PY_BIN" --version)）。" >&2
    exit 1
  }
  if [[ ! -d "$ROOT_DIR/.venv" ]]; then
    "$PY_BIN" -m venv "$ROOT_DIR/.venv"
  fi
  "$ROOT_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
  "$ROOT_DIR/.venv/bin/pip" install -e "$ROOT_DIR"
fi

echo "[2/5] 编译 macOS Vision OCR + PDFKit 辅助工具..."
swiftc -O "$ROOT_DIR/macos/MacOcrExtract.swift" -o "$ROOT_DIR/bin/mac-ocr-extract"
chmod +x "$ROOT_DIR/bin/tidymac" "$ROOT_DIR/bin/mac-ocr-extract"

install_github_asset() {
  local repo="$1"
  local pattern_py="$2"
  local dest_name="$3"
  local bin_name="$4"
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  python3 - "$repo" "$pattern_py" "$tmp_dir" "$LOCAL_BIN" "$dest_name" "$bin_name" <<'PY'
import json, os, shutil, stat, subprocess, sys, tarfile, urllib.request, zipfile
from pathlib import Path

repo, pat, tmp_dir, local_bin, dest_name, bin_name = sys.argv[1:7]
api = f"https://api.github.com/repos/{repo}/releases/latest"
req = urllib.request.Request(api, headers={"User-Agent": "TidyMac-Installer"})
with urllib.request.urlopen(req, timeout=20) as r:
    rel = json.load(r)

assets = rel.get("assets", [])
match = None
for a in assets:
    name = a.get("name", "").lower()
    if all(tok.lower() in name for tok in pat.split(",")):
        match = a
        break
if not match:
    raise RuntimeError(f"No matching asset in {repo} for tokens: {pat}")

dl = Path(tmp_dir) / match["name"]
urllib.request.urlretrieve(match["browser_download_url"], dl)
if dl.name.endswith((".tar.gz", ".tgz")):
    with tarfile.open(dl, "r:gz") as tf:
        tf.extractall(tmp_dir)
elif dl.name.endswith(".zip"):
    with zipfile.ZipFile(dl, "r") as zf:
        zf.extractall(tmp_dir)

target_bin = None
for p in Path(tmp_dir).rglob("*"):
    if p.is_file() and (p.name == bin_name or p.name == match["name"]):
        target_bin = p
        break
if not target_bin:
    raise RuntimeError(f"Binary {bin_name} not found inside {dl.name}")

out = Path(local_bin) / dest_name
shutil.copy2(target_bin, out)
out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
print(f"Installed {dest_name} -> {out} ({rel.get('tag_name', '')})")
PY
  rm -rf "$tmp_dir"
}

if [[ "$INSTALL_ENGINES" -eq 1 ]]; then
  echo "[3/5] 检查并安装开源引擎..."
  ARCH="$(uname -m)"

  if command -v brew >/dev/null 2>&1; then
    command -v mo >/dev/null 2>&1 || brew install mole || true
    command -v kondo >/dev/null 2>&1 || brew install kondo || true
    command -v czkawka_cli >/dev/null 2>&1 || brew install czkawka || true
    if [[ "$WITH_PEARCLEANER" -eq 1 && ! -d "/Applications/Pearcleaner.app" ]]; then
      brew install --cask pearcleaner || true
    fi
  else
    echo "  提示: 未检测到 Homebrew，将尝试从 GitHub Releases 直装预编译包..."
    if ! command -v mo >/dev/null 2>&1; then
      echo "  安装 tw93/Mole..."
      curl -fsSL https://raw.githubusercontent.com/tw93/mole/main/install.sh | bash || echo "  警告: Mole 自动安装失败，请参考 https://github.com/tw93/Mole 手动安装"
    fi
    if ! command -v kondo >/dev/null 2>&1; then
      if [[ "$ARCH" == "arm64" ]]; then
        install_github_asset "tbillington/kondo" "apple-darwin,aarch64" "kondo" "kondo" || install_github_asset "tbillington/kondo" "macos" "kondo" "kondo" || echo "  警告: kondo 安装失败，请手动安装"
      else
        install_github_asset "tbillington/kondo" "apple-darwin,x86_64" "kondo" "kondo" || echo "  警告: kondo 安装失败，请手动安装"
      fi
    fi
    if ! command -v czkawka_cli >/dev/null 2>&1; then
      install_github_asset "qarmin/czkawka" "mac,czkawka_cli" "czkawka_cli" "czkawka_cli" || echo "  警告: czkawka_cli 安装失败，请参考 https://github.com/qarmin/czkawka 手动安装"
    fi
  fi

  if ! command -v organize >/dev/null 2>&1; then
    echo "  安装 tfeldmann/organize..."
    if command -v uv >/dev/null 2>&1; then
      uv tool install organize-tool || "$ROOT_DIR/.venv/bin/pip" install organize-tool
    else
      "$ROOT_DIR/.venv/bin/pip" install organize-tool
    fi
    if [[ ! -x "$LOCAL_BIN/organize" && -x "$ROOT_DIR/.venv/bin/organize" ]]; then
      ln -sf "$ROOT_DIR/.venv/bin/organize" "$LOCAL_BIN/organize"
    fi
  fi
else
  echo "[3/5] 已按 --no-engines 跳过开源引擎安装。"
fi

echo "[4/5] 链接 CLI 命令 -> $LOCAL_BIN/tidymac"
ln -sf "$ROOT_DIR/bin/tidymac" "$LOCAL_BIN/tidymac"

if [[ "$BUILD_APP" -eq 1 ]]; then
  echo "[5/5] 构建 macOS 桌面壳 TidyMac.app..."
  zsh "$ROOT_DIR/macos/build_app.sh"
else
  echo "[5/5] 已按 --no-app 跳过 TidyMac.app 构建。"
fi

echo ""
echo "✅ TidyMac 安装完成！"
echo "1) 自检引擎与权限:  $LOCAL_BIN/tidymac doctor"
echo "2) 只读全项体检:    $LOCAL_BIN/tidymac checkup"
if [[ "$BUILD_APP" -eq 1 ]]; then
  APP_OUT="$(cd "$ROOT_DIR/.." && pwd)/TidyMac.app"
  echo "3) 启动桌面工作台:  open \"$APP_OUT\""
fi
echo ""
echo "提示: 若需清理系统废纸篓与 Safari/Mail 缓存，请在"
echo "「系统设置 → 隐私与安全性 → 完全磁盘访问权限」中添加 TidyMac.app 或终端。"
echo "如 ~/.local/bin 不在 PATH 中，可执行: export PATH=\"\$HOME/.local/bin:\$PATH\""
