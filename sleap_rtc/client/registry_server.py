#!/usr/bin/env python3
"""Simple web server for viewing the client model registry.

This module provides a lightweight web interface to browse models in the
client registry. It serves both the HTML interface and a JSON API.

Usage:
    python -m sleap_rtc.client.registry_server [--port PORT] [--registry PATH]
"""

import json
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

from sleap_rtc.client.client_model_registry import ClientModelRegistry


class RegistryRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the registry web interface."""

    registry: Optional[ClientModelRegistry] = None

    def do_GET(self):
        """Handle GET requests."""
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        # API endpoint for registry data
        if path == "/api/models":
            self.serve_models_json(parsed_path)
        # API endpoint for single model
        elif path.startswith("/api/model/"):
            model_id = path.split("/")[-1]
            self.serve_model_json(model_id)
        # API endpoint for registry stats
        elif path == "/api/stats":
            self.serve_stats_json()
        # Serve the main HTML interface
        elif path == "/" or path == "/index.html":
            self.serve_html()
        # Serve CSS
        elif path == "/style.css":
            self.serve_css()
        # Serve JavaScript
        elif path == "/app.js":
            self.serve_js()
        else:
            self.send_error(404, "Not Found")

    def serve_models_json(self, parsed_path):
        """Serve registry models as JSON."""
        # Parse query parameters for filtering
        query_params = parse_qs(parsed_path.query)
        filters = {}

        if "type" in query_params:
            filters["model_type"] = query_params["type"][0]
        if "location" in query_params:
            filters["location"] = query_params["location"][0]
        if "has_alias" in query_params:
            filters["has_alias"] = query_params["has_alias"][0].lower() == "true"

        models = self.registry.list(filters=filters if filters else None)

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(models, indent=2).encode())

    def serve_model_json(self, model_id):
        """Serve a single model's details as JSON."""
        model = self.registry.get(model_id)

        if model:
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(model, indent=2).encode())
        else:
            self.send_error(404, f"Model {model_id} not found")

    def serve_stats_json(self):
        """Serve registry statistics as JSON."""
        all_models = self.registry.list()

        stats = {
            "total_models": len(all_models),
            "by_type": {},
            "by_location": {
                "local_only": 0,
                "worker_only": 0,
                "both": 0
            },
            "with_alias": sum(1 for m in all_models if m.get("alias")),
            "by_source": {}
        }

        for model in all_models:
            # Count by type
            model_type = model["model_type"]
            stats["by_type"][model_type] = stats["by_type"].get(model_type, 0) + 1

            # Count by location
            on_worker = model.get("on_worker", False)
            if on_worker:
                stats["by_location"]["both"] += 1
            else:
                stats["by_location"]["local_only"] += 1

            # Count by source
            source = model.get("source", "unknown")
            stats["by_source"][source] = stats["by_source"].get(source, 0) + 1

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(stats, indent=2).encode())

    def serve_html(self):
        """Serve the main HTML interface."""
        html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SLEAP-RTC Model Registry</title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <div class="container">
        <header>
            <h1>🧠 SLEAP-RTC Model Registry</h1>
            <p class="subtitle">Client-side model management</p>
        </header>

        <div class="stats-bar" id="stats">
            <div class="stat-card">
                <div class="stat-value" id="total-models">-</div>
                <div class="stat-label">Total Models</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="with-alias">-</div>
                <div class="stat-label">With Aliases</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="on-worker">-</div>
                <div class="stat-label">On Worker</div>
            </div>
        </div>

        <div class="controls">
            <div class="filter-group">
                <label for="type-filter">Filter by Type:</label>
                <select id="type-filter">
                    <option value="">All Types</option>
                    <option value="centroid">Centroid</option>
                    <option value="topdown">Top-down</option>
                    <option value="bottomup">Bottom-up</option>
                </select>
            </div>

            <div class="filter-group">
                <label for="location-filter">Filter by Location:</label>
                <select id="location-filter">
                    <option value="">All Locations</option>
                    <option value="local-only">Local Only</option>
                    <option value="both">Local + Worker</option>
                </select>
            </div>

            <button id="refresh-btn" class="btn-primary">🔄 Refresh</button>
        </div>

        <div id="models-container" class="models-grid">
            <p class="loading">Loading models...</p>
        </div>
    </div>

    <script src="/app.js"></script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_css(self):
        """Serve CSS styles."""
        css = """* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh;
    padding: 20px;
}

.container {
    max-width: 1400px;
    margin: 0 auto;
}

header {
    text-align: center;
    color: white;
    margin-bottom: 30px;
}

header h1 {
    font-size: 2.5rem;
    margin-bottom: 10px;
    text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
}

.subtitle {
    font-size: 1.1rem;
    opacity: 0.9;
}

.stats-bar {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 20px;
    margin-bottom: 30px;
}

.stat-card {
    background: white;
    padding: 25px;
    border-radius: 12px;
    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    text-align: center;
}

.stat-value {
    font-size: 2.5rem;
    font-weight: bold;
    color: #667eea;
    margin-bottom: 8px;
}

.stat-label {
    color: #666;
    font-size: 0.95rem;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.controls {
    background: white;
    padding: 20px;
    border-radius: 12px;
    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    margin-bottom: 30px;
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    align-items: center;
}

.filter-group {
    display: flex;
    align-items: center;
    gap: 10px;
}

.filter-group label {
    font-weight: 500;
    color: #333;
}

select {
    padding: 8px 12px;
    border: 2px solid #e0e0e0;
    border-radius: 6px;
    font-size: 0.95rem;
    background: white;
    cursor: pointer;
    transition: border-color 0.2s;
}

select:hover, select:focus {
    border-color: #667eea;
    outline: none;
}

.btn-primary {
    padding: 8px 20px;
    background: #667eea;
    color: white;
    border: none;
    border-radius: 6px;
    font-size: 0.95rem;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.2s;
    margin-left: auto;
}

.btn-primary:hover {
    background: #5568d3;
}

.models-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
    gap: 20px;
}

.model-card {
    background: white;
    border-radius: 12px;
    padding: 24px;
    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    transition: transform 0.2s, box-shadow 0.2s;
}

.model-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 8px 12px rgba(0,0,0,0.15);
}

.model-header {
    display: flex;
    justify-content: space-between;
    align-items: start;
    margin-bottom: 16px;
    padding-bottom: 16px;
    border-bottom: 2px solid #f0f0f0;
}

.model-title {
    flex: 1;
}

.model-alias {
    font-size: 1.3rem;
    font-weight: 600;
    color: #333;
    margin-bottom: 4px;
}

.model-id {
    font-family: 'Monaco', 'Courier New', monospace;
    font-size: 0.85rem;
    color: #999;
}

.model-type {
    background: #667eea;
    color: white;
    padding: 6px 12px;
    border-radius: 20px;
    font-size: 0.85rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.model-body {
    margin-bottom: 16px;
}

.model-field {
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid #f5f5f5;
}

.model-field:last-child {
    border-bottom: none;
}

.field-label {
    color: #666;
    font-size: 0.9rem;
    font-weight: 500;
}

.field-value {
    color: #333;
    font-size: 0.9rem;
    text-align: right;
    max-width: 60%;
    word-break: break-word;
}

.field-value.code {
    font-family: 'Monaco', 'Courier New', monospace;
    font-size: 0.85rem;
}

.tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 16px;
}

.tag {
    background: #f0f0f0;
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 0.8rem;
    color: #666;
}

.location-badge {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 0.8rem;
    font-weight: 500;
}

.location-badge.local {
    background: #fef3c7;
    color: #92400e;
}

.location-badge.worker {
    background: #d1fae5;
    color: #065f46;
}

.metrics {
    background: #f8f9fa;
    padding: 12px;
    border-radius: 8px;
    margin-top: 12px;
}

.metric-row {
    display: flex;
    justify-content: space-between;
    padding: 4px 0;
    font-size: 0.9rem;
}

.metric-label {
    color: #666;
}

.metric-value {
    font-weight: 500;
    color: #333;
}

.loading, .empty {
    grid-column: 1 / -1;
    text-align: center;
    padding: 60px 20px;
    color: white;
    font-size: 1.2rem;
}

.error {
    grid-column: 1 / -1;
    background: #fee;
    border: 2px solid #fcc;
    padding: 20px;
    border-radius: 8px;
    color: #c33;
    text-align: center;
}

@media (max-width: 768px) {
    .models-grid {
        grid-template-columns: 1fr;
    }

    .controls {
        flex-direction: column;
        align-items: stretch;
    }

    .filter-group {
        flex-direction: column;
        align-items: stretch;
    }

    .btn-primary {
        margin-left: 0;
    }
}"""
        self.send_response(200)
        self.send_header("Content-type", "text/css")
        self.end_headers()
        self.wfile.write(css.encode())

    def serve_js(self):
        """Serve JavaScript application."""
        js = """// State
let allModels = [];

// Fetch and display models
async function loadModels() {
    try {
        const typeFilter = document.getElementById('type-filter').value;
        const locationFilter = document.getElementById('location-filter').value;

        let url = '/api/models?';
        if (typeFilter) url += `type=${typeFilter}&`;
        if (locationFilter) url += `location=${locationFilter}&`;

        const response = await fetch(url);
        allModels = await response.json();

        displayModels(allModels);
        loadStats();
    } catch (error) {
        console.error('Failed to load models:', error);
        document.getElementById('models-container').innerHTML =
            '<div class="error">Failed to load models. Is the registry accessible?</div>';
    }
}

// Display models in the grid
function displayModels(models) {
    const container = document.getElementById('models-container');

    if (models.length === 0) {
        container.innerHTML = '<p class="empty">No models found. Try adjusting your filters.</p>';
        return;
    }

    container.innerHTML = models.map(model => {
        const alias = model.alias || 'Unnamed Model';
        const location = model.on_worker ? 'worker' : 'local';
        const locationLabel = model.on_worker ? 'Local + Worker' : 'Local Only';

        // Format metrics
        let metricsHtml = '';
        if (model.metrics) {
            metricsHtml = '<div class="metrics">';
            for (const [key, value] of Object.entries(model.metrics)) {
                const formattedValue = typeof value === 'number' ? value.toFixed(4) : value;
                metricsHtml += `
                    <div class="metric-row">
                        <span class="metric-label">${formatLabel(key)}:</span>
                        <span class="metric-value">${formattedValue}</span>
                    </div>
                `;
            }
            metricsHtml += '</div>';
        }

        // Format tags
        let tagsHtml = '';
        if (model.tags && model.tags.length > 0) {
            tagsHtml = '<div class="tags">' +
                model.tags.map(tag => `<span class="tag">${tag}</span>`).join('') +
                '</div>';
        }

        // Format timestamp
        const timestamp = model.downloaded_at || model.imported_at || 'Unknown';
        const date = timestamp !== 'Unknown' ? new Date(timestamp).toLocaleString() : 'Unknown';

        return `
            <div class="model-card">
                <div class="model-header">
                    <div class="model-title">
                        <div class="model-alias">${alias}</div>
                        <div class="model-id">${model.id}</div>
                    </div>
                    <span class="model-type">${model.model_type}</span>
                </div>

                <div class="model-body">
                    <div class="model-field">
                        <span class="field-label">Source:</span>
                        <span class="field-value">${model.source || 'unknown'}</span>
                    </div>
                    <div class="model-field">
                        <span class="field-label">Location:</span>
                        <span class="location-badge ${location}">${locationLabel}</span>
                    </div>
                    <div class="model-field">
                        <span class="field-label">Added:</span>
                        <span class="field-value">${date}</span>
                    </div>
                    ${model.local_path ? `
                    <div class="model-field">
                        <span class="field-label">Local Path:</span>
                        <span class="field-value code">${truncatePath(model.local_path)}</span>
                    </div>
                    ` : ''}
                    ${model.notes ? `
                    <div class="model-field">
                        <span class="field-label">Notes:</span>
                        <span class="field-value">${model.notes}</span>
                    </div>
                    ` : ''}
                </div>

                ${metricsHtml}
                ${tagsHtml}
            </div>
        `;
    }).join('');
}

// Load statistics
async function loadStats() {
    try {
        const response = await fetch('/api/stats');
        const stats = await response.json();

        document.getElementById('total-models').textContent = stats.total_models;
        document.getElementById('with-alias').textContent = stats.with_alias;
        document.getElementById('on-worker').textContent = stats.by_location.both;
    } catch (error) {
        console.error('Failed to load stats:', error);
    }
}

// Utility functions
function formatLabel(key) {
    return key.split('_').map(word =>
        word.charAt(0).toUpperCase() + word.slice(1)
    ).join(' ');
}

function truncatePath(path) {
    if (path.length > 40) {
        return '...' + path.slice(-37);
    }
    return path;
}

// Event listeners
document.getElementById('type-filter').addEventListener('change', loadModels);
document.getElementById('location-filter').addEventListener('change', loadModels);
document.getElementById('refresh-btn').addEventListener('click', loadModels);

// Initial load
loadModels();"""
        self.send_response(200)
        self.send_header("Content-type", "application/javascript")
        self.end_headers()
        self.wfile.write(js.encode())

    def log_message(self, format, *args):
        """Override to provide cleaner logging."""
        print(f"[{self.log_date_time_string()}] {format % args}")


