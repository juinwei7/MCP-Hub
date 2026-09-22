using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace MCPHub.Core;

/// <summary>
/// 自訂工具、複合工具、分類、匯入匯出。
///
/// 和下游那組分開檔案:下游是「別人的 MCP server」,這些是 Hub 自己造的東西,
/// 兩者的生命週期和錯誤模式都不一樣。
/// </summary>
public sealed partial class HubClient
{
    // ── 型別 ──────────────────────────────────────────────

    /// <summary>
    /// header 只回報「有沒有設」,不回內容 —— 那裡面是 API 金鑰。
    /// 後端刻意這樣設計,客戶端不該假設拿得到值。
    /// </summary>
    public sealed record HeaderSlot(
        [property: JsonPropertyName("set")] bool Set);

    public sealed record ToolParam(
        [property: JsonPropertyName("name")] string Name,
        [property: JsonPropertyName("type")] string? Type = "string",
        [property: JsonPropertyName("required")] bool? Required = false,
        [property: JsonPropertyName("description")] string? Description = "")
    {
        public string TypeOrDefault => string.IsNullOrEmpty(Type) ? "string" : Type;
        public bool IsRequired => Required ?? false;
    }

    public sealed record CustomTool(
        [property: JsonPropertyName("name")] string Name,
        [property: JsonPropertyName("description")] string Description,
        [property: JsonPropertyName("method")] string Method,
        [property: JsonPropertyName("url_template")] string UrlTemplate,
        [property: JsonPropertyName("headers")] Dictionary<string, HeaderSlot> Headers,
        [property: JsonPropertyName("params")] IReadOnlyList<ToolParam> Params,
        [property: JsonPropertyName("enabled")] bool Enabled,
        [property: JsonPropertyName("needs_confirm")] bool NeedsConfirm,
        [property: JsonPropertyName("group_name")] string GroupName);

    public sealed record CompositeStep(
        [property: JsonPropertyName("id")] string Id,
        [property: JsonPropertyName("tool")] string Tool,
        [property: JsonPropertyName("args")] JsonObject? Args = null);

    public sealed record CompositeTool(
        [property: JsonPropertyName("name")] string Name,
        [property: JsonPropertyName("description")] string Description,
        [property: JsonPropertyName("params")] IReadOnlyList<ToolParam> Params,
        [property: JsonPropertyName("steps")] IReadOnlyList<CompositeStep> Steps,
        [property: JsonPropertyName("output")] string Output,
        [property: JsonPropertyName("enabled")] bool Enabled,
        [property: JsonPropertyName("needs_confirm")] bool NeedsConfirm,
        [property: JsonPropertyName("group_name")] string GroupName);

    public sealed record Category(
        [property: JsonPropertyName("name")] string Name,
        [property: JsonPropertyName("total")] int Total,
        [property: JsonPropertyName("enabled")] int Enabled)
    {
        public bool IsUncategorized => Name.Length == 0;
        public string DisplayName => Name.Length == 0 ? "未分類" : Name;
    }

    public sealed record CategoryOverview(
        [property: JsonPropertyName("categories")] IReadOnlyList<Category> Categories,
        [property: JsonPropertyName("uncategorized")] Category? Uncategorized)
    {
        /// <summary>未分類只在真的有工具時才顯示 —— 否則是一個永遠空著的假項目。</summary>
        public IReadOnlyList<Category> All =>
            Uncategorized is { Total: > 0 } u ? [.. Categories, u] : Categories;
    }

    public sealed record TestResult(
        [property: JsonPropertyName("ok")] bool Ok,
        [property: JsonPropertyName("texts")] IReadOnlyList<string>? Texts,
        [property: JsonPropertyName("error")] string? Error)
    {
        public string Display => Ok
            ? string.Join("\n", Texts ?? [])
            : Error ?? "失敗,但後端沒有說原因";
    }

    public sealed record ImportResult(
        [property: JsonPropertyName("added")] IReadOnlyList<string> Added,
        [property: JsonPropertyName("skipped")] IReadOnlyList<string> Skipped,
        [property: JsonPropertyName("sources")] IReadOnlyList<string>? Sources);

