import AppKit
import CoreGraphics
import Foundation

// MCP Hub 的識別標記。
//
// 概念來自產品本身:多條下游進來,匯成一條出去。而介面裡早就用「列首的色條」
// 表達狀態 —— 圖示用同一個語彙,右邊那條豎槓就是那道色條。
//
// 不用插畫、不用擬物:這是基礎設施,它該看起來像配線盤上的標示,
// 不是像一個會說話的吉祥物。
//
// 幾何全部以 1024 為基準等比縮放,所以任何尺寸都是同一張圖,不是縮小後糊掉的點陣。

enum Mark {
    // 冷調石墨,和 app 的 ground 同一族
    static let bgTop = CGColor(srgbRed: 0.098, green: 0.129, blue: 0.145, alpha: 1)   // #192124
    static let bgBottom = CGColor(srgbRed: 0.043, green: 0.063, blue: 0.075, alpha: 1) // #0B1013

    // 強調色。和 app 的 accent 同一個 —— 圖示和介面該是同一個東西
    static let rail = CGColor(srgbRed: 0.278, green: 0.714, blue: 0.769, alpha: 1)    // #47B6C4
    // 進來的線比出去的暗:視覺上先讀到「匯流的終點」
    static let feed = CGColor(srgbRed: 0.416, green: 0.549, blue: 0.588, alpha: 1)    // #6A8C96

    static func draw(in ctx: CGContext, size: CGFloat, squircle: Bool) {
        let k = size / 1024.0
        func s(_ v: CGFloat) -> CGFloat { v * k }

        ctx.setShouldAntialias(true)
        ctx.interpolationQuality = .high

        // ── 底 ──────────────────────────────────────────
        // macOS 的圖示要留邊(Apple 的模板是 1024 畫布裡 824 的圓角方形),
        // Windows 則是滿版 —— 兩邊的系統各自會再裁切
        let inset: CGFloat = squircle ? s(100) : 0
        let box = CGRect(x: inset, y: inset, width: size - inset * 2, height: size - inset * 2)
        let radius = squircle ? s(185) : s(120)
        let path = CGPath(roundedRect: box, cornerWidth: radius, cornerHeight: radius,
                          transform: nil)

        ctx.saveGState()
        ctx.addPath(path)
        ctx.clip()
        let space = CGColorSpaceCreateDeviceRGB()
        if let gradient = CGGradient(colorsSpace: space,
                                     colors: [bgTop, bgBottom] as CFArray,
                                     locations: [0, 1]) {
            ctx.drawLinearGradient(gradient,
                                   start: CGPoint(x: 0, y: box.maxY),
                                   end: CGPoint(x: 0, y: box.minY),
                                   options: [])
        }
        ctx.restoreGState()

        // ── 標記 ────────────────────────────────────────
        //
        // 第一版是三條橫線配一條豎線。讀得清楚,但那個形狀就是文書軟體的
        // 「文字對齊」圖示 —— 和產品無關,而且和一個每個人都認得的東西撞臉。
        //
        // 改成真的匯流:三條線從左邊不同高度進來,收束到一個節點,
        // 再以一條出去。形狀本身就是這個產品在做的事。
        let stroke = s(74)
        ctx.setLineCap(.round)
        ctx.setLineJoin(.round)

        let node = CGPoint(x: s(596), y: s(512))

        // 進來的三條比出去的暗 —— 讓視線先落在匯流的結果上
        ctx.setStrokeColor(feed)
        ctx.setLineWidth(stroke)
        for y in [s(286), s(512), s(738)] {
            ctx.move(to: CGPoint(x: s(222), y: y))
            ctx.addLine(to: node)
        }
        ctx.strokePath()

        // 出去的那一條:粗一點、亮一點,而且用 app 的強調色
        ctx.setStrokeColor(rail)
        ctx.setLineWidth(s(94))
        ctx.move(to: node)
        ctx.addLine(to: CGPoint(x: s(812), y: s(512)))
        ctx.strokePath()
    }

    static func png(size: Int, squircle: Bool) -> Data {
        let w = size, h = size
        let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8,
                            bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        draw(in: ctx, size: CGFloat(size), squircle: squircle)
        let image = ctx.makeImage()!
        let rep = NSBitmapImageRep(cgImage: image)
        return rep.representation(using: .png, properties: [:])!
    }
}

// ── 產出 ────────────────────────────────────────────────
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "design/out"
try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)

// macOS 的 iconset:每個尺寸都重畫,不是把 1024 縮小
let iconset = "\(out)/MCPHub.iconset"
try? FileManager.default.createDirectory(atPath: iconset, withIntermediateDirectories: true)
for (size, name) in [(16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
                     (128, "128x128"), (256, "128x128@2x"), (256, "256x256"),
                     (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x")] {
    try! Mark.png(size: size, squircle: true)
        .write(to: URL(fileURLWithPath: "\(iconset)/icon_\(name).png"))
}

// Windows 的 .ico:Vista 之後可以直接內嵌 PNG,不必轉成 BMP
let icoSizes = [16, 24, 32, 48, 64, 128, 256]
var entries: [(Int, Data)] = icoSizes.map { ($0, Mark.png(size: $0, squircle: false)) }
var ico = Data()
func u16(_ v: Int) -> Data { withUnsafeBytes(of: UInt16(v).littleEndian) { Data($0) } }
func u32(_ v: Int) -> Data { withUnsafeBytes(of: UInt32(v).littleEndian) { Data($0) } }
ico += u16(0) + u16(1) + u16(entries.count)          // reserved, type=icon, count
var offset = 6 + entries.count * 16
for (size, data) in entries {
    ico += Data([UInt8(size == 256 ? 0 : size), UInt8(size == 256 ? 0 : size), 0, 0])
    ico += u16(1) + u16(32)                           // planes, bpp
    ico += u32(data.count) + u32(offset)
    offset += data.count
}
for (_, data) in entries { ico += data }
try! ico.write(to: URL(fileURLWithPath: "\(out)/MCPHub.ico"))

try! Mark.png(size: 1024, squircle: true).write(to: URL(fileURLWithPath: "\(out)/preview-1024.png"))

// 小尺寸對照表。圖示真正被看到的地方是 16(選單列)到 48(工作列),
// 在 1024 上好看不代表在那裡還認得出來 —— 用最近鄰放大,看到的就是真實的像素。
do {
    let sizes = [16, 24, 32, 48, 64]
    let scale = 6
    let pad = 12
    let w = sizes.reduce(0) { $0 + $1 * scale + pad } + pad
    let h = 64 * scale + pad * 2
    let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpaceCreateDeviceRGB(),
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.setFillColor(CGColor(gray: 0.5, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
    ctx.interpolationQuality = .none   // 最近鄰:看真正的像素,不要被平滑化騙
    var x = pad
    for size in sizes {
        let small = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8,
                              bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        Mark.draw(in: small, size: CGFloat(size), squircle: true)
        let img = small.makeImage()!
        let side = size * scale
        ctx.draw(img, in: CGRect(x: x, y: h - pad - side, width: side, height: side))
        x += side + pad
    }
    let rep = NSBitmapImageRep(cgImage: ctx.makeImage()!)
    try! rep.representation(using: .png, properties: [:])!
        .write(to: URL(fileURLWithPath: "\(out)/preview-small.png"))
}
print("已產出 \(out)")
