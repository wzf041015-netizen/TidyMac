import AppKit
import Foundation
import PDFKit
import Vision

struct ExtractOutput: Codable {
    let path: String
    let kind: String
    let title: String
    let text: String
    let ocrUsed: Bool
    let pageCount: Int
    let error: String?
}

func recognizeText(cgImage: CGImage) -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return ""
    }
    guard let observations = request.results else {
        return ""
    }
    var lines: [String] = []
    var totalLen = 0
    for obs in observations.prefix(160) {
        if let top = obs.topCandidates(1).first {
            let trimmed = top.string.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                lines.append(trimmed)
                totalLen += trimmed.count
                if totalLen >= 4000 { break }
            }
        }
    }
    return lines.joined(separator: " ")
}

func renderPDFPageToCGImage(page: PDFPage, maxDim: CGFloat = 1800) -> CGImage? {
    let bounds = page.bounds(for: .mediaBox)
    guard bounds.width > 0 && bounds.height > 0 else { return nil }
    let scale = min(maxDim / max(bounds.width, bounds.height), 2.5)
    let targetW = max(Int((bounds.width * scale).rounded()), 1)
    let targetH = max(Int((bounds.height * scale).rounded()), 1)

    guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
          let ctx = CGContext(
              data: nil,
              width: targetW,
              height: targetH,
              bitsPerComponent: 8,
              bytesPerRow: 0,
              space: colorSpace,
              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
          )
    else { return nil }

    ctx.setFillColor(NSColor.white.cgColor)
    ctx.fill(CGRect(x: 0, y: 0, width: targetW, height: targetH))
    ctx.scaleBy(x: scale, y: scale)
    page.draw(with: .mediaBox, to: ctx)
    return ctx.makeImage()
}

func extractFile(path: String) -> ExtractOutput {
    let url = URL(fileURLWithPath: path)
    let ext = url.pathExtension.lowercased()

    if ext == "pdf" {
        guard let doc = PDFDocument(url: url) else {
            return ExtractOutput(path: path, kind: "pdf", title: "", text: "", ocrUsed: false, pageCount: 0, error: "无法打开 PDF")
        }
        if doc.isLocked {
            return ExtractOutput(path: path, kind: "pdf", title: "", text: "", ocrUsed: false, pageCount: doc.pageCount, error: "PDF 已加密")
        }
        var docTitle = ""
        if let attrs = doc.documentAttributes, let rawTitle = attrs[PDFDocumentAttribute.titleAttribute] as? String {
            docTitle = rawTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        var collected = ""
        let pageLimit = min(doc.pageCount, 3)
        for i in 0..<pageLimit {
            if let page = doc.page(at: i), let pageText = page.string {
                let cleaned = pageText.components(separatedBy: .whitespacesAndNewlines)
                    .filter { !$0.isEmpty }
                    .joined(separator: " ")
                if !cleaned.isEmpty {
                    collected += cleaned + " "
                }
            }
            if collected.count >= 4000 { break }
        }
        let nonWSCount = collected.unicodeScalars.filter { !CharacterSet.whitespacesAndNewlines.contains($0) }.count
        var ocrUsed = false
        if nonWSCount < 48 {
            var ocrParts: [String] = []
            for i in 0..<min(doc.pageCount, 2) {
                if let page = doc.page(at: i), let cg = renderPDFPageToCGImage(page: page) {
                    let ocrText = recognizeText(cgImage: cg)
                    if !ocrText.isEmpty {
                        ocrParts.append(ocrText)
                        ocrUsed = true
                    }
                }
            }
            if !ocrParts.isEmpty {
                collected = ocrParts.joined(separator: " ")
            }
        }
        let trimmed = String(collected.trimmingCharacters(in: .whitespacesAndNewlines).prefix(4000))
        return ExtractOutput(path: path, kind: "pdf", title: String(docTitle.prefix(200)), text: trimmed, ocrUsed: ocrUsed, pageCount: doc.pageCount, error: nil)
    }

    let imageExts: Set<String> = ["png", "jpg", "jpeg", "heic", "tif", "tiff", "bmp", "webp"]
    if imageExts.contains(ext) {
        guard let nsImg = NSImage(contentsOf: url),
              let cgImg = nsImg.cgImage(forProposedRect: nil, context: nil, hints: nil)
        else {
            return ExtractOutput(path: path, kind: "image", title: "", text: "", ocrUsed: false, pageCount: 1, error: "无法读取图片")
        }
        let ocrText = recognizeText(cgImage: cgImg)
        return ExtractOutput(path: path, kind: "image", title: "", text: String(ocrText.prefix(4000)), ocrUsed: !ocrText.isEmpty, pageCount: 1, error: nil)
    }

    return ExtractOutput(path: path, kind: ext, title: "", text: "", ocrUsed: false, pageCount: 0, error: "不支持的文件类型")
}

let args = Array(CommandLine.arguments.dropFirst())
if args.isEmpty {
    fputs("Usage: mac-ocr-extract <file1> [file2 ...]\n", stderr)
    exit(1)
}
let outputs = args.map { extractFile(path: $0) }
let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted]
if let data = try? encoder.encode(outputs), let json = String(data: data, encoding: .utf8) {
    print(json)
}
