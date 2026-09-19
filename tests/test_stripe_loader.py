from pathlib import Path
import shutil

import networkx as nx

import app as website
from routing import Router
from traffic import TrafficCache


def test_loader_is_javascript_without_runtime_node_modules(tmp_path, monkeypatch):
    """Reproduce the Vercel bundle: static assets present, node_modules omitted."""
    original = Path(website.__file__).parent / "static/stripe-connect-loader.js"
    (tmp_path / "static").mkdir()
    shutil.copyfile(original, tmp_path / "static/stripe-connect-loader.js")
    monkeypatch.setattr(website, "ROOT", tmp_path)
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=-79.39, y=43.66)
    client = website.create_app(router=Router(graph, [], {}), traffic=TrafficCache(None)).test_client()
    response = client.get("/static/stripe-connect-loader.js")
    assert response.status_code == 200
    assert response.mimetype in ("application/javascript", "text/javascript")
    assert response.data == original.read_bytes()
    assert b"export {" in response.data and b"loadConnectAndInitialize" in response.data
