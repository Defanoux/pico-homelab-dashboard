#!/usr/bin/env python3
"""TrueNAS stats bridge for the Pico 2 W dashboard.

Serves GET /stats on port 9191 as JSON. Standard library only, so it runs
directly on the TrueNAS host with no pip installs. Keep this file in a
dataset (e.g. your home dir) so app deletions and OS updates can't wipe it.
"""

import json
import os
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9191
SAMPLE_SECONDS = 2   # matches the Pico's GRAPH_REFRESH_SECONDS
HISTORY_LEN = 60     # 60 samples x 2 s = last 2 minutes on the graph
ZPOOL = "/usr/sbin/zpool"  # absolute path: non-root PATH often lacks /usr/sbin

cpu_history = deque(maxlen=HISTORY_LEN)
temp_history = deque(maxlen=HISTORY_LEN)
latest = {"cpu_percent": None, "cpu_temp_c": None}
lock = threading.Lock()


# ---------------- CPU ----------------
def read_cpu_times():
    """First line of /proc/stat = cumulative CPU ticks across all cores.
    CPU % only means something as a difference between two samples."""
    with open("/proc/stat") as f:
        fields = [int(x) for x in f.readline().split()[1:]]
    idle = fields[3] + fields[4]  # idle + iowait
    return idle, sum(fields)


# ---------------- TEMPERATURE ----------------
def find_temp_sensor():
    """hwmon numbering can change between boots, so search by driver name
    instead of hardcoding hwmonN. temp1 = Package (Intel) / Tctl (AMD)."""
    base = "/sys/class/hwmon"
    for entry in os.listdir(base):
        try:
            with open(os.path.join(base, entry, "name")) as f:
                name = f.read().strip()
        except OSError:
            continue
        if name in ("coretemp", "k10temp", "zenpower"):
            path = os.path.join(base, entry, "temp1_input")
            if os.path.exists(path):
                return path
    return None


def read_temp(path):
    if path is None:
        return None
    try:
        with open(path) as f:
            return round(int(f.read()) / 1000, 1)  # millidegrees -> C
    except (OSError, ValueError):
        return None


# ---------------- BACKGROUND SAMPLER ----------------
def sampler():
    """Samples CPU and temp on a fixed interval so history keeps building
    even when nobody is polling."""
    temp_path = find_temp_sensor()
    print("Temp sensor:", temp_path or "none found")
    prev_idle, prev_total = read_cpu_times()
    while True:
        time.sleep(SAMPLE_SECONDS)
        idle, total = read_cpu_times()
        d_total = total - prev_total
        cpu = 100 * (1 - (idle - prev_idle) / d_total) if d_total else 0.0
        prev_idle, prev_total = idle, total
        temp = read_temp(temp_path)
        with lock:
            latest["cpu_percent"] = round(cpu, 1)
            latest["cpu_temp_c"] = temp
            cpu_history.append(round(cpu, 1))
            temp_history.append(temp)


# ---------------- MEMORY / POOLS (read on request) ----------------
def read_memory():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0])
    total, avail = info["MemTotal"], info["MemAvailable"]
    return {"percent_used": round(100 * (total - avail) / total, 1)}


def read_pools():
    """Errors are returned as data, not raised - the Pico checks for an
    'error' key in pools[0] and skips the pool stat instead of crashing."""
    try:
        out = subprocess.run(
            [ZPOOL, "list", "-Hp", "-o", "name,size,alloc"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return [{"error": str(e)}]
    pools = []
    for line in out.splitlines():
        name, size, alloc = line.split("\t")
        if name == "boot-pool":
            continue  # Pico shows pools[0], so keep the data pool first
        pools.append({
            "name": name,
            "percent_used": round(100 * int(alloc) / int(size), 1),
        })
    return pools


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/stats":
            self.send_error(404)
            return
        with lock:
            body = dict(latest)
            body["cpu_history"] = list(cpu_history)
            body["temp_history"] = list(temp_history)
        body["memory"] = read_memory()
        body["pools"] = read_pools()
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # Pico polls every 2 s; don't flood the journal


if __name__ == "__main__":
    threading.Thread(target=sampler, daemon=True).start()
    print("Serving on :{}/stats".format(PORT))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
