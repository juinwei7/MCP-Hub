# 部署 MCP Hub

Hub 有兩個角色:
- **管理台**(網頁):`gateway.web`,你用瀏覽器管理下游、工具、看 logs。
- **聚合器**(給 Claude):`gateway.hub_server`,以 **stdio** 被 Claude 啟動,把所有下游工具合併曝露。

兩者共用同一份 `actions.db`。

---

## 方式 A:一鍵腳本(本機日常用,推薦)

```bash
./start.sh
```

會自動:建 `.venv` → 裝依賴 → 開管理台(http://localhost:8765),並印出接入指令:

```bash
claude mcp add my-hub -e PYTHONPATH="<專案路徑>" -- "<專案>/.venv/bin/python" -m gateway.hub_server
```

- 管理台與聚合器共用同台機器的 `actions.db`,改完即時生效。
- 零額外依賴(`cryptography` 等都在 `requirements.txt`)。
- 自訂 port:`MCP_HUB_PORT=9000 ./start.sh`。

---

## 方式 B:Docker(要隔離 / 搬機器)

```bash
docker compose up -d --build
```

- 管理台:http://localhost:8765(compose 只綁 `127.0.0.1`,維持「無登入、僅本機可連」)。
- `actions.db` + `.secret_key` 存在具名 volume `hubdata`,容器重建不遺失。
- 映像內含 **git / Node(npx)**,所以推薦目錄裡的 Node 版與 git server 在容器內也能跑。

### 讓 Claude 接上容器裡的 Hub

Claude 跑在 host、用 stdio 啟動聚合器。容器化時,把「啟動指令」換成 `docker exec` 進容器:

```bash
claude mcp add my-hub -- docker exec -i <容器名> python -m gateway.hub_server
```

（容器需先 `docker compose up -d` 在跑;`<容器名>` 用 `docker ps` 查,通常是 `mcp-gateway-mcp-hub-1`。）

這樣管理台(容器內)和聚合器(同容器內)共用同一份 `/data/actions.db`,一致。

---

## 安全備註

- 管理台**沒有登入**,靠「只綁 `127.0.0.1`」保護 —— 單人本機用足夠。要開遠端才需要加驗證。
- `.secret_key` 是解開 DB 內密鑰的金鑰,**請單獨備份**;遺失則 `actions.db` 裡的 token 解不回來。
- 匯出的設定含本機絕對路徑(stdio 下游),搬機器可能要改(匯出檔內已有 `_note` 提醒)。
