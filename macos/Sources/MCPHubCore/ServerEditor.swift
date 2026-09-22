import SwiftUI

/// 新增 / 編輯下游。
///
/// Spec 003 當初刻意跳過這個表單 —— 下游極少變動,而表單是畫面成本最高的部分。
/// 但要拿掉網頁管理台就不能少它:否則連「加一台下游」都得開瀏覽器,
/// 那整個原生化就沒有意義。
///
/// 表單依 transport 換欄位。http 要網址與授權方式,stdio 要指令、參數、環境變數 ——
/// 兩者同時顯示的話,永遠有一半是灰的,看起來像壞掉。
struct ServerEditor: View {
    @ObservedObject var state: AppState
    let existing: HubClient.Server?
    let onDone: (String?) -> Void

    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var slug = ""
    @State private var transport = "http"
    @State private var baseURL = ""
    @State private var authType = "none"
    @State private var bearerToken = ""
    @State private var clearToken = false
    @State private var command = ""
    @State private var argsJSON = "[]"
    @State private var envJSON = "{}"
    @State private var banner: (String, AlertLine.Kind)?
    @State private var busy = false

    private var isNew: Bool { existing == nil }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(isNew ? "新增下游" : "編輯「\(name)」").font(.headline)
                Spacer()
            }
            .padding(Style.Space.section)

            if let banner {
                AlertLine(text: banner.0, kind: banner.1, onDismiss: { self.banner = nil })
            }

            ScrollView {
                VStack(alignment: .leading, spacing: Style.Space.section) {
                    FormRow(label: "名稱") {
                        TextField("OrgPulse", text: $name).textFieldStyle(.roundedBorder)
                    }

                    if isNew {
                        FormRow(label: "識別碼", hint: "留空自動產生") {
                            TextField(suggestedSlug, text: $slug)
                                .textFieldStyle(.roundedBorder).font(Style.Face.monoBody)
                        }
                    }

                    FormRow(label: "連線方式") {
                        Picker("", selection: $transport) {
                            Text("HTTP").tag("http")
                            Text("本機指令 (stdio)").tag("stdio")
                        }
                        .pickerStyle(.segmented).labelsHidden().frame(width: 240)
                        Spacer()
                    }

                    Divider()

                    // 依 transport 換欄位 —— 不相關的欄位不該佔著版面
                    if transport == "http" {
                        httpFields
                    } else {
                        stdioFields
                    }
                }
                .padding(Style.Space.section)
            }

            ActionBar {
                if !isNew {
                    Text("識別碼 \(existing?.slug ?? "") 不可更改")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            } trailing: {
                Button("取消") { onDone(nil); dismiss() }
                Button(isNew ? "新增" : "儲存") { save() }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || !isValid)
            }
        }
        .frame(width: 560, height: 480)
        .onAppear(perform: load)
    }

    private var httpFields: some View {
        VStack(alignment: .leading, spacing: Style.Space.section) {
            FormRow(label: "網址") {
                TextField("https://example.com/mcp", text: $baseURL)
                    .textFieldStyle(.roundedBorder).font(Style.Face.monoBody)
            }
            FormRow(label: "授權") {
                Picker("", selection: $authType) {
                    Text("不需要").tag("none")
                    Text("Bearer token").tag("bearer")
                    Text("OAuth").tag("oauth")
                }
                .labelsHidden().frame(width: 180)
                Spacer()
            }

            if authType == "bearer" {
                FormRow(label: "Token", hint: isNew ? "" : "留空不動") {
                    VStack(alignment: .leading, spacing: Style.Space.tight) {
                        SecureField(existing?.hasToken == true ? "已設定(留空不動)" : "貼上 token",
                                    text: $bearerToken)
                            .textFieldStyle(.roundedBorder)
                            .disabled(clearToken)
                        if existing?.hasToken == true {
                            Toggle("清除現有 token", isOn: $clearToken)
                                .font(.caption).toggleStyle(.checkbox)
                        }
                    }
                }
            }

            if authType == "oauth" {
                Banner(text: "建立之後回到下游清單,用「OAuth 授權」完成登入。", kind: .info)
            }
        }
    }

    private var stdioFields: some View {
        VStack(alignment: .leading, spacing: Style.Space.section) {
            FormRow(label: "指令") {
                TextField("npx", text: $command)
                    .textFieldStyle(.roundedBorder).font(Style.Face.monoBody)
            }
            VStack(alignment: .leading, spacing: Style.Space.tight) {
                Text("參數").font(.caption).foregroundStyle(.secondary)
                CodeEditor(placeholder: #"["-y", "@modelcontextprotocol/server-filesystem", "/path"]"#,
                           text: $argsJSON, minHeight: 60)
            }
            VStack(alignment: .leading, spacing: Style.Space.tight) {
                HStack(spacing: Style.Space.tight) {
                    Text("環境變數").font(.caption).foregroundStyle(.secondary)
                    Pill(text: "含金鑰", tone: .warn)
                }
                CodeEditor(placeholder: #"{"API_KEY": "…"}"#, text: $envJSON, minHeight: 60)
                if existing?.hasEnv == true {
                    Text("已設定的環境變數不會顯示在這裡 —— 那是金鑰。留空儲存會保留原本的。")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
            Text("指令會在這台機器上執行,所需的執行環境(node、git 等)要先裝好。")
                .font(.caption2).foregroundStyle(.tertiary)
        }
    }

    private var suggestedSlug: String {
        let lowered = name.lowercased()
        let mapped = lowered.map { $0.isLetter || $0.isNumber ? $0 : "_" }
        return String(mapped).trimmingCharacters(in: CharacterSet(charactersIn: "_"))
    }

    private var isValid: Bool {
        guard !name.trimmingCharacters(in: .whitespaces).isEmpty else { return false }
        return transport == "http" ? !baseURL.isEmpty : !command.isEmpty
    }

    private func load() {
        guard let s = existing else { return }
        name = s.name
        transport = s.transport
        baseURL = s.baseURL
        authType = s.authType
        command = s.command
        if let data = try? JSONSerialization.data(withJSONObject: s.args,
                                                  options: .withoutEscapingSlashes) {
            argsJSON = String(decoding: data, as: UTF8.self)
        }
    }

    private func save() {
        guard let args = parsedArgs(), let env = parsedEnv() else { return }
        Task {
            busy = true
            defer { busy = false }

            var body: [String: Any] = [
                "name": name.trimmingCharacters(in: .whitespaces),
                "transport": transport,
                "base_url": transport == "http" ? baseURL : "",
                "auth_type": transport == "http" ? authType : "none",
                "command": transport == "stdio" ? command : "",
                "args": args,
            ]
            // 省略 env 代表不動;stdio 且有填才送
            if transport == "stdio", !envJSON.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
               envJSON.trimmingCharacters(in: .whitespacesAndNewlines) != "{}" {
                body["env"] = env
            }
            // bearer_token 省略代表不動,送空字串代表清除
            if clearToken {
                body["bearer_token"] = ""
            } else if !bearerToken.isEmpty {
                body["bearer_token"] = bearerToken
            }

            do {
                let created: HubClient.Server
                if isNew {
                    let trimmed = slug.trimmingCharacters(in: .whitespaces)
                    if !trimmed.isEmpty { body["slug"] = trimmed }
                    created = try await state.client.createServer(body)
                } else {
                    created = try await state.client.updateServer(existing!.slug, body)
                }
                await state.refresh()
                onDone(created.slug)
                dismiss()
            } catch {
                banner = (error.localizedDescription, .problem)
            }
        }
    }

    private func parsedArgs() -> [Any]? {
        let text = argsJSON.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return [] }
        guard let parsed = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [Any] else {
            banner = ("參數必須是合法的 JSON 陣列", .problem)
            return nil
        }
        return parsed
    }

    private func parsedEnv() -> [String: Any]? {
        let text = envJSON.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty || text == "{}" { return [:] }
        guard let parsed = try? JSONSerialization.jsonObject(
                with: Data(text.utf8)) as? [String: Any] else {
            banner = ("環境變數必須是合法的 JSON 物件", .problem)
            return nil
        }
        return parsed
    }
}
