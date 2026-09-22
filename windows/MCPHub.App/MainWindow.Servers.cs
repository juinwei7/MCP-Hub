using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using MCPHub.App.Controls;
using MCPHub.Core;

namespace MCPHub.App;

/// <summary>下游:清單、新增、編輯。</summary>
public partial class MainWindow
{
    private string? _editingServer;      // null = 沒在編輯;"" = 新增
    private string _toolsServer = "";    // 「工具」那頁目前看的是哪一台

    /// <summary>
    /// 截圖用:把「新增」的編輯器打開。編輯器是這個 app 最複雜的版面,
    /// 而示範資料裡沒有自訂 / 複合工具,不主動打開的話那些表單一張圖都渲染不到。
    /// </summary>
    internal void OpenNewEditor()
    {
        switch (_section)
        {
            case Section.Servers: _editingServer = ""; break;
            case Section.Custom: _customEditing = ""; break;
            case Section.Composite: _compositeEditing = ""; break;
            default: return;
        }
        Refresh();
    }

    /// <summary>換頁時把各區域自己的暫存狀態清掉,免得切回來看到上次的編輯中內容。</summary>
    private void ResetSectionState()
    {
        _editingServer = null;
        _customEditing = null;
        _compositeEditing = null;
        _settingsTab = SettingsTab.General;
    }

    // ── 清單 ──────────────────────────────────────────────

    private void BuildServers()
    {
        PageActions.Children.Add(Ui.Button(this, "全部重新檢查", "Quiet",
            () => _ = _state.CheckAllAsync()));
        PageActions.Children.Add(Ui.Button(this, "新增下游", "Primary", () =>
        {
            _editingServer = "";
            Refresh();
        }));

        foreach (var s in _state.Servers)
        {
            var slug = s.Slug;
            var enabled = s.Enabled;

            var trailing = new StackPanel { Orientation = Orientation.Horizontal };
            if (s.ToolCount is int n)
            {
                trailing.Children.Add(new TextBlock
                {
                    Text = $"{n} 工具",
                    Style = Ui.Style(this, "Meta"),
                    VerticalAlignment = VerticalAlignment.Center,
                    Margin = new Thickness(0, 0, 12, 0),
                });
            }
            // OAuth 的下游要能重新授權 —— token 會過期,而過期之後除了重新授權
            // 沒有別的辦法。表單裡本來就寫著「用 OAuth 授權完成登入」,
            // 沒有這顆按鈕的話那句話是在指一個不存在的東西。
            if (s.Transport == "http" && s.AuthType == "oauth")
            {
                trailing.Children.Add(Ui.Button(this, "OAuth 授權", "Quiet",
                    () => _ = StartOAuth(slug)));
            }
            trailing.Children.Add(Ui.Button(this, "編輯", "Quiet", () =>
            {
                _editingServer = slug;
                Refresh();
            }));
            trailing.Children.Add(new Border { Width = 10 });
            trailing.Children.Add(Ui.Switch(this, enabled,
                on => _ = _state.SetServerEnabledAsync(slug, on)));

            // 異常時第二行從等寬的網址換成系統字的錯誤原因 —— 字體本身
            // 就說明了那是給人看的訊息,不是機器產生的識別碼
            var broken = s.Enabled && s.IsErrored && s.StatusDetail.Length > 0;

            Rows.Children.Add(new HubRow
            {
                Health = !s.Enabled ? Controls.Health.Off
                       : s.IsHealthy ? Controls.Health.Ok
                       : s.IsErrored ? Controls.Health.Down
                       : Controls.Health.Warn,
                Title = s.Name,
                Detail = broken ? s.StatusDetail : Target(s),
                DetailIsMachine = !broken,
                Dimmed = !s.Enabled,
                Trailing = trailing,
            });
        }

        if (_state.Servers.Count == 0 && _editingServer is null)
        {
            Rows.Children.Add(Ui.Empty(this, "還沒有下游",
                "下游是 Claude 實際要用的 MCP server。加進來之後,它們的工具會一起從 Hub 出去。"));
        }

        var on = _state.Servers.Count(s => s.Enabled);
        FooterText.Text = $"{_state.Servers.Count} 台,{on} 台啟用";

        if (_editingServer is not null) BuildServerEditor(_editingServer);
    }

    /// <summary>清單上顯示的位址。http 顯示主機,stdio 顯示指令。</summary>
    private static string Target(HubClient.Server s)
    {
        if (s.Transport == "stdio") return s.BaseUrl;
        // 協定對辨識沒有幫助,而且會把有用的路徑擠掉
        var url = s.BaseUrl;
        foreach (var prefix in new[] { "https://", "http://" })
        {
            if (url.StartsWith(prefix, StringComparison.Ordinal)) return url[prefix.Length..];
        }
        return url;
    }

