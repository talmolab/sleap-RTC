#!/usr/bin/env python3
"""Demo script showing programmatic model import usage.

This script demonstrates:
- Creating mock model directories for testing
- Using model detection utilities
- Importing models programmatically
- Listing and querying imported models
"""

from pathlib import Path
import shutil
import yaml
from sleap_rtc.client.client_model_registry import ClientModelRegistry
from sleap_rtc.client.model_utils import (
    find_checkpoint_files,
    detect_model_type,
    calculate_model_size,
    validate_checkpoint_files,
    generate_model_id_from_config,
    format_size,
)


def create_mock_model(base_path: Path, model_name: str, model_type: str) -> Path:
    """Create a mock model directory for testing.

    Args:
        base_path: Base directory to create mock model in
        model_name: Name for the model directory
        model_type: Type of model (centroid, topdown, etc.)

    Returns:
        Path to created model directory
    """
    model_dir = base_path / model_name
    model_dir.mkdir(parents=True, exist_ok=True)

    # Create a mock checkpoint file
    checkpoint = model_dir / "best.ckpt"
    checkpoint.write_bytes(b"x" * 1024 * 100)  # 100 KB mock checkpoint

    # Create a training config
    config = {
        "model_type": model_type,
        "backbone": {"type": f"{model_type}_resnet"},
        "training": {
            "learning_rate": 0.001,
            "batch_size": 4,
            "epochs": 10,
        },
    }
    config_path = model_dir / "training_config.yaml"
    with open(config_path, 'w') as f:
        yaml.dump(config, f)

    print(f"✓ Created mock model: {model_dir}")
    return model_dir


def demo_model_detection(model_path: Path):
    """Demonstrate model detection utilities.

    Args:
        model_path: Path to model directory
    """
    print("=" * 70)
    print("Model Detection Demo")
    print("=" * 70)
    print()

    print(f"Analyzing model at: {model_path}")
    print()

    # Find checkpoint files
    checkpoints = find_checkpoint_files(model_path)
    print(f"Checkpoint files found: {len(checkpoints)}")
    for ckpt in checkpoints:
        print(f"  - {ckpt.name}")
    print()

    # Detect model type
    model_type = detect_model_type(model_path)
    if model_type:
        print(f"Detected model type: {model_type}")
    else:
        print("Could not auto-detect model type")
    print()

    # Calculate size
    size = calculate_model_size(model_path)
    print(f"Model size: {format_size(size)}")
    print()

    # Validate checkpoints
    valid = validate_checkpoint_files(model_path)
    print(f"Checkpoints valid: {valid}")
    print()

    # Generate model ID
    model_id = generate_model_id_from_config(model_path)
    print(f"Generated model ID: {model_id}")
    print()


def demo_programmatic_import(registry: ClientModelRegistry, model_path: Path, alias: str):
    """Demonstrate programmatic model import.

    Args:
        registry: Client model registry instance
        model_path: Path to model to import
        alias: Alias to assign to model
    """
    print("=" * 70)
    print("Programmatic Import Demo")
    print("=" * 70)
    print()

    print(f"Importing model from: {model_path}")
    print(f"Alias: {alias}")
    print()

    # Step 1: Detect model info
    model_type = detect_model_type(model_path)
    if not model_type:
        print("Error: Could not detect model type")
        return

    model_id = generate_model_id_from_config(model_path)
    size = calculate_model_size(model_path)
    checkpoints = find_checkpoint_files(model_path)

    print(f"Model type: {model_type}")
    print(f"Model ID: {model_id}")
    print(f"Size: {format_size(size)}")
    print()

    # Step 2: Check if already exists
    if registry.exists(model_id):
        print(f"Model {model_id} already exists, removing...")
        registry.delete(model_id)

    # Step 3: Create destination and symlink
    dest_dir = Path("/tmp/sleap-rtc-alias-demo/models") / f"{model_type}_{model_id}"
    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    # Remove if exists
    if dest_dir.exists():
        if dest_dir.is_symlink():
            dest_dir.unlink()
        else:
            shutil.rmtree(dest_dir)

    # Create symlink
    dest_dir.symlink_to(model_path.resolve(), target_is_directory=True)
    print(f"✓ Created symlink: {dest_dir}")
    print()

    # Step 4: Register model
    checkpoint_path = dest_dir / checkpoints[0].relative_to(model_path)

    model_info = {
        "id": model_id,
        "model_type": model_type,
        "source": "local-import",
        "local_path": str(dest_dir),
        "checkpoint_path": str(checkpoint_path),
        "on_worker": False,
        "size_bytes": size,
        "import_mode": "symlink",
        "original_path": str(model_path.resolve()),
    }

    registry.register(model_info)
    print(f"✓ Registered model in registry")
    print()

    # Step 5: Set alias
    registry.set_alias(model_id, alias)
    print(f"✓ Set alias: {alias}")
    print()


