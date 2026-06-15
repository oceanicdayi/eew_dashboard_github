import html as html_lib
import json
import math
import re
import uuid
from functools import lru_cache
from pathlib import Path

import folium
import gradio as gr
import pandas as pd
from huggingface_hub import hf_hub_download, list_repo_files

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
WAVEFORM_DATASET_ID = "oceanicdayi/eew_hermes_dashboard"
WAVEFORM_PREFIX = "waveforms/"
SUPPORTED_WAVEFORM_SUFFIXES = (".csv", ".json", ".txt")


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


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


def _fixture_choices():
    files = sorted(FIXTURES.glob("*.json"))
    return [p.name for p in files if p.name != "malformed.json"] or ["normal_event.json"]


def render_dashboard(filename: str):
    path = FIXTURES / filename
    payload = _load_json(path)
    event = _parse_event(payload)

    lat = event.get("lat") or 23.7
    lon = event.get("lon") or 121.0
    mag = event.get("magnitude") or 0

    fmap = folium.Map(location=[lat, lon], zoom_start=7, tiles="CartoDB positron")
    radius = max(8000, float(mag) * 12000) if mag else 8000
    folium.Circle(
        location=[lat, lon],
        radius=radius,
        popup=f"M{mag:.2f}" if mag else "Standby",
        fill=True,
    ).add_to(fmap)
    folium.Marker(
        [lat, lon],
        tooltip=f"{event['status']} | M{mag:.2f}" if mag else event["status"],
    ).add_to(fmap)

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

    details = json.dumps(payload, ensure_ascii=False, indent=2)
    return status, summary, fmap._repr_html_(), details


@lru_cache(maxsize=1)
def _waveform_choices_cached():
    try:
        files = list_repo_files(WAVEFORM_DATASET_ID, repo_type="dataset")
        choices = [
            f for f in files
            if f.startswith(WAVEFORM_PREFIX) and f.lower().endswith(SUPPORTED_WAVEFORM_SUFFIXES)
        ]
        return sorted(choices)[:100] or ["demo://synthetic"]
    except Exception:
        return ["demo://synthetic"]


def refresh_waveform_choices():
    _waveform_choices_cached.cache_clear()
    choices = _waveform_choices_cached()
    value = choices[0]
    return gr.update(choices=choices, value=value), f"已重新讀取波形清單：{len(choices)} 筆"


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


def _downsample(values, max_points=1400):
    if len(values) <= max_points:
        return values
    step = max(1, math.ceil(len(values) / max_points))
    return values[::step]


