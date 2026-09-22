using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using MCPHub.App.Controls;
using MCPHub.Core;
using WinForms = System.Windows.Forms;

namespace MCPHub.App;

/// <summary>
/// 設定:一般、匯入下游、分類、匯出。
///
/// 這些的共通點是「不常用、但需要時得找得到」,所以收在同一個區域用左側清單切換,
/// 而不是各自佔掉側邊欄的一個位置。
/// </summary>
public partial class MainWindow
{
    internal enum SettingsTab { General, Import, Categories, Export }

    private SettingsTab _settingsTab = SettingsTab.General;

    private static string TabLabel(SettingsTab t) => t switch
    {
        SettingsTab.General => "一般",
        SettingsTab.Import => "匯入下游",
        SettingsTab.Categories => "分類",
        _ => "匯出設定",
    };

    private void BuildSettings()
    {
        foreach (var t in Enum.GetValues<SettingsTab>())
        {
            var tab = t;
            var selected = t == _settingsTab;
            var row = new HubRow
            {
                // 這裡的色條沒有狀態意義,所以一律不給顏色 ——
                // 顏色只表達狀態,拿來當選取指示會稀釋它的意思
                Health = Controls.Health.Off,
                Title = TabLabel(t),
                Detail = "",
                Trailing = selected
                    ? Ui.Text(this, "檢視中", "Meta")
                    : null,
            };
            row.Opacity = selected ? 1.0 : 0.7;
            row.MouseLeftButtonUp += (_, _) =>
            {
                _settingsTab = tab;
                Refresh();
            };
            row.Cursor = System.Windows.Input.Cursors.Hand;
            Rows.Children.Add(row);
        }

        ShowDetail(true, 560);
        switch (_settingsTab)
        {
            case SettingsTab.General: BuildGeneral(); break;
            case SettingsTab.Import: BuildImport(); break;
            case SettingsTab.Categories: BuildCategories(); break;
            default: BuildExport(); break;
        }
    }

    // ── 一般 ──────────────────────────────────────────────

