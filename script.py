import os
import bz2
import csv
import json
import math
import requests
import networkx as nx
from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta
import threading
import time
from heapq import heappush, heappop

SYSTEMS_URL = "https://www.fuzzwork.co.uk/dump/latest/mapSolarSystems.csv.bz2"
JUMPS_URL = "https://www.fuzzwork.co.uk/dump/latest/mapSolarSystemJumps.csv.bz2"
SYSTEMS_FILE = "mapSolarSystems.csv.bz2"
JUMPS_FILE = "mapSolarSystemJumps.csv.bz2"
WORMHOLE_FILE = "wormhole.json"

LY_CONVERSION = 9.4607e15
DEFAULT_CYNO_RANGE = 6.0
DEFAULT_MAX_CYNOS = 6.0

app = Flask(__name__)
gate_graph = nx.Graph()
name_to_id = {}
id_to_name = {}
system_meta = {}
wormhole_links = {}

def download_sde():
    for url, filename in [(SYSTEMS_URL, SYSTEMS_FILE), (JUMPS_URL, JUMPS_FILE)]:
        if not os.path.exists(filename):
            with open(filename, "wb") as f:
                f.write(requests.get(url).content)

def load_sde():
    with bz2.open(SYSTEMS_FILE, "rt") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = int(row["solarSystemID"])
            name = row["solarSystemName"]
            name_to_id[name] = sid
            id_to_name[sid] = name
            system_meta[sid] = {
                "name": name,
                "x": float(row["x"]),
                "y": float(row["y"]),
                "z": float(row["z"]),
                "security": float(row["security"]),
                "regionID": int(row["regionID"]),
            }

    with bz2.open(JUMPS_FILE, "rt") as f:
        reader = csv.DictReader(f)
        for row in reader:
            a = id_to_name.get(int(row["fromSolarSystemID"]))
            b = id_to_name.get(int(row["toSolarSystemID"]))
            if a and b and a != "Zarzakh" and b != "Zarzakh":
                gate_graph.add_edge(a, b)

def load_custom_wormholes():
    """
    Loads custom wormholes from wormhole.json if present and not expired (48h UTC).
    """
    global wormhole_links

    now = datetime.now(timezone.utc)
    valid_custom_links = []
    seen_edges = set()

    if os.path.exists(WORMHOLE_FILE):
        with open(WORMHOLE_FILE, "r") as f:
            try:
                data = json.load(f)
                for link in data.get("links", []):
                    # Preserve only if custom and still within 48h
                    is_custom = link.get("source") == "custom"
                    added_at_str = link.get("added_at")
                    if is_custom and added_at_str:
                        added_at = datetime.fromisoformat(added_at_str)
                        if (now - added_at) > timedelta(hours=48):
                            continue  # Expired custom wormhole

                    a, b = link["a"], link["b"]
                    edge = frozenset([a, b])
                    valid_custom_links.append(link)
                    seen_edges.add(edge)
                    gate_graph.add_edge(a, b)
                    wormhole_links[edge] = link
            except Exception as e:
                print(f"Failed to load wormhole.json: {e}")

    return valid_custom_links


