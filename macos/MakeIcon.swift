import AppKit
import CoreGraphics
import Foundation

func drawSquirclePath(in rect: CGRect, radius: CGFloat) -> CGPath {
    // Smooth continuous-style rounded rect path
    let path = CGMutablePath()
    let minX = rect.minX
    let maxX = rect.maxX
    let minY = rect.minY
    let maxY = rect.maxY
    let r = min(radius, rect.width / 2, rect.height / 2)

    path.move(to: CGPoint(x: minX + r, y: minY))
    path.addLine(to: CGPoint(x: maxX - r, y: minY))
    path.addCurve(
        to: CGPoint(x: maxX, y: minY + r),
        control1: CGPoint(x: maxX - r * 0.35, y: minY),
        control2: CGPoint(x: maxX, y: minY + r * 0.35)
    )
    path.addLine(to: CGPoint(x: maxX, y: maxY - r))
    path.addCurve(
        to: CGPoint(x: maxX - r, y: maxY),
        control1: CGPoint(x: maxX, y: maxY - r * 0.35),
        control2: CGPoint(x: maxX - r * 0.35, y: maxY)
    )
    path.addLine(to: CGPoint(x: minX + r, y: maxY))
    path.addCurve(
        to: CGPoint(x: minX, y: maxY - r),
        control1: CGPoint(x: minX + r * 0.35, y: maxY),
        control2: CGPoint(x: minX, y: maxY - r * 0.35)
    )
    path.addLine(to: CGPoint(x: minX, y: minY + r))
    path.addCurve(
        to: CGPoint(x: minX + r, y: minY),
        control1: CGPoint(x: minX, y: minY + r * 0.35),
        control2: CGPoint(x: minX + r * 0.35, y: minY)
    )
    path.closeSubpath()
    return path
}

func drawSparkle(ctx: CGContext, center: CGPoint, radius: CGFloat, color: CGColor) {
    let p = CGMutablePath()
    let inner = radius * 0.24
    p.move(to: CGPoint(x: center.x, y: center.y + radius))
    p.addQuadCurve(to: CGPoint(x: center.x + radius, y: center.y), control: CGPoint(x: center.x + inner, y: center.y + inner))
    p.addQuadCurve(to: CGPoint(x: center.x, y: center.y - radius), control: CGPoint(x: center.x + inner, y: center.y - inner))
    p.addQuadCurve(to: CGPoint(x: center.x - radius, y: center.y), control: CGPoint(x: center.x - inner, y: center.y - inner))
    p.addQuadCurve(to: CGPoint(x: center.x, y: center.y + radius), control: CGPoint(x: center.x - inner, y: center.y + inner))
    p.closeSubpath()
    ctx.setFillColor(color)
    ctx.addPath(p)
    ctx.fillPath()
}

