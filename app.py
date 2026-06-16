import base64
import html
import json
import re
from functools import lru_cache
from pathlib import Path

import folium
import gradio as gr
from huggingface_hub import hf_hub_download, list_repo_files

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

STATUS_DATASET_ID = "oceanicdayi/eew_status"
EVENT_DATASET_ID = "oceanicdayi/eew_hermes_dashboard"
WAVEFORM_DATASET_ID = "oceanicdayi/eew_hermes_dashboard"
DEFAULT_STATUS = "status/eew_status_report.json"
DEFAULT_STATUS_ALT = "eew_status_report.json"
EVENT_STATUS_PATH = "status/eew_status_report.json"
WAVEFORM_IMAGE_PATH = "tsmip/tsmip_hlz_3min_clusters.png"

LAT_KEYS = {
    "lat", "latitude", "event_lat", "event_latitude", "epicenter_lat", "epicenter_latitude",
    "origin_lat", "origin_latitude", "hypo_lat", "hypocenter_lat", "eq_lat", "eqlat", "evlat",
}
LON_KEYS = {
    "lon", "lng", "longitude", "event_lon", "event_lng", "event_longitude",
    "epicenter_lon", "epicenter_lng", "epicenter_longitude", "origin_lon", "origin_lng",
    "hypo_lon", "hypocenter_lon", "eq_lon", "eqlon", "evlon",
}
MAG_KEYS = {"magnitude", "mag", "ml", "mw", "m", "mall", "mpd", "mtc", "M", "Mpd", "Mtc", "Mall"}
DEPTH_KEYS = {"depth", "depth_km", "dep", "hypo_depth", "hypocenter_depth"}
TIME_KEYS = {"time", "origin_time", "event_time", "timestamp", "datetime", "report_time", "trigger_time"}
LOCATION_KEYS = {"location", "area", "epicenter", "place", "region", "description"}
SOURCE_KEYS = {"source_file", "file", "filename", "source"}


def esc(value):
    return html.escape("" if value is None else str(value))


def to_float(value):
    try:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        return float(match.group(0))
    except Exception:
        return None


def first_value(obj, keys):
    if not isinstance(obj, dict):
        return None
    lower_map = {str(k).lower(): k for k in obj.keys()}
    for key in keys:
        actual = lower_map.get(str(key).lower())
        if actual is not None:
            value = obj.get(actual)
            if value not in (None, ""):
                return value
    return None


@lru_cache(maxsize=1)
def status_files():
    try:
        return list_repo_files(STATUS_DATASET_ID, repo_type="dataset")
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


def refresh_status():
    status_files.cache_clear()
    choices = status_choices()
    return gr.update(choices=choices, value=choices[0]), f"已重新讀取 {STATUS_DATASET_ID}：{len(choices)} 筆"


def load_system_status(source):
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


def load_event_status():
    path = hf_hub_download(repo_id=EVENT_DATASET_ID, filename=EVENT_STATUS_PATH, repo_type="dataset")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f), EVENT_STATUS_PATH


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
        payload, label = load_system_status(source)
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


def score_event_candidate(obj, path, lat, lon):
    path_text = ".".join(path).lower()
    score = 0
    keys = {str(k).lower() for k in obj.keys()}
    if keys.intersection({k.lower() for k in MAG_KEYS}):
        score += 6
    if keys.intersection({k.lower() for k in DEPTH_KEYS}):
        score += 5
    if keys.intersection({k.lower() for k in TIME_KEYS}):
        score += 3
    if any(word in path_text for word in ["event", "eew", "earthquake", "latest", "report", "hypo"]):
        score += 3
    if any(word in path_text for word in ["station", "stations", "sta"]):
        score -= 5
    if 18 <= lat <= 28 and 118 <= lon <= 124:
        score += 2
    return score


def collect_station_candidates(obj, path=None, out=None):
    path = path or []
    out = out or []
    if isinstance(obj, dict):
        lat = to_float(first_value(obj, LAT_KEYS))
        lon = to_float(first_value(obj, LON_KEYS))
        path_text = ".".join(path).lower()
        if lat is not None and lon is not None and any(word in path_text for word in ["station", "stations", "sta"]):
            name = first_value(obj, {"station", "sta", "name", "code", "id"}) or f"STA{len(out) + 1}"
            out.append({"station": str(name), "lat": lat, "lon": lon})
        for key, value in obj.items():
            collect_station_candidates(value, path + [str(key)], out)
    elif isinstance(obj, list):
        for idx, value in enumerate(obj[:300]):
            collect_station_candidates(value, path + [str(idx)], out)
    return out


