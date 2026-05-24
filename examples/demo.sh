#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MEDIA_DIR="$ROOT_DIR/docs/media"
WORK_DIR="${TMPDIR:-/tmp}/multi-tenant-file-delivery-demo"
CAPTURE_DIR="${WORK_DIR}/frames"
OUT_GIF="${MEDIA_DIR}/demo.gif"

mkdir -p "$CAPTURE_DIR"
rm -f "$CAPTURE_DIR"/*.html "$CAPTURE_DIR"/*.png "$OUT_GIF"

CHROME_BIN="${CHROME_BIN:-}"
if [[ -z "$CHROME_BIN" ]]; then
  if command -v google-chrome >/dev/null 2>&1; then
    CHROME_BIN="$(command -v google-chrome)"
  elif command -v chromium >/dev/null 2>&1; then
    CHROME_BIN="$(command -v chromium)"
  else
    echo "google-chrome or chromium is required to capture demo frames" >&2
    exit 1
  fi
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required to assemble docs/media/demo.gif" >&2
  exit 1
fi

python3 - "$ROOT_DIR" "$WORK_DIR" <<'PY'
from __future__ import annotations

import pathlib
import sys

root = pathlib.Path(sys.argv[1])
work_dir = pathlib.Path(sys.argv[2])
frame_dir = work_dir / "frames"
sample_root = work_dir / "sample-folder"
sample_root.mkdir(parents=True, exist_ok=True)
(sample_root / "订单").mkdir(exist_ok=True)
(sample_root / "考核").mkdir(exist_ok=True)
(sample_root / "订单" / "蜂鸟配送费明细-爱施德.csv").write_text(
    "date,amount\n2026-05-24,1280.50\n",
    encoding="utf-8",
)
(sample_root / "考核" / "超时拣货考核-新燕海佳.zip").write_text(
    "demo payload\n",
    encoding="utf-8",
)

css = """
body{margin:0;font-family:Inter,Arial,sans-serif;background:#f7f4ed;color:#181713}
.frame{width:1280px;height:720px;box-sizing:border-box;padding:38px 50px;background:linear-gradient(180deg,#fffcf5,#f4efe4)}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:30px}
.brand{font-size:13px;font-weight:800;letter-spacing:.18em;text-transform:uppercase;color:#6f675b}
.pill{border:1px solid #ded6c8;border-radius:999px;padding:9px 14px;font-size:13px;background:#fffaf0}
h1{font-size:46px;line-height:.98;margin:0 0 12px;font-weight:850;letter-spacing:-.03em}
p{font-size:18px;line-height:1.55;margin:0;color:#514a42}
.grid{display:grid;grid-template-columns:1.05fr .95fr;gap:22px}
.panel{border:1px solid #ded6c8;background:rgba(255,252,245,.86);border-radius:18px;padding:22px;box-shadow:0 16px 40px rgba(45,36,22,.08)}
.panel h2{font-size:24px;margin:0 0 14px}
.drop{border:2px dashed #b8ad9b;border-radius:18px;padding:44px;text-align:center;background:#fffaf0}
.button{display:inline-block;margin-top:20px;padding:12px 20px;border-radius:999px;background:#b4ea58;color:#16130f;font-weight:800}
.steps{display:grid;gap:10px}
.step{display:flex;align-items:center;gap:12px;padding:13px 14px;border-radius:12px;background:#fffaf0;border:1px solid #eee5d8;font-size:15px}
.dot{width:12px;height:12px;border-radius:99px;background:#b4ea58}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:#6f675b;font-size:12px;text-transform:uppercase;letter-spacing:.08em;padding:9px;border-bottom:1px solid #ded6c8}
td{padding:11px 9px;border-bottom:1px solid #eee5d8}
.ok{color:#1f7a45;font-weight:800}.warn{color:#9a6700;font-weight:800}
.sidebar{display:flex;flex-direction:column;gap:10px}
.workspace{border:1px solid #ded6c8;border-radius:14px;padding:15px;background:#fffaf0}
.workspace.active{border-color:#b4ea58;background:#eef9d5}
.muted{font-size:13px;color:#6f675b}.hash{font-family:monospace;font-size:12px;color:#6f675b}
.footer{position:absolute;left:50px;bottom:30px;font-size:13px;color:#6f675b}
"""

frames = [
    (
        "01-upload.html",
        """
        <div class=top><div class=brand>Multi-Tenant File Delivery</div><div class=pill>HQ uploader</div></div>
        <div class=grid>
          <div>
            <h1>Upload a folder, not a zip.</h1>
            <p>The browser keeps relative paths. The control plane turns the selected folder into an internal source archive only after receiving individual files.</p>
          </div>
          <div class=panel>
            <h2>Upload desk</h2>
            <div class=drop>
              <div style="font-size:54px;font-weight:900">2 files</div>
              <p>demo-sample-folder</p>
              <span class=button>Select folder</span>
            </div>
          </div>
        </div>
        <div class=footer>Frame 1 / 4</div>
        """,
    ),
    (
        "02-preview.html",
        """
        <div class=top><div class=brand>Classification Preview</div><div class=pill>blocking errors: 0</div></div>
        <div class=panel>
          <h1>Review before delivery.</h1>
          <table>
            <thead><tr><th>File</th><th>Target</th><th>Document</th><th>Destination</th><th>Status</th></tr></thead>
            <tbody>
              <tr><td>蜂鸟配送费明细-爱施德.csv</td><td>爱施德</td><td>feniao_bill</td><td>订单/feniao_bill/...</td><td class=ok>ok</td></tr>
              <tr><td>超时拣货考核-新燕海佳.zip</td><td>新燕海佳</td><td>timeout_picking_assessment</td><td>考核/timeout_picking_assessment/...</td><td class=warn>warning</td></tr>
            </tbody>
          </table>
        </div>
        <div class=footer>Frame 2 / 4</div>
        """,
    ),
    (
        "03-delivery.html",
        """
        <div class=top><div class=brand>Control Plane -> Data Plane</div><div class=pill>file-spool or Kafka</div></div>
        <div class=grid>
          <div class=panel>
            <h1>Go worker moves bytes.</h1>
            <p>FastAPI owns state and tenant rules. The worker consumes delivery tasks, uploads to the sink, and emits result receipts.</p>
          </div>
          <div class=panel>
            <div class=steps>
              <div class=step><span class=dot></span>delivery.tasks.v1 published</div>
              <div class=step><span class=dot></span>data-plane source resolved</div>
              <div class=step><span class=dot></span>mock / S3 / MinIO sink uploaded</div>
              <div class=step><span class=dot></span>delivery.results.v1 applied</div>
            </div>
          </div>
        </div>
        <div class=footer>Frame 3 / 4</div>
        """,
    ),
    (
        "04-workspace.html",
        """
        <div class=top><div class=brand>Workspace Read View</div><div class=pill>subsidiary_viewer</div></div>
        <div class=grid>
          <div class=sidebar>
            <div class="workspace active"><strong>爱施德 workspace</strong><div class=muted>target tenant: subsidiary-a</div></div>
            <div class=workspace><strong>新燕海佳 workspace</strong><div class=muted>target tenant: subsidiary-b</div></div>
          </div>
          <div class=panel>
            <h1>Subsidiaries only see their own files.</h1>
            <table>
              <thead><tr><th>Object</th><th>Path</th><th>SHA-256</th><th></th></tr></thead>
              <tbody>
                <tr><td>蜂鸟配送费明细-爱施德.csv</td><td>订单/feniao_bill</td><td class=hash>cafe...1024</td><td class=ok>download URL</td></tr>
              </tbody>
            </table>
          </div>
        </div>
        <div class=footer>Frame 4 / 4</div>
        """,
    ),
]

for index, (name, body) in enumerate(frames, start=1):
    html = f"<!doctype html><meta charset='utf-8'><style>{css}</style><div class=frame>{body}</div>"
    (frame_dir / name).write_text(html, encoding="utf-8")
PY

for html in "$CAPTURE_DIR"/*.html; do
  png="$CAPTURE_DIR/$(basename "${html%.html}.png")"
  "$CHROME_BIN" \
    --headless \
    --disable-gpu \
    --no-sandbox \
    --window-size=1280,720 \
    --screenshot="$png" \
    "file://$html" >/dev/null 2>&1
done

ffmpeg -y -framerate 0.7 -pattern_type glob -i "$CAPTURE_DIR/*.png" \
  -vf "fps=7,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
  -loop 0 "$OUT_GIF" >/dev/null 2>&1

echo "Wrote $OUT_GIF"