func renderIcon(pixelSize: Int, outURL: URL) {
    let s = CGFloat(pixelSize)
    guard let cs = CGColorSpace(name: CGColorSpace.sRGB),
          let ctx = CGContext(
              data: nil,
              width: pixelSize,
              height: pixelSize,
              bitsPerComponent: 8,
              bytesPerRow: 0,
              space: cs,
              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
          )
    else { return }

    ctx.clear(CGRect(x: 0, y: 0, width: s, height: s))
    ctx.setShouldAntialias(true)
    ctx.setAllowsAntialiasing(true)

    // Standard macOS icon canvas margin (~10% inset on each side -> 824/1024)
    let inset = s * 0.098
    let iconRect = CGRect(x: inset, y: inset, width: s - inset * 2, height: s - inset * 2)
    let cornerRadius = iconRect.width * 0.228
    let squircle = drawSquirclePath(in: iconRect, radius: cornerRadius)

    // Outer ambient shadow
    ctx.saveGState()
    ctx.setShadow(
        offset: CGSize(width: 0, height: -s * 0.02),
        blur: s * 0.04,
        color: CGColor(red: 0, green: 0, blue: 0, alpha: 0.34)
    )
    ctx.addPath(squircle)
    ctx.setFillColor(CGColor(red: 0.05, green: 0.14, blue: 0.28, alpha: 1))
    ctx.fillPath()
    ctx.restoreGState()

    // Clip to squircle & draw rich macOS backdrop gradient
    ctx.saveGState()
    ctx.addPath(squircle)
    ctx.clip()

    let bgColors = [
        CGColor(red: 0.05, green: 0.11, blue: 0.24, alpha: 1),
        CGColor(red: 0.04, green: 0.34, blue: 0.58, alpha: 1),
        CGColor(red: 0.08, green: 0.62, blue: 0.78, alpha: 1),
    ] as CFArray
    if let grad = CGGradient(colorsSpace: cs, colors: bgColors, locations: [0.0, 0.52, 1.0]) {
        ctx.drawLinearGradient(
            grad,
            start: CGPoint(x: iconRect.minX, y: iconRect.minY),
            end: CGPoint(x: iconRect.maxX, y: iconRect.maxY),
            options: []
        )
    }

    // Subtle radial mint/cyan highlight top-right
    let glowColors = [
        CGColor(red: 0.42, green: 0.94, blue: 0.88, alpha: 0.36),
        CGColor(red: 0.42, green: 0.94, blue: 0.88, alpha: 0.0),
    ] as CFArray
    if let glow = CGGradient(colorsSpace: cs, colors: glowColors, locations: [0.0, 1.0]) {
        let gCenter = CGPoint(x: iconRect.maxX - iconRect.width * 0.2, y: iconRect.maxY - iconRect.height * 0.18)
        ctx.drawRadialGradient(glow, startCenter: gCenter, startRadius: 0, endCenter: gCenter, endRadius: iconRect.width * 0.7, options: [])
    }

    // Top inner rim highlight
    ctx.saveGState()
    ctx.addPath(squircle)
    ctx.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.28))
    ctx.setLineWidth(max(1.5, s * 0.012))
    ctx.strokePath()
    ctx.restoreGState()

    // Emblem 1: Clean Stacked Document / Storage Cards (lower-center)
    let w = iconRect.width
    let h = iconRect.height
    let cx = iconRect.midX
    let cy = iconRect.midY

    // Back stacked card (tilted slightly)
    ctx.saveGState()
    ctx.translateBy(x: cx, y: cy + h * 0.02)
    ctx.rotate(by: -0.07)
    let backCard = CGRect(x: -w * 0.26, y: -h * 0.21, width: w * 0.52, height: h * 0.42)
    let backPath = CGPath(roundedRect: backCard, cornerWidth: w * 0.06, cornerHeight: w * 0.06, transform: nil)
    ctx.addPath(backPath)
    ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.24))
    ctx.fillPath()
    ctx.restoreGState()

    // Front folder/card with clean Mac-style notch tab
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -s * 0.015), blur: s * 0.03, color: CGColor(red: 0, green: 0.05, blue: 0.15, alpha: 0.35))
    let fLeft = cx - w * 0.29
    let fRight = cx + w * 0.29
    let fBottom = cy - h * 0.25
    let fTop = cy + h * 0.17
    let cr = w * 0.065
    let tabW = w * 0.23
    let tabH = h * 0.06

    let folder = CGMutablePath()
    folder.move(to: CGPoint(x: fLeft + cr, y: fBottom))
    folder.addLine(to: CGPoint(x: fRight - cr, y: fBottom))
    folder.addArc(tangent1End: CGPoint(x: fRight, y: fBottom), tangent2End: CGPoint(x: fRight, y: fBottom + cr), radius: cr)
    folder.addLine(to: CGPoint(x: fRight, y: fTop - cr))
    folder.addArc(tangent1End: CGPoint(x: fRight, y: fTop), tangent2End: CGPoint(x: fRight - cr, y: fTop), radius: cr)
    folder.addLine(to: CGPoint(x: fLeft + tabW + cr * 0.7, y: fTop))
    folder.addLine(to: CGPoint(x: fLeft + tabW, y: fTop + tabH))
    folder.addLine(to: CGPoint(x: fLeft + cr, y: fTop + tabH))
    folder.addArc(tangent1End: CGPoint(x: fLeft, y: fTop + tabH), tangent2End: CGPoint(x: fLeft, y: fTop + tabH - cr), radius: cr)
    folder.addLine(to: CGPoint(x: fLeft, y: fBottom + cr))
    folder.addArc(tangent1End: CGPoint(x: fLeft, y: fBottom), tangent2End: CGPoint(x: fLeft + cr, y: fBottom), radius: cr)
    folder.closeSubpath()

    ctx.addPath(folder)
    ctx.clip()
    let folderColors = [
        CGColor(red: 0.86, green: 0.94, blue: 1.0, alpha: 0.96),
        CGColor(red: 1.0, green: 1.0, blue: 1.0, alpha: 0.99),
    ] as CFArray
    if let fGrad = CGGradient(colorsSpace: cs, colors: folderColors, locations: [0.0, 1.0]) {
        ctx.drawLinearGradient(fGrad, start: CGPoint(x: cx, y: fBottom), end: CGPoint(x: cx, y: fTop + tabH), options: [])
    }
    ctx.restoreGState()

    // Crisp storage gauge / check bars inside folder card
    let barX = cx - w * 0.19
    let barW = w * 0.38
    let barH = h * 0.042
    let bar1 = CGRect(x: barX, y: cy + h * 0.015, width: barW, height: barH)
    let bar2 = CGRect(x: barX, y: cy - h * 0.065, width: barW * 0.72, height: barH)
    let bar3 = CGRect(x: barX, y: cy - h * 0.145, width: barW * 0.48, height: barH)

    ctx.setFillColor(CGColor(red: 0.05, green: 0.45, blue: 0.85, alpha: 0.85))
    ctx.addPath(CGPath(roundedRect: bar1, cornerWidth: barH / 2, cornerHeight: barH / 2, transform: nil))
    ctx.fillPath()

    ctx.setFillColor(CGColor(red: 0.07, green: 0.65, blue: 0.75, alpha: 0.85))
    ctx.addPath(CGPath(roundedRect: bar2, cornerWidth: barH / 2, cornerHeight: barH / 2, transform: nil))
    ctx.fillPath()

    ctx.setFillColor(CGColor(red: 0.14, green: 0.78, blue: 0.62, alpha: 0.9))
    ctx.addPath(CGPath(roundedRect: bar3, cornerWidth: barH / 2, cornerHeight: barH / 2, transform: nil))
    ctx.fillPath()

    // Sparkles (top-right & bottom-right inside squircle) symbolizing clean & AI smart sort
    drawSparkle(
        ctx: ctx,
        center: CGPoint(x: cx + w * 0.24, y: cy + h * 0.25),
        radius: w * 0.11,
        color: CGColor(red: 0.56, green: 0.98, blue: 0.86, alpha: 1.0)
    )
    drawSparkle(
        ctx: ctx,
        center: CGPoint(x: cx + w * 0.34, y: cy + h * 0.13),
        radius: w * 0.052,
        color: CGColor(red: 1.0, green: 1.0, blue: 1.0, alpha: 0.95)
    )

    ctx.restoreGState()

    guard let cgImg = ctx.makeImage() else { return }
    let rep = NSBitmapImageRep(cgImage: cgImg)
    if let pngData = rep.representation(using: .png, properties: [:]) {
        try? pngData.write(to: outURL)
    }
}

let outDir = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

let specs: [(String, Int)] = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]

for (name, px) in specs {
    renderIcon(pixelSize: px, outURL: outDir.appendingPathComponent(name))
}
print("Generated iconset at \(outDir.path)")
