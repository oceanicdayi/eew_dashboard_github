import html
import json
import math
import re
from functools import lru_cache
from pathlib import Path

import folium
import gradio as gr
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from huggingface_hub import hf_hub_download, list_repo_files

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

STATUS_DATASET_ID = "oceanicdayi/eew_status"
WAVEFORM_DATASET_ID = "oceanicdayi/eew_hermes_dashboard"
DEFAULT_STATUS = "status/eew_status_report.json"
DEFAULT_STATUS_ALT = "eew_status_report.json"
WAVEFORM_PREFIX = "tsmip/"
DEFAULT_WAVEFORM = "tsmip/rolling.json"
MAX_TRACES = 10
MAX_POINTS = 2500


def esc(value):
    return html.escape("" if value is None else str(value))


def to_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


@lru_cache(maxsize=1)
def status_files():
    try:
        return list_repo_files(STATUS_DATASET_ID, repo_type="dataset")
    except Exception:
        return []


@lru_cache(maxsize=1)
def waveform_files():
    try:
        return list_repo_files(WAVEFORM_DATASET_ID, repo_type="dataset")
    except Exception:
        return []


def status_choices():
    files = status_files()
    choices = []
    for name in [DEFAULT_STATUS, DEFAULT_STATUS_ALT]:
        if name in files and name not in choices:
            choices.append(name)
    if not choices:
        choices.append(DEFAULT_STATUS)
    choices += [f for f in sorted(files, reverse=True) if f.endswith(".json") and f not in choices]
    return choices or ["fixtures://normal_event.json"]


def waveform_choices():
    files = waveform_files()
    tsmip = [
        f for f in sorted(files, reverse=True)
        if f.startswith(WAVEFORM_PREFIX) and f.lower().endswith((".json", ".csv", ".txt"))
    ]
    return tsmip or [DEFAULT_WAVEFORM, "demo://synthetic"]


def refresh_status():
    status_files.cache_clear()
    choices = status_choices()
    return gr.update(choices=choices, value=choices[0]), f"已重新讀取 {STATUS_DATASET_ID}：{len(choices)} 筆"


def refresh_waveforms():
    waveform_files.cache_clear()
    choices = waveform_choices()
    return gr.update(choices=choices, value=choices[0]), f"已重新讀取 {WAVEFORM_DATASET_ID}/{WAVEFORM_PREFIX}：{len(choices)} 筆"


def load_status(source):
    if source and source.startswith("fixtures://"):
        with open(FIXTURES / source.split("://", 1)[1], "r", encoding="utf-8") as f:
            return json.load(f), source
    errors = []
    for name in [source, DEFAULT_STATUS, DEFAULT_STATUS_ALT]:
        if not name:
            continue
        try:
            path = hf_hub_download(repo_id=STATUS_DATASET_ID, filename=name, repo_type="dataset")
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f), name
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("; ".join(errors[-2:]))


def status_level(text):
    t = str(text).lower()
    if any(k in t for k in ["alive", "up", "ok", "running", "healthy", "success", "available", "exists", "運行", "正常"]):
        return "ok"
    if any(k in t for k in ["warn", "warning", "partial", "degraded", "警告"]):
        return "warn"
    if any(k in t for k in ["down", "fail", "error", "exited", "stopped", "missing", "停止"]):
        return "bad"
    return "neutral"


def normalize_status(info):
    if isinstance(info, dict):
        if "alive" in info or "is_alive" in info:
            value = info.get("alive", info.get("is_alive"))
            return "alive" if str(value).strip().lower() in {"true", "1", "yes", "alive"} else "not alive"
        return info.get("status") or info.get("state") or info.get("health") or info.get("ok") or "unknown"
    return info


def module_items(payload):
    rows = []
    for key in ["containers", "container_status", "container", "docker", "modules", "module_status", "services", "checks"]:
        value = payload.get(key)
        if isinstance(value, dict):
            for name, info in value.items():
                rows.append((str(name), str(normalize_status(info))))
        elif isinstance(value, list):
            for i, info in enumerate(value):
                if isinstance(info, dict):
                    name = info.get("name") or info.get("module") or info.get("container") or f"module_{i + 1}"
                    rows.append((str(name), str(normalize_status(info))))
                else:
                    rows.append((f"module_{i + 1}", str(info)))
    header = payload.get("latest_rep_header") or {}
    if isinstance(header, dict) and header.get("file"):
        rows.append(("Earthworm EEW header", "available"))
    rep_files = payload.get("latest_rep_files")
    if isinstance(rep_files, list):
        rows.append((".rep files", f"{len(rep_files)} files" if rep_files else "missing"))
    warning = payload.get("earthworm_log_warning")
    rows.append(("Earthworm log", "warning" if warning else "ok"))
    return rows or [("status payload", "loaded")]


