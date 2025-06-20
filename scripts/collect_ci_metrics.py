#!/usr/bin/env python3
import os, requests, zipfile, io, xml.etree.ElementTree as ET, json
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

# ── Config ─────────────────────────────────────────────────
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO         = os.getenv("GITHUB_REPO", "kiranaitaagi123/assistant-bot")
RUN_ID       = os.getenv("GITHUB_RUN_ID")       # from GH Action
PGW_URL      = os.getenv("PUSHGATEWAY_URL", "http://localhost:9091")
JOB_NAME     = "assistant-bot-ci"
# ───────────────────────────────────────────────────────────

headers = {"Authorization": f"token {GITHUB_TOKEN}"}
base_api = f"https://api.github.com/repos/{REPO}/actions/runs/{RUN_ID}/artifacts"

# 1) List artifacts
resp = requests.get(base_api, headers=headers)
resp.raise_for_status()
arts = resp.json()["artifacts"]

registry = CollectorRegistry()

def download_and_extract(name_prefix):
    art = next(a for a in arts if a["name"].startswith(name_prefix))
    dl = requests.get(art["archive_download_url"], headers=headers)
    with zipfile.ZipFile(io.BytesIO(dl.content)) as z:
        # extract all into memory buffer
        for fn in z.namelist():
            if fn.endswith(".xml") or fn.endswith(".json"):
                return z.read(fn), fn
    raise RuntimeError("File not found in artifact")

# ── Tests / JUnit ─────────────────────────────────────────────
data, fn = download_and_extract("pytest-results")
root = ET.fromstring(data)
tests    = int(root.attrib["tests"])
failures = int(root.attrib["failures"]) + int(root.attrib["errors"])
Gauge("ci_tests_total",  "Total tests", registry=registry).set(tests)
Gauge("ci_tests_failed", "Failed tests", registry=registry).set(failures)

# ── Coverage XML ─────────────────────────────────────────────
data, fn = download_and_extract("backend-coverage")
cov_root = ET.fromstring(data)
line_rate = float(cov_root.find("coverage").attrib["line-rate"]) * 100
Gauge("ci_coverage_percent", "Line coverage %", registry=registry).set(line_rate)

# ── Bandit JSON ──────────────────────────────────────────────
data, fn = download_and_extract("bandit-report")
issues = json.loads(data)["results"]
sev = {"LOW":0,"MEDIUM":0,"HIGH":0}
for i in issues:
    sev[i["issue_severity"].upper()] += 1
for level,count in sev.items():
    Gauge(f"ci_bandit_issues_{level.lower()}",
          f"Bandit {level} issues", registry=registry).set(count)

# ── Push to Pushgateway ──────────────────────────────────────
push_to_gateway(PGW_URL, job=JOB_NAME, registry=registry)
print("Metrics pushed to", PGW_URL)
