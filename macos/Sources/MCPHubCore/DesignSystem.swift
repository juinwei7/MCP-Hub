import AppKit
import SwiftUI

/// 全 app 共用的視覺語彙。
///
/// 這是基礎設施的監控面板,不是文件工具。它平常坐在那裡,使用者偶爾瞥一眼想知道
/// 「管線通不通」。所以排序是:狀態 → 控制 → 內容。
///
/// 四個決定貫穿整份設計:
///   1. 狀態是列首的色條,不是浮空的圓點 —— 掃一眼就知道哪幾條是通的
///   2. 等寬字代表「機器產生的值」(網址、slug、耗時),系統字代表我寫的標籤
///   3. 一切正常時不顯示摘要 —— 永遠掛著「0 異常」是噪音
///   4. 顏色只表達狀態。強調色刻意避開綠色,只出現在可互動的東西上

// ── 顏色 ──────────────────────────────────────────────────
extension Color {
    /// 深色模式各有一組值,不是把淺色反轉 —— 反轉會讓語意色在深底上失去區辨度。
    static func adaptive(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return NSColor(rgb: isDark ? dark : light)
        })
    }
}

extension NSColor {
    convenience init(rgb: UInt32) {
        self.init(srgbRed: CGFloat((rgb >> 16) & 0xFF) / 255,
                  green: CGFloat((rgb >> 8) & 0xFF) / 255,
                  blue: CGFloat(rgb & 0xFF) / 255,
                  alpha: 1)
    }
}

enum Palette {
    // 冷調石墨,偏藍綠 —— 這是網路工具,不是文件工具
    static let ground = Color.adaptive(0xF4F7F7, 0x0E1315)
    static let surface = Color.adaptive(0xFFFFFF, 0x161D20)
    static let sunken = Color.adaptive(0xECF1F1, 0x11171A)

    static let ink = Color.adaptive(0x141A1C, 0xE7EEF0)
    static let ink2 = Color.adaptive(0x4A585C, 0x9EACB0)
    static let ink3 = Color.adaptive(0x7C8A8E, 0x6F7D81)

    static let line = Color.adaptive(0xDCE4E5, 0x232D31)
    static let lineSoft = Color.adaptive(0xE9EFEF, 0x1B2326)

    /// 訊號 / 連線的意象。只用在可互動的東西上,不當裝飾。
    static let accent = Color.adaptive(0x0D7482, 0x47B6C4)

    // 語意色 —— 只表達狀態
    static let ok = Color.adaptive(0x2C7A52, 0x58B183)
    static let warn = Color.adaptive(0x9E6208, 0xD0952F)
    static let down = Color.adaptive(0xB03A2C, 0xE0705E)
    static let off = Color.adaptive(0xA3AEB1, 0x5A676B)
}

// ── 尺度 ──────────────────────────────────────────────────
enum Style {
    enum Space {
        static let hair: CGFloat = 2
        static let tight: CGFloat = 5
        static let row: CGFloat = 9
        static let section: CGFloat = 16
        static let block: CGFloat = 24
    }

    static let radius: CGFloat = 6
    static let rowHeight: CGFloat = 42

    /// 字級。等寬的用在機器產生的值上。
    enum Face {
        static let sectionTitle = Font.system(size: 15, weight: .semibold)
        static let rowTitle = Font.system(size: 13, weight: .semibold)
        static let body = Font.system(size: 13)
        static let meta = Font.system(size: 11)
        static let mono = Font.system(size: 11, design: .monospaced)
        static let monoBody = Font.system(size: 12.5, design: .monospaced)
        static let number = Font.system(size: 11.5, design: .monospaced)
    }
}

// ── 狀態 ──────────────────────────────────────────────────
enum Health {
    case ok, warn, down, off

    var color: Color {
        switch self {
        case .ok: return Palette.ok
        case .warn: return Palette.warn
        case .down: return Palette.down
        case .off: return Palette.off
        }
    }

    /// 停用的東西不給顏色 —— 它不是壞掉,不該和異常同一個視覺層級。
    var railColor: Color { self == .off ? .clear : color }
}

/// 列首的狀態色條。貼齊前緣,像混音台的通道條。
struct StatusRail: View {
    let health: Health

    var body: some View {
        Rectangle()
            .fill(health.railColor)
            .frame(width: 3)
    }
}