def recursive_event_candidates(obj, path=None, out=None):
    path = path or []
    out = out or []
    if isinstance(obj, dict):
        lat = to_float(first_value(obj, LAT_KEYS))
        lon = to_float(first_value(obj, LON_KEYS))
        if lat is not None and lon is not None:
            score = score_event_candidate(obj, path, lat, lon)
            out.append({
                "time": first_value(obj, TIME_KEYS),
                "lat": lat,
                "lon": lon,
                "depth_km": to_float(first_value(obj, DEPTH_KEYS)),
                "magnitude": to_float(first_value(obj, MAG_KEYS)),
                "location": first_value(obj, LOCATION_KEYS),
                "source_file": first_value(obj, SOURCE_KEYS),
                "stations": [],
                "_score": score,
            })
        for key, value in obj.items():
            recursive_event_candidates(value, path + [str(key)], out)
    elif isinstance(obj, list):
        for idx, value in enumerate(obj[:300]):
            recursive_event_candidates(value, path + [str(idx)], out)
    return out


def event_from_payload(payload):
    event = parse_rep_event(payload)
    if event:
        return event

    candidates = recursive_event_candidates(payload)
    if candidates:
        candidates.sort(key=lambda item: item.get("_score", 0), reverse=True)
        event = candidates[0]
        event.pop("_score", None)
        event["stations"] = collect_station_candidates(payload)
        return event

    text = json.dumps(payload, ensure_ascii=False)
    lat_match = re.search(r"(?:lat|latitude)[^0-9\-]{0,20}(-?\d+(?:\.\d+)?)", text, re.I)
    lon_match = re.search(r"(?:lon|lng|longitude)[^0-9\-]{0,20}(-?\d+(?:\.\d+)?)", text, re.I)
    if lat_match and lon_match:
        return {"lat": to_float(lat_match.group(1)), "lon": to_float(lon_match.group(1)), "stations": []}
    return None


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


def render_event():
    try:
        payload, label = load_event_status()
    except Exception as exc:
        with open(FIXTURES / "normal_event.json", "r", encoding="utf-8") as f:
            payload = json.load(f)
        label = f"fallback: {exc}"
    event = event_from_payload(payload)
    return event_info_html(event, f"{EVENT_DATASET_ID}/{label}"), folium_map_html(event)


def render_waveform_image():
    image_url = f"https://huggingface.co/datasets/{WAVEFORM_DATASET_ID}/resolve/main/{WAVEFORM_IMAGE_PATH}"
    try:
        image_path = hf_hub_download(repo_id=WAVEFORM_DATASET_ID, filename=WAVEFORM_IMAGE_PATH, repo_type="dataset")
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        image_html = (
            "<div style='border:1px solid #e5e7eb;border-radius:18px;padding:10px;background:#fff;box-shadow:0 8px 22px rgba(15,23,42,.06)'>"
            f"<img src='data:image/png;base64,{encoded}' alt='TSMIP HLZ 3-minute clusters' style='width:100%;height:auto;border-radius:12px;display:block'/>"
            "</div>"
        )
        return f"✅ 已載入波形圖：{WAVEFORM_DATASET_ID}/{WAVEFORM_IMAGE_PATH}", image_html
    except Exception as exc:
        fallback_html = (
            "<div style='padding:16px;border-radius:16px;background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;font-weight:800'>"
            f"無法透過 huggingface_hub 讀取圖檔：{esc(exc)}<br>"
            f"<a href='{esc(image_url)}' target='_blank'>直接開啟波形圖</a>"
            "</div>"
        )
        return f"⚠️ 無法讀取指定波形圖：{exc}", fallback_html


status_opts = status_choices()

with gr.Blocks(title="EEW Dashboard") as demo:
    gr.Markdown("# EEW Dashboard\n系統狀態、地震事件與靜態波形儀表板。地震事件資料讀取 `oceanicdayi/eew_hermes_dashboard/status/eew_status_report.json`；波形圖讀取 `tsmip/tsmip_hlz_3min_clusters.png`。")
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
        em = gr.Markdown(f"事件資料來源：`{EVENT_DATASET_ID}/{EVENT_STATUS_PATH}`")
        el = gr.Button("載入事件")
        event_info = gr.HTML()
        event_map = gr.HTML(label="Folium earthquake map")
        el.click(render_event, outputs=[event_info, event_map])
        demo.load(render_event, outputs=[event_info, event_map])
    with gr.Tab("靜態波形"):
        wm = gr.Markdown(f"波形圖來源：`{WAVEFORM_DATASET_ID}/{WAVEFORM_IMAGE_PATH}`")
        wp = gr.Button("載入波形圖")
        wave_msg = gr.Markdown()
        wave_img = gr.HTML(label="TSMIP HLZ 3-minute clusters")
        wp.click(render_waveform_image, outputs=[wave_msg, wave_img])
        demo.load(render_waveform_image, outputs=[wave_msg, wave_img])

if __name__ == "__main__":
    demo.launch()
