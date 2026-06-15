import html
import json
import math
import re
from functools import lru_cache
from pathlib import Path

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
DEFAULT_WAVEFORM = "waveforms/rolling.json"
MAX_TRACES = 10
MAX_POINTS = 2500


def esc(x):
    return html.escape("" if x is None else str(x))


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
    choices = [DEFAULT_WAVEFORM]
    choices += [
        f for f in sorted(files, reverse=True)
        if f.startswith("waveforms/") and f.lower().endswith((".json", ".csv", ".txt")) and f not in choices
    ]
    return choices or ["demo://synthetic"]


def refresh_status():
    status_files.cache_clear()
    choices = status_choices()
    return gr.update(choices=choices, value=choices[0]), f"已重新讀取 {STATUS_DATASET_ID}：{len(choices)} 筆"


def refresh_waveforms():
    waveform_files.cache_clear()
    choices = waveform_choices()
    return gr.update(choices=choices, value=choices[0]), f"已重新讀取 {WAVEFORM_DATASET_ID}：{len(choices)} 筆"


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
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError("; ".join(errors[-2:]))


def level(text):
    t = str(text).lower()
    if any(k in t for k in ["up", "ok", "running", "healthy", "success", "available", "exists", "運行", "正常"]):
        return "ok"
    if any(k in t for k in ["warn", "warning", "partial", "degraded", "警告"]):
        return "warn"
    if any(k in t for k in ["down", "fail", "error", "exited", "stopped", "missing", "停止"]):
        return "bad"
    return "neutral"


def module_items(payload):
    rows = []
    sources = ["containers", "container_status", "container", "docker", "modules", "module_status", "services", "checks"]
    for key in sources:
        value = payload.get(key)
        if isinstance(value, dict):
            for name, info in value.items():
                if isinstance(info, dict):
                    status = info.get("status") or info.get("state") or info.get("health") or info.get("ok") or "unknown"
                else:
                    status = info
                rows.append((str(name), str(status)))
        elif isinstance(value, list):
            for i, info in enumerate(value):
                if isinstance(info, dict):
                    name = info.get("name") or info.get("module") or info.get("container") or f"module_{i+1}"
                    status = info.get("status") or info.get("state") or info.get("health") or "unknown"
                    rows.append((str(name), str(status)))
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
    except Exception as e:
        with open(FIXTURES / "normal_event.json", "r", encoding="utf-8") as f:
            payload = json.load(f)
        label = f"fallback: {e}"
    cards = []
    for name, status in module_items(payload):
        cls = level(status)
        cards.append(f"<div class='card {cls}'><span class='lamp'></span><div><b>{esc(name)}</b><small>{esc(status)}</small></div></div>")
    return f"""
<style>
.wrap{{display:grid;gap:14px}}.hero{{border-radius:20px;padding:18px;color:#fff;background:linear-gradient(135deg,#0f172a,#2563eb);box-shadow:0 10px 24px rgba(15,23,42,.16)}}
.hero h2{{margin:0 0 6px;font-size:24px}}.src{{font-size:13px;opacity:.85;word-break:break-all}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
.card{{display:flex;gap:12px;align-items:center;padding:14px;border-radius:16px;background:#fff;border:1px solid #e5e7eb;box-shadow:0 6px 18px rgba(15,23,42,.06)}}
.lamp{{width:22px;height:22px;border-radius:99px;background:#64748b;box-shadow:0 0 0 6px rgba(100,116,139,.12)}}
.ok .lamp{{background:#22c55e;box-shadow:0 0 0 6px rgba(34,197,94,.16),0 0 18px rgba(34,197,94,.65)}}
.warn .lamp{{background:#f59e0b;box-shadow:0 0 0 6px rgba(245,158,11,.18),0 0 18px rgba(245,158,11,.65)}}
.bad .lamp{{background:#ef4444;box-shadow:0 0 0 6px rgba(239,68,68,.18),0 0 18px rgba(239,68,68,.65)}}
b{{display:block;color:#0f172a}}small{{display:block;color:#64748b;margin-top:3px;word-break:break-all}}
@media(max-width:640px){{.grid{{grid-template-columns:1fr}}.hero h2{{font-size:21px}}}}
</style>
<div class='wrap'><div class='hero'><h2>系統狀態亮燈</h2><div class='src'>來源：{esc(STATUS_DATASET_ID)} / {esc(label)}</div></div><div class='grid'>{''.join(cards)}</div></div>
"""


