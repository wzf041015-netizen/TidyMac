# TidyMac — 轻量 macOS 清理与文档整理工作台

> **无重型全盘索引 · 零后台常驻进程 · 默认 Dry-Run 预览 · 全程废纸篓保护**

TidyMac 不重新造轮子，而是将社区最成熟的 macOS 清理与整理开源引擎编排到一个统一的**原生轻量桌面壳（`TidyMac.app`）**与 **CLI 控制台（`tidymac`）**中，并结合 macOS 原生 `PDFKit` + `Vision OCR` 完成文档即时分拣与全本机应用空间审计。

---

## 核心特性

1. **零重型后台常驻**：不维护百万级全盘数据库，仅在用户手动触发体检或操作时按需扫描；`TidyMac.app` 关闭窗口即彻底退出服务进程（`--watch-parent` 联动退出）。
2. **默认 Dry-Run 预览 + 废纸篓保护**：
   - 所有扫描默认只读，完整列出路径与预计释放空间；
   - 删除目录/文件与废弃工程整包**默认调用 macOS `/usr/bin/trash`（或 Finder AppleScript 兜底）移入废纸篓**，绝不静默 `rm -rf`，并在代码层拦截系统根目录、用户主目录等危险路径。
3. **持久化结果缓存 + 增量同步**：
   - 体检结果持久化存储在 `~/.config/tidymac/cache/`，二次打开秒级还原上次状态；
   - 执行清理、移入废纸篓或归档后，局部增量裁剪对应条目并刷新可释放空间，无需整盘重扫。
4. **九大模块并联体检（进度实时推送）**：
   - **Mole 安全缓存与残留**（`tw93/Mole`）：`mo clean --dry-run` 预览可安全清理的缓存与日志；`mo analyze` 交互透视任意目录占用排行。
   - **孤立配置文件扫描**（兼容 `alienator88/Pearcleaner` 逆向规则）：检索 `~/Library/Preferences/*.plist`、`LaunchAgents`、`Application Support`、`Containers`、`Caches` 中主程序已不存在的无主残留，标明推断应用名与体积。
   - **所有应用 & 关联空间（应用图谱）**：全盘关联应用本体（`/Applications`、`~/Applications`）、数据目录（`Application Support` / `Containers` / `Group Containers` / `Preferences`）与缓存日志（`Caches` / `Logs`），附带应用**用途说明（`APP_CATALOG` 内置高频应用百科 + plist 信息识别）**，支持单独清理应用缓存或连带关联目录卸载至废纸篓。
   - **工程依赖清理**（`tbillington/kondo`）：精准识别 `node_modules`、`target`、`.venv`、`dist`、`build` 等构建产物，支持**只清依赖、保留源码**。
   - **废弃 AI 试水工程识别**：扫描长期未修改、无 Git 远程仓库或仅初始提交的实验/半成品工程，列出大小与最后活跃时间，勾选后整包移入废纸篓。
   - **文档与图片极速查重**（`qarmin/czkawka`）：基于 `czkawka_cli` 提供 BLAKE3 文档/压缩包精确查重、相似图片感知哈希检测及 Top30 超大文件排行。
   - **规则自动归档**（`tfeldmann/organize`）：预设常用格式/关键词 YAML 规则，先执行 `organize sim`（Dry-Run）模拟预览，确认后再移动；每次批量移动均写入 `~/.config/tidymac/history/`，支持**一键撤回（Revert）**。
   - **未命中文档即时 OCR / AI 分拣**：仅对规则未命中的杂乱文件按需调用 Swift 原生 `PDFKit` + Apple `Vision OCR` 提取正文标题（可选接入 OpenAI 兼容接口）生成重命名与归类建议。

---

## 已集成开源项目与引擎

