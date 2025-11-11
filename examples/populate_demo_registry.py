#!/usr/bin/env python3
"""Populate the registry with demo data for testing the web viewer."""

from pathlib import Path
from sleap_rtc.client.client_model_registry import ClientModelRegistry


def populate_demo_data():
    """Add various demo models to showcase the web viewer."""
    print("Populating registry with demo data...")

    # Use consistent demo path
    demo_path = Path("/tmp/sleap-rtc-alias-demo")
    demo_path.mkdir(exist_ok=True)
    registry_path = demo_path / "manifest.json"

    registry = ClientModelRegistry(registry_path=registry_path)

    # Clear existing data
    for model_id in list(registry.get_all_models().keys()):
        try:
            registry.delete(model_id)
        except:
            pass

    # Model 1: Production centroid model
    registry.register({
        "id": "a3f5e8c9",
        "model_type": "centroid",
        "alias": "production-mouse-v1",
        "source": "worker-training",
        "local_path": "~/.sleap-rtc/models/centroid_a3f5e8c9/",
        "checkpoint_path": "~/.sleap-rtc/models/centroid_a3f5e8c9/best.ckpt",
        "on_worker": True,
        "worker_path": "models/centroid_a3f5e8c9/",
        "metrics": {
            "final_val_loss": 0.0234,
            "epochs_completed": 5,
            "train_loss": 0.0189,
            "val_accuracy": 0.9823
        },
        "training_hyperparameters": {
            "learning_rate": 0.001,
            "batch_size": 4,
            "optimizer": "Adam",
            "max_epochs": 10
        },
        "tags": ["mouse", "production", "validated", "c57bl6"],
        "notes": "Best performing model for C57BL/6 mice, validated 2025-11-10"
    })

    # Model 2: Legacy imported model
    registry.register({
        "id": "7f2a1b3c",
        "model_type": "centroid",
        "alias": "legacy-mouse-2023",
        "source": "local-import",
        "local_path": "~/old-models/mouse_centroid_2023/",
        "checkpoint_path": "~/old-models/mouse_centroid_2023/best.ckpt",
        "on_worker": False,
        "tags": ["mouse", "legacy", "archived"],
        "notes": "Imported from previous SLEAP version"
    })

    # Model 3: Top-down rat model
    registry.register({
        "id": "9c4d2e1f",
        "model_type": "topdown",
        "alias": "rat-baseline-v2",
        "source": "worker-training",
        "local_path": "~/.sleap-rtc/models/topdown_9c4d2e1f/",
        "checkpoint_path": "~/.sleap-rtc/models/topdown_9c4d2e1f/best.ckpt",
        "on_worker": True,
        "worker_path": "models/topdown_9c4d2e1f/",
        "metrics": {
            "final_val_loss": 0.0456,
            "epochs_completed": 10,
            "train_loss": 0.0398,
            "val_accuracy": 0.9654
        },
        "training_hyperparameters": {
            "learning_rate": 0.0005,
            "batch_size": 8,
            "optimizer": "AdamW",
            "max_epochs": 15
        },
        "tags": ["rat", "production", "topdown"],
        "notes": "Updated baseline model with improved architecture"
    })

    # Model 4: Experimental model
    registry.register({
        "id": "b1d8e3a2",
        "model_type": "centroid",
        "alias": "experimental-highres",
        "source": "worker-training",
        "local_path": "~/.sleap-rtc/models/centroid_b1d8e3a2/",
        "checkpoint_path": "~/.sleap-rtc/models/centroid_b1d8e3a2/best.ckpt",
        "on_worker": True,
        "metrics": {
            "final_val_loss": 0.0312,
            "epochs_completed": 3,
            "train_loss": 0.0289
        },
        "training_hyperparameters": {
            "learning_rate": 0.002,
            "batch_size": 2,
            "optimizer": "Adam",
            "max_epochs": 5
        },
        "tags": ["mouse", "experimental", "high-resolution"],
        "notes": "Testing with 2x resolution images"
    })

    # Model 5: Bottom-up model
    registry.register({
        "id": "e4c7f1a9",
        "model_type": "bottomup",
        "alias": "multi-animal-v1",
        "source": "worker-pull",
        "local_path": "~/.sleap-rtc/models/bottomup_e4c7f1a9/",
        "checkpoint_path": "~/.sleap-rtc/models/bottomup_e4c7f1a9/best.ckpt",
        "on_worker": True,
        "worker_path": "models/bottomup_e4c7f1a9/",
        "metrics": {
            "final_val_loss": 0.0567,
            "epochs_completed": 8,
            "train_loss": 0.0512,
            "val_accuracy": 0.9421
        },
        "training_hyperparameters": {
            "learning_rate": 0.001,
            "batch_size": 6,
            "optimizer": "Adam",
            "max_epochs": 12
        },
        "tags": ["mouse", "multi-animal", "social-behavior"],
        "notes": "For tracking multiple animals in social behavior experiments"
    })

    # Model 6: Quick test model
    registry.register({
        "id": "3a9b2f5d",
        "model_type": "centroid",
        "source": "local-import",
        "local_path": "~/test-models/quick-test/",
        "checkpoint_path": "~/test-models/quick-test/best.ckpt",
        "on_worker": False,
        "tags": ["test", "development"]
    })

    # Model 7: Transferred from client
    registry.register({
        "id": "f2e8c1d4",
        "model_type": "topdown",
        "alias": "pretrained-human",
        "source": "client-upload",
        "local_path": "~/.sleap-rtc/models/topdown_f2e8c1d4/",
        "checkpoint_path": "~/.sleap-rtc/models/topdown_f2e8c1d4/best.ckpt",
        "on_worker": True,
        "worker_path": "models/topdown_f2e8c1d4/",
        "tags": ["human", "pretrained", "transfer-learning"],
        "notes": "Pre-trained model uploaded from external source"
    })

    print(f"✓ Added {len(registry.list())} demo models")
    print(f"Registry location: {registry.registry_path}")


if __name__ == "__main__":
    populate_demo_data()
