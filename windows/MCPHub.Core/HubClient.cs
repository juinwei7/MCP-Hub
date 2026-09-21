using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace MCPHub.Core;

/// <summary>
/// Hub 後端 JSON API 的 client。
///
/// 只負責傳輸與解碼 —— 任何「要做什麼」的判斷都留在 Python 端。
/// 端點與欄位名稱與 macOS 版完全相同,兩個平台共用同一份 API 契約;
/// 這裡若為了 C# 方便而改名,契約就開始分岔了。
/// </summary>
public sealed class HubClient : IDisposable
{
    private readonly HttpClient _http;
    private readonly JsonSerializerOptions _json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    public HubClient(string token, int port = BackendSupervisor.Port)
    {
        _http = new HttpClient
        {
            BaseAddress = new Uri($"http://127.0.0.1:{port}/api/v1/"),
            Timeout = TimeSpan.FromSeconds(15),
        };
        _http.DefaultRequestHeaders.Add("Authorization", $"Bearer {token}");
    }

    public void Dispose() => _http.Dispose();

    // ── 資料形狀 ──────────────────────────────────────────

    public sealed record Server(
        [property: JsonPropertyName("slug")] string Slug,
        [property: JsonPropertyName("name")] string Name,
        [property: JsonPropertyName("base_url")] string BaseUrl,
        [property: JsonPropertyName("transport")] string Transport,
        [property: JsonPropertyName("auth_type")] string AuthType,
        [property: JsonPropertyName("enabled")] bool Enabled,
        [property: JsonPropertyName("status")] string Status,
        [property: JsonPropertyName("status_detail")] string StatusDetail,
        [property: JsonPropertyName("has_token")] bool HasToken,
        [property: JsonPropertyName("tool_count")] int? ToolCount = null)
    {
        public bool IsHealthy => Status == "ok";
        public bool IsErrored => Status == "error";
    }

    public sealed record Tool(
        [property: JsonPropertyName("name")] string Name,
        [property: JsonPropertyName("description")] string Description,
        [property: JsonPropertyName("desc_override")] string DescOverride,
        [property: JsonPropertyName("enabled")] bool Enabled,
        [property: JsonPropertyName("needs_confirm")] bool NeedsConfirm)
    {
        public string ShownDescription =>
            string.IsNullOrEmpty(DescOverride) ? Description : DescOverride;
    }

    public sealed record LogRow(
        [property: JsonPropertyName("id")] int Id,
        [property: JsonPropertyName("time")] string Time,
        [property: JsonPropertyName("server_slug")] string ServerSlug,
        [property: JsonPropertyName("tool")] string Tool,
        [property: JsonPropertyName("status")] string Status,
        [property: JsonPropertyName("duration_ms")] int? DurationMs,
        [property: JsonPropertyName("error")] string Error)
    {
        public bool IsError => Status == "error";
        public string QualifiedName =>
            string.IsNullOrEmpty(ServerSlug) ? Tool : $"{ServerSlug}__{Tool}";
    }

    public sealed record LogPage(
        [property: JsonPropertyName("rows")] IReadOnlyList<LogRow> Rows,
        [property: JsonPropertyName("page")] int Page,
        [property: JsonPropertyName("pages")] int Pages,
        [property: JsonPropertyName("total")] int Total,
        [property: JsonPropertyName("errors")] int Errors);

    public sealed record HubAction(
        [property: JsonPropertyName("id")] string Id,
        [property: JsonPropertyName("tool")] string Tool,
        [property: JsonPropertyName("status")] string Status,
        [property: JsonPropertyName("created_at")] string CreatedAt,
        [property: JsonPropertyName("decided_at")] string DecidedAt)
    {
        public bool IsWaiting => Status == "WAITING";
    }

    public sealed record CheckResult(
        [property: JsonPropertyName("status")] string Status,
        [property: JsonPropertyName("detail")] string Detail,
        [property: JsonPropertyName("tool_count")] int ToolCount);

    public sealed record Health(
        [property: JsonPropertyName("ready")] bool Ready,
        [property: JsonPropertyName("db_path")] string DbPath,
        [property: JsonPropertyName("api_enabled")] bool ApiEnabled);

    /// <summary>後端的錯誤一律是扁平的 {"error", "message"}。</summary>
    public sealed record ApiError(
        [property: JsonPropertyName("error")] string Error,
        [property: JsonPropertyName("message")] string Message);

