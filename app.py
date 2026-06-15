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

STATUS_DATASET_ID = "oceanicdayi/eew_status"
WAVEFORM_DATASET_ID = "oceanicdayi/eew_hermes_dashboard"
DEFAULT_STATUS_CANDIDATES = ["status/eew_status_report.json", "eew_status_report.json"]
DEFAULT_STATUS_PATH = DEFAULT_STATUS_CANDIDATES[0]
DEFAULT_WAVEFORM_PATH = "waveforms/rolling.json"
WAVEFORM_PREFIX = "waveforms/"
SUPPORTED_WAVEFORM_SUFFIXES = (".csv", ".json", ".txt")


def _esc(value):
    return html_lib.escape("" if value is None else str(value))


def _as_float(value):
    try:
        if value is None:
            return None
        if isinstance(value, str):
            value = (
                value.strip()
                .replace("km", "")
                .replace("秒", "")
                .replace("sec", "")
                .replace("s", "")
                .replace("%", "")
            )
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_num(value, digits=2, suffix=""):
    value = _as_float(value)
    if value is None:
        return "—"
    return f"{value:.{digits}f}{suffix}"


def _load_local_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _status_repo_files():
    try:
        return list_repo_files(STATUS_DATASET_ID, repo_type="dataset")
    except Exception:
        return []


@lru_cache(maxsize=1)
def _waveform_repo_files():
    try:
        return list_repo_files(WAVEFORM_DATASET_ID, repo_type="dataset")
    except Exception:
        return []


def _status_choices():
    files = _status_repo_files()
    json_files = sorted([f for f in files if f.lower().endswith(".json")], reverse=True)
    choices = []
    for preferred in DEFAULT_STATUS_CANDIDATES:
        if preferred in files and preferred not in choices:
            choices.append(preferred)
    if not choices:
        choices.append(DEFAULT_STATUS_PATH)
    choices.extend([f for f in json_files if f not in choices])
    return choices or ["fixtures://normal_event.json"]


def _waveform_choices():
    files = _waveform_repo_files()
    waveforms = sorted([
        f for f in files
        if f.startswith(WAVEFORM_PREFIX) and f.lower().endswith(SUPPORTED_WAVEFORM_SUFFIXES)
    ], reverse=True)
    choices = []
    if DEFAULT_WAVEFORM_PATH in files:
        choices.append(DEFAULT_WAVEFORM_PATH)
    else:
        choices.append(DEFAULT_WAVEFORM_PATH)
    choices.extend([f for f in waveforms if f not in choices])
    return choices or ["demo://synthetic"]


def refresh_status_choices():
    _status_repo_files.cache_clear()
    choices = _status_choices()
    return gr.update(choices=choices, value=choices[0]), f"已重新讀取系統狀態清單：{len(choices)} 筆，來源 {STATUS_DATASET_ID}"


def refresh_waveform_choices():
    _waveform_repo_files.cache_clear()
    choices = _waveform_choices()
    return gr.update(choices=choices, value=choices[0]), f"已重新讀取波形清單：{len(choices)} 筆，來源 {WAVEFORM_DATASET_ID}"


def _download_status_json(repo_path):
    local = hf_hub_download(repo_id=STATUS_DATASET_ID, filename=repo_path, repo_type="dataset")
    with open(local, "r", encoding="utf-8") as f:
        return json.load(f), repo_path


def _load_status_payload(source):
    if source and source.startswith("fixtures://"):
        return _load_local_json(FIXTURES / source.split("://", 1)[1]), source

    candidates = [source] if source else []
    candidates.extend([p for p in DEFAULT_STATUS_CANDIDATES if p not in candidates])

    last_error = None
    for candidate in candidates:
        try:
            return _download_status_json(candidate)
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"無法從 {STATUS_DATASET_ID} 讀取系統狀態：{last_error}")


def _first_value(obj, keys):
    if not isinstance(obj, dict):
        return None
    lower = {str(k).lower(): v for k, v in obj.items()}
    for key in keys:
        if key in obj:
            return obj[key]
        lk = str(key).lower()
        if lk in lower:
            return lower[lk]
    return None