// ── 列 ────────────────────────────────────────────────────
/// 清單的一列。所有清單共用同一個骨架,才不會變成七個長得不一樣的畫面。
struct HubRow<Trailing: View>: View {
    let health: Health
    let title: String
    let detail: String
    /// detail 是機器產生的值(網址、指令)還是人看的訊息(錯誤原因)
    var detailIsMachine: Bool = true
    var dimmed: Bool = false
    /// 右側的計數。數字與單位分開 —— 中文在等寬字裡是全形,混在一起會撐出空隙。
    var count: (value: String, unit: String)?
    @ViewBuilder var trailing: Trailing

    @State private var hovering = false

    var body: some View {
        HStack(spacing: 0) {
            StatusRail(health: health)

            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(dimmed ? Style.Face.body : Style.Face.rowTitle)
                    .foregroundStyle(dimmed ? Palette.ink3 : Palette.ink)
                Text(detail)
                    .font(detailIsMachine ? Style.Face.mono : Style.Face.meta)
                    .foregroundStyle(health == .down ? Palette.down : Palette.ink3)
                    .lineLimit(1).truncationMode(.middle)
            }
            .padding(.leading, 13)
            .padding(.vertical, 7)

            Spacer(minLength: Style.Space.section)

            if let count {
                HStack(spacing: 3) {
                    Text(count.value)
                        .font(Style.Face.number).monospacedDigit()
                    Text(count.unit).font(Style.Face.meta)
                }
                .foregroundStyle(Palette.ink3)
            }

            HStack(spacing: Style.Space.row) {
                trailing
            }
            .padding(.leading, 13)
            .padding(.trailing, 14)
        }
        .frame(minHeight: Style.rowHeight)
        .background(hovering ? Palette.sunken.opacity(0.6) : Color.clear)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Palette.lineSoft).frame(height: 1)
        }
        .onHover { hovering = $0 }
        .environment(\.rowHovering, hovering)
    }
}

/// 只在滑過該列時才顯示的動作 —— 靜止狀態保持乾淨。
struct RowActions<Content: View>: View {
    @Environment(\.rowHovering) private var hovering
    @ViewBuilder var content: Content

    var body: some View {
        content
            .opacity(hovering ? 1 : 0)
            .allowsHitTesting(hovering)
    }
}

private struct RowHoveringKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    var rowHovering: Bool {
        get { self[RowHoveringKey.self] }
        set { self[RowHoveringKey.self] = newValue }
    }
}

/// 狀態開關。用專案的強調色 —— 系統藍在這個冷調面板裡是唯一不屬於這裡的顏色。
struct HubSwitch: View {
    @Binding var isOn: Bool

    var body: some View {
        Toggle("", isOn: $isOn)
            .toggleStyle(.switch)
            .controlSize(.mini)
            .labelsHidden()
            .tint(Palette.accent)
    }
}

/// 列上的「更多」選單。把 SwiftUI 預設的下拉箭頭關掉 —— 那個箭頭看起來像瑕疵。
struct RowMenu<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        Menu {
            content
        } label: {
            Image(systemName: "ellipsis")
                .foregroundStyle(Palette.ink3)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
    }
}

// ── 訊息 ──────────────────────────────────────────────────
/// 需要注意的事才浮上來,而且直接帶修復動作。
///
/// 一切正常時什麼都不顯示 —— 永遠掛著「0 異常」是噪音,側邊欄的計數已經
/// 回答了「有幾個」,內容區只負責回答「要不要處理」。
struct AlertLine: View {
    enum Kind { case problem, success, info }

    let text: String
    var kind: Kind = .problem
    var actionTitle: String?
    var action: (() -> Void)?
    var onDismiss: (() -> Void)?

    var body: some View {
        HStack(spacing: Style.Space.row) {
            Image(systemName: icon).font(.system(size: 11))
            Text(text).lineLimit(2).textSelection(.enabled)
            Spacer(minLength: Style.Space.section)
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .buttonStyle(.plain)
                    .font(.system(size: 12.5, weight: .medium))
                    .underline()
            }
            if let onDismiss {
                Button(action: onDismiss) {
                    Image(systemName: "xmark").font(.system(size: 9, weight: .medium))
                }
                .buttonStyle(.plain)
            }
        }
        .font(.system(size: 12.5))
        .foregroundStyle(tint)
        .padding(.horizontal, 18)
        .padding(.vertical, Style.Space.row)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint.opacity(0.09))
        .overlay(alignment: .bottom) {
            Rectangle().fill(Palette.lineSoft).frame(height: 1)
        }
    }

    private var icon: String {
        switch kind {
        case .problem: return "exclamationmark.triangle.fill"
        case .success: return "checkmark.circle.fill"
        case .info: return "info.circle"
        }
    }

    private var tint: Color {
        switch kind {
        case .problem: return Palette.down
        case .success: return Palette.ok
        case .info: return Palette.ink2
        }
    }
}

