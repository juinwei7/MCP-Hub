using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using MCPHub.App.Controls;
using MCPHub.Core;

namespace MCPHub.App;

/// <summary>自訂工具與複合工具。</summary>
public partial class MainWindow
{
    private string? _customEditing;     // null = 沒在編輯;"" = 新增
    private string? _compositeEditing;

    // ── 自訂工具 ──────────────────────────────────────────
    //
    // 把一個 HTTP API 包成 MCP 工具。版面是「左清單、右編輯」而不是兩個頁面:
    // 調整一個工具通常要來回改幾次然後試跑,每次都要往返兩個頁面很煩。

    private void BuildCustom()
    {
        PageActions.Children.Add(Ui.Button(this, "新增", "Primary", () =>
        {
            _customEditing = "";
            Refresh();
        }));

        foreach (var t in _state.CustomTools)
        {
            var name = t.Name;
            var trailing = new StackPanel { Orientation = Orientation.Horizontal };
            if (t.NeedsConfirm) trailing.Children.Add(Ui.Pill(this, "需確認", "warn"));
            trailing.Children.Add(Ui.Pill(this, t.Method, "neutral"));
            trailing.Children.Add(Ui.Button(this, "編輯", "Quiet", () =>
            {
                _customEditing = name;
                Refresh();
            }));
            trailing.Children.Add(new Border { Width = 10 });
            trailing.Children.Add(Ui.Switch(this, t.Enabled,
                on => _ = SetCustomEnabled(name, on)));

            Rows.Children.Add(new HubRow
            {
                Health = t.Enabled ? Controls.Health.Ok : Controls.Health.Off,
                Title = name,
                Detail = t.UrlTemplate,   // 網址樣板是機器格式,等寬
                DetailIsMachine = true,
                Dimmed = !t.Enabled,
                Trailing = trailing,
            });
        }

        if (_state.CustomTools.Count == 0 && _customEditing is null)
        {
            Rows.Children.Add(Ui.Empty(this, "還沒有自訂工具",
                "自訂工具把任意 HTTP API 包成 MCP 工具,Claude 就能直接呼叫它。"));
        }

        FooterText.Text = $"{_state.CustomTools.Count} 個";
        if (_customEditing is not null) BuildCustomEditor(_customEditing);
    }

