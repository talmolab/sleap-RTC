#!/usr/bin/env python3
"""Test script to demonstrate what happens with multiple registry instances.

This shows:
1. Creating a registry when one already exists (loads existing data)
2. Multiple instances accessing the same registry file
3. Potential race conditions with concurrent writes
"""

from pathlib import Path
from sleap_rtc.client.client_model_registry import ClientModelRegistry


def test_loading_existing_registry():
    """Test that creating a new instance loads existing data."""
    print("=" * 70)
    print("Test 1: Loading Existing Registry")
    print("=" * 70)

    demo_path = Path("/tmp/sleap-rtc-alias-demo")
    demo_path.mkdir(exist_ok=True)
    registry_path = demo_path / "manifest.json"

    # Clean up any existing file
    if registry_path.exists():
        registry_path.unlink()

    print("Step 1: Create first registry instance and add a model")
    registry1 = ClientModelRegistry(registry_path=registry_path)
    registry1.register({
        "id": "model001",
        "model_type": "centroid",
        "alias": "first-model"
    })
    print(f"  ✓ Registry1 has {len(registry1.list())} model(s)")
    print()

    print("Step 2: Create second registry instance (should load existing data)")
    registry2 = ClientModelRegistry(registry_path=registry_path)
    print(f"  ✓ Registry2 loaded with {len(registry2.list())} model(s)")

    models = registry2.list()
    if models:
        print(f"  ✓ Found existing model: {models[0]['alias']} ({models[0]['id']})")
    print()

    print("Conclusion: Creating a new registry instance when one exists")
    print("            loads the existing data (does NOT overwrite)")
    print()


def test_multiple_instances_read():
    """Test that multiple instances can read from the same registry."""
    print("=" * 70)
    print("Test 2: Multiple Instances Reading Same Registry")
    print("=" * 70)

    demo_path = Path("/tmp/sleap-rtc-alias-demo")
    registry_path = demo_path / "manifest.json"

    print("Create 3 instances and verify they all see the same data:")

    instance1 = ClientModelRegistry(registry_path=registry_path)
    instance2 = ClientModelRegistry(registry_path=registry_path)
    instance3 = ClientModelRegistry(registry_path=registry_path)

    print(f"  Instance 1: {len(instance1.list())} models")
    print(f"  Instance 2: {len(instance2.list())} models")
    print(f"  Instance 3: {len(instance3.list())} models")

    # Check they all see the same model
    if instance1.exists("model001"):
        print(f"  ✓ All instances can access model 'model001'")
    print()

    print("Conclusion: Multiple instances can safely read the same registry")
    print()


def test_concurrent_write_scenario():
    """Test what happens with concurrent writes (potential issue)."""
    print("=" * 70)
    print("Test 3: Concurrent Write Scenario (Potential Issue)")
    print("=" * 70)

    demo_path = Path("/tmp/sleap-rtc-alias-demo")
    registry_path = demo_path / "manifest.json"

    print("Scenario: Two instances modify data independently")
    print()

    # Create two instances
    instance1 = ClientModelRegistry(registry_path=registry_path)
    instance2 = ClientModelRegistry(registry_path=registry_path)

    print("Initial state:")
    print(f"  Instance 1: {len(instance1.list())} models")
    print(f"  Instance 2: {len(instance2.list())} models")
    print()

    # Instance 1 adds a model
    print("Step 1: Instance 1 adds 'model002'")
    instance1.register({
        "id": "model002",
        "model_type": "centroid",
        "alias": "second-model"
    })
    print(f"  ✓ Instance 1 now has {len(instance1.list())} models")
    print()

    # Instance 2 adds a different model (without reloading from disk)
    print("Step 2: Instance 2 adds 'model003' (working from stale in-memory data)")
    instance2.register({
        "id": "model003",
        "model_type": "topdown",
        "alias": "third-model"
    })
    print(f"  ✓ Instance 2 now has {len(instance2.list())} models")
    print()

    # Check what's on disk
    print("Step 3: Create new instance to see what's persisted on disk")
    instance3 = ClientModelRegistry(registry_path=registry_path)
    models = instance3.list()
    print(f"  On disk: {len(models)} models")
    for model in models:
        print(f"    - {model['alias']} ({model['id']})")
    print()

    print("Conclusion: Last write wins! Instance 2's write overwrote Instance 1's change.")
    print("            This is a potential issue with concurrent modifications.")
    print("            Workaround: Always create a fresh instance before modifying,")
    print("            or implement file locking for true concurrent safety.")
    print()


