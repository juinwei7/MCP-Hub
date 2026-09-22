# 組裝 MCP Hub.app。
#
# 刻意不依賴 Xcode 專案檔 —— swift build 加一個組裝步驟就夠,而且可以在
# 沒有 GUI 的情況下執行(CI、遠端、自動化)。
#
# 開發時跑:make run
# 打包時跑:make app   → build/MCP Hub.app

APP      := build/MCP Hub.app
REPO     := $(shell cd .. && pwd)
PYTHON   := $(REPO)/.venv/bin/python

# 交叉打包:ARCH=x86_64 make dist 會在 Apple Silicon 上產出 Intel 版。
# GitHub 已經不提供 Intel 的 macOS runner(macos-13 退役),這是還能出
# Intel 版的唯一路徑。
ARCH     ?= $(shell uname -m)
# 用 --show-bin-path 問 SPM,不要自己拼 .build/release —— 那是指向原生架構的
# 符號連結,交叉建置時它不會跟著換,於是會默默打包錯架構的執行檔。
# 用 = 而不是 := ,這樣只有真的要用時才去問(make clean 不必)。
BIN       = $(shell swift build -c release --arch $(ARCH) --show-bin-path)/MCPHub

.PHONY: all app dist sign run debug clean test release

all: app

# 一定要是 phony:把它寫成檔案目標時,只要 .build/release/MCPHub 已存在,
# make 就認定不用重建 —— 於是 make app 會默默打包出舊的執行檔。
# SPM 自己會做增量判斷,重跑的成本接近零。
release:
	swift build -c release --arch $(ARCH)

app: release
	rm -rf "$(APP)"
	mkdir -p "$(APP)/Contents/MacOS" "$(APP)/Contents/Resources"
	cp "$(BIN)" "$(APP)/Contents/MacOS/MCPHub"
	cp Resources/Info.plist "$(APP)/Contents/Info.plist"
	@# 沒有這行,Dock、Finder、Alt-Tab 裡都是那個空白的預設圖示。
	@# 圖示由 design/MarkRenderer.swift 產生,改了要重跑它。
	cp Resources/MCPHub.icns "$(APP)/Contents/Resources/MCPHub.icns"
	@# ad-hoc 簽章:通知與開機啟動都要求 app 有簽章身分。
	@# 這不需要 Developer 帳號,但也不能散布給別人(見 sign-and-notarize.sh)。
	codesign --force --deep --sign - "$(APP)"
	@echo "已產出 $(APP)(ad-hoc 簽章,未內附 Python)"
	@echo "開發時可直接跑;要給別人請改用 make dist。"

# 自足版本:內附 Python runtime 與 gateway 原始碼,不需要專案目錄
dist: app
	MCPHUB_ARCH=$(ARCH) ./scripts/bundle-backend.sh "$(APP)"
	@# 一定要在內附之後重簽。bundle-backend.sh 往 Contents/Resources 塞了上百個
	@# 檔案,那會破壞 make app 蓋的封印 —— codesign --verify 會說
	@# 「a sealed resource is missing or invalid」。而通知與開機啟動都要求
	@# 簽章驗得過,所以壞掉的封印不只是驗證時難看,是功能會少。
	codesign --force --deep --sign - "$(APP)"
	@codesign --verify --deep --strict "$(APP)" \
		&& echo "簽章驗證通過" \
		|| { echo "✗ 簽章驗證失敗"; exit 1; }

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