| 模块 | 上游项目 / 技术 | 能力说明 |
| :--- | :--- | :--- |
| 系统缓存与大目录透视 | [`tw93/Mole`](https://github.com/tw93/Mole) | `mo clean --dry-run` 安全缓存扫描；`mo analyze --json` 目录占用透视 |
| 孤立配置残留规则 | [`alienator88/Pearcleaner`](https://github.com/alienator88/Pearcleaner) | `Preferences` / `LaunchAgents` / `Application Support` / `Containers` 无主配置逆向匹配（可配合安装原版 App） |
| 工程依赖清理 | [`tbillington/kondo`](https://github.com/tbillington/kondo) | 一键识别多语言构建与虚拟环境产物，清依赖保源码 |
| 极速查重与大图排查 | [`qarmin/czkawka`](https://github.com/qarmin/czkawka) | `czkawka_cli` BLAKE3 查重、相似图片与大文件榜单 |
| 规则化自动整理 | [`tfeldmann/organize`](https://github.com/tfeldmann/organize) | `organize sim` 模拟预览、冲突重命名与可撤回批处理 |
| 本地文档文本提取 | Apple `PDFKit` + `Vision` + `AppKit` | 原生 Swift 桌面壳、`mac-ocr-extract` 与矢量图标构建 |

---

## 快速安装

环境要求：**macOS 13+**（Apple Silicon / Intel）、**Xcode Command Line Tools**（`xcode-select --install`）、**Python >= 3.11**。

```bash
git clone https://github.com/wzf041015-netizen/TidyMac.git
cd TidyMac
chmod +x scripts/install.sh
./scripts/install.sh
```

安装脚本会自动完成以下步骤：
1. 创建 `.venv` 虚拟环境并安装 `TidyMac`（`pyyaml` 等依赖）；
2. 编译原生辅助工具 `bin/mac-ocr-extract`；
3. 自动检测 Homebrew（若无 Homebrew 则通过 GitHub Releases / `uv` 直装）安装 `mo`、`kondo`、`czkawka_cli`、`organize`；
4. 软链接 CLI 到 `~/.local/bin/tidymac`；
5. 构建独立的 macOS 桌面应用 `../TidyMac.app`（支持 `--no-app` 跳过桌面壳或 `--with-pearcleaner` 一并安装 Pearcleaner）。

---

## 使用方式

### 1. 图形桌面工作台（推荐）

双击生成的 **`TidyMac.app`** 或在终端运行：

```bash
tidymac ui
# 或者直接通过系统默认浏览器打开本地 Web 控制台：
tidymac serve --open
```

### 2. 命令行控制台（CLI）

```bash
# 检查所有开源引擎与完全磁盘访问权限状态
tidymac doctor

# 执行全部 9 项只读体检 (Dry-Run) 并输出报告（自动写入缓存）
tidymac checkup

# 强制忽略缓存，实时重扫全项
tidymac checkup --refresh

# 分模块单独检查
tidymac apps                 # 扫描第三方已安装应用与关联空间（加 --system 含系统内置应用）
tidymac orphans              # 扫描孤立配置文件
tidymac projects --action all
tidymac dupes --mode docs    # docs | all | images | big
tidymac organize sim --dir ~/Downloads
tidymac organize triage --dir ~/Downloads
tidymac organize revert --batch-id <BATCH_ID>
```

---

## 自定义整理规则

仓库内置了开箱即用的通用规则 [`config/organize_rules.yaml`](config/organize_rules.yaml)（安装包、票据合同、技术数据、Office/PDF、电子书、截图图片、压缩包）。

如果你希望在不修改 Git 仓库的前提下长期维护个人专属规则，可将模板复制到用户配置目录：

```bash
mkdir -p ~/.config/tidymac
cp config/organize_rules.yaml ~/.config/tidymac/organize_rules.yaml
```

当 `~/.config/tidymac/organize_rules.yaml` 存在时，TidyMac 会自动优先加载该个人配置文件。  
如需为未命中文档的 Vision OCR / 内容识别扩充自定义领域词库，也可创建 `~/.config/tidymac/ocr_topics.json`。

---

## 权限说明（完全磁盘访问权限 / Full Disk Access）

在未授权完全磁盘访问权限时，TidyMac 仍可正常扫描下载、文档、桌面、开发者目录及大部分用户目录；仅以下敏感位置会被 macOS SIP/TCC 拦截：
- 系统废纸篓（`~/.Trash`）
- `Safari` / `Mail` 沙盒数据与部分系统级缓存

如需获得完整体检结果，请在 **系统设置 → 隐私与安全性 → 完全磁盘访问权限** 中点击 `+`，添加 `TidyMac.app`（或你在使用的终端 App）。

> **开发提示**：macOS 会将本地 Ad-Hoc 签名的授权绑定到特定二进制签名哈希（CDHash）。如果你修改了 Swift 源码并重新执行 `macos/build_app.sh` 重建了 `TidyMac.app`，系统设置中的旧开关会失效；此时在列表中选中 `TidyMac` 点击 `-` 删除后重新 `+` 添加即可。仅修改 Python/HTML 文件无需重建 `.app`。

---

## 运行自检测试

```bash
.venv/bin/python tests/smoke_test.py
```

测试覆盖引擎就绪状态、缓存与增量裁剪、HTTP 接口生命周期、`organize` 模拟/移动/撤回，以及废纸篓路径安全防护拦截。

---

## 开源致谢与协议

感谢 [`tw93/Mole`](https://github.com/tw93/Mole)、[`alienator88/Pearcleaner`](https://github.com/alienator88/Pearcleaner)、[`tbillington/kondo`](https://github.com/tbillington/kondo)、[`qarmin/czkawka`](https://github.com/qarmin/czkawka) 与 [`tfeldmann/organize`](https://github.com/tfeldmann/organize) 提供的优秀开源基础设施。

本项目代码基于 [MIT License](LICENSE) 开源，不构成对上述第三方独立项目许可协议的变更。