def render_status(source):
    try:
        payload, label = load_status(source)
    except Exception as exc:
        with open(FIXTURES / "normal_event.json", "r", encoding="utf-8") as f:
            payload = json.load(f)
        label = f"fallback: {exc}"

    alive_cards = []
    info_cards = []
    for name, status in module_items(payload):
        if str(status).strip().lower() == "alive":
            alive_cards.append(f"<div class='alive-card'><span class='lamp'></span><div><b>{esc(name)}</b><small>{esc(status)}</small></div></div>")
        else:
            cls = status_level(status)
            info_cards.append(f"<div class='info-card {cls}'><div><b>{esc(name)}</b><small>{esc(status)}</small></div><span class='tag'>{esc(status)}</span></div>")

    alive_html = "".join(alive_cards) or "<div class='empty'>目前沒有 alive 型式狀態。</div>"
    info_html = "".join(info_cards) or "<div class='empty'>沒有其他狀態資訊。</div>"
    source_text = f"{STATUS_DATASET_ID} / {label}"

    return f"""
<style>
.status-wrap{{display:grid;gap:16px}}
.status-hero{{position:relative;overflow:hidden;display:flex;justify-content:space-between;align-items:center;gap:18px;border-radius:24px;padding:24px 22px;color:#fff!important;background:radial-gradient(circle at 86% 24%,rgba(125,211,252,.45),transparent 28%),linear-gradient(135deg,#1d4ed8 0%,#2563eb 45%,#0284c7 100%);box-shadow:0 18px 38px rgba(37,99,235,.28),0 6px 18px rgba(15,23,42,.14)}}
.status-hero h2{{margin:4px 0 12px!important;font-size:32px!important;line-height:1.08!important;color:#fff!important;font-weight:950!important;text-shadow:0 2px 10px rgba(15,23,42,.22)}}
.hero-kicker{{font-size:12px;font-weight:900;letter-spacing:.12em;color:rgba(255,255,255,.76)!important}}
.src{{display:inline-block;max-width:100%;padding:9px 12px;border-radius:14px;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.26);color:#fff!important;font-size:14px!important;line-height:1.55!important;font-weight:700!important;word-break:break-word}}
.src span{{color:rgba(255,255,255,.82)!important;margin-right:6px}}
.hero-icon{{flex:0 0 auto;width:76px;height:76px;border-radius:999px;border:8px solid rgba(255,255,255,.22);display:flex;align-items:center;justify-content:center;color:rgba(255,255,255,.60)!important;font-size:38px;font-weight:900}}
.section{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:14px;box-shadow:0 8px 22px rgba(15,23,42,.06)}}
.section-title{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;color:#0f172a;font-weight:900;font-size:17px}}
.section-note{{font-size:12px;color:#64748b;font-weight:600}}
.alive-grid,.info-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}
.alive-card{{display:flex;gap:12px;align-items:center;padding:15px;border-radius:16px;background:linear-gradient(180deg,#f0fdf4,#ffffff);border:1px solid rgba(34,197,94,.28)}}
.lamp{{width:24px;height:24px;border-radius:99px;background:#22c55e;box-shadow:0 0 0 7px rgba(34,197,94,.16),0 0 20px rgba(34,197,94,.70);flex:0 0 auto}}
.info-card{{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:14px;border-radius:16px;background:#f8fafc;border:1px solid #e2e8f0}}
.info-card.ok{{border-color:#dbeafe}}.info-card.warn{{background:#fffbeb;border-color:#fde68a}}.info-card.bad{{background:#fef2f2;border-color:#fecaca}}
b{{display:block;color:#0f172a}}small{{display:block;color:#64748b;margin-top:4px;word-break:break-all}}
.alive-card b{{font-size:18px!important}}.alive-card small{{font-size:14px!important;color:#14532d!important}}
.tag{{font-size:12px;font-weight:800;border-radius:999px;padding:6px 10px;background:#e2e8f0;color:#334155;white-space:nowrap;max-width:140px;overflow:hidden;text-overflow:ellipsis}}
.info-card.ok .tag{{background:#dbeafe;color:#1d4ed8}}.info-card.warn .tag{{background:#fef3c7;color:#b45309}}.info-card.bad .tag{{background:#fee2e2;color:#b91c1c}}
.empty{{padding:14px;border-radius:14px;background:#f8fafc;color:#64748b;border:1px dashed #cbd5e1}}
@media(max-width:640px){{.alive-grid,.info-grid{{grid-template-columns:1fr}}.status-hero{{padding:22px 18px;align-items:flex-start}}.status-hero h2{{font-size:28px!important}}.hero-icon{{width:58px;height:58px;border-width:6px;font-size:30px}}.src{{font-size:13px!important}}}}
</style>
<div class='status-wrap'>
  <div class='status-hero'><div><div class='hero-kicker'>EEW STATUS</div><h2>系統狀態</h2><div class='src'><span>來源</span>{esc(source_text)}</div></div><div class='hero-icon'>⌁</div></div>
  <div class='section'><div class='section-title'>Alive 模組綠燈 <span class='section-note'>僅 alive 使用燈號</span></div><div class='alive-grid'>{alive_html}</div></div>
  <div class='section'><div class='section-title'>其他狀態資訊 <span class='section-note'>非 alive 不使用燈號</span></div><div class='info-grid'>{info_html}</div></div>
</div>
"""


