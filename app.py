import html as html_lib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

import folium
import gradio as gr
import pandas as pd
from huggingface_hub import hf_hub_download, list_repo_files

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

DATASET_ID = "oceanicdayi/eew_hermes_dashboard"
STATUS_PREFIX = "status/"
WAVEFORM_PREFIX = "waveforms/"
SUPPORTED_WAVEFORM_SUFFIXES = (".csv", ".json", ".txt")


def _esc(value):
    return html_lib.escape("" if value is None else str(value))


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _fixture_choices():
    files = sorted(FIXTURES.glob("*.json"))
    return [p.name for p in files if p.name != "malformed.json"] or ["normal_event.json"]


@lru_cache(maxsize=1)
def _repo_files_cached():
    try:
        return list_repo_files(DATASET_ID, repo_type="dataset")
    except Exception:
        return []


def _choices(prefix, suffixes):
    files = _repo_files_cached()
    items = [f for f in files if f.startswith(prefix) and f.lower().endswith(suffixes)]
    return sorted(items, reverse=True)


def _status_choices_cached():
    return _choices(STATUS_PREFIX, (".json",)) or ["fixtures://normal_event.json"]


def _waveform_choices_cached():
    return _choices(WAVEFORM_PREFIX, SUPPORTED_WAVEFORM_SUFFIXES) or ["demo://synthetic"]


def refresh_status_choices():
    _repo_files_cached.cache_clear()
    choices = _status_choices_cached()
    return gr.update(choices=choices, value=choices[0]), f"已重新讀取狀態清單：{len(choices)} 筆"


def refresh_waveform_choices():
    _repo_files_cached.cache_clear()
    choices = _waveform_choices_cached()
    return gr.update(choices=choices, value=choices[0]), f"已重新讀取波形清單：{len(choices)} 筆"


def _download_dataset_json(repo_path: str):
    local = hf_hub_download(repo_id=DATASET_ID, filename=repo_path, repo_type="dataset")
    with open(local, "r", encoding="utf-8") as f:
        return json.load(f), repo_path


def _load_status_payload(source: str):
    if source and source.startswith("fixtures://"):
        name = source.split("://", 1)[1]
        return _load_json(FIXTURES / name), source
    if source:
        return _download_dataset_json(source)
    return _load_json(FIXTURES / "normal_event.json"), "fixtures://normal_event.json"


def _parse_event(payload: dict) -> dict:
    header = payload.get("latest_rep_header") or {}
    head = header.get("head") or []
    event_line = ""
    for line in head:
        if re.match(r"^\s*\d{4}\s+\d+\s+\d+\s+\d+\s+\d+", str(line)):
            event_line = str(line)
            break

    event = {
        "collected_utc": payload.get("collected_utc", ""),
        "runner": payload.get("runner", ""),
        "file": header.get("file", ""),
        "magnitude": None,
        "lat": None,
        "lon": None,
        "depth_km": None,
        "status": "standby",
        "raw_event_line": event_line,
    }

    if event_line:
        parts = event_line.split()
        try:
            event["lat"] = float(parts[6])
            event["lon"] = float(parts[7])
            event["depth_km"] = float(parts[8])
            event["magnitude"] = float(parts[9])
            event["status"] = "event" if event["magnitude"] and event["magnitude"] >= 1 else "standby"
        except (ValueError, IndexError):
            event["status"] = "partial"

    return event


def _status_class(text):
    text = str(text or "").lower()
    if any(k in text for k in ["up", "ok", "running", "healthy", "正常", "online"]):
        return "ok"
    if any(k in text for k in ["warn", "warning", "partial", "degraded", "警告"]):
        return "warn"
    if any(k in text for k in ["down", "fail", "error", "exited", "停止", "錯誤"]):
        return "bad"
    return "neutral"


def _container_rows(payload):
    containers = payload.get("containers") or payload.get("container") or {}
    rows = []
    if isinstance(containers, dict):
        for name, info in containers.items():
            if isinstance(info, dict):
                status = info.get("status") or info.get("state") or info.get("health") or ""
                ports = info.get("ports", "")
            else:
                status = info
                ports = ""
            rows.append({"module": name, "status": status, "ports": ports})
    return rows


