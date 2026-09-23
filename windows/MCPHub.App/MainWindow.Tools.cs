using System.Windows;
using System.Windows.Controls;
using MCPHub.App.Controls;
using MCPHub.Core;

namespace MCPHub.App;

/// <summary>工具與記錄。</summary>
public partial class MainWindow
{
    // ── 工具 ──────────────────────────────────────────────
    //
    // 一次只看一台下游的工具。全部混在一起看起來資訊量大,但實際上
    // 「我要調某一台的哪個工具」才是真正的任務 —— 混在一起反而要先過濾。

    private void BuildTools()
    {
        var servers = _state.Servers.Where(s => s.Enabled).ToList();
        if (servers.Count == 0)
        {
            Rows.Children.Add(Ui.Empty(this, "沒有啟用中的下游",
                "工具是從下游聚合來的。先到「下游」加一台並啟用,它的工具才會出現在這裡。"));
            return;
        }

        if (_toolsServer.Length == 0 || servers.All(s => s.Slug != _toolsServer))
        {
            _toolsServer = servers[0].Slug;
        }

        var picker = Ui.Choice(this, servers.Select(s => s.Name),
                               servers.First(s => s.Slug == _toolsServer).Name);
        picker.Width = 180;
        picker.SelectionChanged += (_, _) =>
        {
            var pickedName = (string)picker.SelectedItem;
            var picked = servers.FirstOrDefault(s => s.Name == pickedName);
            if (picked is not null && picked.Slug != _toolsServer)
            {
                _toolsServer = picked.Slug;
                _ = LoadTools();
            }
        };
        PageActions.Children.Add(picker);

        var slug = _toolsServer;
        PageActions.Children.Add(Ui.Button(this, "全部啟用", "Quiet",
            () => _ = SetAllTools(slug, true)));
        PageActions.Children.Add(Ui.Button(this, "全部停用", "Quiet",
            () => _ = SetAllTools(slug, false)));

        var tools = _state.ToolsFor(slug);
        if (tools is null)
        {
            Rows.Children.Add(Ui.Empty(this, "載入中", "正在向後端要這台下游的工具清單。"));
            _ = LoadTools();
            return;
        }

        foreach (var t in tools)
        {
            var name = t.Name;
            var enabled = t.Enabled;
            var confirm = t.NeedsConfirm;

            var trailing = new StackPanel { Orientation = Orientation.Horizontal };
            // 「需確認」是比「啟用」更重的決定,所以標成琥珀而不是強調色
            var confirmLabel = new TextBlock
            {
                Text = "需確認",
                Style = Ui.Style(this, "Meta"),
                Foreground = confirm ? Ui.Brush(this, "Warn") : Ui.Brush(this, "Ink3"),
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 0, 8, 0),
            };
            trailing.Children.Add(confirmLabel);
            trailing.Children.Add(Ui.Switch(this, confirm,
                on => _ = SetTool(slug, name, null, on)));
            trailing.Children.Add(new Border { Width = 18 });
            trailing.Children.Add(Ui.Switch(this, enabled,
                on => _ = SetTool(slug, name, on, null)));

            Rows.Children.Add(new HubRow
            {
                Health = enabled ? Controls.Health.Ok : Controls.Health.Off,
                Title = name,
                // 工具的描述是人寫的說明,不是機器產生的識別碼
                Detail = t.ShownDescription,
                DetailIsMachine = false,
                Dimmed = !enabled,
                Trailing = trailing,
            });
        }

        if (tools.Count == 0)
        {
            Rows.Children.Add(Ui.Empty(this, "這台下游沒有工具",
                "可能是還沒連上,或它真的沒有提供工具。到「下游」按重新檢查看看。"));
        }

        var on2 = tools.Count(t => t.Enabled);
        FooterText.Text = $"{tools.Count} 個工具,{on2} 個啟用";
    }

    private async Task LoadTools()
    {
        await _state.LoadToolsAsync(_toolsServer).ConfigureAwait(true);
    }

    private async Task SetTool(string slug, string tool, bool? enabled, bool? needsConfirm)
    {
        try
        {
            using var c = _state.NewClient();
            await c.SetToolAsync(slug, tool, enabled, needsConfirm).ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
        await _state.LoadToolsAsync(slug).ConfigureAwait(true);
    }

    private async Task SetAllTools(string slug, bool enabled)
    {
        try
        {
            using var c = _state.NewClient();
            await c.SetAllToolsAsync(slug, enabled).ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
        await _state.LoadToolsAsync(slug).ConfigureAwait(true);
    }

    // ── 記錄 ──────────────────────────────────────────────

    private void BuildLogs()
    {
        var page = _state.Logs;
        if (page is null)
        {
            Rows.Children.Add(Ui.Empty(this, "載入中", "正在向後端要呼叫記錄。"));
            _ = _state.LoadLogsAsync();
            return;
        }

        var onlyErrors = _state.LogsErrorsOnly;
        PageActions.Children.Add(Ui.Button(this, onlyErrors ? "顯示全部" : "只看錯誤", "Quiet",
            () => _ = _state.LoadLogsAsync(1, !onlyErrors)));

        foreach (var r in page.Rows)
        {
            // 耗時是機器產生的數字,和時間戳一樣用等寬 —— 對齊才比得出快慢
            var meta = new StackPanel { Orientation = Orientation.Horizontal };
            if (r.DurationMs is int ms)
            {
                meta.Children.Add(new TextBlock
                {
                    Text = $"{ms} ms",
                    Style = Ui.Style(this, "Mono"),
                    VerticalAlignment = VerticalAlignment.Center,
                    Margin = new Thickness(0, 0, 14, 0),
                });
            }
            meta.Children.Add(new TextBlock
            {
                Text = Moment(r.Time),
                Style = Ui.Style(this, "Mono"),
                VerticalAlignment = VerticalAlignment.Center,
            });

            Rows.Children.Add(new HubRow
            {
                Health = r.IsError ? Controls.Health.Down : Controls.Health.Ok,
                Title = r.QualifiedName,
                // 成功的那列沒有第二行:狀態已經由色條表達,再寫一次「成功」是噪音
                Detail = r.IsError ? r.Error : "",
                DetailIsMachine = false,
                Trailing = meta,
            });
        }

        if (page.Rows.Count == 0)
        {
            Rows.Children.Add(Ui.Empty(this,
                onlyErrors ? "沒有錯誤記錄" : "還沒有呼叫記錄",
                onlyErrors
                    ? "目前這一頁沒有失敗的呼叫。"
                    : "Claude 透過 Hub 呼叫工具時,每一次都會記在這裡。"));
        }

        if (page.Pages > 1)
        {
            var nav = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 14, 0, 14),
            };
            if (page.Page > 1)
            {
                nav.Children.Add(Ui.Button(this, "上一頁", "Quiet",
                    () => _ = _state.LoadLogsAsync(page.Page - 1, onlyErrors)));
            }
            if (page.Page < page.Pages)
            {
                nav.Children.Add(Ui.Button(this, "下一頁", "Quiet",
                    () => _ = _state.LoadLogsAsync(page.Page + 1, onlyErrors)));
            }
            Rows.Children.Add(nav);
        }

        FooterText.Text = $"第 {page.Page} / {page.Pages} 頁,共 {page.Total} 筆"
                        + (page.Errors > 0 ? $",{page.Errors} 筆錯誤" : "");
    }
}