def main():
    """Run the model import demo."""
    print("=" * 70)
    print("Model Import Demo")
    print("=" * 70)
    print()

    # Setup demo environment
    demo_path = Path("/tmp/sleap-rtc-alias-demo")
    demo_path.mkdir(exist_ok=True)
    registry_path = demo_path / "manifest.json"

    # Create mock models directory
    mock_models_dir = demo_path / "mock_models"
    mock_models_dir.mkdir(exist_ok=True)

    print(f"Demo directory: {demo_path}")
    print(f"Registry path: {registry_path}")
    print()

    # Initialize registry
    registry = ClientModelRegistry(registry_path=registry_path)
    print(f"✓ Registry initialized")
    print()

    # Create mock models
    print("=" * 70)
    print("Creating Mock Models")
    print("=" * 70)
    print()

    centroid_model = create_mock_model(mock_models_dir, "centroid_mouse", "centroid")
    topdown_model = create_mock_model(mock_models_dir, "topdown_rat", "topdown")
    bottomup_model = create_mock_model(mock_models_dir, "bottomup_multi", "bottomup")
    print()

    # Demonstrate model detection
    demo_model_detection(centroid_model)

    # Import models programmatically
    print("=" * 70)
    print("Importing Models")
    print("=" * 70)
    print()

    demo_programmatic_import(registry, centroid_model, "centroid-prod-v1")
    demo_programmatic_import(registry, topdown_model, "topdown-baseline")
    demo_programmatic_import(registry, bottomup_model, "bottomup-exp-v1")

    # List imported models
    print("=" * 70)
    print("Imported Models")
    print("=" * 70)
    print()

    models = registry.list()
    print(f"Total models: {len(models)}")
    print()

    for model in models:
        alias = model.get("alias") or "(no alias)"
        size = model.get("size_bytes", 0)
        print(f"  {model['id']:12s} | {alias:20s} | {model['model_type']:10s} | {format_size(size)}")
    print()

    # Show aliases
    print("=" * 70)
    print("Alias Mappings")
    print("=" * 70)
    print()

    aliases = registry.list_aliases(sort=True)
    for alias, model_id in aliases.items():
        model = registry.get(model_id)
        print(f"  {alias:20s} -> {model_id} ({model['model_type']})")
    print()

    # Demonstrate resolution
    print("=" * 70)
    print("Model Resolution")
    print("=" * 70)
    print()

    test_identifiers = [
        "centroid-prod-v1",  # Alias
        list(models)[0]["id"],  # Model ID
        "nonexistent",  # Not found
    ]

    for identifier in test_identifiers:
        resolved = registry.resolve(identifier)
        if resolved:
            model = registry.get(resolved)
            alias_str = f" (alias: {model.get('alias')})" if model.get('alias') else ""
            print(f"  '{identifier}' -> {resolved}{alias_str}")
        else:
            print(f"  '{identifier}' -> Not found")
    print()

    # Show detailed model info
    print("=" * 70)
    print("Detailed Model Info")
    print("=" * 70)
    print()

    model = registry.get_by_alias("centroid-prod-v1")
    if model:
        print(f"Model ID:          {model['id']}")
        print(f"Alias:             {model.get('alias', 'N/A')}")
        print(f"Type:              {model['model_type']}")
        print(f"Source:            {model['source']}")
        print(f"Local path:        {model['local_path']}")
        print(f"Checkpoint:        {model['checkpoint_path']}")
        print(f"Size:              {format_size(model.get('size_bytes', 0))}")
        print(f"Import mode:       {model.get('import_mode', 'N/A')}")
        print(f"Original path:     {model.get('original_path', 'N/A')}")
        print(f"On worker:         {model['on_worker']}")
    print()

    # Summary
    print("=" * 70)
    print("Demo Complete!")
    print("=" * 70)
    print()
    print(f"Registry saved to: {registry_path}")
    print(f"Mock models created in: {mock_models_dir}")
    print()
    print("CLI Usage Examples:")
    print(f"  sleap-rtc import-model {centroid_model} --alias my-model")
    print(f"  sleap-rtc import-model {topdown_model} --model-type topdown --copy")
    print()


if __name__ == "__main__":
    main()
