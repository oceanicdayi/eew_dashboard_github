import json
import os
import re
from datetime import datetime
from pathlib import Path

import folium
import gradio as gr
import pandas as pd

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"


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


choices = _fixture_choices()

with gr.Blocks(title="EEW Dashboard") as demo:
    gr.Markdown("# EEW Dashboard\n臺灣地震預警資料展示與部署測試介面")
    with gr.Row():
        source = gr.Dropdown(choices=choices, value=choices[0], label="Replay fixture")
        refresh = gr.Button("載入資料")
    status = gr.Markdown()
    summary = gr.Dataframe(label="事件摘要", interactive=False)
    map_html = gr.HTML(label="Map")
    raw = gr.Code(label="Raw JSON", language="json")

    refresh.click(render_dashboard, inputs=source, outputs=[status, summary, map_html, raw])
    source.change(render_dashboard, inputs=source, outputs=[status, summary, map_html, raw])
    demo.load(render_dashboard, inputs=source, outputs=[status, summary, map_html, raw])

if __name__ == "__main__":
    demo.launch()
