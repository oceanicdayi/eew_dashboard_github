# EEW Dashboard — 台灣地震預警儀表板

> 自動將 Gradio 儀表板部署至 Hugging Face Spaces，即時呈現台灣地震預警（EEW）系統狀態、歷史地震事件與 TSMIP 波形圖像。

---

## 目的

本專案為台灣地震預警（Earthquake Early Warning，EEW）系統提供一個**可視化監控儀表板**，主要達成以下目標：

1. **系統狀態監控** — 彙整 EEW 後端各模組（Docker 容器、Earthworm 模組等）的運作狀態，以燈號與卡片方式呈現。
2. **地震事件瀏覽** — 自動掃描並解析來自 Hugging Face 資料集的 JSON 報告，以表格和地圖展示所有解析成功的地震事件（震央座標、規模、深度、發震時間、測站分布）。
3. **波形圖像展示** — 從 TSMIP（臺灣強震即時監測系統）資料夾載入波形圖片，以重點圖、縮圖列與圖庫排版呈現。
4. **持續整合部署** — 透過 GitHub Actions 在每次推送 `main` 分支時，自動執行驗收測試並同步更新至 Hugging Face Space，確保線上版本始終為最新狀態。

---

## 專案架構

```
eew_dashboard_github/
├── app.py                        # Gradio 儀表板主程式
├── deploy.py                     # 部署腳本（上傳至 Hugging Face Space）
├── test_loop.py                  # 部署前驗收測試（replay 模式）
├── requirements.txt              # Python 相依套件
├── fixtures/                     # 範例／測試用資料
│   ├── normal_event.json         # 一般地震事件 JSON
│   ├── large_event.json          # 大規模地震事件 JSON
│   ├── quiet_standby.json        # 無事件待機狀態 JSON
│   ├── malformed.json            # 格式異常測試用 JSON
│   └── history_sample.csv        # 歷史地震樣本 CSV
└── .github/
    └── workflows/
        └── deploy-hf.yml         # GitHub Actions CI/CD 工作流程
```

---

## 主要元件說明

### `app.py` — 儀表板核心

