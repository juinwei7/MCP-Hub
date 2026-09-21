namespace MCPHub.Core;

/// <summary>
/// 決定「該不該通知」的純邏輯 —— macOS 版 NotificationGate.swift 的對應實作。
///
/// 特意與系統 API 分開:通知能不能送出取決於作業系統與使用者授權,那是環境問題;
/// 但「同一筆票券不該通知兩次」是規則問題,必須能被測試。混在一起的話,唯一的
/// 驗證方式就是盯著螢幕看通知有沒有跳 —— 那既不可重複,也測不到「第二次不該跳」
/// 這種否定條件。
///
/// 規則與 macOS 版完全一致,連測試都是對照移植的。兩個平台的通知行為若在這裡
/// 分岔,使用者換平台時的體驗就會不一樣,而那種差異通常是無意造成的。
/// </summary>
public sealed class NotificationGate
{
    /// <param name="ActionId">有值代表是待確認票券,通知上要附核准 / 拒絕按鈕。</param>
    public sealed record Item(string Id, string Title, string Body, string? ActionId);

    private HashSet<string> _notifiedActions = new(StringComparer.Ordinal);
    private HashSet<string> _erroredServers = new(StringComparer.Ordinal);

    public bool Primed { get; private set; }

    /// <summary>
    /// 第一次載入時只記錄現況,不產生通知 —— 否則開 app 就被既有狀態洗版。
    /// </summary>
    public void Prime(IEnumerable<HubClient.HubAction> actions,
                      IEnumerable<HubClient.Server> servers)
    {
        _notifiedActions = WaitingIds(actions);
        _erroredServers = ErroredSlugs(servers);
        Primed = true;
    }

    /// <summary>
    /// 回傳這一輪該送出的通知。呼叫後內部狀態即更新,同樣的輸入再呼叫一次會回空清單。
    /// </summary>
    public IReadOnlyList<Item> Evaluate(IReadOnlyList<HubClient.HubAction> actions,
                                        IReadOnlyList<HubClient.Server> servers)
    {
        if (!Primed)
        {
            Prime(actions, servers);
            return Array.Empty<Item>();
        }

        var result = new List<Item>();

        var waiting = WaitingIds(actions);
        foreach (var a in actions)
        {
            if (a.IsWaiting && !_notifiedActions.Contains(a.Id))
            {
                result.Add(new Item($"action.{a.Id}", "有工具需要你確認", a.Tool, a.Id));
            }
        }
        // 已離開待確認的就忘掉 —— 萬一同一個 id 之後又回到 WAITING,應該要再通知
        _notifiedActions = waiting;

        var nowErrored = ErroredSlugs(servers);
        foreach (var slug in nowErrored.Except(_erroredServers).OrderBy(s => s, StringComparer.Ordinal))
        {
            var s = servers.FirstOrDefault(x => x.Slug == slug);
            if (s is null) continue;
            var body = string.IsNullOrEmpty(s.StatusDetail) ? "連線失敗" : s.StatusDetail;
            result.Add(new Item($"server.{slug}", $"下游異常:{s.Name}", body, null));
        }
        _erroredServers = nowErrored;

        return result;
    }

    private static HashSet<string> WaitingIds(IEnumerable<HubClient.HubAction> actions) =>
        actions.Where(a => a.IsWaiting).Select(a => a.Id).ToHashSet(StringComparer.Ordinal);

    /// <summary>只看啟用中的下游 —— 使用者自己停用的不該跳通知說它壞了。</summary>
    private static HashSet<string> ErroredSlugs(IEnumerable<HubClient.Server> servers) =>
        servers.Where(s => s.Enabled && s.IsErrored)
               .Select(s => s.Slug).ToHashSet(StringComparer.Ordinal);
}