    private void BuildCustomEditor(string name)
    {
        var isNew = name.Length == 0;
        var t = isNew ? null : _state.CustomTools.FirstOrDefault(x => x.Name == name);
        if (!isNew && t is null) { _customEditing = null; return; }

        ShowDetail(true);
        Detail.Children.Add(Ui.Heading(this, isNew ? "新增自訂工具" : $"編輯「{name}」"));

        var nameBox = Ui.Input(this, t?.Name ?? "");
        nameBox.IsEnabled = isNew;
        Detail.Children.Add(Ui.Field(this, "名稱", nameBox,
            isNew ? "Claude 看到的工具名稱。英數與底線。" : "名稱建立後不可更改。"));

        var desc = Ui.Input(this, t?.Description ?? "");
        Detail.Children.Add(Ui.Field(this, "說明", desc,
            "Claude 靠這句話決定什麼時候該用它 —— 寫清楚用途比寫清楚參數更重要。"));

        var method = Ui.Choice(this, ["GET", "POST", "PUT", "PATCH", "DELETE"],
                               t?.Method ?? "GET");
        Detail.Children.Add(Ui.Field(this, "方法", method));

        var url = Ui.Input(this, t?.UrlTemplate ?? "");
        Detail.Children.Add(Ui.Field(this, "網址樣板", url,
            "參數用大括號代入,例如 https://api.example.com/items/{id}"));

        var pars = Ui.Code(this, ParamsJson(t?.Params), 100);
        Detail.Children.Add(Ui.Field(this, "參數", pars,
            """JSON 陣列,例如 [{"name":"id","type":"string","required":true}]"""));

        // header 裡多半是 API 金鑰。後端只回報「有沒有設」不回內容,
        // 所以這裡也只能顯示 key,不能顯示值
        var headerNames = t is null ? [] : t.Headers.Keys.OrderBy(k => k).ToList();
        var headers = Ui.Code(this, "{}", 60);
        Detail.Children.Add(Ui.Field(this, "標頭", headers,
            headerNames.Count > 0
                ? $"已設定:{string.Join("、", headerNames)}(值不會顯示,那是金鑰)。"
                  + "留空代表不動;要刪除某個標頭就把它的值設成空字串。"
                : """JSON 物件,例如 {"Authorization": "Bearer …"}。會加密後存在本機。"""));

        var group = Ui.Input(this, t?.GroupName ?? "");
        Detail.Children.Add(Ui.Field(this, "分類", group, "留空代表未分類。"));

        var confirm = Ui.Switch(this, t?.NeedsConfirm ?? false, _ => { });
        Detail.Children.Add(Ui.Field(this, "呼叫前需人工確認", confirm,
            "開啟後,Claude 呼叫它會先停在「待確認」等你核准。"));

        Detail.Children.Add(Ui.Divider(this));

        var bar = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            HorizontalAlignment = HorizontalAlignment.Right,
        };
        if (!isNew)
        {
            var del = Ui.Button(this, "刪除", "Quiet", () => _ = DeleteCustom(name));
            del.Foreground = Ui.Brush(this, "Down");
            bar.Children.Add(del);
        }
        bar.Children.Add(Ui.Button(this, "取消", "Quiet", () =>
        {
            _customEditing = null;
            Refresh();
        }));
        bar.Children.Add(Ui.Button(this, isNew ? "建立" : "儲存", "Primary", () =>
            _ = SaveCustom(isNew, name, nameBox.Text, desc.Text,
                           (string)method.SelectedItem, url.Text, pars.Text,
                           headers.Text, group.Text, confirm.IsChecked == true)));
        Detail.Children.Add(bar);
    }

    private static string ParamsJson(IReadOnlyList<HubClient.ToolParam>? p)
    {
        if (p is null || p.Count == 0) return "[]";
        return JsonSerializer.Serialize(p, new JsonSerializerOptions { WriteIndented = true });
    }

    private async Task SaveCustom(bool isNew, string original, string name, string description,
                                  string method, string url, string paramsJson,
                                  string headersJson, string group, bool needsConfirm)
    {
        if (name.Trim().Length == 0) { Fail("名稱不能空白"); return; }
        if (url.Trim().Length == 0) { Fail("網址樣板不能空白"); return; }
        if (!TryJson<JsonArray>(paramsJson, "參數", out var pars)) return;

        var body = new Dictionary<string, object?>
        {
            ["name"] = name.Trim(),
            ["description"] = description.Trim(),
            ["method"] = method,
            ["url_template"] = url.Trim(),
            ["params"] = pars,
            ["group_name"] = group.Trim(),
            ["needs_confirm"] = needsConfirm,
        };

        // 省略 headers 代表不動 —— 送空物件會把既有的金鑰清掉
        var h = headersJson.Trim();
        if (h.Length > 0 && h != "{}")
        {
            if (!TryJson<JsonObject>(h, "標頭", out var parsed)) return;
            body["headers"] = parsed;
        }

        try
        {
            using var c = _state.NewClient();
            if (isNew) await c.CreateCustomToolAsync(body).ConfigureAwait(true);
            else await c.UpdateCustomToolAsync(original, body).ConfigureAwait(true);
            _customEditing = null;
            await _state.RefreshAsync().ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
    }

    private async Task SetCustomEnabled(string name, bool enabled)
    {
        try
        {
            using var c = _state.NewClient();
            await c.UpdateCustomToolAsync(name, new { enabled }).ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
        await _state.RefreshAsync().ConfigureAwait(true);
    }

    private async Task DeleteCustom(string name)
    {
        if (MessageBox.Show($"刪除「{name}」?無法復原。", "MCP Hub",
                            MessageBoxButton.OKCancel, MessageBoxImage.Warning)
            != MessageBoxResult.OK) return;
        try
        {
            using var c = _state.NewClient();
            await c.DeleteCustomToolAsync(name).ConfigureAwait(true);
            _customEditing = null;
            await _state.RefreshAsync().ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
    }

    // ── 複合工具 ──────────────────────────────────────────
    //
    // 依序執行多個既有工具,把結果組成一個。步驟用 JSON 編輯而不是
    // 一列一列的結構化編輯器:步驟的參數本來就是任意 JSON,做成表單反而要
    // 先猜形狀,猜錯就卡住。

    private void BuildComposite()
    {
        PageActions.Children.Add(Ui.Button(this, "新增", "Primary", () =>
        {
            _compositeEditing = "";
            Refresh();
        }));

        foreach (var t in _state.CompositeTools)
        {
            var name = t.Name;
            var trailing = new StackPanel { Orientation = Orientation.Horizontal };
            if (t.NeedsConfirm) trailing.Children.Add(Ui.Pill(this, "需確認", "warn"));
            trailing.Children.Add(Ui.Button(this, "編輯", "Quiet", () =>
            {
                _compositeEditing = name;
                Refresh();
            }));
            trailing.Children.Add(new Border { Width = 10 });
            trailing.Children.Add(Ui.Switch(this, t.Enabled,
                on => _ = SetCompositeEnabled(name, on)));

            Rows.Children.Add(new HubRow
            {
                Health = t.Enabled ? Controls.Health.Ok : Controls.Health.Off,
                Title = name,
                Detail = $"{t.Steps.Count} 個步驟",
                DetailIsMachine = false,
                Dimmed = !t.Enabled,
                Trailing = trailing,
            });
        }

        if (_state.CompositeTools.Count == 0 && _compositeEditing is null)
        {
            Rows.Children.Add(Ui.Empty(this, "還沒有複合工具",
                "把幾個常一起用的工具串成一個。Claude 呼叫一次就拿到全部結果,不必自己拆成多步。"));
        }

        FooterText.Text = $"{_state.CompositeTools.Count} 個";
        if (_compositeEditing is not null) BuildCompositeEditor(_compositeEditing);
    }

    private void BuildCompositeEditor(string name)
    {
        var isNew = name.Length == 0;
        var t = isNew ? null : _state.CompositeTools.FirstOrDefault(x => x.Name == name);
        if (!isNew && t is null) { _compositeEditing = null; return; }

        ShowDetail(true);
        Detail.Children.Add(Ui.Heading(this, isNew ? "新增複合工具" : $"編輯「{name}」"));

        var nameBox = Ui.Input(this, t?.Name ?? "");
        nameBox.IsEnabled = isNew;
        Detail.Children.Add(Ui.Field(this, "名稱", nameBox,
            isNew ? "英數與底線。" : "名稱建立後不可更改。"));

        var desc = Ui.Input(this, t?.Description ?? "");
        Detail.Children.Add(Ui.Field(this, "說明", desc));

        var steps = Ui.Code(this, StepsJson(t?.Steps), 140);
        var available = _state.StepTools.Count > 0
            ? $"可用的工具:{string.Join("、", _state.StepTools.Take(8).Select(x => x.Name))}"
              + (_state.StepTools.Count > 8 ? " …" : "")
            : "後端還沒回報可用的工具。";
        Detail.Children.Add(Ui.Field(this, "步驟", steps,
            """JSON 陣列,例如 [{"id":"a","tool":"orgpulse__list_projects","args":{}}]。"""
            + available));

        var pars = Ui.Code(this, ParamsJson(t?.Params), 80);
        Detail.Children.Add(Ui.Field(this, "參數", pars, "JSON 陣列,和自訂工具同一種格式。"));

        var output = Ui.Choice(this, ["collect", "last"], t?.Output ?? "collect");
        Detail.Children.Add(Ui.Field(this, "輸出", output,
            "collect = 把每一步的結果都回傳;last = 只回最後一步。"));

        var group = Ui.Input(this, t?.GroupName ?? "");
        Detail.Children.Add(Ui.Field(this, "分類", group, "留空代表未分類。"));

        var confirm = Ui.Switch(this, t?.NeedsConfirm ?? false, _ => { });
        Detail.Children.Add(Ui.Field(this, "呼叫前需人工確認", confirm));

        Detail.Children.Add(Ui.Divider(this));

        var bar = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            HorizontalAlignment = HorizontalAlignment.Right,
        };
        if (!isNew)
        {
            var del = Ui.Button(this, "刪除", "Quiet", () => _ = DeleteComposite(name));
            del.Foreground = Ui.Brush(this, "Down");
            bar.Children.Add(del);
        }
        bar.Children.Add(Ui.Button(this, "取消", "Quiet", () =>
        {
            _compositeEditing = null;
            Refresh();
        }));
        bar.Children.Add(Ui.Button(this, isNew ? "建立" : "儲存", "Primary", () =>
            _ = SaveComposite(isNew, name, nameBox.Text, desc.Text, steps.Text,
                              pars.Text, (string)output.SelectedItem, group.Text,
                              confirm.IsChecked == true)));
        Detail.Children.Add(bar);
    }

    private static string StepsJson(IReadOnlyList<HubClient.CompositeStep>? steps)
    {
        if (steps is null || steps.Count == 0)
        {
            return """
                   [
                     {"id": "step1", "tool": "", "args": {}}
                   ]
                   """;
        }
        return JsonSerializer.Serialize(steps,
            new JsonSerializerOptions { WriteIndented = true });
    }

    private async Task SaveComposite(bool isNew, string original, string name,
                                     string description, string stepsJson, string paramsJson,
                                     string output, string group, bool needsConfirm)
    {
        if (name.Trim().Length == 0) { Fail("名稱不能空白"); return; }
        if (!TryJson<JsonArray>(stepsJson, "步驟", out var steps)) return;
        if (steps is null || steps.Count == 0) { Fail("至少要有一個步驟"); return; }
        if (!TryJson<JsonArray>(paramsJson, "參數", out var pars)) return;

        var body = new Dictionary<string, object?>
        {
            ["name"] = name.Trim(),
            ["description"] = description.Trim(),
            ["steps"] = steps,
            ["params"] = pars,
            ["output"] = output,
            ["group_name"] = group.Trim(),
            ["needs_confirm"] = needsConfirm,
        };

        try
        {
            using var c = _state.NewClient();
            if (isNew) await c.CreateCompositeToolAsync(body).ConfigureAwait(true);
            else await c.UpdateCompositeToolAsync(original, body).ConfigureAwait(true);
            _compositeEditing = null;
            await _state.RefreshAsync().ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
    }

    private async Task SetCompositeEnabled(string name, bool enabled)
    {
        try
        {
            using var c = _state.NewClient();
            await c.UpdateCompositeToolAsync(name, new { enabled }).ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
        await _state.RefreshAsync().ConfigureAwait(true);
    }

    private async Task DeleteComposite(string name)
    {
        if (MessageBox.Show($"刪除「{name}」?連同它的 Skill 草稿一起刪除,無法復原。",
                            "MCP Hub", MessageBoxButton.OKCancel, MessageBoxImage.Warning)
            != MessageBoxResult.OK) return;
        try
        {
            using var c = _state.NewClient();
            await c.DeleteCompositeToolAsync(name).ConfigureAwait(true);
            _compositeEditing = null;
            await _state.RefreshAsync().ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
    }
}