def _disk_rows(payload):
    disk = payload.get("disk_root") or payload.get("disk") or payload.get("disk_usage")
    if isinstance(disk, list) and len(disk) >= 6:
        return [{
            "device": disk[0],
            "size": disk[1],
            "used": disk[2],
            "available": disk[3],
            "use_percent": disk[4],
            "mount": disk[5],
        }]
    if isinstance(disk, dict):
        return [{
            "device": disk.get("device") or disk.get("filesystem") or "/",
            "size": disk.get("size") or disk.get("total"),
            "used": disk.get("used"),
            "available": disk.get("available") or disk.get("avail") or disk.get("free"),
            "use_percent": disk.get("use_percent") or disk.get("percent") or disk.get("usage"),
            "mount": disk.get("mount") or disk.get("mounted_on") or "/",
        }]
    return []


def _percent_number(value):
    m = re.search(r"(\d+(?:\.\d+)?)", str(value or ""))
    return float(m.group(1)) if m else None


def _collect_module_summary(payload):
    rows = []
    containers = _container_rows(payload)
    for row in containers:
        rows.append({
            "item": row["module"],
            "status": row["status"],
            "detail": f"ports: {row.get('ports', '')}" if row.get("ports") else "",
        })

    rep_files = payload.get("latest_rep_files")
    if isinstance(rep_files, list):
        rows.append({
            "item": ".rep 即時檔案",
            "status": f"{len(rep_files)} files",
            "detail": rep_files[0] if rep_files else "無最新 .rep 檔",
        })

    header = payload.get("latest_rep_header") or {}
    if isinstance(header, dict) and header.get("file"):
        rows.append({
            "item": "Earthworm EEW header",
            "status": "available",
            "detail": header.get("file"),
        })

    warning = payload.get("earthworm_log_warning")
    rows.append({
        "item": "Earthworm log",
        "status": "warning" if warning else "ok",
        "detail": warning or "未偵測到 warning",
    })

    for key in ["waveform_summary", "latest_waveform", "station_heartbeat", "heartbeat_summary"]:
        if key in payload:
            rows.append({
                "item": key,
                "status": "available",
                "detail": json.dumps(payload.get(key), ensure_ascii=False)[:120],
            })

    if not rows:
        rows.append({"item": "status", "status": "no data", "detail": "未找到狀態欄位"})
    return rows