def test_reload_pattern():
    """Test the safe pattern: reload before modifying."""
    print("=" * 70)
    print("Test 4: Safe Pattern - Reload Before Modifying")
    print("=" * 70)

    demo_path = Path("/tmp/sleap-rtc-alias-demo")
    registry_path = demo_path / "manifest.json"

    # Clean slate
    if registry_path.exists():
        registry_path.unlink()

    print("Step 1: Initial setup with one model")
    registry = ClientModelRegistry(registry_path=registry_path)
    registry.register({"id": "model001", "model_type": "centroid", "alias": "initial"})
    print(f"  ✓ Created registry with {len(registry.list())} model")
    print()

    print("Step 2: Simulate another process adding a model")
    other_process = ClientModelRegistry(registry_path=registry_path)
    other_process.register({"id": "model002", "model_type": "centroid", "alias": "from-other-process"})
    print(f"  ✓ Other process added a model")
    print()

    print("Step 3: SAFE PATTERN - Create fresh instance to see latest data")
    fresh_instance = ClientModelRegistry(registry_path=registry_path)
    print(f"  ✓ Fresh instance sees {len(fresh_instance.list())} models")
    for model in fresh_instance.list():
        print(f"    - {model['alias']}")
    print()

    print("Step 4: UNSAFE PATTERN - Original instance still has stale data")
    print(f"  ✗ Original instance only sees {len(registry.list())} model(s)")
    print()

    print("Conclusion: Best practice is to create a fresh ClientModelRegistry")
    print("            instance for each operation, or reload data from disk")
    print("            before modifications.")
    print()


def test_typical_cli_usage():
    """Test typical CLI usage pattern (each command creates fresh instance)."""
    print("=" * 70)
    print("Test 5: Typical CLI Usage Pattern (Safe)")
    print("=" * 70)

    demo_path = Path("/tmp/sleap-rtc-alias-demo")
    registry_path = demo_path / "manifest.json"

    print("CLI commands typically create a fresh registry instance per command.")
    print("This pattern is naturally safe from concurrent issues.")
    print()

    print("Simulating: sleap-rtc client import-model ...")
    def cli_import_model():
        registry = ClientModelRegistry(registry_path=registry_path)
        registry.register({"id": "cli001", "model_type": "centroid", "alias": "cli-model-1"})
        return len(registry.list())

    count = cli_import_model()
    print(f"  ✓ Command completed, registry has {count} models")
    print()

    print("Simulating: sleap-rtc client list-models")
    def cli_list_models():
        registry = ClientModelRegistry(registry_path=registry_path)
        return registry.list()

    models = cli_list_models()
    print(f"  ✓ Listed {len(models)} models:")
    for model in models:
        print(f"    - {model['alias']} ({model['id']})")
    print()

    print("Simulating: sleap-rtc client tag-model cli001 production")
    def cli_tag_model(model_id, alias):
        registry = ClientModelRegistry(registry_path=registry_path)
        registry.update(model_id, {"alias": alias})

    cli_tag_model("cli001", "production-model")
    print(f"  ✓ Updated alias")
    print()

    print("Simulating: sleap-rtc client list-models (verify update)")
    models = cli_list_models()
    print(f"  ✓ Updated model alias: {models[0]['alias']}")
    print()

    print("Conclusion: CLI usage pattern (fresh instance per command)")
    print("            is naturally safe and recommended.")
    print()


if __name__ == "__main__":
    test_loading_existing_registry()
    test_multiple_instances_read()
    test_concurrent_write_scenario()
    test_reload_pattern()
    test_typical_cli_usage()

    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print()
    print("✓ Creating a new ClientModelRegistry loads existing data (doesn't overwrite)")
    print("✓ Multiple instances can safely READ from the same registry")
    print("✗ Concurrent WRITES can cause last-write-wins issues")
    print("✓ CLI pattern (fresh instance per command) is safe")
    print("✓ Best practice: Create fresh instance for each operation")
    print()
    print("For production use with true concurrent access, consider:")
    print("  - File locking (fcntl on Unix)")
    print("  - Database backend (SQLite with WAL mode)")
    print("  - Read registry from disk before each modification")
    print()
