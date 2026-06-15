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
    if any(k in t for k in ["alive", "up", "ok", "running", "healthy", "success", "available", "exists", "運行", "正常"]):
        return "ok"
    if any(k in t for k in ["warn", "warning", "partial", "degraded", "警告"]):
        return "warn"
    if any(k in t for k in ["down", "fail", "error", "exited", "stopped", "missing", "停止"]):
        return "bad"
    return "neutral"


def is_alive_status(text):
    return str(text).strip().lower() == "alive"


def normalized_status_from_info(info):
    if isinstance(info, dict):
        if "alive" in info or "is_alive" in info:
            value = info.get("alive", info.get("is_alive"))
            return "alive" if str(value).strip().lower() in {"true", "1", "yes", "alive"} else "not alive"
        return info.get("status") or info.get("state") or info.get("health") or info.get("ok") or "unknown"
    return info


def module_items(payload):
    rows = []
    sources = ["containers", "container_status", "container", "docker", "modules", "module_status", "services", "checks"]
    for key in sources:
        value = payload.get(key)
        if isinstance(value, dict):
            for name, info in value.items():
                status = normalized_status_from_info(info)
                rows.append((str(name), str(status)))
        elif isinstance(value, list):
            for i, info in enumerate(value):
                if isinstance(info, dict):
                    name = info.get("name") or info.get("module") or info.get("container") or f"module_{i+1}"
                    status = normalized_status_from_info(info)
                    rows.append((str(name), str(status)))
                else:
                    rows.append((f"module_{i+1}", str(info)))
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

    alive_cards = []
    info_cards = []
    for name, status in module_items(payload):
        if is_alive_status(status):
            alive_cards.append(
                f"<div class='alive-card'><span class='lamp'></span><div><b>{esc(name)}</b><small>{esc(status)}</small></div></div>"
            )
        else:
            cls = level(status)
            info_cards.append(
                f"<div class='info-card {cls}'><div><b>{esc(name)}</b><small>{esc(status)}</small></div><span class='tag'>{esc(status)}</span></div>"
            )

    alive_html = "".join(alive_cards) if alive_cards else "<div class='empty'>目前沒有 alive 型式狀態。</div>"
    info_html = "".join(info_cards) if info_cards else "<div class='empty'>沒有其他狀態資訊。</div>"
    source_text = f"{STATUS_DATASET_ID} / {label}"

    return f"""
<style>
.status-wrap{{display:grid;gap:16px}}
.status-hero{{
  position:relative;overflow:hidden;display:flex;justify-content:space-between;align-items:center;gap:18px;
  border-radius:24px;padding:24px 22px;color:#fff!important;
  background:radial-gradient(circle at 86% 24%,rgba(125,211,252,.45),transparent 28%),linear-gradient(135deg,#1d4ed8 0%,#2563eb 45%,#0284c7 100%);
  box-shadow:0 18px 38px rgba(37,99,235,.28),0 6px 18px rgba(15,23,42,.14);
}}
.status-hero h2{{margin:4px 0 12px!important;font-size:32px!important;line-height:1.08!important;color:#fff!important;font-weight:950!important;letter-spacing:.02em;text-shadow:0 2px 10px rgba(15,23,42,.22)}}
.hero-kicker{{font-size:12px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:rgba(255,255,255,.76)!important}}
.src{{display:inline-block;max-width:100%;padding:9px 12px;border-radius:14px;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.26);color:#fff!important;font-size:14px!important;line-height:1.55!important;font-weight:700!important;word-break:break-word;box-shadow:inset 0 1px 0 rgba(255,255,255,.18)}}
.src span{{color:rgba(255,255,255,.82)!important;margin-right:6px}}
.hero-icon{{flex:0 0 auto;width:76px;height:76px;border-radius:999px;border:8px solid rgba(255,255,255,.22);display:flex;align-items:center;justify-content:center;color:rgba(255,255,255,.60)!important;font-size:38px;font-weight:900}}
.section{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:14px;box-shadow:0 8px 22px rgba(15,23,42,.06)}}
.section-title{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;color:#0f172a;font-weight:900;font-size:17px}}
.section-note{{font-size:12px;color:#64748b;font-weight:600}}
.alive-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}}
.info-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}
.alive-card{{display:flex;gap:12px;align-items:center;padding:15px;border-radius:16px;background:linear-gradient(180deg,#f0fdf4,#ffffff);border:1px solid rgba(34,197,94,.28)}}
.lamp{{width:24px;height:24px;border-radius:99px;background:#22c55e;box-shadow:0 0 0 7px rgba(34,197,94,.16),0 0 20px rgba(34,197,94,.70);flex:0 0 auto}}
.info-card{{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:14px;border-radius:16px;background:#f8fafc;border:1px solid #e2e8f0}}
.info-card.ok{{background:#f8fafc;border-color:#dbeafe}}.info-card.warn{{background:#fffbeb;border-color:#fde68a}}.info-card.bad{{background:#fef2f2;border-color:#fecaca}}
b{{display:block;color:#0f172a}}small{{display:block;color:#64748b;margin-top:4px;word-break:break-all}}
.alive-card b{{font-size:18px!important}}
.alive-card small{{font-size:14px!important;color:#14532d!important}}
.tag{{font-size:12px;font-weight:800;border-radius:999px;padding:6px 10px;background:#e2e8f0;color:#334155;white-space:nowrap;max-width:140px;overflow:hidden;text-overflow:ellipsis}}
.info-card.ok .tag{{background:#dbeafe;color:#1d4ed8}}.info-card.warn .tag{{background:#fef3c7;color:#b45309}}.info-card.bad .tag{{background:#fee2e2;color:#b91c1c}}
.empty{{padding:14px;border-radius:14px;background:#f8fafc;color:#64748b;border:1px dashed #cbd5e1}}
@media(max-width:640px){{.alive-grid,.info-grid{{grid-template-columns:1fr}}.status-hero{{padding:22px 18px;align-items:flex-start}}.status-hero h2{{font-size:28px!important}}.hero-icon{{width:58px;height:58px;border-width:6px;font-size:30px}}.src{{font-size:13px!important}}}}
</style>
<div class='status-wrap'>
  <div class='status-hero'>
    <div><div class='hero-kicker'>EEW STATUS</div><h2>系統狀態</h2><div class='src'><span>來源</span>{esc(source_text)}</div></div>
    <div class='hero-icon'>⌁</div>
  </div>
  <div class='section'><div class='section-title'>Alive 模組綠燈 <span class='section-note'>僅 alive 使用燈號</span></div><div class='alive-grid'>{alive_html}</div></div>
  <div class='section'><div class='section-title'>其他狀態資訊 <span class='section-note'>非 alive 不使用燈號</span></div><div class='info-grid'>{info_html}</div></div>
</div>
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
        msg = f"✅ 已載入 {label}，波形分開繪製並附統計資訊。"
    except Exception as e:
        series, label = demo_series(), "demo://synthetic fallback"
        msg = f"⚠️ 遠端波形讀取失敗，改顯示示範資料：{e}"

    clean = []
    stats_rows = []
    for item in series[:MAX_TRACES]:
        raw_y = floats(item.get("y", []))
        y = downsample(raw_y)
        if len(y) >= 2:
            trace_label = str(item.get("label") or f"trace_{len(clean)+1}")
            clean.append({"label": trace_label, "y": y})
            stats_rows.append(trace_stats(trace_label, raw_y))

    if not clean:
        fig = go.Figure()
        fig.add_annotation(text="無可繪製的波形資料", x=.5, y=.5, showarrow=False)
        fig.update_layout(height=420, template="plotly_white")
        return msg, fig, pd.DataFrame([{"status": "no numeric waveform data"}])

    rows = min(len(clean), MAX_TRACES)
    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.014,
        subplot_titles=[item["label"] for item in clean[:rows]],
    )
    for idx, item in enumerate(clean[:rows], start=1):
        y = item["y"]
        ymin, ymax = min(y), max(y)
        pad = max((ymax - ymin) * 0.12, 1e-9)
        fig.add_trace(
            go.Scatter(
                x=list(range(len(y))),
                y=y,
                mode="lines",
                name=item["label"],
                line={"width": 1.6},
                hovertemplate="sample=%{x}<br>amp=%{y:.6f}<extra></extra>",
                showlegend=False,
            ),
            row=idx,
            col=1,
        )
        fig.update_yaxes(
            title_text=f"T{idx}",
            showgrid=True,
            zeroline=True,
            zerolinewidth=1,
            zerolinecolor="rgba(30,41,59,0.45)",
            range=[ymin - pad, ymax + pad],
            row=idx,
            col=1,
        )

    fig.update_xaxes(title_text="Samples / time", showgrid=True, zeroline=False, row=rows, col=1)
    fig.update_layout(
        title=f"靜態波形：{label}",
        height=max(1050, rows * 220),
        template="plotly_white",
        margin={"l":80,"r":28,"t":80,"b":60},
        hovermode="x unified",
    )
    return msg, fig, pd.DataFrame(stats_rows)


status_opts = status_choices()
wave_opts = waveform_choices()

with gr.Blocks(title="EEW Dashboard") as demo:
    gr.Markdown("# EEW Dashboard\n系統狀態分為 Alive 綠燈與其他狀態資訊；非 alive 不使用燈號。靜態波形使用 Plotly，10 條波線分開畫、one column，並附統計資訊。")
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
        stats = gr.Dataframe(label="波形統計資訊", interactive=False)
        wr.click(refresh_waveforms, outputs=[w, wm])
        wp.click(plot_waveform, inputs=w, outputs=[wm, plot, stats])
        w.change(plot_waveform, inputs=w, outputs=[wm, plot, stats])
        demo.load(plot_waveform, inputs=w, outputs=[wm, plot, stats])

if __name__ == "__main__":
    demo.launch()