def _status_cards_html(payload, source_label):
    module_rows = _collect_module_summary(payload)
    disk_rows = _disk_rows(payload)
    containers = _container_rows(payload)
    event = _parse_event(payload)

    main_status = "standby"
    if event.get("status") == "event":
        main_status = f"M{event.get('magnitude'):.2f} event"
    elif containers:
        main_status = "system online"

    disk = disk_rows[0] if disk_rows else {}
    disk_pct = _percent_number(disk.get("use_percent"))
    disk_bar = min(100, max(0, disk_pct or 0))

    module_cards = []
    for row in module_rows[:8]:
        cls = _status_class(row.get("status"))
        module_cards.append(f"""
        <div class="dash-mini-card {cls}">
          <div class="mini-title">{_esc(row.get('item'))}</div>
          <div class="mini-status">{_esc(row.get('status'))}</div>
          <div class="mini-detail">{_esc(row.get('detail'))}</div>
        </div>
        """)

    disk_html = """
      <div class="disk-empty">未提供硬碟資料</div>
    """
    if disk_rows:
        disk_html = f"""
        <div class="disk-line">
          <span>{_esc(disk.get('device'))}</span>
          <strong>{_esc(disk.get('use_percent'))}</strong>
        </div>
        <div class="disk-bar"><div class="disk-fill" style="width:{disk_bar:.1f}%"></div></div>
        <div class="disk-meta">
          used {_esc(disk.get('used'))} / {_esc(disk.get('size'))}，available {_esc(disk.get('available'))}，mount {_esc(disk.get('mount'))}
        </div>
        """

    return f"""
<style>
.dashboard-wrap {{ display: grid; gap: 14px; }}
.hero-card {{
  border-radius: 20px; padding: 18px; color: #fff;
  background: linear-gradient(135deg, #0f172a, #1d4ed8);
  box-shadow: 0 12px 28px rgba(15,23,42,.18);
}}
.hero-title {{font-size: 26px; font-weight: 800; margin-bottom: 8px;}}
.hero-sub {{opacity: .86; font-size: 14px; word-break: break-all;}}
.hero-pills {{display:flex; flex-wrap:wrap; gap:8px; margin-top:14px;}}
.pill {{background: rgba(255,255,255,.14); padding:8px 10px; border-radius: 999px; font-weight:700;}}
.card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }}
.dash-card, .dash-mini-card {{
  border: 1px solid rgba(15,23,42,.10); border-radius: 18px;
  background: rgba(255,255,255,.96); padding: 14px;
  box-shadow: 0 6px 20px rgba(15,23,42,.06);
}}
.dash-card h3 {{margin: 0 0 10px 0; font-size: 18px;}}
.dash-mini-card {{min-height: 112px;}}
.dash-mini-card.ok {{border-left: 6px solid #16a34a;}}
.dash-mini-card.warn {{border-left: 6px solid #f59e0b;}}
.dash-mini-card.bad {{border-left: 6px solid #dc2626;}}
.dash-mini-card.neutral {{border-left: 6px solid #64748b;}}
.mini-title {{font-weight: 800; color:#0f172a; margin-bottom:8px;}}
.mini-status {{font-size: 22px; font-weight: 900; color:#111827;}}
.mini-detail {{margin-top:8px; color:#64748b; font-size:13px; word-break: break-all;}}
.disk-line {{display:flex; justify-content:space-between; gap:10px; align-items:center;}}
.disk-bar {{height: 16px; background:#e5e7eb; border-radius:999px; overflow:hidden; margin:10px 0;}}
.disk-fill {{height:100%; background:linear-gradient(90deg,#22c55e,#f59e0b); border-radius:999px;}}
.disk-meta, .disk-empty {{color:#64748b; font-size:13px;}}
@media (max-width: 640px) {{ .hero-title {{font-size: 22px;}} .card-grid {{grid-template-columns: 1fr;}} }}
</style>
<div class="dashboard-wrap">
  <div class="hero-card">
    <div class="hero-title">EEW / Earthworm 即時狀態</div>
    <div class="hero-sub">{_esc(source_label)}</div>
    <div class="hero-pills">
      <div class="pill">狀態：{_esc(main_status)}</div>
      <div class="pill">時間：{_esc(payload.get('host_time') or payload.get('collected_utc'))}</div>
      <div class="pill">Runner：{_esc(payload.get('runner'))}</div>
    </div>
  </div>
  <div class="dash-card">
    <h3>Earthworm 模組狀態</h3>
    <div class="card-grid">{''.join(module_cards)}</div>
  </div>
  <div class="dash-card">
    <h3>硬碟使用量</h3>
    {disk_html}
  </div>
</div>
"""


def render_system_status(status_source: str):
    try:
        payload, label = _load_status_payload(status_source)
    except Exception as exc:
        payload = _load_json(FIXTURES / "normal_event.json")
        label = f"fixtures://normal_event.json（遠端讀取失敗：{exc}）"
    html = _status_cards_html(payload, label)
    module_table = pd.DataFrame(_collect_module_summary(payload))
    disk_table = pd.DataFrame(_disk_rows(payload) or [{"status": "no disk data"}])
    return html, module_table, disk_table


def render_dashboard(filename: str):
    path = FIXTURES / filename
    payload = _load_json(path)
    event = _parse_event(payload)

    lat = event.get("lat") or 23.7
    lon = event.get("lon") or 121.0
    mag = event.get("magnitude") or 0

    fmap = folium.Map(location=[lat, lon], zoom_start=7, tiles="CartoDB positron")
    radius = max(8000, float(mag) * 12000) if mag else 8000
    folium.Circle(location=[lat, lon], radius=radius, popup=f"M{mag:.2f}" if mag else "Standby", fill=True).add_to(fmap)
    folium.Marker([lat, lon], tooltip=f"{event['status']} | M{mag:.2f}" if mag else event["status"]).add_to(fmap)

    status = "🟢 Standby"
    if event["status"] == "event":
        status = "🔴 EEW Event"
    elif event["status"] == "partial":
        status = "🟠 Partial data"

    summary = pd.DataFrame([
        {"field": "status", "value": status},
        {"field": "magnitude", "value": event.get("magnitude")},
        {"field": "latitude", "value": event.get("lat")},
        {"field": "longitude", "value": event.get("lon")},
        {"field": "depth_km", "value": event.get("depth_km")},
        {"field": "source_file", "value": event.get("file")},
        {"field": "collected_utc", "value": event.get("collected_utc")},
    ])
    return status, summary, fmap._repr_html_(), json.dumps(payload, ensure_ascii=False, indent=2)


