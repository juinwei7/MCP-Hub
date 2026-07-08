# MCP Hub —— 管理台 + 聚合器
FROM python:3.11-slim

# 讓 stdio 下游可用:git(mcp-server-git 需要)、Node/npx(Node 版目錄 server 需要)
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先裝依賴(善用 layer 快取)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway/ ./gateway/

# 狀態與金鑰放獨立 volume,容器重建也不遺失
ENV MCP_HUB_DB=/data/actions.db \
    MCP_HUB_KEY=/data/.secret_key \
    MCP_HUB_HOST=0.0.0.0 \
    MCP_HUB_PORT=8765
VOLUME ["/data"]
EXPOSE 8765

CMD ["python", "-m", "gateway.web"]
