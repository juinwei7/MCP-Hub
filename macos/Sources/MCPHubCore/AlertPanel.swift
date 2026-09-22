import AppKit
import SwiftUI

/// 系統通知拿不到時的替代品。
///
/// macOS 的 UNUserNotificationCenter 需要穩定的簽章身分 —— ad-hoc 簽章的識別碼
/// 每次重建都會變,系統連權限提示都不會跳(實測:authorizationStatus 直接是
/// denied,而且系統的通知設定裡根本沒有這個 app 的紀錄,不管放在哪個目錄)。
///
/// 但「沒有憑證」不該等於「不提醒」。待確認的票券就是卡在那裡等人,
/// 而使用者不會定時去看選單列。所以自己畫一個。
///
/// 三個刻意的決定:
///   1. 非搶焦點(.nonactivatingPanel)—— 你正在打字時它不該把游標搶走
///   2. 出現在右上角,通知本來會出現的位置 —— 不要發明新的地方讓人找
///   3. 需要決定的提醒不自動消失 —— 一個等你回答的問題自己消失掉,
///      比沒出現更糟,因為你會以為處理過了
@MainActor
final class AlertPanel {
    /// 同時最多疊幾個。再多就變成洗版,反而讓人整片忽略。
    private static let maxVisible = 3
    private static var shown: [AlertPanel] = []

    private let panel: NSPanel
    private var dismissTask: Task<Void, Never>?

    static func show(title: String,
                     body: String,
                     approve: (() -> Void)? = nil,
                     reject: (() -> Void)? = nil,
                     open: (() -> Void)? = nil) {
        // 超過上限就把最舊的收掉,新的比較重要
        while shown.count >= maxVisible { shown.first?.close() }
        let panel = AlertPanel(title: title, body: body,
                               approve: approve, reject: reject, open: open)
        shown.append(panel)
        layout()
        panel.panel.orderFrontRegardless()
        // 聲音是這個方案唯一能「把人從別的視窗拉回來」的手段。
        // 用系統既有的提示音,不自己塞音檔。
        NSSound(named: "Submarine")?.play()
    }

    private init(title: String, body: String,
                 approve: (() -> Void)?, reject: (() -> Void)?, open: (() -> Void)?) {
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 340, height: 10),
                        // nonactivatingPanel:出現時不把焦點從使用者正在做的事上搶走
                        styleMask: [.nonactivatingPanel, .fullSizeContentView, .borderless],
                        backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.level = .statusBar            // 在一般視窗之上,但不蓋住選單列
        panel.hasShadow = true
        panel.isOpaque = false
        panel.backgroundColor = .clear
        // 切換桌面 / 全螢幕 app 時也要看得到 —— 不然它只在某一個空間裡有效
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.hidesOnDeactivate = false

        var me: AlertPanel?
        let view = AlertPanelView(
            title: title, message: body,
            approve: approve.map { action in { action(); me?.close() } },
            reject: reject.map { action in { action(); me?.close() } },
            open: open.map { action in { action(); me?.close() } },
            dismiss: { me?.close() })

        let host = NSHostingView(rootView: view)
        host.frame.size = host.fittingSize
        panel.setContentSize(host.fittingSize)
        panel.contentView = host
        me = self

        // 需要決定的不自動消失 —— 那是一個等你回答的問題,
        // 自己消失掉比沒出現更糟,因為你會以為處理過了。
        if approve == nil {
            dismissTask = Task { [weak self] in
                try? await Task.sleep(for: .seconds(12))
                self?.close()
            }
        }
    }

    private func close() {
        dismissTask?.cancel()
        panel.orderOut(nil)
        Self.shown.removeAll { $0 === self }
        Self.layout()
    }

    /// 由上往下排在右上角。選單列高度用實際的 visibleFrame 算,不寫死。
    private static func layout() {
        guard let screen = NSScreen.main else { return }
        let area = screen.visibleFrame
        var y = area.maxY - 12
        for p in shown {
            let size = p.panel.frame.size
            p.panel.setFrameOrigin(NSPoint(x: area.maxX - size.width - 12,
                                           y: y - size.height))
            y -= size.height + 8
        }
    }
}

/// 面板的內容。沿用 app 的設計語彙 —— 它是 app 的一部分,不是系統元件。
private struct AlertPanelView: View {
    let title: String
    /// 不能叫 body —— 會和 View 的 body 撞名
    let message: String
    let approve: (() -> Void)?
    let reject: (() -> Void)?
    let open: (() -> Void)?
    let dismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.row) {
            HStack(alignment: .top, spacing: Style.Space.row) {
                // 需要決定的用琥珀(在等人),純告知的用強調色
                Circle()
                    .fill(approve == nil ? Palette.accent : Palette.warn)
                    .frame(width: 7, height: 7)
                    .padding(.top, 5)

                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(Style.Face.rowTitle)
                        .foregroundStyle(Palette.ink)
                    Text(message)
                        .font(Style.Face.body)
                        .foregroundStyle(Palette.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)

                Button(action: dismiss) {
                    Image(systemName: "xmark")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundStyle(Palette.ink3)
                }
                .buttonStyle(.plain)
            }

            if approve != nil || open != nil {
                HStack(spacing: Style.Space.tight) {
                    Spacer()
                    if let reject {
                        Button("拒絕", action: reject)
                    }
                    if let approve {
                        Button("核准", action: approve).buttonStyle(.borderedProminent)
                    }
                    if let open {
                        Button("開啟", action: open)
                    }
                }
                .controlSize(.small)
            }
        }
        .padding(Style.Space.section)
        .frame(width: 340)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Palette.surface)
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .stroke(Palette.line, lineWidth: 1)))
        .tint(Palette.accent)
    }
}
