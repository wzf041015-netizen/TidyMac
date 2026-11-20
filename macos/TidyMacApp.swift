import AppKit
import Foundation
import WebKit

/// Transparent strip over the (hidden) titlebar area so the window can still be
/// dragged / double-click-zoomed even though the WKWebView fills the whole window.
final class TitlebarDragView: NSView {
    weak var scrollTarget: NSView?

    override func mouseDown(with event: NSEvent) {
        if event.clickCount == 2 {
            let action = UserDefaults.standard.string(forKey: "AppleActionOnDoubleClick") ?? "Maximize"
            if action == "Minimize" { window?.performMiniaturize(nil) } else { window?.performZoom(nil) }
            return
        }
        window?.performDrag(with: event)
    }

    override func scrollWheel(with event: NSEvent) {
        scrollTarget?.scrollWheel(with: event)
    }

    override var mouseDownCanMoveWindow: Bool { true }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var serverProcess: Process?
    private let tidymacBin: String
    private let titlebarHeight: CGFloat = 28

    override init() {
        let bundleDir = Bundle.main.bundleURL.deletingLastPathComponent()
        let candidateInParent = bundleDir.appendingPathComponent("TidyMac/bin/tidymac").path
        let candidateResources = Bundle.main.resourcePath.map { "\($0)/tidymac-root" } ?? ""
        if FileManager.default.isExecutableFile(atPath: candidateInParent) {
            self.tidymacBin = candidateInParent
        } else if FileManager.default.isExecutableFile(atPath: candidateResources) {
            self.tidymacBin = candidateResources
        } else {
            self.tidymacBin = "\(NSHomeDirectory())/.local/bin/tidymac"
        }
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMenu()
        setupWindow()
        startBackendAndLoad()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let proc = serverProcess, proc.isRunning {
            proc.terminate()
        }
    }