    public sealed record ClaudeEntry(
        [property: JsonPropertyName("name")] string Name,
        [property: JsonPropertyName("slug")] string Slug,
        [property: JsonPropertyName("transport")] string Transport,
        [property: JsonPropertyName("target")] string Target,
        [property: JsonPropertyName("auth_type")] string AuthType,
        [property: JsonPropertyName("already_exists")] bool AlreadyExists);

    public sealed record ClaudePreview(
        [property: JsonPropertyName("sources")] IReadOnlyList<string> Sources,
        [property: JsonPropertyName("entries")] IReadOnlyList<ClaudeEntry> Entries);

    public sealed record StepTool(
        [property: JsonPropertyName("name")] string Name,
        [property: JsonPropertyName("description")] string Description);

    // ── 下游的建立與編輯 ──────────────────────────────────

    public Task<Server> CreateServerAsync(object body, CancellationToken ct = default) =>
        SendAsync<Server>(HttpMethod.Post, "servers", body, ct);

    public Task<Server> UpdateServerAsync(string slug, object body,
                                          CancellationToken ct = default) =>
        SendAsync<Server>(HttpMethod.Patch, $"servers/{slug}", body, ct);

    public Task DeleteServerAsync(string slug, CancellationToken ct = default) =>
        SendVoidAsync(HttpMethod.Delete, $"servers/{slug}", null, ct);

    public Task<CheckResult> RefreshServerAsync(string slug, CancellationToken ct = default) =>
        SendAsync<CheckResult>(HttpMethod.Post, $"servers/{slug}/refresh", null, ct);

    public sealed record OAuthStart(
        [property: JsonPropertyName("authorization_url")] string AuthorizationUrl);

    /// <summary>
    /// 開始 OAuth 授權。後端回一個授權網址,由這邊用系統瀏覽器打開;
    /// callback 由後端的 /oauth/callback 接住 —— 那也是為什麼 API 一定要和
    /// 授權流程在同一個行程裡。
    /// </summary>
    /// <summary>忘掉這台下游的 OAuth 註冊與 token,下次授權會重跑一次動態註冊。</summary>
    public Task ResetOAuthAsync(string slug, CancellationToken ct = default) =>
        SendVoidAsync(HttpMethod.Delete, $"servers/{slug}/oauth", null, ct);

    public Task<OAuthStart> StartOAuthAsync(string slug, CancellationToken ct = default) =>
        SendAsync<OAuthStart>(HttpMethod.Post, $"servers/{slug}/oauth/start",
                              new { }, ct);

    // ── 自訂工具 ──────────────────────────────────────────

    public Task<IReadOnlyList<CustomTool>> CustomToolsAsync(CancellationToken ct = default) =>
        GetAsync<IReadOnlyList<CustomTool>>("custom-tools", ct);

    public Task<CustomTool> CreateCustomToolAsync(object body, CancellationToken ct = default) =>
        SendAsync<CustomTool>(HttpMethod.Post, "custom-tools", body, ct);

    public Task<CustomTool> UpdateCustomToolAsync(string name, object body,
                                                  CancellationToken ct = default) =>
        SendAsync<CustomTool>(HttpMethod.Patch, $"custom-tools/{name}", body, ct);

    public Task DeleteCustomToolAsync(string name, CancellationToken ct = default) =>
        SendVoidAsync(HttpMethod.Delete, $"custom-tools/{name}", null, ct);

    public Task<TestResult> TestCustomToolAsync(string name, JsonObject args,
                                                CancellationToken ct = default) =>
        SendAsync<TestResult>(HttpMethod.Post, $"custom-tools/{name}/test",
                              new { arguments = args }, ct);

    // ── 複合工具 ──────────────────────────────────────────

    public Task<IReadOnlyList<CompositeTool>> CompositeToolsAsync(CancellationToken ct = default) =>
        GetAsync<IReadOnlyList<CompositeTool>>("composite-tools", ct);

