from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import json, os, threading
from pathlib import Path

APP_DIR = Path(__file__).parent
DATA_FILE = APP_DIR / "tree.json"

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev"
socketio = SocketIO(app, cors_allowed_origins="*")

_file_lock = threading.Lock()

def load_tree():
    with _file_lock:
        if not DATA_FILE.exists():
            # default root if file missing
            root = {"id":"root","title":"Root","description":"Root node","children":[]}
            DATA_FILE.write_text(json.dumps(root, indent=2))
        return json.loads(DATA_FILE.read_text())

def save_tree(tree):
    with _file_lock:
        DATA_FILE.write_text(json.dumps(tree, indent=2))

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
    """
    body: { "parentId": "id", "title": "t", "description": "d", "id": (optional) }
    """
    data = request.get_json(force=True) or {}
    parent_id = data.get("parentId")
    title = data.get("title", "Untitled")
    desc = data.get("description", "")
    new_id = data.get("id") or f"n{os.urandom(4).hex()}"

    tree = load_tree()
    parent, _ = find_node(tree, parent_id)
    if not parent:
        return jsonify({"error":"parent not found"}), 404

    parent.setdefault("children", []).append({
        "id": new_id,
        "title": title,
        "description": desc,
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