def parse_rep_event(payload):
    header = payload.get("latest_rep_header") or {}
    lines = header.get("head") or []
    if not isinstance(lines, list):
        return None
    event = {"source_file": header.get("file"), "stations": []}
    for i, line in enumerate(lines):
        text = str(line)
        if text.strip().lower().startswith("year") and i + 1 < len(lines):
            parts = str(lines[i + 1]).split()
            if len(parts) >= 14:
                year, month, day, hour, minute = parts[0:5]
                sec = parts[5]
                event.update({
                    "time": f"{int(year):04d}/{int(month):02d}/{int(day):02d} {int(hour):02d}:{int(minute):02d}:{float(sec):05.2f}",
                    "lat": to_float(parts[6]),
                    "lon": to_float(parts[7]),
                    "depth_km": to_float(parts[8]),
                    "magnitude": to_float(parts[9]),
                    "mpd": to_float(parts[12]) if len(parts) > 12 else None,
                    "mtc": to_float(parts[13]) if len(parts) > 13 else None,
                    "process_time": to_float(parts[14]) if len(parts) > 14 else None,
                })
        if text.strip().lower().startswith("sta"):
            for row in lines[i + 1:]:
                cols = str(row).split()
                if len(cols) >= 6:
                    sta_lat = to_float(cols[4])
                    sta_lon = to_float(cols[5])
                    if sta_lat is not None and sta_lon is not None:
                        event["stations"].append({"station": cols[0], "lat": sta_lat, "lon": sta_lon})
    return event if event.get("lat") is not None and event.get("lon") is not None else None


def event_from_payload(payload):
    for key in ["latest_event", "event", "latest_report", "eew_event", "earthquake", "summary", "latest_event_summary", "eew_summary"]:
        obj = payload.get(key)
        if isinstance(obj, dict):
            lat = to_float(obj.get("lat") or obj.get("latitude"))
            lon = to_float(obj.get("lon") or obj.get("lng") or obj.get("longitude"))
            if lat is not None and lon is not None:
                return {
                    "time": obj.get("time") or obj.get("origin_time") or obj.get("datetime") or obj.get("timestamp"),
                    "lat": lat,
                    "lon": lon,
                    "depth_km": to_float(obj.get("depth") or obj.get("depth_km") or obj.get("dep")),
                    "magnitude": to_float(obj.get("magnitude") or obj.get("mag") or obj.get("Mall") or obj.get("ml")),
                    "location": obj.get("location") or obj.get("area") or obj.get("epicenter"),
                    "source_file": obj.get("file") or obj.get("source"),
                    "stations": obj.get("stations") if isinstance(obj.get("stations"), list) else [],
                }
    return parse_rep_event(payload)


