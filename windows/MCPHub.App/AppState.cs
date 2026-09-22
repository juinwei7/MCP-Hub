using System.Windows;
using MCPHub.Core;

namespace MCPHub.App;

/// <summary>
/// 整個 app 共用的狀態。
///
/// 刻意不用 MVVM 的資料繫結,而是一個 Changed 事件加上各畫面自己重畫。
/// 原因很實際:開發機是 macOS,沒有辦法執行這個 app —— 繫結寫錯是執行期
/// 才會靜靜失敗的那種錯(WPF 的繫結失敗預設只寫到 Debug 輸出,不丟例外),
/// 而直接呼叫的程式碼編譯器抓得到。
/// </summary>
public sealed class AppState : IDisposable
{
    private readonly System.Threading.CancellationTokenSource _cts = new();
    private Task? _loop;

    public BackendSupervisor Supervisor { get; } = new();

    public BackendSupervisor.State Backend => Supervisor.Current;
    public IReadOnlyList<HubClient.Server> Servers { get; private set; } = [];
    public IReadOnlyList<HubClient.HubAction> Actions { get; private set; } = [];
    public bool Paused { get; private set; }
    public string? LastError { get; private set; }

    /// <summary>狀態變了,畫面該重畫。一律在 UI 執行緒上發出。</summary>
    public event Action? Changed;

    public HubClient NewClient() => new(Supervisor.Token);

    public int Healthy => Servers.Count(s => s.Enabled && s.IsHealthy);
    public int Errored => Servers.Count(s => s.Enabled && s.IsErrored);
    public int PendingCount => Actions.Count(a => a.IsWaiting);
    public int TotalTools => Servers.Sum(s => s.ToolCount ?? 0);

    public void Boot()
    {
        Supervisor.StateChanged += _ =>
        {
            Raise();
            if (Supervisor.Current == BackendSupervisor.State.Ready) StartRefreshing();
        };
        Supervisor.Start();
    }

    private void StartRefreshing()
    {
        if (_loop is not null) return;
        _loop = Task.Run(async () =>
        {
            while (!_cts.IsCancellationRequested)
            {
                await RefreshAsync().ConfigureAwait(false);
                try
                {
                    await Task.Delay(TimeSpan.FromSeconds(10), _cts.Token).ConfigureAwait(false);
                }
                catch (OperationCanceledException) { return; }
            }
        });
    }

    public async Task RefreshAsync()
    {
        try
        {
            using var c = NewClient();
            // 一次抓完再一起更新。分開抓會讓畫面短暫出現「下游已經更新、
            // 待確認還是舊的」那種對不起來的中間狀態。
            var servers = await c.ServersAsync(_cts.Token).ConfigureAwait(false);
            var actions = await c.ActionsAsync(_cts.Token).ConfigureAwait(false);
            var paused = await c.PausedAsync(_cts.Token).ConfigureAwait(false);

            Servers = servers;
            Actions = actions;
            Paused = paused;
            LastError = null;
        }
        catch (OperationCanceledException) { return; }
        catch (HubClient.HubException e) { LastError = e.Message; }
        Raise();
    }

    /// <summary>
    /// 先改本地狀態再送出:按下去要立刻反映,不能等一輪 round trip。
    /// 失敗就回捲,並把原因留在 LastError。
    /// </summary>
    public async Task SetPausedAsync(bool want)
    {
        var before = Paused;
        Paused = want;
        Raise();
        try
        {
            using var c = NewClient();
            Paused = await c.SetPausedAsync(want, _cts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) { return; }
        catch (HubClient.HubException e)
        {
            Paused = before;
            LastError = e.Message;
        }
        Raise();
    }

    public async Task SetServerEnabledAsync(string slug, bool enabled)
    {
        try
        {
            using var c = NewClient();
            await c.SetEnabledAsync(slug, enabled, _cts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) { return; }
        catch (HubClient.HubException e) { LastError = e.Message; }
        await RefreshAsync().ConfigureAwait(false);
    }

    public async Task CheckAllAsync()
    {
        try
        {
            using var c = NewClient();
            await c.CheckAllAsync(_cts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) { return; }
        catch (HubClient.HubException e) { LastError = e.Message; }
        await RefreshAsync().ConfigureAwait(false);
    }

    public async Task CheckAsync(string slug)
    {
        try
        {
            using var c = NewClient();
            await c.CheckAsync(slug, _cts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) { return; }
        catch (HubClient.HubException e) { LastError = e.Message; }
        await RefreshAsync().ConfigureAwait(false);
    }

    public async Task DecideAsync(string id, bool approve)
    {
        try
        {
            using var c = NewClient();
            await c.DecideAsync(id, approve, _cts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) { return; }
        catch (HubClient.HubException e) { LastError = e.Message; }
        await RefreshAsync().ConfigureAwait(false);
    }

    public void ClearError()
    {
        LastError = null;
        Raise();
    }

    /// <summary>
    /// 輪詢跑在背景執行緒,但畫面只能在 UI 執行緒上動 —— 統一在這裡切回去,
    /// 訂閱者才不必各自記得這件事。
    /// </summary>
    private void Raise()
    {
        var app = Application.Current;
        if (app is null) return;
        if (app.Dispatcher.CheckAccess()) Changed?.Invoke();
        else app.Dispatcher.BeginInvoke(() => Changed?.Invoke());
    }

    public void Dispose()
    {
        _cts.Cancel();
        Supervisor.Dispose();
        _cts.Dispose();
    }
}
