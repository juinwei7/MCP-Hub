# 組裝 MCP Hub.app。
#
# 刻意不依賴 Xcode 專案檔 —— swift build 加一個組裝步驟就夠,而且可以在
# 沒有 GUI 的情況下執行(CI、遠端、自動化)。
#
# 開發時跑:make run
# 打包時跑:make app   → build/MCP Hub.app

APP      := build/MCP Hub.app
BIN      := .build/release/MCPHub
REPO     := $(shell cd .. && pwd)
PYTHON   := $(REPO)/.venv/bin/python

.PHONY: all app dist sign run debug clean test release

all: app

# 一定要是 phony:把它寫成檔案目標時,只要 .build/release/MCPHub 已存在,
# make 就認定不用重建 —— 於是 make app 會默默打包出舊的執行檔。
# SPM 自己會做增量判斷,重跑的成本接近零。
release:
	swift build -c release

app: release
	rm -rf "$(APP)"
	mkdir -p "$(APP)/Contents/MacOS" "$(APP)/Contents/Resources"
	cp "$(BIN)" "$(APP)/Contents/MacOS/MCPHub"
	cp Resources/Info.plist "$(APP)/Contents/Info.plist"
	@# ad-hoc 簽章:通知與開機啟動都要求 app 有簽章身分。
	@# 這不需要 Developer 帳號,但也不能散布給別人(見 sign-and-notarize.sh)。
	codesign --force --deep --sign - "$(APP)"
	@echo "已產出 $(APP)(ad-hoc 簽章,未內附 Python)"
	@echo "開發時可直接跑;要給別人請改用 make dist。"

# 自足版本:內附 Python runtime 與 gateway 原始碼,不需要專案目錄
dist: app
	./scripts/bundle-backend.sh "$(APP)"

# 以 Developer ID 簽章並公證。需要 Apple Developer 帳號,
# 缺憑證時腳本會說明缺什麼。
sign: dist
	./scripts/sign-and-notarize.sh "$(APP)"

# 開發用:直接跑,後端指向專案的 .venv
run: debug
	MCPHUB_PYTHON="$(PYTHON)" MCPHUB_REPO="$(REPO)" ./.build/debug/MCPHub

debug:
	swift build

test:
	swift test
	./scripts/acceptance.sh

clean:
	rm -rf .build build