def _coerce_floats(values, limit=6000):
    out = []
    for value in values:
        if len(out) >= limit:
            break
        try:
            if value is None or (isinstance(value, float) and math.isnan(value)):
                continue
            out.append(float(value))
        except (TypeError, ValueError):
            continue
    return out


def _downsample(values, max_points=1200):
    if len(values) <= max_points:
        return values
    step = max(1, math.ceil(len(values) / max_points))
    return values[::step]


def _series_from_records(records, prefix="records"):
    if not records or not all(isinstance(x, dict) for x in records[:5]):
        return []
    numeric_by_key = {}
    time_like = {"time", "t", "timestamp", "datetime", "sec", "second", "seconds", "sample", "index"}
    for row in records:
        for key, value in row.items():
            k = str(key)
            if k.lower() in time_like:
                continue
            try:
                numeric_by_key.setdefault(k, []).append(float(value))
            except (TypeError, ValueError):
                pass
    series = []
    for key, values in numeric_by_key.items():
        if len(values) >= 8:
            series.append({"label": f"{prefix}.{key}", "y": values})
        if len(series) >= 3:
            break
    return series


def _series_from_csv(path: str):
    df = pd.read_csv(path)
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            values = _coerce_floats(re.split(r"[\s,]+", f.read()))
        return [{"label": "waveform", "y": values}] if values else []

    time_like = {"time", "t", "timestamp", "sec", "second", "seconds", "sample", "index"}
    y_columns = [c for c in numeric.columns if str(c).strip().lower() not in time_like] or list(numeric.columns)
    series = []
    for col in y_columns[:3]:
        values = _coerce_floats(numeric[col].tolist())
        if values:
            series.append({"label": str(col), "y": values})
    return series