以 [Gradio](https://www.gradio.app/) 框架建構，分為三個頁籤：

#### 頁籤一：系統狀態

- 從 Hugging Face 資料集 `oceanicdayi/eew_status` 下載 JSON 狀態報告。
- 解析報告中的模組清單（容器、服務、Earthworm 模組等），依狀態分類：
  - 🟢 **Alive**（綠燈）：模組正常運行。
  - 🔵 **OK / Healthy**：資訊型正常狀態。
  - 🟡 **Warning / Degraded**：部分異常警告。
  - 🔴 **Error / Down**：模組停止或失敗。
- 支援手動重新讀取狀態清單與切換不同狀態檔案。

#### 頁籤二：地震事件

- 從資料集 `oceanicdayi/eew_hermes_dashboard/status/` 下載所有 JSON 事件報告。
- 使用多層次解析策略：
  - 優先嘗試解析 **Earthworm `.rep` 格式**（`latest_rep_header`），直接讀取震源參數（年月日時分秒、緯度、經度、深度、規模、Mpd、Mtc）與測站資料。
  - 若不符合 `.rep` 格式，則以**遞迴掃描**方式在 JSON 中搜尋所有含有座標（緯度、經度）的物件，並依關鍵字評分（含震源深度、規模、發震時間欄位者得分較高），篩選出最可能的震源節點。
- 以 HTML 表格呈現所有已解析事件，欄位包含：來源檔案、發震時間、規模、深度、緯度、經度、位置描述、測站數量。
- 以 [Folium](https://python-visualization.github.io/folium/) 互動地圖標示所有震央（紅色圓點與圖釘）及測站位置（藍色圓點）。
- 同步載入 `rep_summary_detailed.md` 解析文字摘要。

#### 頁籤三：靜態波形

- 從資料集 `oceanicdayi/eew_hermes_dashboard/tsmip/waveform/` 列舉所有波形圖片（`.png`、`.jpg`、`.jpeg`、`.webp`），最多顯示 48 張。
- 排版分為：重點大圖、水平縮圖滾動列、完整圖庫網格。

---

### `deploy.py` — 部署腳本

負責將本機檔案上傳至 Hugging Face Space `oceanicdayi/Eew_dashboard`：

1. 執行前先跑 `test_loop.py` 驗收測試（可透過環境變數 `EEW_SKIP_PREDEPLOY_TEST=1` 略過）。
2. 逐一上傳 `app.py`、`requirements.txt`、`test_loop.py`。
3. 批次上傳整個 `fixtures/` 資料夾。
4. 所有上傳均包含**指數退避重試機制**（預設最多 6 次），以應對 Hugging Face API 的 `429 Too Many Requests` 速率限制。

---

### `test_loop.py` — 驗收測試

在部署前驗證 `fixtures/` 資料夾的完整性：

| 測試項目 | 說明 |
|---|---|
| `test_required_files_exist` | 確認四個必要 fixture 檔案存在 |
| `test_json_fixtures_parse` | 確認所有合法 JSON 可正確解析，且具有預期的頂層鍵 |
| `test_malformed_fixture_is_detectable` | 確認格式異常的 JSON 仍可偵測到部分 header |

---

### `.github/workflows/deploy-hf.yml` — CI/CD 流程

觸發條件：
- 推送至 `main` 分支，且變更的檔案屬於 `app.py`、`requirements.txt`、`test_loop.py`、`deploy.py`、`fixtures/**` 或工作流程本身。
- 在 GitHub Actions 頁面手動觸發（`workflow_dispatch`）。

執行步驟：

```
1. Checkout 程式碼
2. 安裝 Python 3.11
3. pip install -r requirements.txt
4. 執行 test_loop.py（replay 模式驗收）
5. 執行 deploy.py（上傳至 HF Space）
```

使用 `concurrency` 設定確保同一時間只有一個部署任務執行，避免競態覆蓋。

---

## 資料來源

| 資料集 | 用途 |
|---|---|
| `oceanicdayi/eew_status` | EEW 系統各模組狀態 JSON |
| `oceanicdayi/eew_hermes_dashboard` | 地震事件報告 JSON、REP 摘要 Markdown、TSMIP 波形圖片 |

---

## 快速開始

### 本地執行儀表板

```bash
pip install -r requirements.txt
python app.py
```

### 執行驗收測試

```bash
EEW_DATA_SOURCE=replay python test_loop.py
```

### 手動部署至 Hugging Face Space

```bash
export HF_TOKEN=<your_token>
export EEW_SPACE_ID=oceanicdayi/Eew_dashboard  # 可自訂目標 Space
python deploy.py
```

---

## CI/CD 設定

1. 在 GitHub 倉庫的 **Settings → Secrets and variables → Actions** 新增 Secret：
   - `HF_TOKEN`：Hugging Face 的 User Access Token（需有對應 Space 的寫入權限）。
2. 推送至 `main` 分支，GitHub Actions 將自動執行測試並部署。
3. 如需更改目標 Space，修改 `.github/workflows/deploy-hf.yml` 中的 `EEW_SPACE_ID`。

---

## 相依套件

| 套件 | 用途 |
|---|---|
| `gradio` | Web 儀表板框架（由 Hugging Face Spaces 自動安裝） |
| `requests==2.32.3` | HTTP 請求 |
| `folium==0.17.0` | 互動式地圖渲染 |
| `huggingface_hub>=1.2.0,<2.0` | HF 資料集下載與 Space 上傳 |
| `pandas==2.2.3` | 資料處理 |
| `plotly>=5.24.1,<7.0` | 圖表（保留供擴充使用） |

---

## 授權

本專案採用 [LICENSE](./LICENSE) 文件所載明之授權條款。
