# Testing Model Registry Integration

This guide provides practical examples for testing the model registry integration with `client-track`.

## Quick Test Script

The fastest way to test the complete workflow:

```bash
python examples/test_model_registry_integration.py
```

This script will:
1. Create temporary test models
2. Import them using the CLI
3. Test model resolution by path, ID, and alias
4. Demonstrate error handling
5. Show example CLI commands

## Manual Testing Steps

### 1. Create or Use Existing Models

If you have existing SLEAP models:
```bash
# Use your existing model directories
MODEL1="/path/to/your/centroid_model"
MODEL2="/path/to/your/topdown_model"
```

Or create mock models for testing:
```bash
python examples/demo_model_import.py
```

### 2. Import Models into Registry

Import models with aliases:

```bash
# Import centroid model
sleap-rtc import-model /path/to/centroid_model \
  --alias centroid-prod-v1

# Import centered instance model
sleap-rtc import-model /path/to/topdown_model \
  --alias topdown-dev \
  --model-type centered_instance

# Import without alias (auto-generated ID only)
sleap-rtc import-model /path/to/bottomup_model
```

### 3. View Imported Models

**Web Interface** (recommended):
```bash
python -m sleap_rtc.client.registry_server
# Open http://localhost:8765 in your browser
```

**Programmatically**:
```python
from sleap_rtc.client.client_model_registry import ClientModelRegistry

registry = ClientModelRegistry()
models = registry.list()

for model in models:
    print(f"ID: {model['id']}")
    print(f"Alias: {model.get('alias', 'N/A')}")
    print(f"Type: {model['model_type']}")
    print(f"Path: {model['local_path']}")
    print()
```

### 4. Test Model Resolution

Test that models can be resolved by different identifiers:

```python
from sleap_rtc.client.model_utils import resolve_model_path

# Resolve by alias
path, source = resolve_model_path("centroid-prod-v1")
print(f"Resolved: {path} (via {source})")

# Resolve by model ID (8-char hex)
path, source = resolve_model_path("a3b4c5d6")
print(f"Resolved: {path} (via {source})")

# Resolve by filesystem path
path, source = resolve_model_path("/path/to/model")
print(f"Resolved: {path} (via {source})")
```

### 5. Test with client-track CLI

**Option A: Test Resolution Only (Dry Run)**

Test that models resolve correctly without actually running inference:

```bash
# This will fail at connection stage but will show if models resolve
sleap-rtc client-track \
  --data-path test.slp \
  --model-paths centroid-prod-v1 \
  --session-string fake-session
```

Look for these log messages:
```
Resolving 1 model(s)...
✓ Resolved model: 'centroid-prod-v1' (alias)
Successfully resolved 1 model(s)
```

**Option B: Test with Multiple Model References**

Mix paths, IDs, and aliases:

```bash
sleap-rtc client-track \
  --data-path test.slp \
  --model-paths /path/to/model1 \
  --model-paths centroid-prod-v1 \
  --model-paths a3b4c5d6 \
  --session-string fake-session
```

**Option C: Full Integration Test**

If you have a worker running:

```bash
# Start worker (in another terminal)
sleap-rtc worker

# Use imported model by alias
sleap-rtc client-track \
  --session-string <session_from_worker> \
  --data-path your_data.slp \
  --model-paths centroid-prod-v1 \
  --output predictions.slp
```

### 6. Test Error Handling

Test that helpful errors are shown for invalid models:

```bash
# Try nonexistent alias
sleap-rtc client-track \
  --data-path test.slp \
  --model-paths nonexistent-model \
  --session-string fake

# Expected output:
# Failed to resolve 1 model(s):
#   - nonexistent-model: Model not found...
# Available options:
#   1. Use a filesystem path
#   2. Import the model first: sleap-rtc import-model ...
#   3. List available models: python -m sleap_rtc.client.registry_server
```

### 7. Test Custom Registry Path

Test with a custom registry location:

```bash
# Create custom registry in tmp
CUSTOM_REGISTRY="/tmp/test_registry.json"

# Import to custom registry
sleap-rtc import-model /path/to/model \
  --alias test-model \
  --registry-path "$CUSTOM_REGISTRY"

# Use with client-track
sleap-rtc client-track \
  --data-path test.slp \
  --model-paths test-model \
  --registry-path "$CUSTOM_REGISTRY" \
  --session-string fake
```

## Example Workflows

### Workflow 1: Import and Track

```bash
# 1. Import model
sleap-rtc import-model models/my_trained_model \
  --alias production-v1

# 2. View in web UI
python -m sleap_rtc.client.registry_server

# 3. Use for inference
sleap-rtc client-track \
  --room-id <room> --token <token> \
  --data-path data.slp \
  --model-paths production-v1 \
  --output predictions.slp
```

### Workflow 2: Multi-Model Inference

```bash
# Import centroid model
sleap-rtc import-model models/centroid_251024 \
  --alias centroid-v1

# Import centered-instance model
sleap-rtc import-model models/topdown_251025 \
  --alias topdown-v1

# Run two-stage inference
sleap-rtc client-track \
  --room-id <room> --token <token> \
  --data-path data.slp \
  --model-paths centroid-v1 \
  --model-paths topdown-v1 \
  --output predictions.slp
```

### Workflow 3: Version Management

```bash
# Import different versions
sleap-rtc import-model models/centroid_v1 --alias centroid-prod
sleap-rtc import-model models/centroid_v2 --alias centroid-dev

# Test with dev version
sleap-rtc client-track \
  --data-path test.slp \
  --model-paths centroid-dev \
  --session-string <session>

# Switch to prod version (just change alias)
sleap-rtc client-track \
  --data-path test.slp \
  --model-paths centroid-prod \
  --session-string <session>
```

## Demo Scripts

Additional demo scripts for testing:

```bash
# Create mock models and import them
python examples/demo_model_import.py

# Test alias management
python examples/demo_alias_management.py

# Populate registry with test data
python examples/populate_demo_registry.py

# View registry programmatically
python examples/demo_client_registry.py
```

## Troubleshooting

### Model not found
```bash
# List all imported models
python -m sleap_rtc.client.registry_server

# Or use programmatic access
python -c "from sleap_rtc.client.client_model_registry import ClientModelRegistry; \
           r = ClientModelRegistry(); \
           print([m.get('alias', m['id']) for m in r.list()])"
```

### Registry location
Default registry location:
```bash
~/.sleap-rtc/models/manifest.json
```

View registry contents:
```bash
cat ~/.sleap-rtc/models/manifest.json | python -m json.tool
```

### Reset registry
```bash
# Backup first
cp ~/.sleap-rtc/models/manifest.json ~/.sleap-rtc/models/manifest.json.backup

# Remove registry
rm ~/.sleap-rtc/models/manifest.json

# Models will be re-initialized on next import
```

## Running Tests

Run the unit tests:

```bash
# All model utils tests
uv run pytest tests/test_model_utils.py -v

# Just resolution tests
uv run pytest tests/test_model_utils.py::TestResolveModelPath -v
uv run pytest tests/test_model_utils.py::TestResolveModelPaths -v

# With coverage
uv run pytest tests/test_model_utils.py --cov=sleap_rtc.client.model_utils
```