def event_info_html(event, source_label):
    if not event:
        return "<div class='event-empty'>目前狀態檔內沒有可解析的地震事件經緯度資料。</div>"
    mag = event.get("magnitude")
    dep = event.get("depth_km")
    lat = event.get("lat")
    lon = event.get("lon")
    cards = [
        ("發震時間", event.get("time") or "—"),
        ("規模", f"M {mag:.2f}" if mag is not None else "—"),
        ("深度", f"{dep:.1f} km" if dep is not None else "—"),
        ("震央座標", f"{lat:.4f}, {lon:.4f}" if lat is not None and lon is not None else "—"),
        ("來源檔案", event.get("source_file") or source_label or "—"),
        ("測站數", str(len(event.get("stations") or []))),
    ]
    card_html = "".join(f"<div class='eq-card'><div class='eq-label'>{esc(k)}</div><div class='eq-value'>{esc(v)}</div></div>" for k, v in cards)
    location = event.get("location") or "震央位置依狀態資料解析"
    return f"""
<style>
.eq-wrap{{display:grid;gap:14px}}
.eq-hero{{border-radius:22px;padding:20px;color:#fff;background:linear-gradient(135deg,#7f1d1d,#dc2626 55%,#f97316);box-shadow:0 14px 28px rgba(127,29,29,.18)}}
.eq-hero h2{{margin:0 0 8px!important;color:#fff!important;font-size:28px!important;font-weight:950!important}}
.eq-hero p{{margin:0;color:rgba(255,255,255,.88)!important;font-weight:700!important}}
.eq-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}
.eq-card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:14px;box-shadow:0 6px 18px rgba(15,23,42,.06)}}
.eq-label{{font-size:12px;color:#64748b;font-weight:800;margin-bottom:6px}}
.eq-value{{font-size:18px;color:#0f172a;font-weight:900;word-break:break-word}}
.event-empty{{padding:16px;border-radius:16px;background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;font-weight:800}}
</style>
<div class='eq-wrap'><div class='eq-hero'><h2>地震事件資訊</h2><p>{esc(location)}</p></div><div class='eq-grid'>{card_html}</div></div>
"""


def folium_map_html(event):
    if not event or event.get("lat") is None or event.get("lon") is None:
        return "<div style='padding:16px;border-radius:16px;background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;font-weight:800'>無可繪製的地震事件座標</div>"

    lat = event["lat"]
    lon = event["lon"]
    mag = event.get("magnitude")
    dep = event.get("depth_km")
    mag_text = f"{mag:.2f}" if isinstance(mag, (int, float)) else "—"
    dep_text = f"{dep:.1f}" if isinstance(dep, (int, float)) else "—"
    radius = max(8, min(22, 6 + (mag or 0) * 2.5))

    fmap = folium.Map(location=[lat, lon], zoom_start=8, tiles="OpenStreetMap", control_scale=True)
    popup = folium.Popup(
        f"<b>震央</b><br>時間：{esc(event.get('time') or '—')}<br>規模：M {mag_text}<br>深度：{dep_text} km<br>座標：{lat:.4f}, {lon:.4f}",
        max_width=320,
    )
    folium.CircleMarker(
        location=[lat, lon],
        radius=radius,
        color="#b91c1c",
        weight=3,
        fill=True,
        fill_color="#ef4444",
        fill_opacity=0.72,
        tooltip="震央",
        popup=popup,
    ).add_to(fmap)
    folium.Marker(
        location=[lat, lon],
        icon=folium.Icon(color="red", icon="exclamation-sign"),
        tooltip="震央",
    ).add_to(fmap)

    for station in event.get("stations") or []:
        sta_lat = station.get("lat")
        sta_lon = station.get("lon")
        if sta_lat is None or sta_lon is None:
            continue
        name = station.get("station", "station")
        folium.CircleMarker(
            location=[sta_lat, sta_lon],
            radius=4,
            color="#1d4ed8",
            weight=2,
            fill=True,
            fill_color="#3b82f6",
            fill_opacity=0.65,
            tooltip=f"測站：{esc(name)}",
        ).add_to(fmap)

    folium.LayerControl().add_to(fmap)
    map_html = fmap.get_root().render()
    return f"<iframe srcdoc=\"{html.escape(map_html, quote=True)}\" style=\"width:100%;height:560px;border:0;border-radius:18px;box-shadow:0 8px 22px rgba(15,23,42,.08);\"></iframe>"


def render_event(source):
    try:
        payload, label = load_status(source)
    except Exception as exc:
        with open(FIXTURES / "normal_event.json", "r", encoding="utf-8") as f:
            payload = json.load(f)
        label = f"fallback: {exc}"
    event = event_from_payload(payload)
    return event_info_html(event, label), folium_map_html(event)


