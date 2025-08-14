from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import json, os, threading
from pathlib import Path

from copy import deepcopy

def _flow_root_title(flow):
    by_title = {n["title"]: n for n in flow}
    referenced = set()
    for n in flow:
        for e in n.get("next", []):
            tgt = e.get("next") if isinstance(e, dict) else e
            if tgt: referenced.add(tgt)
    if "START" in by_title:
        return "START"
    for t in by_title:
        if t not in referenced:
            return t
    return flow[0]["title"]  # fallback

def _flow_to_children(flow):
    """Convert array-of-nodes with .next[label,next:title] into nested children tree."""
    by_title = {n["title"]: n for n in flow}
    root_title = _flow_root_title(flow)

    def build(title, seen):
        src = by_title.get(title)
        if not src:
            # missing reference; make a stub
            return {"id": f"missing:{title}", "title": title, "description": "", "children": []}

        node = {
            "id": src.get("id", title),
            "title": src.get("title", title),
            "description": src.get("description", ""),
            "children": []
        }
        if title in seen:
            # cycle guard: return node without expanding children
            return node

        seen = seen | {title}
        for edge in src.get("next", []):
            tgt_title = edge["next"] if isinstance(edge, dict) else edge
            child = build(tgt_title, seen)
            if isinstance(edge, dict) and "label" in edge:
                # keep edge label on the child so we can round-trip it later
                child["edgeLabel"] = edge["label"]
            node["children"].append(child)
        return node

    return build(root_title, set())

def _children_to_flow(tree):
    """Convert nested children tree back to array-of-nodes with .next[label,next:title]."""
    nodes = {}

    def ensure_node(n):
        t = n.get("title", "")
        nid = n.get("id") or t
        if t not in nodes:
            nodes[t] = {"id": nid, "title": t, "description": n.get("description", ""), "next": []}

    def walk(n):
        ensure_node(n)
        for c in n.get("children", []):
            ensure_node(c)
            # push an edge from n to c by *title*, with the label we carried
            nodes[n["title"]]["next"].append({
                "label": c.get("edgeLabel", ""),
                "next": c.get("title", "")
            })
            walk(c)

    walk(tree)
    # return as a list, preserving at least root first
    root_first = [nodes[tree["title"]]] + [v for k, v in nodes.items() if k != tree["title"]]
    return root_first


APP_DIR = Path(__file__).parent
DATA_FILE = APP_DIR / "tree.json"

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev"
socketio = SocketIO(app, cors_allowed_origins="*")

_file_lock = threading.Lock()

def load_tree():
    with _file_lock:
        if not DATA_FILE.exists():
            # If missing, seed with a minimal flow that matches your file format
            seed = [{
                "id": "root-seed",
                "title": "START",
                "description": "Start",
                "next": []
            }]
            DATA_FILE.write_text(json.dumps(seed, indent=2))

        raw = json.loads(DATA_FILE.read_text())
        # If file is already a nested dict, just return it; otherwise convert flow → children
        if isinstance(raw, list):
            return _flow_to_children(raw)
        elif isinstance(raw, dict):
            return raw
        else:
            raise ValueError("Unsupported tree.json format")

def save_tree(tree):
    with _file_lock:
        # Always store as the original flow (array) so your file shape is preserved
        flow = _children_to_flow(tree)
        DATA_FILE.write_text(json.dumps(flow, indent=2))


def find_node(node, node_id, parent=None):
    if node["id"] == node_id:
        return node, parent
    for child in node.get("children", []):
        found, p = find_node(child, node_id, node)
        if found:
            return found, p
    return None, None

@app.route("/")
def index():
    return render_template("index.html")

@app.get("/api/tree")
def api_get_tree():
    return jsonify(load_tree())

@app.post("/api/node")
def api_add_node():
    data = request.get_json(force=True) or {}
    parent_id = data.get("parentId")
    title = data.get("title", "Untitled")
    desc = data.get("description", "")
    edge_label = data.get("edgeLabel", "")   # <-- NEW
    new_id = data.get("id") or f"n{os.urandom(4).hex()}"

    tree = load_tree()
    parent, _ = find_node(tree, parent_id)
    if not parent:
        return jsonify({"error":"parent not found"}), 404

    parent.setdefault("children", []).append({
        "id": new_id,
        "title": title,
        "description": desc,
        "edgeLabel": edge_label,            # <-- keep it here
        "children": []
    })
    save_tree(tree)
    socketio.emit("tree_updated", tree)
    return jsonify({"ok": True, "tree": tree, "newId": new_id})

@app.put("/api/node/<node_id>")
def api_edit_node(node_id):
    """
    body: { "title": "...", "description": "..." }
    """
    data = request.get_json(force=True) or {}
    tree = load_tree()
    node, _ = find_node(tree, node_id)
    if not node:
        return jsonify({"error":"node not found"}), 404

    if "title" in data: node["title"] = data["title"]
    if "description" in data: node["description"] = data["description"]

    save_tree(tree)
    socketio.emit("tree_updated", tree)
    return jsonify({"ok": True, "tree": tree})

@app.delete("/api/node/<node_id>")
def api_delete_node(node_id):
    tree = load_tree()
    node, parent = find_node(tree, node_id)
    if not node:
        return jsonify({"error":"node not found"}), 404
    if parent is None:
        return jsonify({"error":"cannot delete root"}), 400

    parent["children"] = [c for c in parent.get("children", []) if c["id"] != node_id]
    save_tree(tree)
    socketio.emit("tree_updated", tree)
    return jsonify({"ok": True, "tree": tree})

if __name__ == "__main__":
    # Create default tree if not present
    load_tree()
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