    public Task<CompositeTool> CreateCompositeToolAsync(object body,
                                                        CancellationToken ct = default) =>
        SendAsync<CompositeTool>(HttpMethod.Post, "composite-tools", body, ct);

    public Task<CompositeTool> UpdateCompositeToolAsync(string name, object body,
                                                        CancellationToken ct = default) =>
        SendAsync<CompositeTool>(HttpMethod.Patch, $"composite-tools/{name}", body, ct);

    public Task DeleteCompositeToolAsync(string name, CancellationToken ct = default) =>
        SendVoidAsync(HttpMethod.Delete, $"composite-tools/{name}", null, ct);

    public Task<TestResult> TestCompositeToolAsync(string name, JsonObject args,
                                                   CancellationToken ct = default) =>
        SendAsync<TestResult>(HttpMethod.Post, $"composite-tools/{name}/test",
                              new { arguments = args }, ct);

    /// <summary>複合工具的步驟可以挑哪些工具。名單是後端實際可用的。</summary>
    public Task<IReadOnlyList<StepTool>> StepToolsAsync(CancellationToken ct = default) =>
        GetAsync<IReadOnlyList<StepTool>>("step-tools", ct);

    // ── 分類 ──────────────────────────────────────────────

    public Task<CategoryOverview> CategoriesAsync(CancellationToken ct = default) =>
        GetAsync<CategoryOverview>("categories", ct);

    public Task<CategoryOverview> CreateCategoryAsync(string name,
                                                      CancellationToken ct = default) =>
        SendAsync<CategoryOverview>(HttpMethod.Post, "categories", new { name }, ct);

    public Task DeleteCategoryAsync(string name, CancellationToken ct = default) =>
        SendVoidAsync(HttpMethod.Delete, $"categories/{name}", null, ct);

    // ── 接進 AI client ────────────────────────────────────

    public sealed record ClientTarget(
        [property: JsonPropertyName("id")] string Id,
        [property: JsonPropertyName("label")] string Label,
        [property: JsonPropertyName("path")] string Path,
        [property: JsonPropertyName("detected")] bool Detected,
        [property: JsonPropertyName("installed")] bool Installed,
        [property: JsonPropertyName("stale")] bool Stale,
        [property: JsonPropertyName("note")] string Note);

    public sealed record ClientResult(
        [property: JsonPropertyName("path")] string Path,
        [property: JsonPropertyName("note")] string Note);

    public Task<IReadOnlyList<ClientTarget>> ClientsAsync(CancellationToken ct = default) =>
        GetAsync<IReadOnlyList<ClientTarget>>("clients", ct);

    public Task<ClientResult> InstallClientAsync(string id, CancellationToken ct = default) =>
        SendAsync<ClientResult>(HttpMethod.Post, $"clients/{id}/install", new { }, ct);

    public Task<ClientResult> UninstallClientAsync(string id, CancellationToken ct = default) =>
        SendAsync<ClientResult>(HttpMethod.Delete, $"clients/{id}", null, ct);

    // ── 匯入 / 匯出 ───────────────────────────────────────

    public Task<ClaudePreview> PeekClaudeConfigAsync(CancellationToken ct = default) =>
        GetAsync<ClaudePreview>("import/claude-config", ct);

    public Task<ImportResult> ImportClaudeConfigAsync(CancellationToken ct = default) =>
        SendAsync<ImportResult>(HttpMethod.Post, "import/claude-config", null, ct);

    public Task<ImportResult> ImportMcpServersAsync(string config,
                                                    CancellationToken ct = default) =>
        SendAsync<ImportResult>(HttpMethod.Post, "import/mcp-servers", new { config }, ct);

    /// <summary>
    /// 匯出設定。預設不含密文 —— 匯出檔很容易被順手貼到別的地方,
    /// 預設帶金鑰是在幫使用者外洩。
    /// </summary>
    public Task<JsonObject> ExportConfigAsync(bool includeSecrets = false,
                                              CancellationToken ct = default) =>
        GetAsync<JsonObject>(
            $"config/export?include_secrets={(includeSecrets ? "true" : "false")}", ct);
}
