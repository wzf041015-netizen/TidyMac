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

func drawStarburst(ctx: CGContext, center: CGPoint, outer: CGFloat, color: CGColor) {
    // Organic 12-ray spark: long/short rays alternate, short rays slightly offset.
    ctx.setStrokeColor(color)
    ctx.setLineCap(.round)
    ctx.setLineWidth(outer * 0.17)
    let inner = outer * 0.26
    for i in 0..<12 {
        let isLong = i % 2 == 0
        let deg = Double(i) * 30.0 + 90.0 - (isLong ? 0 : 7)
        let a = CGFloat(deg * Double.pi / 180.0)
        let r1 = isLong ? outer : outer * 0.74
        ctx.move(to: CGPoint(x: center.x + inner * cos(a), y: center.y + inner * sin(a)))
        ctx.addLine(to: CGPoint(x: center.x + r1 * cos(a), y: center.y + r1 * sin(a)))
    }
    ctx.strokePath()
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

    let inset = s * 0.098
    let iconRect = CGRect(x: inset, y: inset, width: s - inset * 2, height: s - inset * 2)
    let squircle = drawSquirclePath(in: iconRect, radius: iconRect.width * 0.228)

    // Soft ambient shadow
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -s * 0.014), blur: s * 0.035,
                  color: CGColor(red: 0.18, green: 0.12, blue: 0.08, alpha: 0.28))
    ctx.addPath(squircle)
    ctx.setFillColor(CGColor(red: 0.96, green: 0.94, blue: 0.90, alpha: 1))
    ctx.fillPath()
    ctx.restoreGState()

    ctx.saveGState()
    ctx.addPath(squircle)
    ctx.clip()

    // Warm ivory paper gradient
    let bg = [
        CGColor(red: 0.925, green: 0.894, blue: 0.835, alpha: 1),
        CGColor(red: 0.980, green: 0.973, blue: 0.953, alpha: 1),
    ] as CFArray
    if let g = CGGradient(colorsSpace: cs, colors: bg, locations: [0, 1]) {
        ctx.drawLinearGradient(g, start: CGPoint(x: iconRect.midX, y: iconRect.minY),
                               end: CGPoint(x: iconRect.midX, y: iconRect.maxY), options: [])
    }
    // Faint terracotta glow behind the spark
    let glow = [
        CGColor(red: 0.85, green: 0.47, blue: 0.34, alpha: 0.20),
        CGColor(red: 0.85, green: 0.47, blue: 0.34, alpha: 0.0),
    ] as CFArray
    if let g = CGGradient(colorsSpace: cs, colors: glow, locations: [0, 1]) {
        let c = CGPoint(x: iconRect.midX, y: iconRect.midY)
        ctx.drawRadialGradient(g, startCenter: c, startRadius: 0, endCenter: c, endRadius: iconRect.width * 0.5, options: [])
    }

    // Terracotta spark
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -s * 0.006), blur: s * 0.012,
                  color: CGColor(red: 0.45, green: 0.2, blue: 0.1, alpha: 0.25))
    drawStarburst(ctx: ctx, center: CGPoint(x: iconRect.midX, y: iconRect.midY),
                  outer: iconRect.width * 0.33,
                  color: CGColor(red: 0.788, green: 0.392, blue: 0.259, alpha: 1))
    ctx.restoreGState()

    ctx.restoreGState()

    // Hairline rim
    ctx.saveGState()
    ctx.addPath(squircle)
    ctx.setStrokeColor(CGColor(red: 0.45, green: 0.35, blue: 0.25, alpha: 0.12))
    ctx.setLineWidth(max(1, s * 0.004))
    ctx.strokePath()
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
