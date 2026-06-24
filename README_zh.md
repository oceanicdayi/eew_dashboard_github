# EEW Dashboard GitHub — 中文說明

> 台灣地震預警（EEW）系統狀態暨資料視覺化儀表板，透過 GitHub Actions 自動部署至 Hugging Face Spaces。

---

## 目錄

1. [專案目的](#專案目的)
2. [整體架構](#整體架構)
3. [檔案結構](#檔案結構)
4. [功能說明](#功能說明)
   - [系統狀態頁籤](#系統狀態頁籤)
   - [地震事件頁籤](#地震事件頁籤)
   - [靜態波形頁籤](#靜態波形頁籤)
5. [資料來源](#資料來源)
6. [部署流程](#部署流程)
7. [環境變數與密鑰](#環境變數與密鑰)
8. [本機開發與測試](#本機開發與測試)
9. [依賴套件](#依賴套件)

---

## 專案目的

本專案為台灣地震預警（Earthquake Early Warning，EEW）系統的前端監控儀表板。  
其核心目標為：

- **即時監控** Earthworm EEW 系統與相關 Docker 容器的運行健康狀態。
- **視覺化呈現** 由 `.rep` 格式的地震解算報告中萃取出的地震事件，包含震源位置（經緯度）、規模、深度、發震時間及觸發測站分布。
- **展示 TSMIP 波形圖片**，提供每次地震事件的地震波形縮圖與圖庫。
- 提供一個持續可存取的公開網頁介面，部署於 [Hugging Face Spaces](https://huggingface.co/spaces/oceanicdayi/Eew_dashboard)，無需本機環境即可查看。

---

## 整體架構

```
┌─────────────────────────────────────────────────────────────────┐
│                     GitHub Repository                           │
│  app.py / deploy.py / test_loop.py / fixtures/ / requirements   │
└────────────────────────────┬────────────────────────────────────┘
                             │ git push to main
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│             GitHub Actions  (.github/workflows/deploy-hf.yml)   │
│  1. 安裝相依套件                                                 │
│  2. 執行 test_loop.py  驗收 fixture 資料                        │
│  3. 執行 deploy.py     上傳檔案至 HF Space（含重試機制）         │
└────────────────────────────┬────────────────────────────────────┘
                             │ huggingface_hub API（含指數退避重試）
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│           Hugging Face Space  oceanicdayi/Eew_dashboard         │
│             Gradio  app.py  ── 三個頁籤的互動式儀表板            │
└────────────┬─────────────────────────┬───────────────────────────┘
             │ hf_hub_download          │ hf_hub_download
             ▼                         ▼
┌────────────────────┐    ┌────────────────────────────────────────┐
│  HF Dataset        │    │  HF Dataset                            │
│  oceanicdayi/      │    │  oceanicdayi/eew_hermes_dashboard      │
│  eew_status        │    │  status/*.json   → 地震事件             │
│  status/*.json     │    │  tsmip/waveform/ → 波形圖片            │
│  → 系統狀態         │    │  status/rep_summary_detailed.md       │
└────────────────────┘    └────────────────────────────────────────┘
```

**資料流向簡述：**

1. 外部 EEW agent（`oceanicdayi/DEV_agent`）持續蒐集 Earthworm 系統狀態與地震 `.rep` 解算結果，並上傳至兩個 Hugging Face 資料集。
2. 本 GitHub 儲存庫維護儀表板程式碼；每次推送至 `main` 分支時，GitHub Actions 會自動將最新版本同步至 HF Space。
3. Gradio 應用程式在 HF Space 啟動後，於用戶操作時直接從 HF 資料集動態讀取最新資料，無需重新部署。

---

## 檔案結構

```
eew_dashboard_github/
├── app.py                         # Gradio 儀表板主程式
├── deploy.py                      # 部署腳本（上傳至 HF Space，含重試）
├── test_loop.py                   # 驗收測試：驗證 fixture 資料格式
├── requirements.txt               # Python 相依套件清單
├── fixtures/                      # 範例 / 回放資料
│   ├── normal_event.json          # 正常地震事件（含測站資料）
│   ├── large_event.json           # 大型地震事件
│   ├── quiet_standby.json         # 無事件待機狀態
│   ├── malformed.json             # 格式異常資料（邊界測試用）
│   └── history_sample.csv         # 歷史事件 CSV 範例
└── .github/
    └── workflows/
        └── deploy-hf.yml          # GitHub Actions CI/CD 工作流程
```

---

## 功能說明

### 系統狀態頁籤

**資料來源：** `oceanicdayi/eew_status` HF 資料集  
**功能：**

- 從 JSON 狀態檔中解析各 Docker 容器（`earthworm_eew`、`my_agent_container` 等）及模組的運行狀態。
- 以**綠燈（Alive）** 和**色標卡片**分開呈現：
  - 🟢 `alive`：顯示閃爍綠燈，表示模組正常運行。
  - 🔵 `ok/healthy`：藍色標籤。
  - 🟡 `warn/degraded`：黃色警示卡。
  - 🔴 `error/stopped`：紅色錯誤卡。
- 支援從下拉選單選取不同時間的狀態快照，或點擊「重新讀取」即時更新檔案清單。
- 容錯機制：若 HF 資料集無法存取，自動改讀本機 `fixtures/normal_event.json`。

### 地震事件頁籤

**資料來源：** `oceanicdayi/eew_hermes_dashboard/status/*.json`  
**功能：**

- **批次載入**：掃描 `status/` 資料夾中所有 JSON 檔，並行解析多筆地震事件。
- **優先解析 `.rep` 格式**：若 JSON 中包含 `latest_rep_header`（Earthworm .rep 原始報告頭），則直接以結構化方式解析年月日、時分秒、緯度、經度、深度、規模（Mall、Mpd、Mtc）及觸發測站座標。
- **遞迴候選算法**：對不符合 `.rep` 格式的通用 JSON，以遞迴搜尋的方式收集所有含有緯度/經度欄位的物件，再依評分演算法（包含欄位比對分數、路徑名稱加權、台灣地理範圍加成）選出最佳事件候選。
- **事件資訊表格**：顯示來源檔案、發震時間、規模、深度、座標、位置描述及觸發測站數。
- **互動式地圖**（Folium + OpenStreetMap）：以紅色圓圈標記震央，圓圈大小隨規模縮放；藍色小點標記觸發測站；彈出視窗顯示詳細資訊。
- **解析文字摘要**：讀取 `status/rep_summary_detailed.md` 並顯示於頁籤下方。

### 靜態波形頁籤

**資料來源：** `oceanicdayi/eew_hermes_dashboard/tsmip/waveform/`  
**功能：**

- 每次點擊「載入」皆清除快取，重新讀取資料夾中最新的波形圖片清單（`.png`、`.jpg`、`.jpeg`、`.webp`）。
- **重點圖（Featured）**：以大圖展示清單中第一張波形圖片及其路徑資訊。
- **縮圖橫列**：以可水平滾動的縮圖條顯示所有圖片（最多 48 張），點擊可在新分頁開啟原圖。
- **圖庫（Gallery）**：以響應式格狀排版顯示所有波形圖的詳細卡片（含檔名、路徑與原圖連結）。
- 若圖片超過 48 張，底部顯示「另有 N 張未顯示」提示。

---

## 資料來源

| 用途 | Hugging Face 資料集 ID | 路徑 |
|------|------------------------|------|
| 系統狀態 | `oceanicdayi/eew_status` | `status/eew_status_report.json` |
| 地震事件 | `oceanicdayi/eew_hermes_dashboard` | `status/*.json` |
| 事件文字摘要 | `oceanicdayi/eew_hermes_dashboard` | `status/rep_summary_detailed.md` |
| TSMIP 波形圖片 | `oceanicdayi/eew_hermes_dashboard` | `tsmip/waveform/*.png` |

---

## 部署流程

本專案採用 **GitHub Actions → Hugging Face Space** 的自動化 CI/CD 流程。

### 觸發條件

以下任一情況會自動觸發部署：

- 推送至 `main` 分支，且修改了以下任一檔案：
  - `app.py`、`requirements.txt`、`test_loop.py`、`deploy.py`
  - `fixtures/**`（任何 fixture 檔案）
  - `.github/workflows/deploy-hf.yml`
- 在 GitHub Actions 頁籤手動觸發（`workflow_dispatch`）。

### 部署步驟

```
1. Checkout 程式碼
2. 安裝 Python 3.11 與 requirements.txt 中的相依套件
3. 執行驗收測試：EEW_DATA_SOURCE=replay python test_loop.py
   ├── 確認所有必要 fixture 檔案存在
   ├── 確認各 JSON fixture 可被正確解析
   └── 確認 malformed fixture 的異常處理正確
4. 執行部署：python deploy.py
   ├── 逐一上傳 app.py、requirements.txt、test_loop.py
   └── 整個 fixtures/ 資料夾上傳至 HF Space
```

### 重試機制

`deploy.py` 內建**指數退避（Exponential Backoff）重試機制**，以應對 Hugging Face API 的 429 Too Many Requests 速率限制：

| 參數 | 預設值 | 環境變數 |
|------|--------|----------|
| 最大重試次數 | 6 | `EEW_DEPLOY_MAX_RETRIES` |
| 基礎等待秒數 | 30 秒 | `EEW_DEPLOY_BASE_WAIT_SECONDS` |
| 等待公式 | `base × 2^(attempt-1)` | — |

### 並發保護

工作流程設有 `concurrency` 群組鎖定，同時觸發多次部署時，僅保留最新一次，舊的自動取消。

---

## 環境變數與密鑰

| 名稱 | 說明 | 設定位置 |
|------|------|----------|
| `HF_TOKEN` | Hugging Face API Token（需有 Space 的寫入權限）| GitHub → Settings → Secrets and variables → Actions |
| `EEW_SPACE_ID` | 目標 HF Space ID（預設：`oceanicdayi/Eew_dashboard`）| `deploy-hf.yml` 或本機環境變數 |
| `EEW_SKIP_PREDEPLOY_TEST` | 設為 `"1"` 可跳過 `deploy.py` 內的驗收測試（CI 已跑過時使用）| `deploy-hf.yml` |
| `EEW_DEPLOY_MAX_RETRIES` | 上傳重試上限（選填，預設 6）| 本機環境變數 |
| `EEW_DEPLOY_BASE_WAIT_SECONDS` | 重試基礎等待秒數（選填，預設 30）| 本機環境變數 |

---

## 本機開發與測試

### 安裝相依套件

```bash
pip install -r requirements.txt
pip install gradio  # HF Space 自動提供，本機需手動安裝
```

### 執行驗收測試

```bash
EEW_DATA_SOURCE=replay python test_loop.py
# 輸出：OK: replay fixtures validated
```

### 本機啟動儀表板

```bash
python app.py
# 預設開啟 http://127.0.0.1:7860
```

### 手動部署至 HF Space

```bash
export HF_TOKEN=hf_xxxxxxxxxxxx
export EEW_SPACE_ID=your-account/your-space  # 選填，預設 oceanicdayi/Eew_dashboard
python deploy.py
```

---

## 依賴套件

| 套件 | 用途 |
|------|------|
| `gradio` | 互動式 Web UI 框架（由 HF Space 自動提供版本）|
| `folium==0.17.0` | 互動式地圖（Leaflet.js 封裝）|
| `plotly>=5.24.1,<7.0` | 圖表視覺化（保留供未來擴充）|
| `pandas==2.2.3` | 資料處理（CSV 歷史資料）|
| `requests==2.32.3` | HTTP 請求 |
| `huggingface_hub>=1.2.0,<2.0` | HF 資料集下載與 Space 部署 API |

---

## 授權

本專案以 [LICENSE](./LICENSE) 文件所述授權條款釋出。