def floats(values, limit=50000):
    out = []
    for value in values:
        if len(out) >= limit:
            break
        try:
            if value is None or (isinstance(value, float) and math.isnan(value)):
                continue
            out.append(float(value))
        except Exception:
            pass
    return out


def downsample(y):
    if len(y) <= MAX_POINTS:
        return y
    return y[::max(1, math.ceil(len(y) / MAX_POINTS))]


def from_records(records, prefix="records"):
    if not records or not all(isinstance(x, dict) for x in records[:min(5, len(records))]):
        return []
    skip = {"time", "t", "timestamp", "datetime", "sec", "second", "seconds", "sample", "index"}
    data = {}
    for row in records:
        for key, value in row.items():
            if str(key).lower() in skip:
                continue
            try:
                data.setdefault(str(key), []).append(float(value))
            except Exception:
                pass
    return [{"label": f"{prefix}.{key}", "y": value} for key, value in list(data.items())[:MAX_TRACES] if len(value) >= 8]


def find_series(obj, prefix="", found=None, depth=0):
    if found is None:
        found = []
    if len(found) >= MAX_TRACES or depth > 8:
        return found
    if isinstance(obj, list):
        rec = from_records(obj, prefix or "records")
        if rec:
            found.extend(rec[:MAX_TRACES - len(found)])
            return found
        y = floats(obj)
        if len(y) >= max(8, len(obj) // 2):
            found.append({"label": prefix or f"trace_{len(found) + 1}", "y": y})
        else:
            for i, item in enumerate(obj[:80]):
                find_series(item, f"{prefix}[{i}]" if prefix else f"[{i}]", found, depth + 1)
                if len(found) >= MAX_TRACES:
                    break
    elif isinstance(obj, dict):
        keys = ["samples", "data", "waveform", "waveforms", "values", "amplitude", "acc", "velocity", "displacement", "z", "n", "e", "HLZ", "HLN", "HLE", "stations", "channels", "traces"]
        for key in keys + [key for key in obj.keys() if key not in keys]:
            if key in obj:
                find_series(obj[key], f"{prefix}.{key}" if prefix else str(key), found, depth + 1)
                if len(found) >= MAX_TRACES:
                    break
    return found


def load_waveform(path):
    if not path or path == "demo://synthetic":
        return demo_series(), "demo://synthetic"
    local = hf_hub_download(repo_id=WAVEFORM_DATASET_ID, filename=path, repo_type="dataset")
    if path.lower().endswith(".csv"):
        df = pd.read_csv(local)
        numeric = df.select_dtypes(include="number")
        series = [{"label": str(col), "y": floats(numeric[col].tolist())} for col in list(numeric.columns)[:MAX_TRACES]]
    elif path.lower().endswith(".json"):
        with open(local, "r", encoding="utf-8") as f:
            series = find_series(json.load(f))
    else:
        with open(local, "r", encoding="utf-8", errors="ignore") as f:
            series = [{"label": "waveform", "y": floats(re.split(r"[\s,]+", f.read()))}]
    return series, path


def demo_series():
    series = []
    for j in range(10):
        y = []
        for i in range(900):
            pulse = math.exp(-((i - 260 - j * 10) ** 2) / 9500) * math.sin(i / (4.5 + j * .08))
            coda = .25 * math.exp(-max(0, i - 360) / 260) * math.sin(i / (10 + j * .3))
            y.append(pulse + coda + j * .04)
        series.append({"label": f"demo_trace_{j + 1}", "y": y})
    return series


def trace_stats(label, y):
    n = len(y)
    mean = sum(y) / n if n else 0
    var = sum((v - mean) ** 2 for v in y) / n if n else 0
    rms = math.sqrt(sum(v * v for v in y) / n) if n else 0
    return {
        "trace": label,
        "samples": n,
        "min": round(min(y), 6) if n else None,
        "max": round(max(y), 6) if n else None,
        "mean": round(mean, 6),
        "std": round(math.sqrt(var), 6),
        "rms": round(rms, 6),
        "peak_to_peak": round((max(y) - min(y)), 6) if n else None,
    }


def plot_waveform(source):
    try:
        series, label = load_waveform(source)
        msg = f"✅ 已載入 {label}，來源為 {WAVEFORM_DATASET_ID}/{WAVEFORM_PREFIX}，波形分開繪製並附統計資訊。"
    except Exception as exc:
        series, label = demo_series(), "demo://synthetic fallback"
        msg = f"⚠️ 遠端波形讀取失敗，改顯示示範資料：{exc}"

    clean = []
    stats_rows = []
    for item in series[:MAX_TRACES]:
        raw_y = floats(item.get("y", []))
        y = downsample(raw_y)
        if len(y) >= 2:
            trace_label = str(item.get("label") or f"trace_{len(clean) + 1}")
            clean.append({"label": trace_label, "y": y})
            stats_rows.append(trace_stats(trace_label, raw_y))

    if not clean:
        fig = go.Figure()
        fig.add_annotation(text="無可繪製的波形資料", x=.5, y=.5, showarrow=False)
        fig.update_layout(height=420, template="plotly_white")
        return msg, fig, pd.DataFrame([{"status": "no numeric waveform data"}])

    rows = min(len(clean), MAX_TRACES)
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.014, subplot_titles=[item["label"] for item in clean[:rows]])
    for idx, item in enumerate(clean[:rows], start=1):
        y = item["y"]
        ymin, ymax = min(y), max(y)
        pad = max((ymax - ymin) * 0.12, 1e-9)
        fig.add_trace(
            go.Scatter(x=list(range(len(y))), y=y, mode="lines", name=item["label"], line={"width": 1.6}, hovertemplate="sample=%{x}<br>amp=%{y:.6f}<extra></extra>", showlegend=False),
            row=idx,
            col=1,
        )
        fig.update_yaxes(title_text=f"T{idx}", showgrid=True, zeroline=True, zerolinewidth=1, zerolinecolor="rgba(30,41,59,0.45)", range=[ymin - pad, ymax + pad], row=idx, col=1)

    fig.update_xaxes(title_text="Samples / time", showgrid=True, zeroline=False, row=rows, col=1)
    fig.update_layout(title=f"靜態波形：{label}", height=max(1050, rows * 220), template="plotly_white", margin={"l":80,"r":28,"t":80,"b":60}, hovermode="x unified")
    return msg, fig, pd.DataFrame(stats_rows)


