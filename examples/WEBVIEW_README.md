# SLEAP-RTC Model Registry Web Viewer

A lightweight web interface for browsing and visualizing models in your client-side registry.

## Features

- 📊 **Dashboard**: View statistics about your models at a glance
- 🗂️ **Model Cards**: Clean, organized display of model metadata
- 🔍 **Filtering**: Filter by model type (centroid, topdown, bottomup) and location
- 🏷️ **Metadata Display**: View metrics, hyperparameters, tags, and notes
- 📍 **Location Tracking**: See which models are local-only vs synced with workers
- 🎨 **Responsive Design**: Works on desktop and mobile devices
- ⚡ **Zero Dependencies**: Uses only Python standard library

## Quick Start

### 1. Populate Registry with Demo Data

```bash
# Add sample models to your registry
uv run python examples/populate_demo_registry.py
```

### 2. Start the Web Server

```bash
# Default (port 8765)
uv run python -m sleap_rtc.client.registry_server

# Custom port
uv run python -m sleap_rtc.client.registry_server --port 9000

# Custom registry file
uv run python -m sleap_rtc.client.registry_server --registry /path/to/manifest.json
```

### 3. Open in Browser

Navigate to: **http://localhost:8765**

## Screenshots & Features

### Dashboard Stats
- **Total Models**: Count of all models in registry
- **With Aliases**: Models that have human-readable names
- **On Worker**: Models synced with worker nodes

### Model Cards Display
Each model card shows:
- **Alias** (or "Unnamed Model" if no alias)
- **Model ID** (8-character hash)
- **Model Type** badge (centroid/topdown/bottomup)
- **Source** (worker-training, local-import, worker-pull, client-upload)
- **Location** badge (Local Only / Local + Worker)
- **Timestamp** (when added to registry)
- **Local Path** (checkpoint location)
- **Metrics** (validation loss, epochs, accuracy, etc.)
- **Tags** (production, experimental, validated, etc.)
- **Notes** (user-provided descriptions)

### Filtering Options
- **By Type**: Show only centroid, topdown, or bottomup models
- **By Location**: Filter by local-only or synced models
- **Refresh Button**: Reload data from registry (useful if modified externally)

## API Endpoints

The server provides a simple REST API:

### Get All Models
```bash
# All models
curl http://localhost:8765/api/models

# Filter by type
curl "http://localhost:8765/api/models?type=centroid"

# Filter by location
curl "http://localhost:8765/api/models?location=local-only"

# Combine filters
curl "http://localhost:8765/api/models?type=topdown&location=both"
```

### Get Single Model
```bash
curl http://localhost:8765/api/model/a3f5e8c9
```

### Get Statistics
```bash
curl http://localhost:8765/api/stats
```

Example response:
```json
{
  "total_models": 7,
  "by_type": {
    "centroid": 4,
    "topdown": 2,
    "bottomup": 1
  },
  "by_location": {
    "local_only": 2,
    "worker_only": 0,
    "both": 5
  },
  "with_alias": 6,
  "by_source": {
    "worker-training": 3,
    "local-import": 2,
    "worker-pull": 1,
    "client-upload": 1
  }
}
```

## Using with Your Own Registry

The viewer automatically loads from `~/.sleap-rtc/models/manifest.json` by default.

To view a different registry:
```bash
uv run python -m sleap_rtc.client.registry_server --registry /path/to/custom/manifest.json
```

## Technical Details

### Architecture
- **Backend**: Python HTTP server using `http.server` module
- **Frontend**: Vanilla JavaScript (no frameworks)
- **Styling**: Custom CSS with gradient background and card-based layout
- **Data Format**: JSON API

### Files
- `sleap_rtc/client/registry_server.py` - Main server implementation
- Embedded HTML, CSS, and JavaScript (no external files needed)

### Port Configuration
Default port is **8765**. Change with `--port` flag:
```bash
uv run python -m sleap_rtc.client.registry_server --port 3000
```

### Live Reload
The web interface checks the registry file on each request, so changes made externally (via CLI commands or direct API calls) will be reflected when you refresh the page or click the Refresh button.

## Example Workflow

1. **Train a model** (creates registry entry):
   ```bash
   sleap-rtc client-train --room my-session --config training_config.yaml
   ```

2. **View in browser**:
   - Start server: `uv run python -m sleap_rtc.client.registry_server`
   - Open: http://localhost:8765
   - See your newly trained model appear

3. **Filter and explore**:
   - Filter by type to see only centroid models
   - Click refresh to see latest changes
   - View metrics and hyperparameters

4. **Tag and update** (from CLI):
   ```bash
   sleap-rtc client tag-model a3f5e8c9 production-v1
   sleap-rtc client update-model a3f5e8c9 --notes "Validated on 1000 frames"
   ```

5. **Refresh browser** to see updates

## Troubleshooting

### Server won't start
- **Check if port is in use**: Try a different port with `--port 9000`
- **Registry not found**: Verify registry exists at `~/.sleap-rtc/models/manifest.json`

### No models showing
- **Empty registry**: Run `examples/populate_demo_registry.py` to add demo data
- **Wrong registry**: Specify correct path with `--registry` flag
- **Check browser console**: Open DevTools (F12) to see JavaScript errors

### Models not updating
- **Click Refresh button** in the web interface
- **Check registry file** was actually modified: `cat ~/.sleap-rtc/models/manifest.json`
- **Restart server** if using a custom registry path

## Future Enhancements

Potential features for future versions:
- Model comparison view (side-by-side)
- Delete models from web interface
- Edit aliases and tags directly
- Upload models via drag-and-drop
- Export registry as CSV/Excel
- Search/filter by tags and notes
- Dark mode toggle
- Model performance graphs

## Development

To modify the web interface, edit the embedded HTML/CSS/JS in `registry_server.py`:
- `serve_html()` - Main HTML structure
- `serve_css()` - Styling
- `serve_js()` - Interactive functionality

Then restart the server to see changes.

## Related Commands

```bash
# View registry in terminal
sleap-rtc client list-models

# Get model details
sleap-rtc client model-info <model-id>

# Import a model
sleap-rtc client import-model /path/to/model --alias my-model

# Tag a model
sleap-rtc client tag-model <model-id> <alias>
```

## License

Part of the SLEAP-RTC project. See main project LICENSE for details.