def _event_line_from_header(payload):
    header = payload.get("latest_rep_header") or {}
    head = header.get("head") or []
    if not isinstance(head, list):
        return "", None, header.get("file") or ""

    report_time = None
    event_line = ""
    for line in head:
        text = str(line)
        match = re.search(r"Reporting time\s+([0-9/]{8,10}\s+[0-9:.]+)", text)
        if match:
            report_time = match.group(1)
        if re.match(r"^\s*\d{4}\s+\d+\s+\d+\s+\d+\s+\d+", text):
            event_line = text
    return event_line, report_time, header.get("file") or ""


def _event_from_status(payload):
    event_line, report_time, source_file = _event_line_from_header(payload)
    event = {
        "report_time": report_time,
        "lat": None,
        "lon": None,
        "depth_km": None,
        "Mall": None,
        "Mpd": None,
        "Mtc": None,
        "process_time": None,
        "source_file": source_file,
        "raw_event_line": event_line,
    }

    if event_line:
        parts = event_line.split()
        try:
            event["lat"] = _as_float(parts[6])
            event["lon"] = _as_float(parts[7])
            event["depth_km"] = _as_float(parts[8])
            event["Mall"] = _as_float(parts[9])
            event["Mpd"] = _as_float(parts[12]) if len(parts) > 12 else None
            event["Mtc"] = _as_float(parts[13]) if len(parts) > 13 else None
            event["process_time"] = _as_float(parts[14]) if len(parts) > 14 else None
        except IndexError:
            pass

    candidate_keys = [
        "latest_event", "event", "latest_report", "eew_event", "earthquake",
        "summary", "latest_event_summary", "eew_summary",
    ]
    for candidate in [payload.get(k) for k in candidate_keys if isinstance(payload.get(k), dict)]:
        mapping = {
            "lat": ["lat", "latitude", "epicenter_lat", "epi_lat"],
            "lon": ["lon", "lng", "longitude", "epicenter_lon", "epi_lon"],
            "depth_km": ["depth", "depth_km", "dep"],
            "Mall": ["Mall", "mall", "magnitude", "mag"],
            "Mpd": ["Mpd", "mpd"],
            "Mtc": ["Mtc", "mtc"],
            "process_time": ["process_time", "processing_time", "latency", "elapsed"],
            "report_time": ["report_time", "reporting_time", "time", "origin_time"],
        }
        for target, keys in mapping.items():
            value = _first_value(candidate, keys)
            if value is not None:
                event[target] = value if target == "report_time" else _as_float(value)
    return event


def _container_rows(payload):
    containers = (
        payload.get("containers")
        or payload.get("container_status")
        or payload.get("container")
        or payload.get("docker")
        or {}
    )
    rows = []
    if isinstance(containers, dict):
        for name, info in containers.items():
            if isinstance(info, dict):
                rows.append({
                    "module": name,
                    "status": info.get("status") or info.get("state") or info.get("health") or "",
                    "ports": info.get("ports", ""),
                    "image": info.get("image", ""),
                })
            else:
                rows.append({"module": name, "status": info, "ports": "", "image": ""})
    elif isinstance(containers, list):
        for item in containers:
            if isinstance(item, dict):
                rows.append({
                    "module": item.get("name") or item.get("container") or "container",
                    "status": item.get("status") or item.get("state") or "",
                    "ports": item.get("ports", ""),
                    "image": item.get("image", ""),
                })
    return rows


def _disk_rows(payload):
    disk = payload.get("disk_root") or payload.get("disk_usage") or payload.get("disk")
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


def _status_class(text):
    text = str(text or "").lower()
    if any(k in text for k in ["up", "ok", "running", "healthy", "運行", "正常", "available", "exists", "success"]):
        return "ok"
    if any(k in text for k in ["warn", "warning", "partial", "警告"]):
        return "warn"
    if any(k in text for k in ["down", "fail", "error", "exited", "停止", "missing"]):
        return "bad"
    return "neutral"


