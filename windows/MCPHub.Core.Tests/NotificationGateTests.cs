using MCPHub.Core;
using Xunit;

namespace MCPHub.Core.Tests;

/// <summary>
/// 通知的去重規則 —— 由 macOS 版的 NotificationGateTests.swift 對照移植。
///
/// 刻意保持一對一:兩個平台的通知行為若在這裡分岔,使用者換平台時體驗就不一樣,
/// 而那種差異通常是無意造成的。哪天規則要改,兩邊的測試會一起提醒你。
/// </summary>
public class NotificationGateTests
{
    private static HubClient.HubAction Action(
        string id, string tool = "srv__tool", string status = "WAITING") =>
        new(id, tool, status, "2026-09-21T10:00:00", "");

    private static HubClient.Server Server(
        string slug, string status = "ok", bool enabled = true, string detail = "") =>
        new(slug, slug, "http://x/mcp", "http", "none", enabled, status, detail, false);

    private static readonly HubClient.HubAction[] NoActions = Array.Empty<HubClient.HubAction>();
    private static readonly HubClient.Server[] NoServers = Array.Empty<HubClient.Server>();

    // ── 首次載入 ──────────────────────────────────────────

    [Fact]
    public void FirstEvaluateOnlyPrimesAndNotifiesNothing()
    {
        // 開 app 時不該被既有的待確認與異常洗版
        var gate = new NotificationGate();
        var result = gate.Evaluate(
            new[] { Action("a1"), Action("a2") },
            new[] { Server("s1", status: "error") });

        Assert.Empty(result);
        Assert.True(gate.Primed);
    }

    // ── 待確認票券 ────────────────────────────────────────

    [Fact]
    public void NewActionNotifiesOnce()
    {
        var gate = new NotificationGate();
        gate.Prime(NoActions, NoServers);

        var first = gate.Evaluate(new[] { Action("a1", "srv__drop_table") }, NoServers);
        Assert.Single(first);
        Assert.Equal("a1", first[0].ActionId);
        Assert.Equal("srv__drop_table", first[0].Body);

        var second = gate.Evaluate(new[] { Action("a1", "srv__drop_table") }, NoServers);
        Assert.Empty(second);
    }

    [Fact]
    public void DecidedActionStopsNotifying()
    {
        var gate = new NotificationGate();
        gate.Prime(NoActions, NoServers);
        gate.Evaluate(new[] { Action("a1") }, NoServers);

        var after = gate.Evaluate(new[] { Action("a1", status: "APPROVED") }, NoServers);
        Assert.Empty(after);
    }

    [Fact]
    public void SameIdReturningToWaitingNotifiesAgain()
    {
        // 票券離開 WAITING 後就該被忘掉,否則萬一同一個 id 再次待確認就會靜默
        var gate = new NotificationGate();
        gate.Prime(NoActions, NoServers);
        gate.Evaluate(new[] { Action("a1") }, NoServers);
        gate.Evaluate(new[] { Action("a1", status: "REJECTED") }, NoServers);

        var again = gate.Evaluate(new[] { Action("a1") }, NoServers);
        Assert.Single(again);
    }

    [Fact]
    public void MultipleNewActionsEachNotify()
    {
        var gate = new NotificationGate();
        gate.Prime(NoActions, NoServers);

        var result = gate.Evaluate(
            new[] { Action("a1"), Action("a2"), Action("a3") }, NoServers);

        Assert.Equal(new[] { "a1", "a2", "a3" },
                     result.Select(i => i.ActionId).OrderBy(x => x));
    }

    // ── 下游健康 ──────────────────────────────────────────

    [Fact]
    public void ServerGoingErroredNotifiesOnce()
    {
        var gate = new NotificationGate();
        gate.Prime(NoActions, new[] { Server("s1") });

        var first = gate.Evaluate(NoActions,
                                  new[] { Server("s1", status: "error", detail: "連不上") });
        Assert.Single(first);
        Assert.Equal("連不上", first[0].Body);
        Assert.Null(first[0].ActionId);   // 下游通知沒有核准按鈕

        var second = gate.Evaluate(NoActions,
                                   new[] { Server("s1", status: "error", detail: "連不上") });
        Assert.Empty(second);
    }

    [Fact]
    public void RecoveredThenErroredNotifiesAgain()
    {
        var gate = new NotificationGate();
        gate.Prime(NoActions, new[] { Server("s1") });
        gate.Evaluate(NoActions, new[] { Server("s1", status: "error") });
        gate.Evaluate(NoActions, new[] { Server("s1", status: "ok") });

        var again = gate.Evaluate(NoActions, new[] { Server("s1", status: "error") });
        Assert.Single(again);
    }

    [Fact]
    public void DisabledServerNeverNotifies()
    {
        // 使用者自己停用的下游,不該跳通知說它壞了
        var gate = new NotificationGate();
        gate.Prime(NoActions, new[] { Server("s1", enabled: false) });

        var result = gate.Evaluate(
            NoActions, new[] { Server("s1", status: "error", enabled: false) });
        Assert.Empty(result);
    }

    [Fact]
    public void EmptyDetailFallsBackToGenericMessage()
    {
        var gate = new NotificationGate();
        gate.Prime(NoActions, new[] { Server("s1") });

        var result = gate.Evaluate(NoActions, new[] { Server("s1", status: "error") });
        Assert.Equal("連線失敗", result[0].Body);
    }

    // ── 混合 ──────────────────────────────────────────────

    [Fact]
    public void ActionsAndServersNotifyIndependently()
    {
        var gate = new NotificationGate();
        gate.Prime(NoActions, new[] { Server("s1") });

        var result = gate.Evaluate(new[] { Action("a1") },
                                   new[] { Server("s1", status: "error") });
        Assert.Equal(2, result.Count);
        Assert.Single(result, i => i.ActionId is not null);
    }

    [Fact]
    public void NotificationIdsAreStablePerSubject()
    {
        // 通知 id 決定系統會不會蓋掉舊的那則,不能每次都變
        var gate = new NotificationGate();
        gate.Prime(NoActions, new[] { Server("s1") });

        var result = gate.Evaluate(new[] { Action("a1") },
                                   new[] { Server("s1", status: "error") });
        Assert.Equal(new[] { "action.a1", "server.s1" },
                     result.Select(i => i.Id).OrderBy(x => x, StringComparer.Ordinal));
    }
}