/// 空狀態說「接下來能做什麼」,不是只說「沒有東西」。
struct EmptyState: View {
    let icon: String
    let title: String
    var hint: String = ""
    var actionTitle: String?
    var action: (() -> Void)?

    var body: some View {
        VStack(spacing: Style.Space.row) {
            Image(systemName: icon)
                .font(.system(size: 26, weight: .light))
                .foregroundStyle(Palette.ink3.opacity(0.7))
            Text(title).font(Style.Face.sectionTitle)
            if !hint.isEmpty {
                Text(hint)
                    .font(Style.Face.body)
                    .foregroundStyle(Palette.ink2)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 360)
                    .lineSpacing(2)
            }
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .buttonStyle(.borderedProminent)
                    .padding(.top, Style.Space.tight)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(Style.Space.block)
    }
}

// ── 版面元件 ──────────────────────────────────────────────
/// 內容區頂端的標題列:區域名稱 + 該區的動作。
struct SectionBar<Actions: View>: View {
    let title: String
    @ViewBuilder var actions: Actions

    var body: some View {
        HStack(spacing: Style.Space.row) {
            Text(title).font(Style.Face.sectionTitle)
            Spacer()
            actions
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 13)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Palette.lineSoft).frame(height: 1)
        }
    }
}

/// 底部狀態列。放「最後檢查時間」這類次要資訊,不放動作。
struct StatusBar<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        HStack(spacing: Style.Space.row) {
            content
        }
        .font(Style.Face.meta)
        .foregroundStyle(Palette.ink3)
        .padding(.horizontal, 18)
        .padding(.vertical, Style.Space.row)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .top) {
            Rectangle().fill(Palette.lineSoft).frame(height: 1)
        }
    }
}

/// 小標籤。表達「這是什麼種類」,例如 GET / POST、stdio / http。
struct Pill: View {
    let text: String
    var tone: Tone = .neutral

    enum Tone { case neutral, accent, warn, danger }

    var body: some View {
        Text(text)
            .font(.system(size: 10, weight: .medium))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.14))
            .foregroundStyle(color)
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private var color: Color {
        switch tone {
        case .neutral: return Palette.ink3
        case .accent: return Palette.accent
        case .warn: return Palette.warn
        case .danger: return Palette.down
        }
    }
}

// ── 表單 ──────────────────────────────────────────────────
struct FormRow<Content: View>: View {
    let label: String
    var hint: String = ""
    @ViewBuilder var content: Content

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Style.Space.section) {
            VStack(alignment: .trailing, spacing: 1) {
                Text(label).font(Style.Face.body).foregroundStyle(Palette.ink2)
                if !hint.isEmpty {
                    Text(hint).font(.system(size: 10)).foregroundStyle(Palette.ink3)
                }
            }
            .frame(width: 84, alignment: .trailing)

            content
        }
    }
}

/// 底部操作列。破壞性動作靠左且視覺上分開 —— 不該和「儲存」並排。
struct ActionBar<Leading: View, Trailing: View>: View {
    @ViewBuilder var leading: Leading
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(spacing: Style.Space.row) {
            leading
            Spacer()
            trailing
        }
        .padding(.horizontal, 14)
        .padding(.vertical, Style.Space.row)
        .overlay(alignment: .top) {
            Rectangle().fill(Palette.lineSoft).frame(height: 1)
        }
    }
}

struct CodeEditor: View {
    let placeholder: String
    @Binding var text: String
    var minHeight: CGFloat = 88

    var body: some View {
        ZStack(alignment: .topLeading) {
            if text.isEmpty {
                Text(placeholder)
                    .font(Style.Face.mono)
                    .foregroundStyle(Palette.ink3.opacity(0.7))
                    .padding(.horizontal, 6).padding(.vertical, 9)
                    .allowsHitTesting(false)
            }
            TextEditor(text: $text)
                .font(Style.Face.monoBody)
                .scrollContentBackground(.hidden)
        }
        .frame(minHeight: minHeight)
        .padding(3)
        .background(Palette.sunken)
        .overlay(RoundedRectangle(cornerRadius: Style.radius).strokeBorder(Palette.line))
        .clipShape(RoundedRectangle(cornerRadius: Style.radius))
    }
}

// 舊名保留,讓既有畫面不用一次全改
typealias Banner = AlertLine