def _pct(value):
    match = re.search(r"(\d+(?:\.\d+)?)", str(value or ""))
    return min(100, max(0, float(match.group(1)))) if match else 0


def _module_rows(payload):
    rows = []
    for row in _container_rows(payload):
        rows.append({
            "item": row["module"],
            "status": row["status"],
            "detail": row.get("ports") or row.get("image") or "",
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

    status_files = _status_repo_files()
    waveform_files = _waveform_repo_files()
    status_exists = any(path in status_files for path in DEFAULT_STATUS_CANDIDATES)
    rows.append({
        "item": "系統狀態資料集",
        "status": "exists" if status_exists else "not confirmed",
        "detail": STATUS_DATASET_ID,
    })
    rows.append({
        "item": DEFAULT_WAVEFORM_PATH,
        "status": "exists" if DEFAULT_WAVEFORM_PATH in waveform_files else "not confirmed",
        "detail": WAVEFORM_DATASET_ID,
    })
    return rows


def _cards_html(payload, source):
    event = _event_from_status(payload)
    containers = _container_rows(payload)
    earthworm = next((r for r in containers if "earthworm" in str(r.get("module", "")).lower()), None)
    earthworm_status = earthworm.get("status") if earthworm else "not found"
    disk = (_disk_rows(payload) or [{}])[0]
    disk_percent = _pct(disk.get("use_percent"))
    job_id = payload.get("job_id") or payload.get("cron_job_id") or "—"
    commit = payload.get("commit") or payload.get("commit_sha") or payload.get("hf_commit") or payload.get("upload_commit") or "—"
    collected = payload.get("host_time") or payload.get("collected_utc") or payload.get("time") or "—"

    module_cards = []
    for row in _module_rows(payload)[:10]:
        cls = _status_class(row.get("status"))
        module_cards.append(
            f"<div class='mini {cls}'><b>{_esc(row.get('item'))}</b>"
            f"<strong>{_esc(row.get('status'))}</strong><small>{_esc(row.get('detail'))}</small></div>"
        )

    css = """
<style>
.wrap{display:grid;gap:14px}.hero{border-radius:22px;padding:18px;color:#fff;background:linear-gradient(135deg,#0f172a,#1d4ed8 55%,#0891b2);box-shadow:0 12px 28px rgba(15,23,42,.18)}
.hero h2{margin:0 0 8px 0;font-size:26px}.sub{opacity:.88;font-size:14px;word-break:break-all}.pills{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.pill{background:rgba(255,255,255,.15);padding:8px 10px;border-radius:999px;font-weight:800}
.card{border:1px solid rgba(15,23,42,.1);border-radius:18px;background:#fff;padding:14px;box-shadow:0 6px 20px rgba(15,23,42,.06)}.card h3{margin:0 0 10px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px}.box{background:#f8fafc;border:1px solid #e5e7eb;border-radius:14px;padding:12px}.label{font-size:12px;color:#64748b;font-weight:700}.value{font-size:20px;font-weight:900;color:#0f172a;margin-top:4px}
.modules{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}.mini{border:1px solid #e5e7eb;border-left:6px solid #64748b;border-radius:16px;padding:12px;min-height:100px}.mini.ok{border-left-color:#16a34a}.mini.warn{border-left-color:#f59e0b}.mini.bad{border-left-color:#dc2626}.mini strong{display:block;font-size:19px;margin-top:8px}.mini small{display:block;color:#64748b;margin-top:8px;word-break:break-all}
.diskbar{height:16px;background:#e5e7eb;border-radius:999px;overflow:hidden;margin:10px 0}.diskfill{height:100%;background:linear-gradient(90deg,#22c55e,#f59e0b)}.muted{color:#64748b;font-size:13px;word-break:break-all}
@media(max-width:640px){.hero h2{font-size:22px}.grid,.modules{grid-template-columns:1fr}.value{font-size:18px}}
</style>
"""
    return css + f"""
<div class='wrap'>
  <div class='hero'>
    <h2>HF Dataset EEW Status Upload</h2>
    <div class='sub'>系統狀態來源：{_esc(STATUS_DATASET_ID)} / {_esc(source)}</div>
    <div class='pills'>
      <div class='pill'>earthworm_eew：{_esc(earthworm_status)}</div>
      <div class='pill'>Job ID：{_esc(job_id)}</div>
      <div class='pill'>波形來源：{_esc(WAVEFORM_DATASET_ID)}</div>
    </div>
  </div>
  <div class='card'>
    <h3>最新事件</h3>
    <div class='grid'>
      <div class='box'><div class='label'>報告時間</div><div class='value'>{_esc(event.get('report_time') or '—')}</div></div>
      <div class='box'><div class='label'>Latitude</div><div class='value'>{_format_num(event.get('lat'),4)}</div></div>
      <div class='box'><div class='label'>Longitude</div><div class='value'>{_format_num(event.get('lon'),4)}</div></div>
      <div class='box'><div class='label'>Depth</div><div class='value'>{_format_num(event.get('depth_km'),1,' km')}</div></div>
      <div class='box'><div class='label'>Mall</div><div class='value'>{_format_num(event.get('Mall'),2)}</div></div>
      <div class='box'><div class='label'>Mpd</div><div class='value'>{_format_num(event.get('Mpd'),2)}</div></div>
      <div class='box'><div class='label'>Mtc</div><div class='value'>{_format_num(event.get('Mtc'),2)}</div></div>
      <div class='box'><div class='label'>處理耗時</div><div class='value'>{_format_num(event.get('process_time'),2,' 秒')}</div></div>
    </div>
  </div>
  <div class='card'><h3>Earthworm / EEW 模組狀態</h3><div class='modules'>{''.join(module_cards)}</div></div>
  <div class='card'>
    <h3>硬碟使用量</h3>
    <b>{_esc(disk.get('device') or '/')}</b> <span class='muted'>{_esc(disk.get('use_percent') or '—')}</span>
    <div class='diskbar'><div class='diskfill' style='width:{disk_percent:.1f}%'></div></div>
    <div class='muted'>used {_esc(disk.get('used'))} / {_esc(disk.get('size'))}，available {_esc(disk.get('available'))}，mount {_esc(disk.get('mount'))}</div>
  </div>
  <div class='card'>
    <h3>資料集與上傳資訊</h3>
    <div class='muted'>系統狀態 Dataset：{_esc(STATUS_DATASET_ID)}</div>
    <div class='muted'>狀態檔：{_esc(source)}</div>
    <div class='muted'>Commit：{_esc(commit)}</div>
    <div class='muted'>波形 Dataset：{_esc(WAVEFORM_DATASET_ID)}</div>
    <div class='muted'>預設波形：{_esc(DEFAULT_WAVEFORM_PATH)}</div>
    <div class='muted'>資料時間：{_esc(collected)}</div>
  </div>
</div>
"""


def render_system_status(status_source):
    try:
        payload, label = _load_status_payload(status_source)
    except Exception as exc:
        payload = _load_local_json(FIXTURES / "normal_event.json")
        label = f"fixtures://normal_event.json（遠端讀取失敗：{exc}）"
    return (
        _cards_html(payload, label),
        pd.DataFrame(_module_rows(payload)),
        pd.DataFrame(_disk_rows(payload) or [{"status": "no disk data"}]),
        pd.DataFrame([_event_from_status(payload)]),
    )


def render_event_map(status_source):
    try:
        payload, label = _load_status_payload(status_source)
    except Exception as exc:
        payload = _load_local_json(FIXTURES / "normal_event.json")
        label = f"fixtures://normal_event.json（遠端讀取失敗：{exc}）"

    event = _event_from_status(payload)
    lat = event.get("lat") or 23.7
    lon = event.get("lon") or 121.0
    mag = event.get("Mall") or event.get("Mpd") or event.get("Mtc") or 0

    fmap = folium.Map(location=[lat, lon], zoom_start=7, tiles="CartoDB positron")
    radius = max(8000, float(mag) * 12000) if mag else 8000
    folium.Circle(location=[lat, lon], radius=radius, popup=f"Mall {mag:.2f}" if mag else "EEW event", fill=True).add_to(fmap)
    folium.Marker([lat, lon], tooltip=f"{event.get('report_time') or label} | Mall {mag:.2f}" if mag else label).add_to(fmap)

    summary = pd.DataFrame([
        {"field": "status_dataset", "value": STATUS_DATASET_ID},
        {"field": "source", "value": label},
        {"field": "report_time", "value": event.get("report_time")},
        {"field": "latitude", "value": event.get("lat")},
        {"field": "longitude", "value": event.get("lon")},
        {"field": "depth_km", "value": event.get("depth_km")},
        {"field": "Mall", "value": event.get("Mall")},
        {"field": "Mpd", "value": event.get("Mpd")},
        {"field": "Mtc", "value": event.get("Mtc")},
        {"field": "process_time", "value": event.get("process_time")},
    ])
    return "✅ 已從 oceanicdayi/eew_status 載入最新事件", summary, fmap._repr_html_(), json.dumps(payload, ensure_ascii=False, indent=2)


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
    time_like = {"time", "t", "timestamp", "datetime", "sec", "second", "seconds", "sample", "index"}
    numeric = {}
    for row in records:
        for key, value in row.items():
            if str(key).lower() in time_like:
                continue
            try:
                numeric.setdefault(str(key), []).append(float(value))
            except (TypeError, ValueError):
                pass
    out = []
    for key, vals in numeric.items():
        if len(vals) >= 8:
            out.append({"label": f"{prefix}.{key}", "y": vals})
        if len(out) >= 3:
            break
    return out


def _series_from_csv(path):
    df = pd.read_csv(path)
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            vals = _coerce_floats(re.split(r"[\s,]+", f.read()))
        return [{"label": "waveform", "y": vals}] if vals else []
    time_like = {"time", "t", "timestamp", "sec", "second", "seconds", "sample", "index"}
    cols = [c for c in numeric.columns if str(c).strip().lower() not in time_like] or list(numeric.columns)
    return [{"label": str(c), "y": _coerce_floats(numeric[c].tolist())} for c in cols[:3]]


def _find_numeric_arrays(obj, prefix="", found=None):
    if found is None:
        found = []
    if len(found) >= 3:
        return found
    if isinstance(obj, list):
        records = _series_from_records(obj, prefix or "records")
        if records:
            found.extend(records[: 3 - len(found)])
            return found
        vals = _coerce_floats(obj)
        if len(vals) >= max(8, len(obj) // 2):
            found.append({"label": prefix or "waveform", "y": vals})
        else:
            for i, item in enumerate(obj[:50]):
                _find_numeric_arrays(item, f"{prefix}[{i}]", found)
                if len(found) >= 3:
                    break
    elif isinstance(obj, dict):
        preferred = ["samples", "data", "waveform", "waveforms", "values", "amplitude", "acc", "velocity", "displacement", "z", "n", "e", "HLZ", "HLN", "HLE"]
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


def _series_from_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return _find_numeric_arrays(json.load(f))


def _series_from_txt(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        vals = _coerce_floats(re.split(r"[\s,]+", f.read()))
    return [{"label": "waveform", "y": vals}] if vals else []


def _synthetic_waveform():
    vals = []
    for i in range(900):
        pulse = math.exp(-((i - 260) ** 2) / 9500) * math.sin(i / 4.5)
        coda = 0.35 * math.exp(-max(0, i - 360) / 260) * math.sin(i / 11)
        vals.append(pulse + coda)
    return [{"label": "demo waveform", "y": vals}]


def _load_waveform_series(path):
    if not path or path == "demo://synthetic":
        return _synthetic_waveform(), "demo://synthetic"
    local = hf_hub_download(repo_id=WAVEFORM_DATASET_ID, filename=path, repo_type="dataset")
    low = path.lower()
    if low.endswith(".csv"):
        series = _series_from_csv(local)
    elif low.endswith(".json"):
        series = _series_from_json(local)
    elif low.endswith(".txt"):
        series = _series_from_txt(local)
    else:
        series = []
    return series, path


def _svg_path(values, width, height, pad, min_y, max_y):
    if not values:
        return ""
    if min_y == max_y:
        min_y -= 1
        max_y += 1
    denom = max(1, len(values) - 1)
    pts = []
    for i, v in enumerate(values):
        x = pad + (i / denom) * (width - pad * 1.5)
        y = height - pad - ((v - min_y) / (max_y - min_y)) * (height - pad * 2)
        pts.append(f"{x:.2f},{y:.2f}")
    return "M " + " L ".join(pts)


def _waveform_html(series, label):
    clean = []
    for item in series[:3]:
        y = _downsample(_coerce_floats(item.get("y", [])))
        if y:
            clean.append({"label": str(item.get("label") or "waveform"), "y": y})
    if not clean:
        return "<div style='padding:1rem;border:1px solid #ddd;border-radius:12px;color:#64748b'>無可繪製的波形資料。請確認 JSON/CSV 內含數值序列。</div>"

    w, h, p = 1100, 320, 44
    colors = ["#0ea5e9", "#22c55e", "#f97316"]
    all_y = [v for item in clean for v in item["y"]]
    min_y, max_y = min(all_y), max(all_y)
    if min_y == max_y:
        min_y -= 1
        max_y += 1

    grid = []
    for i in range(7):
        y = p + i * (h - p * 2) / 6
        grid.append(f"<line x1='{p}' y1='{y:.2f}' x2='{w-p/2}' y2='{y:.2f}' class='grid'/>")
    for i in range(11):
        x = p + i * (w - p * 1.5) / 10
        grid.append(f"<line x1='{x:.2f}' y1='{p/2}' x2='{x:.2f}' y2='{h-p}' class='grid'/>")

    paths, legends = [], []
    for idx, item in enumerate(clean):
        color = colors[idx % len(colors)]
        d = _svg_path(item["y"], w, h, p, min_y, max_y)
        paths.append(f"<path d='{d}' class='line' stroke='{color}'/>")
        lx = p + 8 + idx * 190
        legends.append(f"<g><line x1='{lx}' y1='28' x2='{lx+24}' y2='28' stroke='{color}' stroke-width='4'/><text x='{lx+32}' y='32' class='legend'>{_esc(item['label'])}</text></g>")

    css = """
<style>
.wave-card{border:1px solid rgba(15,23,42,.12);border-radius:18px;padding:14px;background:#fff;box-shadow:0 8px 22px rgba(15,23,42,.07)}
.wave-title{font-weight:900;font-size:18px;margin-bottom:10px;color:#0f172a;word-break:break-all}.wave-wrap{border-radius:14px;overflow:hidden;background:#f8fafc;border:1px solid #e5e7eb}.wave-wrap svg{width:100%;height:320px;display:block}
.bg{fill:#f8fafc}.grid{stroke:rgba(100,116,139,.22);stroke-width:1}.axis{stroke:rgba(15,23,42,.45);stroke-width:1.2}.axis-label{fill:#475569;font:14px system-ui,sans-serif}.legend{fill:#334155;font:13px system-ui,sans-serif}.line{fill:none;stroke-width:2.3;stroke-linecap:round;stroke-linejoin:round}.caption{font-size:13px;color:#64748b;margin-top:8px}
@media(max-width:640px){.wave-wrap svg{height:260px}}
</style>
"""
    return css + f"""
<div class='wave-card'>
  <div class='wave-title'>靜態波形：{_esc(label)}</div>
  <div class='wave-wrap'>
    <svg viewBox='0 0 {w} {h}' preserveAspectRatio='none' role='img' aria-label='Static waveform'>
      <rect width='{w}' height='{h}' rx='14' class='bg'/>
      {''.join(grid)}
      <line x1='{p}' y1='{p/2}' x2='{p}' y2='{h-p}' class='axis'/>
      <line x1='{p}' y1='{h-p}' x2='{w-p/2}' y2='{h-p}' class='axis'/>
      <text x='12' y='24' class='axis-label'>Amplitude</text>
      <text x='{w-155}' y='{h-14}' class='axis-label'>Samples / time</text>
      <text x='{p+4}' y='{p-8}' class='axis-label'>{_esc(f'{max_y:.4g}')}</text>
      <text x='{p+4}' y='{h-p-5}' class='axis-label'>{_esc(f'{min_y:.4g}')}</text>
      {''.join(legends)}
      {''.join(paths)}
    </svg>
  </div>
  <div class='caption'>預設讀取 {DEFAULT_WAVEFORM_PATH}；靜態展示可避免手機黑畫面。</div>
</div>
"""


def render_waveform(path):
    try:
        series, label = _load_waveform_series(path)
        html = _waveform_html(series, label)
        rows = []
        for item in series[:3]:
            y = _coerce_floats(item.get("y", []))
            if y:
                rows.append({"channel": item.get("label", "waveform"), "samples": len(y), "min": min(y), "max": max(y), "mean": sum(y) / len(y)})
        return f"✅ 已載入波形：{label}", html, pd.DataFrame(rows or [{"status": "no numeric waveform found"}])
    except Exception as exc:
        return "⚠️ 無法讀取遠端波形，已顯示示範波形。", _waveform_html(_synthetic_waveform(), "demo://synthetic fallback"), pd.DataFrame([{"error": str(exc)}])


status_choices = _status_choices()
waveform_choices = _waveform_choices()

with gr.Blocks(title="EEW Dashboard") as demo:
    gr.Markdown("# EEW Dashboard\n系統狀態讀取 `oceanicdayi/eew_status`；波形讀取 `oceanicdayi/eew_hermes_dashboard`。")

    with gr.Tab("系統狀態"):
        gr.Markdown("## Cronjob Response: HF Dataset EEW Status Upload")
        with gr.Row():
            status_source = gr.Dropdown(choices=status_choices, value=status_choices[0], label="Status file")
            status_refresh = gr.Button("重新讀取狀態清單")
            status_load = gr.Button("載入狀態")
        status_msg = gr.Markdown()
        cards = gr.HTML()
        module_table = gr.Dataframe(label="模組與遠端檔案狀態", interactive=False)
        disk_table = gr.Dataframe(label="硬碟使用量", interactive=False)
        event_table = gr.Dataframe(label="最新事件明細", interactive=False)
        status_refresh.click(refresh_status_choices, outputs=[status_source, status_msg])
        status_load.click(render_system_status, inputs=status_source, outputs=[cards, module_table, disk_table, event_table])
        status_source.change(render_system_status, inputs=status_source, outputs=[cards, module_table, disk_table, event_table])
        demo.load(render_system_status, inputs=status_source, outputs=[cards, module_table, disk_table, event_table])

    with gr.Tab("事件地圖"):
        gr.Markdown("## 最新事件震央地圖\n預設使用 `oceanicdayi/eew_status` 的狀態檔。")
        with gr.Row():
            map_source = gr.Dropdown(choices=status_choices, value=status_choices[0], label="Status file")
            map_refresh = gr.Button("載入地圖")
        map_status = gr.Markdown()
        summary = gr.Dataframe(label="事件摘要", interactive=False)
        map_html = gr.HTML(label="Map")
        raw = gr.Code(label="Raw JSON", language="json")
        map_refresh.click(render_event_map, inputs=map_source, outputs=[map_status, summary, map_html, raw])
        map_source.change(render_event_map, inputs=map_source, outputs=[map_status, summary, map_html, raw])
        demo.load(render_event_map, inputs=map_source, outputs=[map_status, summary, map_html, raw])

    with gr.Tab("靜態波形"):
        gr.Markdown("## 波形資料\n預設使用 `waveforms/rolling.json`，來源 `oceanicdayi/eew_hermes_dashboard`。")
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