    private void BuildGeneral()
    {
        Detail.Children.Add(Ui.Heading(this, "開機時自動啟動"));
        var startup = Ui.Switch(this, StartupShortcut.IsEnabled, on =>
        {
            var problem = StartupShortcut.Set(on);
            if (problem is not null) Fail(problem);
            Refresh();
        });
        Detail.Children.Add(Ui.Field(this, "登入時把 MCP Hub 一起帶起來", startup,
            "寫進登錄檔的 Run 機碼。你在「工作管理員 → 啟動」裡看得到它,也可以從那裡關掉。"));

        Detail.Children.Add(Ui.Divider(this));

        Detail.Children.Add(Ui.Heading(this, "接進 Claude"));
        Detail.Children.Add(Ui.Text(this,
            "把下面的指令貼到終端機執行一次。環境變數不能省 —— 少了它們,"
            + "聚合器會讀到另一份空的資料庫,你在這裡的設定一個都不會生效。",
            "Body", wrap: true));

        var resolved = BackendSupervisor.ResolvePython();
        var command = BackendSupervisor.ClaudeAddCommand(
            BackendSupervisor.DataDirectory,
            resolved?.Python ?? @"<專案>\.venv\Scripts\python.exe",
            resolved?.Repo ?? "<專案路徑>");
        var box = Ui.Code(this, command, 110);
        box.IsReadOnly = true;
        Detail.Children.Add(new Border { Height = 10 });
        Detail.Children.Add(box);
        Detail.Children.Add(Right(Ui.Button(this, "複製", "Quiet", () =>
        {
            try { Clipboard.SetText(command); }
            catch (System.Runtime.InteropServices.ExternalException)
            {
                Fail("剪貼簿正被其他程式佔用,請稍後再試一次。");
            }
        })));

        Detail.Children.Add(Ui.Divider(this));
        Detail.Children.Add(Ui.Heading(this, "資料位置"));
        var dir = Ui.Code(this, BackendSupervisor.DataDirectory, 40);
        dir.IsReadOnly = true;
        Detail.Children.Add(dir);
        Detail.Children.Add(Right(Ui.Button(this, "開啟資料夾", "Quiet", () =>
        {
            try
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                {
                    FileName = BackendSupervisor.DataDirectory,
                    UseShellExecute = true,
                });
            }
            catch (Exception e) when (e is System.ComponentModel.Win32Exception
                                           or FileNotFoundException)
            {
                Fail($"打不開資料夾:{e.Message}");
            }
        })));
    }

    // ── 匯入下游 ──────────────────────────────────────────

    private void BuildImport()
    {
        Detail.Children.Add(Ui.Heading(this, "從 Claude 設定匯入"));
        Detail.Children.Add(Ui.Text(this,
            "讀取這台機器上 Claude Desktop 與 Claude Code 的設定,把裡面的 MCP server 加進來。",
            "Body", wrap: true));
        Detail.Children.Add(new Border { Height = 10 });

        var preview = _state.ClaudePreview;
        if (preview is null)
        {
            Detail.Children.Add(Ui.Text(this, "讀取中…", "Meta"));
            _ = _state.LoadClaudePreviewAsync();
        }
        else if (preview.Entries.Count == 0)
        {
            Detail.Children.Add(Ui.Text(this, "找不到可匯入的項目。", "Body"));
        }
        else
        {
            // 先讓人看清楚會發生什麼,再按匯入
            foreach (var e in preview.Entries)
            {
                var trailing = new StackPanel { Orientation = Orientation.Horizontal };
                trailing.Children.Add(Ui.Pill(this, e.Transport,
                    e.Transport == "stdio" ? "neutral" : "accent"));
                if (e.AuthType != "none") trailing.Children.Add(Ui.Pill(this, e.AuthType, "warn"));
                if (e.AlreadyExists) trailing.Children.Add(Ui.Text(this, "已存在", "Meta"));

                Detail.Children.Add(new HubRow
                {
                    Health = e.AlreadyExists ? Controls.Health.Off : Controls.Health.Ok,
                    Title = e.Name,
                    Detail = e.Target,
                    DetailIsMachine = true,
                    Dimmed = e.AlreadyExists,
                    Trailing = trailing,
                });
            }

            var fresh = preview.Entries.Count(e => !e.AlreadyExists);
            var summary = Ui.Text(this,
                $"{fresh} 個可新增,{preview.Entries.Count - fresh} 個已存在", "Meta");
            summary.Margin = new Thickness(0, 12, 0, 0);
            Detail.Children.Add(summary);

            var import = Ui.Button(this, "匯入", "Primary", () => _ = ImportClaude());
            import.IsEnabled = fresh > 0;
            Detail.Children.Add(Right(import));

            if (preview.Sources.Count > 0)
            {
                Detail.Children.Add(Ui.Text(this,
                    "來源:" + string.Join("、", preview.Sources), "Meta", wrap: true));
            }
        }

        Detail.Children.Add(Ui.Divider(this));
        Detail.Children.Add(Ui.Heading(this, "貼上設定"));
        Detail.Children.Add(Ui.Text(this,
            "貼一份 mcpServers JSON,或整份 Claude 設定檔的內容。", "Body", wrap: true));
        Detail.Children.Add(new Border { Height = 8 });
        var pasted = Ui.Code(this, "", 120);
        Detail.Children.Add(pasted);
        Detail.Children.Add(Right(Ui.Button(this, "匯入", "Primary",
            () => _ = ImportPasted(pasted.Text))));
    }

    private async Task ImportClaude()
    {
        try
        {
            using var c = _state.NewClient();
            var r = await c.ImportClaudeConfigAsync().ConfigureAwait(true);
            Fail(Summary(r));
            await _state.RefreshAsync().ConfigureAwait(true);
            await _state.LoadClaudePreviewAsync(force: true).ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
    }

    private async Task ImportPasted(string config)
    {
        if (config.Trim().Length == 0) { Fail("先貼上要匯入的內容"); return; }
        try
        {
            using var c = _state.NewClient();
            var r = await c.ImportMcpServersAsync(config).ConfigureAwait(true);
            Fail(Summary(r));
            await _state.RefreshAsync().ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
    }

    private static string Summary(HubClient.ImportResult r)
    {
        if (r.Added.Count == 0)
        {
            return "沒有新增任何下游"
                 + (r.Skipped.Count == 0 ? "。" : $",{r.Skipped.Count} 個已存在或格式不符。");
        }
        var text = $"已新增 {r.Added.Count} 台:{string.Join("、", r.Added)}";
        if (r.Skipped.Count > 0) text += $",略過 {r.Skipped.Count} 個";
        return text;
    }

    // ── 分類 ──────────────────────────────────────────────

    private void BuildCategories()
    {
        Detail.Children.Add(Ui.Heading(this, "分類"));
        Detail.Children.Add(Ui.Text(this,
            "分類讓你一次開關一整組工具。工具的分類在它自己的編輯畫面裡設定。",
            "Body", wrap: true));
        Detail.Children.Add(new Border { Height = 10 });

        var overview = _state.Categories;
        if (overview is null)
        {
            Detail.Children.Add(Ui.Text(this, "讀取中…", "Meta"));
            _ = _state.LoadCategoriesAsync();
            return;
        }

        foreach (var cat in overview.All)
        {
            var name = cat.Name;
            var trailing = new StackPanel { Orientation = Orientation.Horizontal };
            trailing.Children.Add(Ui.Text(this, $"{cat.Enabled}/{cat.Total}", "Mono"));
            if (!cat.IsUncategorized)
            {
                var del = Ui.Button(this, "刪除", "Quiet", () => _ = DeleteCategory(name));
                del.Foreground = Ui.Brush(this, "Down");
                trailing.Children.Add(del);
            }

            Detail.Children.Add(new HubRow
            {
                Health = cat.Enabled > 0 ? Controls.Health.Ok : Controls.Health.Off,
                Title = cat.DisplayName,
                Detail = "",
                Dimmed = cat.Enabled == 0,
                Trailing = trailing,
            });
        }

        if (overview.All.Count == 0)
        {
            Detail.Children.Add(Ui.Text(this, "還沒有分類。", "Body"));
        }

        Detail.Children.Add(Ui.Divider(this));
        var input = Ui.Input(this, "");
        Detail.Children.Add(Ui.Field(this, "新增分類", input));
        Detail.Children.Add(Right(Ui.Button(this, "新增", "Primary",
            () => _ = AddCategory(input.Text))));
    }

    private async Task AddCategory(string name)
    {
        if (name.Trim().Length == 0) { Fail("分類名稱不能空白"); return; }
        try
        {
            using var c = _state.NewClient();
            await c.CreateCategoryAsync(name.Trim()).ConfigureAwait(true);
            await _state.LoadCategoriesAsync(force: true).ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
    }

    private async Task DeleteCategory(string name)
    {
        try
        {
            using var c = _state.NewClient();
            await c.DeleteCategoryAsync(name).ConfigureAwait(true);
            await _state.LoadCategoriesAsync(force: true).ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
    }

    // ── 匯出 ──────────────────────────────────────────────

    private void BuildExport()
    {
        Detail.Children.Add(Ui.Heading(this, "匯出設定"));
        Detail.Children.Add(Ui.Text(this,
            "把下游、自訂工具、複合工具與分類匯成一份 JSON,可以搬到另一台機器。",
            "Body", wrap: true));
        Detail.Children.Add(new Border { Height = 12 });

        var secrets = Ui.Switch(this, false, _ => { });
        Detail.Children.Add(Ui.Field(this, "包含金鑰", secrets,
            "預設不含。匯出檔很容易被順手貼到別的地方 —— 預設帶金鑰是在幫你外洩。"));

        Detail.Children.Add(Right(Ui.Button(this, "匯出到檔案", "Primary",
            () => _ = Export(secrets.IsChecked == true))));
    }

    private async Task Export(bool includeSecrets)
    {
        try
        {
            using var c = _state.NewClient();
            var config = await c.ExportConfigAsync(includeSecrets).ConfigureAwait(true);

            var dialog = new WinForms.SaveFileDialog
            {
                Title = "匯出 MCP Hub 設定",
                Filter = "JSON|*.json",
                FileName = $"mcp-hub-{DateTime.Now:yyyyMMdd}.json",
            };
            if (dialog.ShowDialog() != WinForms.DialogResult.OK) return;

            await File.WriteAllTextAsync(dialog.FileName,
                config.ToJsonString(new JsonSerializerOptions { WriteIndented = true }))
                .ConfigureAwait(true);
            Fail($"已匯出到 {dialog.FileName}");
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            Fail($"寫入失敗:{e.Message}");
        }
    }

    // ── 零件 ──────────────────────────────────────────────

    private static FrameworkElement Right(FrameworkElement element)
    {
        element.HorizontalAlignment = HorizontalAlignment.Right;
        element.Margin = new Thickness(0, 10, 0, 0);
        return element;
    }
}