status_opts = status_choices()
wave_opts = waveform_choices()

with gr.Blocks(title="EEW Dashboard") as demo:
    gr.Markdown("# EEW Dashboard\n系統狀態、地震事件與靜態波形儀表板。地震事件使用 Folium 地圖呈現；波形讀取 `oceanicdayi/eew_hermes_dashboard/tsmip`。")
    with gr.Tab("系統狀態"):
        with gr.Row():
            s = gr.Dropdown(choices=status_opts, value=status_opts[0], label="Status file")
            sr = gr.Button("重新讀取狀態清單")
            sl = gr.Button("載入狀態")
        sm = gr.Markdown()
        lights = gr.HTML()
        sr.click(refresh_status, outputs=[s, sm])
        sl.click(render_status, inputs=s, outputs=lights)
        s.change(render_status, inputs=s, outputs=lights)
        demo.load(render_status, inputs=s, outputs=lights)
    with gr.Tab("地震事件"):
        with gr.Row():
            es = gr.Dropdown(choices=status_opts, value=status_opts[0], label="Status file")
            er = gr.Button("重新讀取狀態清單")
            el = gr.Button("載入事件")
        em = gr.Markdown()
        event_info = gr.HTML()
        event_map = gr.HTML(label="Folium earthquake map")
        er.click(refresh_status, outputs=[es, em])
        el.click(render_event, inputs=es, outputs=[event_info, event_map])
        es.change(render_event, inputs=es, outputs=[event_info, event_map])
        demo.load(render_event, inputs=es, outputs=[event_info, event_map])
    with gr.Tab("靜態波形"):
        with gr.Row():
            w = gr.Dropdown(choices=wave_opts, value=wave_opts[0], label="Waveform file")
            wr = gr.Button("重新讀取波形清單")
            wp = gr.Button("顯示波形")
        wm = gr.Markdown()
        plot = gr.Plot(label="Plotly waveform")
        stats = gr.Dataframe(label="波形統計資訊", interactive=False)
        wr.click(refresh_waveforms, outputs=[w, wm])
        wp.click(plot_waveform, inputs=w, outputs=[wm, plot, stats])
        w.change(plot_waveform, inputs=w, outputs=[wm, plot, stats])
        demo.load(plot_waveform, inputs=w, outputs=[wm, plot, stats])

if __name__ == "__main__":
    demo.launch()