def fetch_evescout_wormholes():
    global wormhole_links
    print("Fetching wormholes from Eve-Scout public API...")

    try:
        response = requests.get("https://api.eve-scout.com/v2/public/signatures", timeout=10)
        if response.status_code != 200:
            print(f"Failed to fetch signatures: {response.status_code}")
            return

        data = response.json()
        new_links = []
        new_edges = set()
        now = datetime.now(timezone.utc)

        # Load and preserve custom wormholes first
        valid_custom_links = load_custom_wormholes()

        for sig in data:
            a = sig.get("in_system_name")
            b = sig.get("out_system_name")
            if not a or not b:
                continue

            exp_time = datetime.fromisoformat(sig.get("expires_at").replace("Z", "+00:00"))
            if (exp_time - now).total_seconds() < 0:
                continue  # Already expired

            hours_remaining = max(0, round((exp_time - now).total_seconds() / 3600))
            hours_ago = max(0, round((now - datetime.fromisoformat(sig.get("created_at").replace("Z", "+00:00"))).total_seconds() / 3600))

            link = {
                "a": a,
                "b": b,
                "sig_a": sig.get("in_signature"),
                "sig_b": sig.get("out_signature"),
                "age": sig.get("remaining_hours"),
                "wh_type": sig.get("wh_type"),
                "max_ship_size": sig.get("max_ship_size"),
                "expires_at": sig.get("expires_at"),
                "in_system_class": sig.get("in_system_class"),
                "out_system_id": sig.get("out_system_id"),
                "in_system_id": sig.get("in_system_id"),
                "completed": sig.get("completed"),
                "updated_at": sig.get("updated_at"),
                "created_by_name": sig.get("created_by_name"),
                "max_remaining": hours_remaining,
                "time_found": hours_ago,
                "type": "wormhole",
                "wh_mass": sig.get("wh_type"),
                "private": False,
                "source": "evescout"
            }

            edge = frozenset([a, b])
            new_links.append(link)
            new_edges.add(edge)

        # Remove any Eve-Scout edges that are no longer present
        stale_edges = [edge for edge, link in wormhole_links.items() if link.get("source") == "evescout" and edge not in new_edges]
        for edge in stale_edges:
            a, b = tuple(edge)
            if gate_graph.has_edge(a, b):
                gate_graph.remove_edge(a, b)
            del wormhole_links[edge]

        # Add new Eve-Scout links
        for link in new_links:
            a, b = link["a"], link["b"]
            edge = frozenset([a, b])
            gate_graph.add_edge(a, b)
            wormhole_links[edge] = link

        # Merge for saving (customs already in wormhole_links)
        all_links = list(wormhole_links.values())
        with open(WORMHOLE_FILE, "w") as f:
            json.dump({"links": all_links}, f, indent=2)

        print(f"Saved {len(all_links)} total wormhole links (Eve-Scout + custom).")
    except Exception as e:
        print(f"Failed to fetch wormholes from Eve-Scout: {e}")

def wormhole_updater():
    while True:
        fetch_evescout_wormholes()
        time.sleep(60)

if os.path.exists(WORMHOLE_FILE):
    with open(WORMHOLE_FILE, "r") as f:
        data = json.load(f)
        for link in data.get("links", []):
            a, b = link["a"], link["b"]
            gate_graph.add_edge(a, b)
            wormhole_links[frozenset([a, b])] = link

def ly_dist(a, b):
    dx = (a["x"] - b["x"]) / LY_CONVERSION
    dy = (a["y"] - b["y"]) / LY_CONVERSION
    dz = (a["z"] - b["z"]) / LY_CONVERSION
    return math.sqrt(dx ** 2 + dy ** 2 + dz ** 2)

def get_valid_cyno_candidates(bridge_type):
    if bridge_type == "none":
        return []
    return [
        meta["name"]
        for sid, meta in system_meta.items()
        if meta["security"] <= 0.4
    ]

def build_route(start, end, max_ly, max_cynos, bridge_type):
    if start not in name_to_id or end not in name_to_id:
        return [{"error": f"System not found in SDE: {start if start not in name_to_id else end}"}]
    if start not in gate_graph or end not in gate_graph:
        return [{"error": f"System not found in gate graph: {start if start not in gate_graph else end}"}]
        raise ValueError(f"Start or end system not in graph: {start}, {end}")
    cynos = {
        system for system in get_valid_cyno_candidates(bridge_type)
        if system in name_to_id and system in gate_graph
    }
    visited = set()
    heap = []

    # (total_cost, cynos_used, current_system, path_list)
    heappush(heap, (0, 0, start, [("start", start)]))

    while heap:
        cost, cynos_used, current, path = heappop(heap)

        if (current, cynos_used) in visited:
            continue
        visited.add((current, cynos_used))

        if current == end:
            # Convert tuples to dicts
            result = []
            for i, step in enumerate(path):
                if step[0] == "wormhole":
                    prev_system = path[i - 1][1] if i > 0 else None
                    edge = frozenset([prev_system, step[1]]) if prev_system else None
                    wh_info = wormhole_links.get(edge, {})
                    result.append({
                        "type": "wormhole",
                        "system": step[1],
                        "info": wh_info
                    })
                else:
                    result.append({"type": step[0], "system": step[1]})
            return result

        # Gate and wormhole neighbors
        for neighbor in gate_graph.neighbors(current):
            edge = frozenset([current, neighbor])
            if edge in wormhole_links:
                heappush(heap, (cost + 1, cynos_used, neighbor, path + [("wormhole", neighbor)]))
            else:
                heappush(heap, (cost + 1, cynos_used, neighbor, path + [("gate", neighbor)]))

        # Cyno jumps if allowed
        if current in cynos and cynos_used < max_cynos:
            for target in cynos:
                if target == current or (target, cynos_used + 1) in visited:
                    continue
                dist = ly_dist(system_meta[name_to_id[current]], system_meta[name_to_id[target]])
                if dist <= max_ly:
                    heappush(heap, (cost + 1, cynos_used + 1, target, path + [("cyno", target)]))

    return None

