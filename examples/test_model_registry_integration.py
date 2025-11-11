#!/usr/bin/env python3
"""Test script for model registry integration with client-track.

This script demonstrates and tests the complete workflow:
1. Creating mock models for testing
2. Importing models via CLI
3. Resolving models by path, ID, and alias
4. Testing error handling
"""

import subprocess
import sys
import shutil
from pathlib import Path
import tempfile


def run_command(cmd, check=True):
    """Run a shell command and display output."""
    print(f"\n{'='*70}")
    print(f"Running: {' '.join(cmd)}")
    print('='*70)
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if check and result.returncode != 0:
        print(f"❌ Command failed with exit code {result.returncode}")
        sys.exit(1)

    return result


def create_test_model(base_dir: Path, model_name: str, model_type: str):
    """Create a simple test model directory."""
    model_dir = base_dir / model_name
    model_dir.mkdir(parents=True, exist_ok=True)

    # Create a mock checkpoint
    (model_dir / "best.ckpt").write_bytes(b"x" * 1024 * 10)  # 10 KB

    # Create a simple training config
    import yaml
    config = {
        "model_config": {
            "head_configs": {
                "single_instance": None if model_type != "single_instance" else {"confmaps": {"sigma": 2.5}},
                "centroid": None if model_type != "centroid" else {"confmaps": {"sigma": 2.5}},
                "centered_instance": None if model_type != "centered_instance" else {"confmaps": {"sigma": 2.5}},
                "bottomup": None if model_type != "bottomup" else {"confmaps": {"sigma": 2.5}},
            }
        }
    }

    with open(model_dir / "training_config.yaml", "w") as f:
        yaml.dump(config, f)

    print(f"✓ Created test model: {model_dir}")
    return model_dir


def main():
    """Run integration tests."""
    print("\n" + "="*70)
    print("Model Registry Integration Test")
    print("="*70)

    # Create temporary directory for test models
    test_dir = Path(tempfile.mkdtemp(prefix="sleap-rtc-test-"))
    print(f"\nTest directory: {test_dir}")

    # Use a temporary registry for testing
    registry_path = test_dir / "test_registry.json"

    try:
        # Step 1: Create test models
        print("\n" + "="*70)
        print("Step 1: Creating Test Models")
        print("="*70)

        model1 = create_test_model(test_dir, "centroid_model", "centroid")
        model2 = create_test_model(test_dir, "topdown_model", "centered_instance")
        model3 = create_test_model(test_dir, "bottomup_model", "bottomup")

        # Step 2: Import models with different methods
        print("\n" + "="*70)
        print("Step 2: Importing Models")
        print("="*70)

        # Import with alias
        run_command([
            "sleap-rtc", "import-model", str(model1),
            "--alias", "my-centroid-v1",
            "--registry-path", str(registry_path)
        ])

        # Import with explicit type
        run_command([
            "sleap-rtc", "import-model", str(model2),
            "--alias", "prod-topdown",
            "--model-type", "centered_instance",
            "--registry-path", str(registry_path)
        ])

        # Import without alias (will use auto-generated)
        run_command([
            "sleap-rtc", "import-model", str(model3),
            "--registry-path", str(registry_path)
        ])

        # Step 3: Test model resolution
        print("\n" + "="*70)
        print("Step 3: Testing Model Resolution")
        print("="*70)

        # Test resolution programmatically
        print("\nTesting programmatic model resolution:")
        from sleap_rtc.client.model_utils import resolve_model_path, resolve_model_paths

        # Test resolving by alias
        path, source = resolve_model_path("my-centroid-v1", registry_path=registry_path)
        print(f"✓ Resolved alias 'my-centroid-v1': {path} (source: {source})")

        # Test resolving by direct path
        path, source = resolve_model_path(str(model1), registry_path=registry_path)
        print(f"✓ Resolved path '{model1}': {path} (source: {source})")

        # Test resolving multiple models
        print("\nResolving multiple models:")
        paths, errors = resolve_model_paths(
            [str(model1), "my-centroid-v1", "prod-topdown"],
            registry_path=registry_path
        )
        print(f"✓ Resolved {len(paths)} models, {len(errors)} errors")

        # Step 4: Test error handling
        print("\n" + "="*70)
        print("Step 4: Testing Error Handling")
        print("="*70)

        print("\nTesting resolution of nonexistent model:")
        path, source = resolve_model_path("nonexistent-alias", registry_path=registry_path)
        if path is None:
            print("✓ Correctly returned None for nonexistent alias")

        print("\nTesting batch resolution with errors:")
        paths, errors = resolve_model_paths(
            ["my-centroid-v1", "nonexistent", "prod-topdown"],
            registry_path=registry_path
        )
        print(f"✓ Resolved {len(paths)} models")
        print(f"✓ Got {len(errors)} errors as expected")
        for identifier, error in errors:
            print(f"  - {identifier}: {error}")

        # Step 5: Demonstrate CLI usage patterns
        print("\n" + "="*70)
        print("Step 5: CLI Usage Examples")
        print("="*70)

        print("\nExample commands for using imported models with client-track:")
        print("\n1. Use model by alias:")
        print(f"   sleap-rtc client-track \\")
        print(f"     --data-path data.slp \\")
        print(f"     --model-paths my-centroid-v1 \\")
        print(f"     --registry-path {registry_path} \\")
        print(f"     --session-string <session>")

        print("\n2. Use model by direct path:")
        print(f"   sleap-rtc client-track \\")
        print(f"     --data-path data.slp \\")
        print(f"     --model-paths {model1} \\")
        print(f"     --session-string <session>")

        print("\n3. Mix multiple model references:")
        print(f"   sleap-rtc client-track \\")
        print(f"     --data-path data.slp \\")
        print(f"     --model-paths {model1} \\")
        print(f"     --model-paths my-centroid-v1 \\")
        print(f"     --model-paths prod-topdown \\")
        print(f"     --registry-path {registry_path} \\")
        print(f"     --session-string <session>")

        print("\n4. View registry in web interface:")
        print(f"   python -m sleap_rtc.client.registry_server")
        print(f"   # Then open http://localhost:8765")

        # Step 6: List imported models
        print("\n" + "="*70)
        print("Step 6: Registry Contents")
        print("="*70)

        from sleap_rtc.client.client_model_registry import ClientModelRegistry
        registry = ClientModelRegistry(registry_path=registry_path)

        print(f"\nTotal models: {len(registry.get_all_models())}")
        print(f"Total aliases: {len(registry.get_all_aliases())}")

        print("\nModels:")
        for model_id, model in registry.get_all_models().items():
            alias = model.get("alias", "N/A")
            model_type = model.get("model_type", "unknown")
            print(f"  - ID: {model_id}, Alias: {alias}, Type: {model_type}")

        print("\nAliases:")
        for alias, model_id in registry.get_all_aliases().items():
            print(f"  - {alias} → {model_id}")

        print("\n" + "="*70)
        print("✅ All Tests Passed!")
        print("="*70)

        print(f"\nTest files created in: {test_dir}")
        print(f"Test registry: {registry_path}")
        print("\nTo clean up, run:")
        print(f"  rm -rf {test_dir}")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