def floats(values, limit=50000):
    out = []
    for v in values:
        if len(out) >= limit:
            break
        try:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            out.append(float(v))
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
        for k, v in row.items():
            if str(k).lower() in skip:
                continue
            try:
                data.setdefault(str(k), []).append(float(v))
            except Exception:
                pass
    return [{"label": f"{prefix}.{k}", "y": v} for k, v in list(data.items())[:MAX_TRACES] if len(v) >= 8]


def find_series(obj, prefix="", found=None, depth=0):
    if found is None:
        found = []
    if len(found) >= MAX_TRACES or depth > 8:
        return found
    if isinstance(obj, list):
        rec = from_records(obj, prefix or "records")
        if rec:
            found.extend(rec[:MAX_TRACES-len(found)])
            return found
        y = floats(obj)
        if len(y) >= max(8, len(obj)//2):
            found.append({"label": prefix or f"trace_{len(found)+1}", "y": y})
        else:
            for i, item in enumerate(obj[:80]):
                find_series(item, f"{prefix}[{i}]" if prefix else f"[{i}]", found, depth+1)
                if len(found) >= MAX_TRACES:
                    break
    elif isinstance(obj, dict):
        keys = ["samples", "data", "waveform", "waveforms", "values", "amplitude", "acc", "velocity", "displacement", "z", "n", "e", "HLZ", "HLN", "HLE", "stations", "channels", "traces"]
        for k in keys + [k for k in obj.keys() if k not in keys]:
            if k in obj:
                find_series(obj[k], f"{prefix}.{k}" if prefix else str(k), found, depth+1)
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
        series = [{"label": str(c), "y": floats(numeric[c].tolist())} for c in list(numeric.columns)[:MAX_TRACES]]
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
            pulse = math.exp(-((i-260-j*10)**2)/9500) * math.sin(i/(4.5+j*.08))
            coda = .25 * math.exp(-max(0, i-360)/260) * math.sin(i/(10+j*.3))
            y.append(pulse + coda + j*.04)
        series.append({"label": f"demo_trace_{j+1}", "y": y})
    return series


def plot_waveform(source):
    try:
        series, label = load_waveform(source)
        msg = f"✅ 已載入 {label}，10 條波線分開畫，one column。"
    except Exception as e:
        series, label = demo_series(), "demo://synthetic fallback"
        msg = f"⚠️ 遠端波形讀取失敗，改顯示示範資料：{e}"

    clean = []
    for item in series[:MAX_TRACES]:
        y = downsample(floats(item.get("y", [])))
        if len(y) >= 2:
            clean.append({"label": str(item.get("label") or f"trace_{len(clean)+1}"), "y": y})

    if not clean:
        fig = go.Figure()
        fig.add_annotation(text="無可繪製的波形資料", x=.5, y=.5, showarrow=False)
        fig.update_layout(height=420, template="plotly_white")
        return msg, fig

    rows = min(len(clean), MAX_TRACES)
    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.018,
        subplot_titles=[item["label"] for item in clean[:rows]],
    )
    for idx, item in enumerate(clean[:rows], start=1):
        y = item["y"]
        fig.add_trace(
            go.Scatter(
                x=list(range(len(y))),
                y=y,
                mode="lines",
                name=item["label"],
                line={"width": 1.15},
                showlegend=False,
            ),
            row=idx,
            col=1,
        )
        fig.update_yaxes(title_text=f"T{idx}", showgrid=True, zeroline=False, row=idx, col=1)

    fig.update_xaxes(title_text="Samples / time", showgrid=True, zeroline=False, row=rows, col=1)
    fig.update_layout(
        title=f"靜態波形：{label}",
        height=max(760, rows * 170),
        template="plotly_white",
        margin={"l":70,"r":24,"t":72,"b":55},
        hovermode="x unified",
    )
    return msg, fig


status_opts = status_choices()
wave_opts = waveform_choices()

with gr.Blocks(title="EEW Dashboard") as demo:
    gr.Markdown("# EEW Dashboard\n系統狀態以亮燈呈現；靜態波形使用 Plotly，10 條波線分開畫、one column。")
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
    with gr.Tab("靜態波形"):
        with gr.Row():
            w = gr.Dropdown(choices=wave_opts, value=wave_opts[0], label="Waveform file")
            wr = gr.Button("重新讀取波形清單")
            wp = gr.Button("顯示波形")
        wm = gr.Markdown()
        plot = gr.Plot(label="Plotly waveform")
        wr.click(refresh_waveforms, outputs=[w, wm])
        wp.click(plot_waveform, inputs=w, outputs=[wm, plot])
        w.change(plot_waveform, inputs=w, outputs=[wm, plot])
        demo.load(plot_waveform, inputs=w, outputs=[wm, plot])

if __name__ == "__main__":
    demo.launch()