def _find_numeric_arrays(obj, prefix="", found=None):
    if found is None:
        found = []
    if len(found) >= 3:
        return found

    if isinstance(obj, list):
        record_series = _series_from_records(obj, prefix or "records")
        if record_series:
            found.extend(record_series[: max(0, 3 - len(found))])
            return found
        values = _coerce_floats(obj)
        if len(values) >= max(8, len(obj) // 2):
            found.append({"label": prefix or "waveform", "y": values})
        else:
            for idx, item in enumerate(obj[:50]):
                _find_numeric_arrays(item, f"{prefix}[{idx}]", found)
                if len(found) >= 3:
                    break

    elif isinstance(obj, dict):
        preferred = ["samples", "data", "waveform", "waveforms", "values", "amplitude", "acc", "velocity", "displacement", "z", "n", "e"]
        for key in preferred:
            if key in obj:
                _find_numeric_arrays(obj[key], f"{prefix}.{key}" if prefix else key, found)
                if len(found) >= 3:
                    return found
        for key, value in obj.items():
            _find_numeric_arrays(value, f"{prefix}.{key}" if prefix else str(key), found)
            if len(found) >= 3:
                break
    return found


def _series_from_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return _find_numeric_arrays(payload)


def _series_from_txt(path: str):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        values = _coerce_floats(re.split(r"[\s,]+", f.read()))
    return [{"label": "waveform", "y": values}] if values else []


def _synthetic_waveform():
    values = []
    for i in range(900):
        pulse = math.exp(-((i - 260) ** 2) / 9500) * math.sin(i / 4.5)
        coda = 0.35 * math.exp(-max(0, i - 360) / 260) * math.sin(i / 11)
        values.append(pulse + coda)
    return [{"label": "demo waveform", "y": values}]


def _load_waveform_series(repo_path: str):
    if not repo_path or repo_path == "demo://synthetic":
        return _synthetic_waveform(), "demo://synthetic"
    local = hf_hub_download(repo_id=DATASET_ID, filename=repo_path, repo_type="dataset")
    suffix = repo_path.lower()
    if suffix.endswith(".csv"):
        series = _series_from_csv(local)
    elif suffix.endswith(".json"):
        series = _series_from_json(local)
    elif suffix.endswith(".txt"):
        series = _series_from_txt(local)
    else:
        series = []
    return series, repo_path


def _path_from_values(values, width, height, pad, min_y, max_y):
    if not values:
        return ""
    if min_y == max_y:
        min_y -= 1
        max_y += 1
    usable_w = width - pad * 1.5
    usable_h = height - pad * 2
    denom = max(1, len(values) - 1)
    points = []
    for idx, value in enumerate(values):
        x = pad + (idx / denom) * usable_w
        y = height - pad - ((value - min_y) / (max_y - min_y)) * usable_h
        points.append(f"{x:.2f},{y:.2f}")
    return "M " + " L ".join(points)


def _waveform_html(series, source_label: str):
    clean_series = []
    for item in series[:3]:
        y = _downsample(_coerce_floats(item.get("y", [])))
        if y:
            clean_series.append({"label": str(item.get("label") or "waveform"), "y": y})
    if not clean_series:
        return "<div class='empty-wave'>無可繪製的波形資料。請確認 JSON/CSV 內含數值序列。</div>"

    width, height, pad = 1100, 320, 44
    colors = ["#0ea5e9", "#22c55e", "#f97316"]
    all_values = [v for item in clean_series for v in item["y"]]
    min_y, max_y = min(all_values), max(all_values)
    if min_y == max_y:
        min_y -= 1
        max_y += 1

    grid = []
    for i in range(7):
        y = pad + i * (height - pad * 2) / 6
        grid.append(f"<line x1='{pad}' y1='{y:.2f}' x2='{width - pad / 2}' y2='{y:.2f}' class='grid' />")
    for i in range(11):
        x = pad + i * (width - pad * 1.5) / 10
        grid.append(f"<line x1='{x:.2f}' y1='{pad / 2}' x2='{x:.2f}' y2='{height - pad}' class='grid' />")

    paths = []
    legend = []
    for idx, item in enumerate(clean_series):
        color = colors[idx % len(colors)]
        label = _esc(item["label"])
        d = _path_from_values(item["y"], width, height, pad, min_y, max_y)
        paths.append(f"<path d='{d}' class='wave-line' stroke='{color}' />")
        lx = pad + 8 + idx * 190
        legend.append(
            f"<g><line x1='{lx}' y1='28' x2='{lx + 24}' y2='28' stroke='{color}' stroke-width='4' />"
            f"<text x='{lx + 32}' y='32' class='legend'>{label}</text></g>"
        )

    return f"""
<style>
.wave-card {{ border: 1px solid rgba(15,23,42,.12); border-radius: 18px; padding: 14px; background: #ffffff; box-shadow: 0 8px 22px rgba(15,23,42,.07); }}
.wave-title {{font-weight: 900; font-size: 18px; margin-bottom: 10px; color:#0f172a; word-break: break-all;}}
.wave-wrap {{border-radius: 14px; overflow:hidden; background:#f8fafc; border:1px solid #e5e7eb;}}
.wave-wrap svg {{width: 100%; height: 320px; display: block;}}
.bg {{fill: #f8fafc;}}
.grid {{stroke: rgba(100,116,139,.22); stroke-width: 1;}}
.axis {{stroke: rgba(15,23,42,.45); stroke-width: 1.2;}}
.axis-label {{fill: #475569; font: 14px system-ui, sans-serif;}}
.legend {{fill: #334155; font: 13px system-ui, sans-serif;}}
.wave-line {{ fill: none; stroke-width: 2.3; stroke-linecap: round; stroke-linejoin: round; }}
.wave-caption {{font-size: 13px; color:#64748b; margin-top:8px;}}
.empty-wave {{padding:1rem;border:1px solid #ddd;border-radius:12px;color:#64748b;}}
@media (max-width: 640px) {{ .wave-wrap svg {{height: 260px;}} }}
</style>
<div class="wave-card">
  <div class="wave-title">靜態波形：{_esc(source_label)}</div>
  <div class="wave-wrap">
    <svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" aria-label="Static waveform">
      <rect width="{width}" height="{height}" rx="14" class="bg" />
      {''.join(grid)}
      <line x1="{pad}" y1="{pad / 2}" x2="{pad}" y2="{height - pad}" class="axis" />
      <line x1="{pad}" y1="{height - pad}" x2="{width - pad / 2}" y2="{height - pad}" class="axis" />
      <text x="12" y="24" class="axis-label">Amplitude</text>
      <text x="{width - 155}" y="{height - 14}" class="axis-label">Samples / time</text>
      <text x="{pad + 4}" y="{pad - 8}" class="axis-label">{_esc(f'{max_y:.4g}')}</text>
      <text x="{pad + 4}" y="{height - pad - 5}" class="axis-label">{_esc(f'{min_y:.4g}')}</text>
      {''.join(legend)}
      {''.join(paths)}
    </svg>
  </div>
  <div class="wave-caption">已改為靜態展示，避免手機瀏覽器或 Gradio HTML 阻擋動畫造成黑畫面。</div>
</div>
"""


def render_waveform(repo_path: str):
    try:
        series, source_label = _load_waveform_series(repo_path)
        html = _waveform_html(series, source_label)
        rows = []
        for item in series[:3]:
            y = _coerce_floats(item.get("y", []))
            if y:
                rows.append({"channel": item.get("label", "waveform"), "samples": len(y), "min": min(y), "max": max(y), "mean": sum(y) / len(y)})
        preview = pd.DataFrame(rows) if rows else pd.DataFrame([{"status": "no numeric waveform found"}])
        return f"✅ 已載入波形：{source_label}", html, preview
    except Exception as exc:
        html = _waveform_html(_synthetic_waveform(), "demo://synthetic fallback")
        preview = pd.DataFrame([{"error": str(exc), "fallback": "demo waveform"}])
        return "⚠️ 無法讀取遠端波形，已顯示示範波形。", html, preview


fixture_choices = _fixture_choices()
status_choices = _status_choices_cached()
waveform_choices = _waveform_choices_cached()

with gr.Blocks(title="EEW Dashboard") as demo:
    gr.Markdown("# EEW Dashboard\n臺灣地震預警資料展示、Earthworm 狀態監控與波形檢視")

    with gr.Tab("系統狀態"):
        gr.Markdown("## Earthworm / EEW 狀態與硬碟使用量")
        with gr.Row():
            status_source = gr.Dropdown(choices=status_choices, value=status_choices[0], label="Status file")
            status_refresh = gr.Button("重新讀取狀態清單")
            status_load = gr.Button("載入狀態")
        status_msg = gr.Markdown()
        status_cards = gr.HTML()
        module_table = gr.Dataframe(label="模組狀態明細", interactive=False)
        disk_table = gr.Dataframe(label="硬碟使用量", interactive=False)
        status_refresh.click(refresh_status_choices, outputs=[status_source, status_msg])
        status_load.click(render_system_status, inputs=status_source, outputs=[status_cards, module_table, disk_table])
        status_source.change(render_system_status, inputs=status_source, outputs=[status_cards, module_table, disk_table])
        demo.load(render_system_status, inputs=status_source, outputs=[status_cards, module_table, disk_table])

    with gr.Tab("事件地圖"):
        with gr.Row():
            source = gr.Dropdown(choices=fixture_choices, value=fixture_choices[0], label="Replay fixture")
            refresh = gr.Button("載入資料")
        status = gr.Markdown()
        summary = gr.Dataframe(label="事件摘要", interactive=False)
        map_html = gr.HTML(label="Map")
        raw = gr.Code(label="Raw JSON", language="json")
        refresh.click(render_dashboard, inputs=source, outputs=[status, summary, map_html, raw])
        source.change(render_dashboard, inputs=source, outputs=[status, summary, map_html, raw])
        demo.load(render_dashboard, inputs=source, outputs=[status, summary, map_html, raw])

    with gr.Tab("靜態波形"):
        gr.Markdown("## 波形資料\n來源：`oceanicdayi/eew_hermes_dashboard/waveforms`。已改為靜態展示，避免黑畫面。")
        with gr.Row():
            wave_source = gr.Dropdown(choices=waveform_choices, value=waveform_choices[0], label="Waveform file")
            wave_refresh = gr.Button("重新讀取波形清單")
            wave_play = gr.Button("顯示波形")
        wave_status = gr.Markdown()
        wave_html = gr.HTML()
        wave_table = gr.Dataframe(label="波形統計", interactive=False)
        wave_refresh.click(refresh_waveform_choices, outputs=[wave_source, wave_status])
        wave_play.click(render_waveform, inputs=wave_source, outputs=[wave_status, wave_html, wave_table])
        wave_source.change(render_waveform, inputs=wave_source, outputs=[wave_status, wave_html, wave_table])
        demo.load(render_waveform, inputs=wave_source, outputs=[wave_status, wave_html, wave_table])

if __name__ == "__main__":
    demo.launch()
