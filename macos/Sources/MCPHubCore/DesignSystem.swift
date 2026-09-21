import SwiftUI

/// 全 app 共用的視覺語彙。
///
/// 抽出來是因為接下來要加的畫面(自訂工具、複合工具、設定)比原本的四個分頁
/// 複雜得多 —— 表單、清單、測試結果、匯入預覽。每個畫面各自決定間距與樣式的話,
/// 很快就會變成「看起來像同一個 app 的七個畫面」而不是一個 app。
///
/// 原則:
///   · 狀態靠形狀與顏色同時表達,不只靠顏色(色盲可用性,也讓截圖看得懂)
///   · 破壞性操作要在視覺上就和一般操作分開
///   · 空狀態要說「接下來能做什麼」,不是只說「沒有東西」
enum Style {
    /// 垂直節奏。整個 app 只用這幾個值,不臨時湊數字。
    enum Space {
        static let tight: CGFloat = 4
        static let row: CGFloat = 8
        static let section: CGFloat = 16
        static let block: CGFloat = 24
    }

    static let cornerRadius: CGFloat = 6
    static let monoFont = Font.system(.body, design: .monospaced)
    static let monoCaption = Font.system(.caption, design: .monospaced)
}

// ── 狀態指示 ──────────────────────────────────────────────

/// 一個帶形狀的狀態點。顏色說「好或壞」,形狀說「是什麼狀態」。
struct StatusDot: View {
    enum Kind { case ok, warn, bad, off }

    let kind: Kind
    var help: String = ""

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 9, height: 9)
            .overlay(Circle().strokeBorder(color.opacity(0.35), lineWidth: 3))
            .help(help)
    }

    private var color: Color {
        switch kind {
        case .ok: return .green
        case .warn: return .orange
        case .bad: return .red
        case .off: return .secondary.opacity(0.4)
        }
    }
}

/// 小標籤。用來表達「這個東西的種類」,例如 GET / POST、stdio / http。
struct Pill: View {
    let text: String
    var tone: Tone = .neutral

    enum Tone { case neutral, accent, warn, danger }

    var body: some View {
        Text(text)
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(background)
            .foregroundStyle(foreground)
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private var background: Color {
        switch tone {
        case .neutral: return .secondary.opacity(0.15)
        case .accent: return .accentColor.opacity(0.15)
        case .warn: return .orange.opacity(0.15)
        case .danger: return .red.opacity(0.15)
        }
    }

    private var foreground: Color {
        switch tone {
        case .neutral: return .secondary
        case .accent: return .accentColor
        case .warn: return .orange
        case .danger: return .red
        }
    }
}

// ── 訊息 ──────────────────────────────────────────────────

/// 橫幅訊息。用在「動作的結果」,不用彈跳視窗 —— 下游連不上是這個工具的日常,
/// 彈跳視窗會變成噪音。
struct Banner: View {
    enum Kind { case error, success, info }

    let text: String
    var kind: Kind = .error
    var onDismiss: (() -> Void)?

    var body: some View {
        HStack(spacing: Style.Space.row) {
            Image(systemName: icon)
            Text(text).lineLimit(3).textSelection(.enabled)
            Spacer()
            if let onDismiss {
                Button(action: onDismiss) { Image(systemName: "xmark") }
                    .buttonStyle(.borderless)
                    .help("關閉")
            }
        }
        .font(.caption)
        .foregroundStyle(tint)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(tint.opacity(0.08))
    }

    private var icon: String {
        switch kind {
        case .error: return "exclamationmark.triangle.fill"
        case .success: return "checkmark.circle.fill"
        case .info: return "info.circle"
        }
    }

    private var tint: Color {
        switch kind {
        case .error: return .red
        case .success: return .green
        case .info: return .secondary
        }
    }
}

/// 空狀態。說明「接下來能做什麼」,而不是只說「沒有東西」——
/// 後者讓人卡住,前者讓人知道下一步。
struct EmptyState: View {
    let icon: String
    let title: String
    var hint: String = ""
    var actionTitle: String?
    var action: (() -> Void)?

    var body: some View {
        VStack(spacing: Style.Space.row) {
            Image(systemName: icon)
                .font(.system(size: 28))
                .foregroundStyle(.tertiary)
            Text(title).font(.headline)
            if !hint.isEmpty {
                Text(hint)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 380)
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

// ── 表單 ──────────────────────────────────────────────────

/// 一列表單欄位。標籤靠右對齊、寬度固定,讓多個欄位的輸入框對齊成一條線。
struct FormRow<Content: View>: View {
    let label: String
    var hint: String = ""
    @ViewBuilder var content: Content

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Style.Space.section) {
            VStack(alignment: .trailing, spacing: 2) {
                Text(label).foregroundStyle(.secondary)
                if !hint.isEmpty {
                    Text(hint).font(.caption2).foregroundStyle(.tertiary)
                }
            }
            .frame(width: 96, alignment: .trailing)

            content
        }
    }
}

/// 底部的操作列。主要動作靠右,破壞性動作靠左且視覺上分開 ——
/// 「刪除」不該和「儲存」並排,那讓誤按變容易。
struct ActionBar<Leading: View, Trailing: View>: View {
    @ViewBuilder var leading: Leading
    @ViewBuilder var trailing: Trailing

    var body: some View {
        VStack(spacing: 0) {
            Divider()
            HStack(spacing: Style.Space.row) {
                leading
                Spacer()
                trailing
            }
            .padding(Style.Space.row)
        }
    }
}

/// 可捲動的程式碼 / JSON 輸入框。
struct CodeEditor: View {
    let placeholder: String
    @Binding var text: String
    var minHeight: CGFloat = 90

    var body: some View {
        ZStack(alignment: .topLeading) {
            if text.isEmpty {
                Text(placeholder)
                    .font(Style.monoCaption)
                    .foregroundStyle(.tertiary)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 8)
                    .allowsHitTesting(false)
            }
            TextEditor(text: $text)
                .font(Style.monoFont)
                .scrollContentBackground(.hidden)
        }
        .frame(minHeight: minHeight)
        .padding(2)
        .background(Color(nsColor: .textBackgroundColor))
        .overlay(
            RoundedRectangle(cornerRadius: Style.cornerRadius)
                .strokeBorder(Color.secondary.opacity(0.25))
        )
        .clipShape(RoundedRectangle(cornerRadius: Style.cornerRadius))
    }
}