def _series_from_csv(path: str):
    df = pd.read_csv(path)
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        # Try whitespace-delimited numeric files accidentally saved as CSV.
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            values = _coerce_floats(re.split(r"[\s,]+", f.read()))
        return [{"label": "waveform", "y": values}] if values else []

    time_like = {"time", "t", "timestamp", "sec", "second", "seconds", "sample", "index"}
    y_columns = [c for c in numeric.columns if str(c).strip().lower() not in time_like]
    if not y_columns:
        y_columns = list(numeric.columns)

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
        values = _coerce_floats(obj)
        if len(values) >= max(8, len(obj) // 2):
            found.append({"label": prefix or "waveform", "y": values})
        else:
            for idx, item in enumerate(obj[:20]):
                _find_numeric_arrays(item, f"{prefix}[{idx}]", found)
                if len(found) >= 3:
                    break
    elif isinstance(obj, dict):
        preferred = ["samples", "data", "waveform", "values", "amplitude", "acc", "velocity", "displacement"]
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

    local = hf_hub_download(
        repo_id=WAVEFORM_DATASET_ID,
        filename=repo_path,
        repo_type="dataset",
    )
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


def _waveform_html(series, source_label: str):
    clean_series = []
    for item in series[:3]:
        y = _downsample(_coerce_floats(item.get("y", [])))
        if y:
            clean_series.append({"label": str(item.get("label") or "waveform"), "y": y})

    if not clean_series:
        return "<div style='padding:1rem;border:1px solid #ddd;border-radius:12px'>無可繪製的波形資料。</div>"

    packet = {
        "source": source_label,
        "series": clean_series,
    }
    payload = json.dumps(packet, ensure_ascii=False)
    elem_id = f"wave_{uuid.uuid4().hex}"
    title = html_lib.escape(source_label)

    return f"""
<div id="{elem_id}" class="wave-card">
  <div class="wave-title">動態波形：{title}</div>
  <canvas width="1100" height="360"></canvas>
  <div class="wave-caption">波形會由左至右逐步繪製，重新選擇檔案即可重播。</div>
</div>
<style>
  #{elem_id}.wave-card {{
    border: 1px solid rgba(120,120,120,.25);
    border-radius: 16px;
    padding: 14px;
    background: linear-gradient(180deg, rgba(255,255,255,.96), rgba(245,247,250,.96));
  }}
  #{elem_id} .wave-title {{font-weight: 700; margin-bottom: 8px;}}
  #{elem_id} canvas {{width: 100%; height: 360px; border-radius: 12px; background: #08111f; display: block;}}
  #{elem_id} .wave-caption {{font-size: 13px; color: #667085; margin-top: 8px;}}
</style>
<script>
(() => {{
  const packet = {payload};
  const root = document.getElementById("{elem_id}");
  const canvas = root.querySelector("canvas");
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;
  const pad = 42;
  const colors = ["#63e6be", "#74c0fc", "#ffd43b"];
  const series = packet.series.map((s, idx) => ({{
    label: s.label,
    y: s.y.map(Number).filter(Number.isFinite),
    color: colors[idx % colors.length]
  }})).filter(s => s.y.length > 1);
  const all = series.flatMap(s => s.y);
  let minY = Math.min(...all);
  let maxY = Math.max(...all);
  if (!Number.isFinite(minY) || !Number.isFinite(maxY) || minY === maxY) {{ minY = -1; maxY = 1; }}
  const maxLen = Math.max(...series.map(s => s.y.length));
  let frame = 2;
  const speed = Math.max(3, maxLen / 180);

  function xFor(i, len) {{ return pad + (i / Math.max(1, len - 1)) * (W - pad * 1.5); }}
  function yFor(v) {{ return H - pad - ((v - minY) / (maxY - minY)) * (H - pad * 2); }}

  function drawGrid() {{
    ctx.fillStyle = "#08111f";
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "rgba(255,255,255,.12)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 6; i++) {{
      const y = pad + i * (H - pad * 2) / 6;
      ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(W - pad / 2, y); ctx.stroke();
    }}
    for (let i = 0; i <= 10; i++) {{
      const x = pad + i * (W - pad * 1.5) / 10;
      ctx.beginPath(); ctx.moveTo(x, pad / 2); ctx.lineTo(x, H - pad); ctx.stroke();
    }}
    ctx.strokeStyle = "rgba(255,255,255,.45)";
    ctx.beginPath(); ctx.moveTo(pad, pad / 2); ctx.lineTo(pad, H - pad); ctx.lineTo(W - pad / 2, H - pad); ctx.stroke();
    ctx.fillStyle = "rgba(255,255,255,.72)";
    ctx.font = "14px system-ui, sans-serif";
    ctx.fillText("Amplitude", 12, 24);
    ctx.fillText("Samples / time", W - 150, H - 14);
  }}

  function drawLegend() {{
    let x = pad + 8;
    const y = 28;
    series.forEach(s => {{
      ctx.fillStyle = s.color;
      ctx.fillRect(x, y - 10, 18, 4);
      ctx.fillStyle = "rgba(255,255,255,.82)";
      ctx.font = "13px system-ui, sans-serif";
      ctx.fillText(s.label, x + 24, y - 5);
      x += 150;
    }});
  }}

  function drawWave() {{
    drawGrid();
    drawLegend();
    const visible = Math.floor(frame);
    series.forEach(s => {{
      const n = Math.min(visible, s.y.length);
      if (n < 2) return;
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      ctx.moveTo(xFor(0, s.y.length), yFor(s.y[0]));
      for (let i = 1; i < n; i++) ctx.lineTo(xFor(i, s.y.length), yFor(s.y[i]));
      ctx.stroke();
      const hx = xFor(n - 1, s.y.length);
      const hy = yFor(s.y[n - 1]);
      ctx.fillStyle = s.color;
      ctx.beginPath(); ctx.arc(hx, hy, 4, 0, Math.PI * 2); ctx.fill();
    }});
    frame += speed;
    if (frame <= maxLen + speed) requestAnimationFrame(drawWave);
  }}
  drawWave();
}})();
</script>
"""


def render_waveform(repo_path: str):
    try:
        series, source_label = _load_waveform_series(repo_path)
        html = _waveform_html(series, source_label)
        rows = []
        for item in series[:3]:
            y = _coerce_floats(item.get("y", []))
            if y:
                rows.append({
                    "channel": item.get("label", "waveform"),
                    "samples": len(y),
                    "min": min(y),
                    "max": max(y),
                    "mean": sum(y) / len(y),
                })
        preview = pd.DataFrame(rows) if rows else pd.DataFrame([{"status": "no numeric waveform found"}])
        status = f"✅ 已載入波形：{source_label}"
        return status, html, preview
    except Exception as exc:
        html = _waveform_html(_synthetic_waveform(), "demo://synthetic fallback")
        preview = pd.DataFrame([{"error": str(exc), "fallback": "demo waveform"}])
        return "⚠️ 無法讀取遠端波形，已顯示示範動畫。", html, preview


fixture_choices = _fixture_choices()
waveform_choices = _waveform_choices_cached()

with gr.Blocks(title="EEW Dashboard") as demo:
    gr.Markdown("# EEW Dashboard\n臺灣地震預警資料展示、地圖定位與動態波形播放")

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

    with gr.Tab("動態波形"):
        gr.Markdown("## 波形資料\n來源：`oceanicdayi/eew_hermes_dashboard/waveforms`。選擇檔案後，波形會由左至右動態繪製。")
        with gr.Row():
            wave_source = gr.Dropdown(choices=waveform_choices, value=waveform_choices[0], label="Waveform file")
            wave_refresh = gr.Button("重新讀取波形清單")
            wave_play = gr.Button("播放波形")
        wave_status = gr.Markdown()
        wave_html = gr.HTML()
        wave_table = gr.Dataframe(label="波形統計", interactive=False)

        wave_refresh.click(refresh_waveform_choices, outputs=[wave_source, wave_status])
        wave_play.click(render_waveform, inputs=wave_source, outputs=[wave_status, wave_html, wave_table])
        wave_source.change(render_waveform, inputs=wave_source, outputs=[wave_status, wave_html, wave_table])
        demo.load(render_waveform, inputs=wave_source, outputs=[wave_status, wave_html, wave_table])

if __name__ == "__main__":
    demo.launch()
