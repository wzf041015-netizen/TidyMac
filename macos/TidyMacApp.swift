import AppKit
import Foundation
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var serverProcess: Process?
    private let tidymacBin: String

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
        appMenu.addItem(NSMenuItem(title: "退出 TidyMac", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(NSMenuItem(title: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(title: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem(title: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        editMenuItem.submenu = editMenu

        NSApp.mainMenu = mainMenu
    }

    private func setupWindow() {
        let frame = NSRect(x: 0, y: 0, width: 1260, height: 840)
        window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "TidyMac — 轻量 Mac 清理与文档整理工作台"
        window.minSize = NSSize(width: 980, height: 660)
        window.backgroundColor = NSColor.windowBackgroundColor
        window.center()

        let config = WKWebViewConfiguration()
        webView = WKWebView(frame: frame, configuration: config)
        webView.setValue(false, forKey: "drawsBackground")
        webView.navigationDelegate = self
        let loadingHTML = """
        <!doctype html><html><head><meta charset="utf-8"><style>
        body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
        font-family:-apple-system,sans-serif;background:#f5f5f7;color:#374151}
        @media(prefers-color-scheme:dark){body{background:#111318;color:#d1d5db}}
        .box{text-align:center}.t{font-size:15px;font-weight:600;margin-bottom:6px}.s{font-size:12px;opacity:.65}
        </style></head><body><div class="box"><div class="t">TidyMac 正在加载工作台与上次体检缓存...</div><div class="s">无后台常驻 · 秒开还原状态</div></div></body></html>
        """
        webView.loadHTMLString(loadingHTML, baseURL: nil)
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func reloadPage() {
        webView.reload()
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
        let handle = pipe.fileHandleForReading
        handle.readabilityHandler = { [weak self] fh in
            let data = fh.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            if !loaded, let range = text.range(of: "TIDYMAC_PORT=") {
                let tail = text[range.upperBound...]
                let digits = tail.prefix { $0.isNumber }
                if let port = Int(digits) {
                    loaded = true
                    DispatchQueue.main.async {
                        if let url = URL(string: "http://127.0.0.1:\(port)/") {
                            self?.webView.load(URLRequest(url: url))
                        }
                    }
                }
            }
        }

        do {
            try proc.run()
        } catch {
            DispatchQueue.main.async {
                self.webView.loadHTMLString("<h2>启动 TidyMac 服务失败</h2><pre>\(error)</pre>", baseURL: nil)
            }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