    // ── 編輯器 ────────────────────────────────────────────
    //
    // 表單依 transport 換欄位。http 要網址與授權方式,stdio 要指令、參數、
    // 環境變數 —— 兩者同時顯示的話,永遠有一半是灰的,看起來像壞掉。

    private void BuildServerEditor(string slug)
    {
        var isNew = slug.Length == 0;
        var existing = isNew ? null : _state.Servers.FirstOrDefault(s => s.Slug == slug);
        if (!isNew && existing is null) { _editingServer = null; return; }

        ShowDetail(true);
        Detail.Children.Add(Ui.Heading(this, isNew ? "新增下游" : $"編輯「{existing!.Name}」"));

        var name = Ui.Input(this, existing?.Name ?? "");
        Detail.Children.Add(Ui.Field(this, "名稱", name));

        var slugBox = Ui.Input(this, isNew ? "" : slug);
        slugBox.IsEnabled = isNew;
        Detail.Children.Add(Ui.Field(this, "識別碼", slugBox,
            isNew ? "留空自動從名稱產生。建立後不可更改。" : "識別碼建立後不可更改。"));

        var transport = Ui.Choice(this, ["http", "stdio"], existing?.Transport ?? "http");
        Detail.Children.Add(Ui.Field(this, "連線方式", transport));

        // 依 transport 換欄位。用一個容器裝,切換時只重建這一塊
        var variable = new StackPanel();
        Detail.Children.Add(variable);

        var url = Ui.Input(this, existing?.Transport == "stdio" ? "" : existing?.BaseUrl ?? "");
        var auth = Ui.Choice(this, ["none", "bearer", "oauth"], existing?.AuthType ?? "none");
        var token = Ui.Input(this, "");
        var command = Ui.Input(this, existing?.Transport == "stdio" ? existing.BaseUrl : "");
        var args = Ui.Code(this, "[]", 60);
        var env = Ui.Code(this, "{}", 60);

        void Rebuild()
        {
            variable.Children.Clear();
            if ((string)transport.SelectedItem == "http")
            {
                variable.Children.Add(Ui.Field(this, "網址", url, "例如 https://example.com/mcp"));
                variable.Children.Add(Ui.Field(this, "授權", auth));
                if ((string)auth.SelectedItem == "bearer")
                {
                    variable.Children.Add(Ui.Field(this, "Token", token,
                        existing?.HasToken == true
                            ? "已設定。留空代表不動,想清除就填一個空白後儲存。"
                            : "會加密後存在本機。"));
                }
                else if ((string)auth.SelectedItem == "oauth")
                {
                    variable.Children.Add(Ui.Text(this,
                        "建立之後回到清單,用「OAuth 授權」完成登入。", "Meta", wrap: true));
                }
            }
            else
            {
                variable.Children.Add(Ui.Field(this, "指令", command, "例如 npx"));
                variable.Children.Add(Ui.Field(this, "參數", args,
                    """JSON 陣列,例如 ["-y", "@modelcontextprotocol/server-filesystem", "C:\\path"]"""));
                variable.Children.Add(Ui.Field(this, "環境變數", env,
                    existing?.Transport == "stdio"
                        ? "JSON 物件。已設定的值不會顯示在這裡 —— 那是金鑰。留空儲存會保留原本的。"
                        : "JSON 物件,裡面可能有金鑰,會加密後存在本機。"));
                variable.Children.Add(Ui.Text(this,
                    "指令會在這台機器上執行,所需的執行環境(node、git 等)要先裝好。",
                    "Meta", wrap: true));
            }
        }

        transport.SelectionChanged += (_, _) => Rebuild();
        auth.SelectionChanged += (_, _) => Rebuild();
        Rebuild();

        Detail.Children.Add(Ui.Divider(this));
        var bar = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            HorizontalAlignment = HorizontalAlignment.Right,
        };
        if (!isNew)
        {
            var del = Ui.Button(this, "刪除", "Quiet", () => _ = DeleteServer(slug));
            del.Foreground = Ui.Brush(this, "Down");
            del.HorizontalAlignment = HorizontalAlignment.Left;
            bar.Children.Add(del);
        }
        bar.Children.Add(Ui.Button(this, "取消", "Quiet", () =>
        {
            _editingServer = null;
            Refresh();
        }));
        bar.Children.Add(Ui.Button(this, isNew ? "新增" : "儲存", "Primary", () =>
            _ = SaveServer(isNew, slug, name.Text, slugBox.Text,
                           (string)transport.SelectedItem, url.Text,
                           (string)auth.SelectedItem, token.Text,
                           command.Text, args.Text, env.Text)));
        Detail.Children.Add(bar);
    }

    private async Task SaveServer(bool isNew, string slug, string name, string wantedSlug,
                                  string transport, string url, string auth, string token,
                                  string command, string argsJson, string envJson)
    {
        if (name.Trim().Length == 0) { Fail("名稱不能空白"); return; }

        var body = new Dictionary<string, object?>
        {
            ["name"] = name.Trim(),
            ["transport"] = transport,
            ["base_url"] = transport == "http" ? url.Trim() : "",
            ["auth_type"] = transport == "http" ? auth : "none",
            ["command"] = transport == "stdio" ? command.Trim() : "",
        };

        if (transport == "stdio")
        {
            if (!TryJson<JsonArray>(argsJson, "參數", out var parsedArgs)) return;
            body["args"] = parsedArgs;
            // 省略 env 代表不動;有填才送,免得把既有的金鑰清成空的
            var envText = envJson.Trim();
            if (envText.Length > 0 && envText != "{}")
            {
                if (!TryJson<JsonObject>(envText, "環境變數", out var parsedEnv)) return;
                body["env"] = parsedEnv;
            }
        }
        else
        {
            body["args"] = new JsonArray();
        }

        // bearer_token 省略代表不動,送空字串代表清除
        if (transport == "http" && auth == "bearer" && token.Length > 0)
        {
            body["bearer_token"] = token.Trim();
        }

        try
        {
            using var c = _state.NewClient();
            if (isNew)
            {
                var s = wantedSlug.Trim();
                if (s.Length > 0) body["slug"] = s;
                await c.CreateServerAsync(body).ConfigureAwait(true);
            }
            else
            {
                await c.UpdateServerAsync(slug, body).ConfigureAwait(true);
            }
            _editingServer = null;
            await _state.RefreshAsync().ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
    }

    /// <summary>
    /// 開瀏覽器完成 OAuth。授權視窗一定要是使用者自己的瀏覽器 ——
    /// 內嵌 webview 看起來比較整合,但使用者沒辦法確認網址列,
    /// 那正是釣魚最愛的形狀。
    /// </summary>
    private async Task StartOAuth(string slug)
    {
        try
        {
            using var c = _state.NewClient();
            var start = await c.StartOAuthAsync(slug).ConfigureAwait(true);
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = start.AuthorizationUrl,
                UseShellExecute = true,
            });
            Fail("已開啟瀏覽器完成授權。授權後回到這裡按「全部重新檢查」。");
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
        catch (Exception e) when (e is System.ComponentModel.Win32Exception)
        {
            Fail($"打不開瀏覽器:{e.Message}");
        }
    }

    private async Task DeleteServer(string slug)
    {
        var confirm = MessageBox.Show(
            $"刪除「{slug}」?連同它的工具設定與授權一起移除,無法復原。",
            "MCP Hub", MessageBoxButton.OKCancel, MessageBoxImage.Warning);
        if (confirm != MessageBoxResult.OK) return;

        try
        {
            using var c = _state.NewClient();
            await c.DeleteServerAsync(slug).ConfigureAwait(true);
            _editingServer = null;
            await _state.RefreshAsync().ConfigureAwait(true);
        }
        catch (HubClient.HubException e) { Fail(e.Message); }
    }

    // ── 共用 ──────────────────────────────────────────────

    /// <summary>把錯誤送進視窗層級的警示線。錯誤是全域的,不該只在某一頁看得到。</summary>
    private void Fail(string message)
    {
        _state.Report(message);
    }

    /// <summary>
    /// 解析使用者填的 JSON。失敗時講清楚是哪一個欄位 ——
    /// 只說「JSON 格式錯誤」的話,表單上有三個 JSON 欄位,等於沒說。
    /// </summary>
    private bool TryJson<T>(string text, string field, out T? value) where T : JsonNode
    {
        value = null;
        var trimmed = text.Trim();
        if (trimmed.Length == 0)
        {
            value = (T?)(JsonNode?)(typeof(T) == typeof(JsonArray)
                ? new JsonArray() : new JsonObject());
            return true;
        }
        try
        {
            if (JsonNode.Parse(trimmed) is T parsed) { value = parsed; return true; }
            Fail($"「{field}」必須是 {(typeof(T) == typeof(JsonArray) ? "JSON 陣列" : "JSON 物件")}");
            return false;
        }
        catch (JsonException e)
        {
            Fail($"「{field}」不是合法的 JSON:{e.Message}");
            return false;
        }
    }
}