    private func setupMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(title: "刷新工作台", action: #selector(reloadPage), keyEquivalent: "r"))
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(NSMenuItem(title: "隐藏 TidyMac", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h"))
        appMenu.addItem(NSMenuItem(title: "退出 TidyMac", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(NSMenuItem(title: "撤销", action: Selector(("undo:")), keyEquivalent: "z"))
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(NSMenuItem(title: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(title: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem(title: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        editMenuItem.submenu = editMenu

        let windowMenuItem = NSMenuItem()
        mainMenu.addItem(windowMenuItem)
        let windowMenu = NSMenu(title: "窗口")
        windowMenu.addItem(NSMenuItem(title: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m"))
        windowMenu.addItem(NSMenuItem(title: "缩放", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: ""))
        windowMenu.addItem(NSMenuItem(title: "关闭", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w"))
        windowMenuItem.submenu = windowMenu
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = mainMenu
    }

    private func setupWindow() {
        let frame = NSRect(x: 0, y: 0, width: 1280, height: 840)
        window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "TidyMac"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.isMovableByWindowBackground = false
        window.minSize = NSSize(width: 1000, height: 660)
        window.backgroundColor = .clear
        window.center()
        window.setFrameAutosaveName("TidyMacMainWindow")

        // Native frosted backdrop: shows through the translucent web sidebar.
        let effect = NSVisualEffectView(frame: frame)
        effect.material = .sidebar
        effect.blendingMode = .behindWindow
        effect.state = .followsWindowActiveState
        effect.autoresizingMask = [.width, .height]

        let config = WKWebViewConfiguration()
        let marker = WKUserScript(
            source: "document.documentElement.classList.add('native-shell');",
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        )
        config.userContentController.addUserScript(marker)
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")

        webView = WKWebView(frame: effect.bounds, configuration: config)
        webView.setValue(false, forKey: "drawsBackground")
        if #available(macOS 12.0, *) {
            webView.underPageBackgroundColor = .clear
        }
        webView.allowsBackForwardNavigationGestures = false
        webView.allowsMagnification = false
        webView.navigationDelegate = self
        webView.autoresizingMask = [.width, .height]
        effect.addSubview(webView)

        let drag = TitlebarDragView(frame: NSRect(
            x: 0, y: effect.bounds.height - titlebarHeight,
            width: effect.bounds.width, height: titlebarHeight
        ))
        drag.autoresizingMask = [.width, .minYMargin]
        drag.scrollTarget = webView
        effect.addSubview(drag)

        webView.loadHTMLString(Self.loadingHTML, baseURL: nil)
        window.contentView = effect
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private static let loadingHTML = """
    <!doctype html><html class="native-shell"><head><meta charset="utf-8"><style>
    :root{--bg:#faf9f5;--side:rgba(241,239,232,.58);--text:#1f1e1d;--muted:#7a7870;--accent:#c96442}
    @media(prefers-color-scheme:dark){:root{--bg:#262624;--side:rgba(28,27,26,.52);--text:#f4f3ee;--muted:#9d9b92;--accent:#d97757}}
    html,body{margin:0;height:100%;background:transparent;font-family:-apple-system,"PingFang SC",sans-serif;-webkit-font-smoothing:antialiased}
    .shell{display:grid;grid-template-columns:236px 1fr;height:100vh}
    .side{background:var(--side)}
    .main{background:var(--bg);display:flex;align-items:center;justify-content:center;color:var(--text)}
    .box{text-align:center;animation:in .5s cubic-bezier(.22,1,.36,1)}
    svg{width:34px;height:34px;color:var(--accent);animation:spin 2.4s linear infinite}
    .t{font-family:ui-serif,"New York","Songti SC",serif;font-size:20px;margin:14px 0 6px;letter-spacing:-.01em}
    .s{font-size:12px;color:var(--muted)}
    @keyframes spin{to{transform:rotate(360deg)}}
    @keyframes in{from{opacity:0;transform:translateY(6px)}}
    </style></head><body><div class="shell"><div class="side"></div><div class="main"><div class="box">
    <svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" d="M12 9.4V1.8M13.56 9.92l3.01-3.99M14.25 10.7l6.58-3.8M14.58 12.32l4.96.61M14.25 13.3l6.58 3.8M13.02 14.39L14.97 19M12 14.6v7.6M10.44 14.08l-3.01 3.99M9.75 13.3l-6.58 3.8M9.42 11.68l-4.96-.61M9.75 10.7L3.17 6.9M10.98 9.61L9.03 5"/></svg>
    <div class="t">正在唤醒 TidyMac</div><div class="s">还原上次体检结果 · 无后台常驻</div>
    </div></div></div></body></html>
    """

    @objc private func reloadPage() {
        webView.reload()
    }

    private func showError(_ message: String) {
        let safe = message
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
        let html = Self.loadingHTML
            .replacingOccurrences(of: "正在唤醒 TidyMac", with: "启动 TidyMac 服务失败")
            .replacingOccurrences(of: "还原上次体检结果 · 无后台常驻", with: safe)
            .replacingOccurrences(of: "animation:spin 2.4s linear infinite", with: "")
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func probeFullDiskAccess() -> (granted: Bool, detail: String) {
        let fm = FileManager.default
        let home = NSHomeDirectory()
        var results: [String] = []
        var anyGranted = false
        for rel in ["Library/Safari", "Library/Mail", ".Trash"] {
            let p = "\(home)/\(rel)"
            if fm.fileExists(atPath: p) {
                do {
                    _ = try fm.contentsOfDirectory(atPath: p)
                    results.append("\(rel)=OK")
                    anyGranted = true
                } catch {
                    results.append("\(rel)=ERR(\(error.localizedDescription))")
                }
            } else {
                results.append("\(rel)=MISSING")
            }
        }
        return (anyGranted, results.joined(separator: "; "))
    }

    private func startBackendAndLoad() {
        let fda = probeFullDiskAccess()
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
        var env = ProcessInfo.processInfo.environment
        env["TIDYMAC_SWIFT_FDA"] = fda.granted ? "1" : "0"
        env["TIDYMAC_SWIFT_FDA_DETAIL"] = fda.detail
        proc.environment = env
        proc.arguments = [tidymacBin, "serve", "--port", "0", "--print-port", "--watch-parent", "\(ProcessInfo.processInfo.processIdentifier)"]

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        serverProcess = proc

        var loaded = false
        var buffer = ""
        let handle = pipe.fileHandleForReading
        handle.readabilityHandler = { [weak self] fh in
            let data = fh.availableData
            if data.isEmpty {
                fh.readabilityHandler = nil
                return
            }
            guard !loaded, let text = String(data: data, encoding: .utf8) else { return }
            buffer += text
            if let range = buffer.range(of: "TIDYMAC_PORT=") {
                let digits = buffer[range.upperBound...].prefix { $0.isNumber }
                if let port = Int(digits), buffer[range.upperBound...].count > digits.count {
                    loaded = true
                    buffer = ""
                    DispatchQueue.main.async {
                        if let url = URL(string: "http://127.0.0.1:\(port)/") {
                            self?.webView.load(URLRequest(url: url))
                        }
                    }
                }
            } else if buffer.count > 16_384 {
                buffer = String(buffer.suffix(2048))
            }
        }

        proc.terminationHandler = { [weak self] p in
            guard !loaded else { return }
            DispatchQueue.main.async {
                self?.showError("后台服务提前退出（代码 \(p.terminationStatus)）。请在终端运行 tidymac doctor 排查。")
            }
        }

        do {
            try proc.run()
        } catch {
            showError("\(error.localizedDescription)")
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
