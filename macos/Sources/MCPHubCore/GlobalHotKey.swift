import AppKit
import Carbon.HIToolbox

/// 全域快捷鍵 —— 不用開視窗、不用找選單列圖示就能切換暫停。
///
/// 用 Carbon 的 RegisterEventHotKey 而不是 NSEvent 的全域監聽:後者要「輔助使用」
/// 權限(使用者得去系統設定裡勾),而且只能旁聽、攔不住 —— 焦點 app 還是會收到
/// 那個按鍵。Carbon 這條路不需要任何權限,而且會把按鍵吃掉。API 很老,但至今
/// 沒有被取代的替代品。
@MainActor
final class GlobalHotKey {
    static let shared = GlobalHotKey()

    /// ⌃⌥⌘P。刻意用到三個修飾鍵 —— 全域快捷鍵是從「每一個」app 手上把這個組合
    /// 搶走,兩個修飾鍵的組合(⌥⌘P 在 Finder 是顯示路徑列)一定會踩到別人。
    static let displayName = "⌃⌥⌘P"
    private static let keyCode = UInt32(kVK_ANSI_P)
    private static let modifiers = UInt32(controlKey | optionKey | cmdKey)

    private static let signature: OSType = 0x4D435048   // 'MCPH'

    private var hotKey: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private var onFire: (() -> Void)?

    private init() {}

    var isRegistered: Bool { hotKey != nil }

    /// 回 nil 代表成功,否則是可以顯示給使用者看的原因。
    @discardableResult
    func register(_ action: @escaping () -> Void) -> String? {
        unregister()
        onFire = action

        if handler == nil {
            var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                     eventKind: UInt32(kEventHotKeyPressed))
            // callback 是 C 函式指標,不能捕捉環境 —— 只好經由 shared 轉一手。
            let installed = InstallEventHandler(GetApplicationEventTarget(), { _, _, _ -> OSStatus in
                DispatchQueue.main.async { GlobalHotKey.shared.fire() }
                return noErr
            }, 1, &spec, nil, &handler)
            if installed != noErr {
                onFire = nil
                return "無法安裝快捷鍵處理器(錯誤 \(installed))。"
            }
        }

        var ref: EventHotKeyRef?
        let id = EventHotKeyID(signature: Self.signature, id: 1)
        let status = RegisterEventHotKey(Self.keyCode, Self.modifiers, id,
                                         GetApplicationEventTarget(), 0, &ref)
        guard status == noErr, ref != nil else {
            onFire = nil
            // 最常見的原因是別的 app 先搶走了同一個組合,講清楚比只說「失敗」有用
            return "\(Self.displayName) 註冊失敗,可能已被其他 app 佔用(錯誤 \(status))。"
        }
        hotKey = ref
        return nil
    }

    func unregister() {
        if let hotKey {
            UnregisterEventHotKey(hotKey)
            self.hotKey = nil
        }
        onFire = nil
    }

    private func fire() {
        onFire?()
    }
}
