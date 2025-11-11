#!/usr/bin/env python3
"""Demo script showing ClientModelRegistry usage.

This script demonstrates the core functionality of the client model registry:
- Registering models
- Querying models
- Filtering models
- Updating model metadata
- Managing aliases
"""

from pathlib import Path
from sleap_rtc.client.client_model_registry import ClientModelRegistry


def main():
    """Run the demo."""
    print("=" * 70)
    print("Client Model Registry Demo")
    print("=" * 70)
    print()

    # Create a temporary registry for demo purposes
    # In production, this would default to ~/.sleap-rtc/models/manifest.json
    demo_path = Path("/tmp/sleap-rtc-alias-demo")
    demo_path.mkdir(exist_ok=True)
    registry_path = demo_path / "manifest.json"

    print(f"Creating registry at: {registry_path}")
    registry = ClientModelRegistry(registry_path=registry_path)
    print("✓ Registry initialized")
    print()

    # Register some models
    print("=" * 70)
    print("1. Registering Models")
    print("=" * 70)

    model1 = {
        "id": "a3f5e8c9",
        "model_type": "centroid",
        "alias": "good-mouse-v1",
        "source": "worker-training",
        "local_path": "~/.sleap-rtc/models/centroid_a3f5e8c9/",
        "checkpoint_path": "~/.sleap-rtc/models/centroid_a3f5e8c9/best.ckpt",
        "on_worker": True,
        "worker_path": "models/centroid_a3f5e8c9/",
        "metrics": {
            "final_val_loss": 0.0234,
            "epochs_completed": 5
        },
        "training_hyperparameters": {
            "learning_rate": 0.001,
            "batch_size": 4,
            "optimizer": "Adam"
        },
        "tags": ["mouse", "production", "validated"]
    }

    model2 = {
        "id": "7f2a1b3c",
        "model_type": "centroid",
        "alias": "legacy-2023",
        "source": "local-import",
        "local_path": "~/my-models/old_centroid/",
        "checkpoint_path": "~/my-models/old_centroid/best.ckpt",
        "on_worker": False,
        "tags": ["mouse", "legacy"]
    }

    model3 = {
        "id": "9c4d2e1f",
        "model_type": "topdown",
        "alias": "rat-baseline",
        "source": "worker-training",
        "local_path": "~/.sleap-rtc/models/topdown_9c4d2e1f/",
        "checkpoint_path": "~/.sleap-rtc/models/topdown_9c4d2e1f/best.ckpt",
        "on_worker": True,
        "metrics": {
            "final_val_loss": 0.0456,
            "epochs_completed": 10
        },
        "tags": ["rat", "production"]
    }

    registry.register(model1)
    print(f"✓ Registered model: {model1['alias']} ({model1['id']})")

    registry.register(model2)
    print(f"✓ Registered model: {model2['alias']} ({model2['id']})")

    registry.register(model3)
    print(f"✓ Registered model: {model3['alias']} ({model3['id']})")
    print()

    # List all models
    print("=" * 70)
    print("2. Listing All Models")
    print("=" * 70)
    all_models = registry.list()
    for i, model in enumerate(all_models, 1):
        location = "local + worker" if model["on_worker"] else "local only"
        print(f"{i}. {model['alias']:20s} ({model['id']}) - {model['model_type']:10s} [{location}]")
    print()

    # Filter models
    print("=" * 70)
    print("3. Filtering Models")
    print("=" * 70)

    print("Centroid models only:")
    centroid_models = registry.list(filters={"model_type": "centroid"})
    for model in centroid_models:
        print(f"  - {model['alias']} ({model['id']})")
    print()

    print("Local-only models (not on worker):")
    local_only = registry.list(filters={"location": "local-only"})
    for model in local_only:
        print(f"  - {model['alias']} ({model['id']})")
    print()

    print("Models with 'production' tag:")
    # This would require custom filtering logic - demonstrating the extensibility
    production_models = [m for m in registry.list() if "production" in m.get("tags", [])]
    for model in production_models:
        print(f"  - {model['alias']} ({model['id']})")
    print()

    # Get model details
    print("=" * 70)
    print("4. Getting Model Details")
    print("=" * 70)
    model_details = registry.get("a3f5e8c9")
    print(f"Model: {model_details['alias']} ({model_details['id']})")
    print(f"Type: {model_details['model_type']}")
    print(f"Source: {model_details['source']}")
    print(f"On Worker: {model_details['on_worker']}")
    print(f"Metrics:")
    for key, value in model_details.get("metrics", {}).items():
        print(f"  - {key}: {value}")
    print(f"Tags: {', '.join(model_details.get('tags', []))}")
    print()

    # Update model
    print("=" * 70)
    print("5. Updating Model Metadata")
    print("=" * 70)
    print(f"Adding notes to model {model1['alias']}...")
    registry.update("a3f5e8c9", {
        "notes": "Best performing model for C57BL/6 mice, validated 2025-11-10"
    })
    updated = registry.get("a3f5e8c9")
    print(f"✓ Notes added: {updated['notes'][:50]}...")
    print()

    # Update alias
    print("Changing alias from 'good-mouse-v1' to 'production-mouse-v1'...")
    registry.update("a3f5e8c9", {"alias": "production-mouse-v1"})
    print(f"✓ Alias updated")
    print()

    # Show all aliases
    print("=" * 70)
    print("6. All Alias Mappings")
    print("=" * 70)
    aliases = registry.get_all_aliases()
    for alias, model_id in aliases.items():
        model = registry.get(model_id)
        print(f"{alias:25s} -> {model_id} ({model['model_type']})")
    print()

    # Check existence
    print("=" * 70)
    print("7. Checking Model Existence")
    print("=" * 70)
    print(f"Does model 'a3f5e8c9' exist? {registry.exists('a3f5e8c9')}")
    print(f"Does model 'nonexistent' exist? {registry.exists('nonexistent')}")
    print()

    # Delete a model
    print("=" * 70)
    print("8. Deleting a Model")
    print("=" * 70)
    print(f"Deleting model {model2['alias']} ({model2['id']})...")
    registry.delete(model2["id"])
    print(f"✓ Model deleted from registry")
    print(f"Model still exists? {registry.exists(model2['id'])}")
    print(f"Alias '{model2['alias']}' still mapped? {'legacy-2023' in registry.get_all_aliases()}")
    print()

    # Final count
    print("=" * 70)
    print("9. Final Model Count")
    print("=" * 70)
    remaining = registry.list()
    print(f"Total models in registry: {len(remaining)}")
    for model in remaining:
        print(f"  - {model['alias']} ({model['id']})")
    print()

    print("=" * 70)
    print(f"Demo complete! Registry saved to: {registry_path}")
    print("You can inspect the JSON file to see the structure.")
    print("=" * 70)


if __name__ == "__main__":
    main()