    public sealed class HubException : Exception
    {
        public string? Code { get; }
        public HubException(string message, string? code = null) : base(message) => Code = code;
    }

    // ── 下游 ──────────────────────────────────────────────

    public Task<IReadOnlyList<Server>> ServersAsync(CancellationToken ct = default) =>
        GetAsync<IReadOnlyList<Server>>("servers", ct);

    public Task<Server> SetEnabledAsync(string slug, bool enabled, CancellationToken ct = default) =>
        SendAsync<Server>(HttpMethod.Patch, $"servers/{slug}/enabled",
                          new { enabled }, ct);

    public Task<CheckResult> CheckAsync(string slug, CancellationToken ct = default) =>
        SendAsync<CheckResult>(HttpMethod.Post, $"servers/{slug}/check", null, ct);

    public Task<Dictionary<string, int>> CheckAllAsync(CancellationToken ct = default) =>
        SendAsync<Dictionary<string, int>>(HttpMethod.Post, "servers/check-all", null, ct);

    // ── 工具 ──────────────────────────────────────────────

    public Task<IReadOnlyList<Tool>> ToolsAsync(string slug, CancellationToken ct = default) =>
        GetAsync<IReadOnlyList<Tool>>($"servers/{slug}/tools", ct);

    public Task<Tool> SetToolAsync(string slug, string tool,
                                   bool? enabled = null, bool? needsConfirm = null,
                                   CancellationToken ct = default)
    {
        var body = new Dictionary<string, object>();
        if (enabled is not null) body["enabled"] = enabled.Value;
        if (needsConfirm is not null) body["needs_confirm"] = needsConfirm.Value;
        return SendAsync<Tool>(HttpMethod.Patch, $"servers/{slug}/tools/{tool}", body, ct);
    }

    public Task<IReadOnlyList<Tool>> SetAllToolsAsync(string slug, bool enabled,
                                                      CancellationToken ct = default) =>
        SendAsync<IReadOnlyList<Tool>>(HttpMethod.Patch, $"servers/{slug}/tools",
                                       new { enabled }, ct);

    // ── 記錄與待確認 ──────────────────────────────────────

    public Task<LogPage> LogsAsync(int page = 1, int perPage = 50, bool errorsOnly = false,
                                   CancellationToken ct = default) =>
        GetAsync<LogPage>($"logs?page={page}&per_page={perPage}" +
                          $"&errors_only={errorsOnly.ToString().ToLowerInvariant()}", ct);

    public Task<IReadOnlyList<HubAction>> ActionsAsync(CancellationToken ct = default) =>
        GetAsync<IReadOnlyList<HubAction>>("actions", ct);

    public Task<HubAction> DecideAsync(string id, bool approve, CancellationToken ct = default) =>
        SendAsync<HubAction>(HttpMethod.Post, $"actions/{id}/decision",
                             new { approve }, ct);

    // ── 傳輸 ──────────────────────────────────────────────

    private Task<T> GetAsync<T>(string path, CancellationToken ct) =>
        SendAsync<T>(HttpMethod.Get, path, null, ct);

    private async Task<T> SendAsync<T>(HttpMethod method, string path,
                                       object? body, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(method, path);
        if (body is not null)
        {
            req.Content = new StringContent(
                JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        }

        HttpResponseMessage resp;
        try
        {
            resp = await _http.SendAsync(req, ct).ConfigureAwait(false);
        }
        catch (Exception e) when (e is not OperationCanceledException)
        {
            throw new HubException($"連線失敗:{e.Message}");
        }

        using (resp)
        {
            var text = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
            if (!resp.IsSuccessStatusCode)
            {
                throw new HubException(ErrorMessage(text, resp.StatusCode),
                                       TryParseError(text)?.Error);
            }

            var value = JsonSerializer.Deserialize<T>(text, _json);
            if (value is null) throw new HubException("後端回了空的內容");
            return value;
        }
    }

    private static string ErrorMessage(string body, System.Net.HttpStatusCode code) =>
        TryParseError(body)?.Message ?? $"HTTP {(int)code}";

    private static ApiError? TryParseError(string body)
    {
        try { return JsonSerializer.Deserialize<ApiError>(body); }
        catch (JsonException) { return null; }
    }
}