@app.route("/route", methods=["GET"])
def route():
    start = request.args.get("start")
    end = request.args.get("end")
    bridge_type = request.args.get("bridge_type", "titan")

    if bridge_type == "titan":
        max_ly = 6.0
    elif bridge_type == "blops":
        max_ly = 8.0
    else:
        max_ly = float(request.args.get("range", DEFAULT_CYNO_RANGE))

    max_cynos = int(request.args.get("max_cynos", DEFAULT_MAX_CYNOS))

    if not start or not end:
        return jsonify({"error": "Missing 'start' or 'end'"}), 400
    if start not in name_to_id or end not in name_to_id:
        return jsonify({"error": "System not found"}), 404

    steps = build_route(start, end, max_ly, max_cynos, bridge_type)
    if not steps:
        return jsonify({"error": "No path found"}), 404

    return jsonify({
        "from": start,
        "to": end,
        "steps": steps,
        "total_jumps": len(steps) - 1,
        "used_cyno": any(s["type"] == "cyno" for s in steps)
    })

@app.route("/add_wh", methods=["POST"])
def add_wh():
    global wormhole_links

    data = request.get_json()
    system_a = data.get("a")
    system_b = data.get("b")
    sig_a = data.get("sig_a")
    sig_b = data.get("sig_b")
    private = data.get("private", True)

    if not system_a or not system_b:
        return jsonify({"error": "Missing 'a' or 'b' system name"}), 400

    if system_a not in name_to_id or system_b not in name_to_id:
        return jsonify({"error": "One or both systems not recognized"}), 404

    now = datetime.now(timezone.utc).isoformat()
    link = {
        "a": system_a,
        "b": system_b,
        "sig_a": sig_a,
        "sig_b": sig_b,
        "type": "wormhole",
        "source": "custom",
        "added_at": now,
        "wh_mass": "unknown",
        "private": private
    }

    edge = frozenset([system_a, system_b])
    gate_graph.add_edge(system_a, system_b)
    wormhole_links[edge] = link

    # Save updated wormhole list
    with open(WORMHOLE_FILE, "w") as f:
        json.dump({"links": list(wormhole_links.values())}, f, indent=2)

    return jsonify({
        "message": f"Custom wormhole added between {system_a} and {system_b}.",
        "added_at": now,
        "private": private,
        "sig_a": sig_a,
        "sig_b": sig_b
    }), 200

@app.route("/del_wh", methods=["POST"])
def del_wh():
    global wormhole_links

    data = request.get_json()
    system = data.get("system_name")
    sig_id = data.get("sig_id")

    if not system or not sig_id:
        return jsonify({"error": "Both 'system_name' and 'sig_id' are required"}), 400

    to_remove = []

    for edge, link in wormhole_links.items():
        if link.get("source") != "custom":
            continue

        if (system == link.get("a") or system == link.get("b")) and \
           (sig_id == link.get("sig_a") or sig_id == link.get("sig_b")):
            to_remove.append(edge)

    if not to_remove:
        return jsonify({"message": "No matching custom wormhole found."}), 404

    for edge in to_remove:
        a, b = tuple(edge)
        if gate_graph.has_edge(a, b):
            gate_graph.remove_edge(a, b)
        del wormhole_links[edge]

    # Save updated wormhole files
    with open(WORMHOLE_FILE, "w") as f:
        json.dump({"links": list(wormhole_links.values())}, f, indent=2)

    return jsonify({
        "message": f"Removed {len(to_remove)} custom wormhole(s).",
        "criteria": {
            "system_name": system,
            "sig_id": sig_id
        }
    }), 200

if __name__ == "__main__":
    download_sde()
    fetch_evescout_wormholes()
    load_sde()
    threading.Thread(target=wormhole_updater, daemon=True).start()
    app.run(debug=True)