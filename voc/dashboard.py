"""Minimal browser consumer of VOC dataset queries."""

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .datasets import get_dataset, summary

PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>VOC dataset</title>
<style>body{font:16px system-ui;max-width:960px;margin:3rem auto;padding:0 1rem;color:#182632}
table{border-collapse:collapse;min-width:300px;margin:1rem 0}td,th{padding:.5rem 1rem;text-align:left;border-bottom:1px solid #ddd}
code{overflow-wrap:anywhere}button{padding:.6rem 1rem}section{display:inline-block;vertical-align:top;margin-right:2rem}</style>
<h1>Published workable dataset</h1><p id="status">Loading…</p>
<p>Snapshot: <code id="version"></code></p><button onclick="load()">Refresh</button>
<section><h2>Sources</h2><table id="sources"></table></section>
<section><h2>Reviews by month</h2><table id="months"></table></section>
<script>
function table(id,values){const t=document.getElementById(id);t.replaceChildren();
for(const [key,count] of Object.entries(values)){const row=t.insertRow();row.insertCell().textContent=key;row.insertCell().textContent=count;}}
async function load(){try{let r=await fetch('/summary');let d=await r.json();
if(!d.info){document.getElementById('status').textContent=d.status;return;}
document.getElementById('status').textContent=d.info.row_count.toLocaleString()+' reviews · '+d.unknown_dates+' unknown dates';
document.getElementById('version').textContent=d.info.release_id || d.info.artifact_id;
table('sources',d.platform_counts);table('months',d.month_counts);
}catch(e){document.getElementById('status').textContent='Could not load the dataset overview.';}}load();
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/summary"):
            self.send_error(404)
            return
        status = 200
        if self.path == "/":
            body = PAGE.encode()
            content = "text/html; charset=utf-8"
        else:
            content = "application/json"
            try:
                result = summary(get_dataset())
            except LookupError:
                result = {"status": "No dataset published in the selected source yet"}
            except Exception:
                logging.getLogger(__name__).exception("Dataset summary failed")
                status = 503
                result = {"status": "Dataset service unavailable; check service logs"}
            body = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", content)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