def start_server(registry_path: Optional[Path] = None, port: int = 8765):
    """Start the registry web server.

    Args:
        registry_path: Optional path to registry file. Defaults to ~/.sleap-rtc/models/manifest.json
        port: Port to run server on. Defaults to 8765
    """
    # Initialize registry
    if registry_path:
        registry = ClientModelRegistry(registry_path=registry_path)
    else:
        registry = ClientModelRegistry()

    # Set registry on handler class
    RegistryRequestHandler.registry = registry

    # Create and start server
    server = HTTPServer(("localhost", port), RegistryRequestHandler)

    print("=" * 70)
    print("SLEAP-RTC Model Registry Viewer")
    print("=" * 70)
    print(f"Registry: {registry.registry_path}")
    print(f"Models: {len(registry.list())}")
    print(f"\n🌐 Server running at: http://localhost:{port}")
    print("\nPress Ctrl+C to stop the server")
    print("=" * 70)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down server...")
        server.shutdown()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Start the SLEAP-RTC Model Registry web viewer"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to run server on (default: 8765)"
    )
    parser.add_argument(
        "--registry",
        type=Path,
        help="Path to registry file (default: ~/.sleap-rtc/models/manifest.json)"
    )

    args = parser.parse_args()
    start_server(registry_path=args.registry, port=args.port)


if __name__ == "__main__":
    main()
