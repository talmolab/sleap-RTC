"""Unit tests for ClientModelRegistry."""

import json
import pytest
from pathlib import Path
from sleap_rtc.client.client_model_registry import ClientModelRegistry


@pytest.fixture
def temp_registry(tmp_path):
    """Create a temporary registry for testing."""
    registry_path = tmp_path / "manifest.json"
    return ClientModelRegistry(registry_path=registry_path)


class TestRegistryInitialization:
    """Test registry initialization scenarios."""

    def test_fresh_initialization(self, temp_registry):
        """Test creating a new registry from scratch."""
        assert temp_registry.registry_path.exists()
        assert temp_registry._data["version"] == "1.0"
        assert temp_registry._data["models"] == {}
        assert temp_registry._data["aliases"] == {}

    def test_load_existing_registry(self, tmp_path):
        """Test loading an existing valid registry."""
        # Create registry with data
        registry_path = tmp_path / "manifest.json"
        registry_data = {
            "version": "1.0",
            "models": {"abc123de": {"id": "abc123de", "model_type": "centroid"}},
            "aliases": {}
        }
        with open(registry_path, "w") as f:
            json.dump(registry_data, f)

        # Load it
        registry = ClientModelRegistry(registry_path=registry_path)
        assert len(registry._data["models"]) == 1
        assert "abc123de" in registry._data["models"]

    def test_corrupted_registry_recovery(self, tmp_path):
        """Test recovery from corrupted JSON."""
        registry_path = tmp_path / "manifest.json"

        # Write invalid JSON
        with open(registry_path, "w") as f:
            f.write("{invalid json content")

        # Should recover gracefully
        registry = ClientModelRegistry(registry_path=registry_path)
        assert registry._data["version"] == "1.0"
        assert registry._data["models"] == {}

        # Check backup was created
        backups = list(tmp_path.glob("manifest.json.backup.*"))
        assert len(backups) == 1

    def test_directory_creation(self, tmp_path):
        """Test that registry directory is created if missing."""
        registry_path = tmp_path / "new_dir" / "manifest.json"
        registry = ClientModelRegistry(registry_path=registry_path)

        assert registry.models_dir.exists()
        assert registry.registry_path.exists()


class TestCRUDOperations:
    """Test create, read, update, delete operations."""

    def test_register_model(self, temp_registry):
        """Test registering a new model."""
        model_info = {
            "id": "a3f5e8c9",
            "model_type": "centroid",
            "alias": "test-model",
            "source": "local-import",
            "local_path": "~/.sleap-rtc/models/centroid_a3f5e8c9/",
            "checkpoint_path": "~/.sleap-rtc/models/centroid_a3f5e8c9/best.ckpt"
        }

        model_id = temp_registry.register(model_info)
        assert model_id == "a3f5e8c9"
        assert temp_registry.exists("a3f5e8c9")

        # Check alias was registered
        assert temp_registry._data["aliases"]["test-model"] == "a3f5e8c9"

    def test_register_without_alias(self, temp_registry):
        """Test registering a model without an alias."""
        model_info = {"id": "a3f5e8c9", "model_type": "centroid"}
        model_id = temp_registry.register(model_info)

        assert model_id == "a3f5e8c9"
        assert temp_registry.exists("a3f5e8c9")
        assert len(temp_registry._data["aliases"]) == 0

    def test_register_missing_required_fields(self, temp_registry):
        """Test that registering without required fields raises ValueError."""
        with pytest.raises(ValueError, match="must contain 'id' and 'model_type'"):
            temp_registry.register({"id": "test123"})

        with pytest.raises(ValueError, match="must contain 'id' and 'model_type'"):
            temp_registry.register({"model_type": "centroid"})

    def test_register_duplicate_updates(self, temp_registry):
        """Test that re-registering a model updates the entry."""
        model_info = {"id": "a3f5e8c9", "model_type": "centroid"}
        temp_registry.register(model_info)

        # Re-register with additional info
        model_info["notes"] = "Updated notes"
        temp_registry.register(model_info)

        retrieved = temp_registry.get("a3f5e8c9")
        assert retrieved["notes"] == "Updated notes"

    def test_get_model(self, temp_registry):
        """Test retrieving a model by ID."""
        model_info = {"id": "a3f5e8c9", "model_type": "centroid"}
        temp_registry.register(model_info)

        retrieved = temp_registry.get("a3f5e8c9")
        assert retrieved is not None
        assert retrieved["model_type"] == "centroid"
        assert "imported_at" in retrieved or "downloaded_at" in retrieved

    def test_get_nonexistent_model(self, temp_registry):
        """Test retrieving a model that doesn't exist."""
        retrieved = temp_registry.get("nonexistent")
        assert retrieved is None

    def test_update_model(self, temp_registry):
        """Test updating model metadata."""
        model_info = {"id": "a3f5e8c9", "model_type": "centroid"}
        temp_registry.register(model_info)

        temp_registry.update("a3f5e8c9", {"notes": "Test notes", "on_worker": True})

        updated = temp_registry.get("a3f5e8c9")
        assert updated["notes"] == "Test notes"
        assert updated["on_worker"] is True

    def test_update_nonexistent_model(self, temp_registry):
        """Test updating a model that doesn't exist raises KeyError."""
        with pytest.raises(KeyError, match="not found in registry"):
            temp_registry.update("nonexistent", {"notes": "test"})

    def test_update_alias(self, temp_registry):
        """Test updating a model's alias."""
        model_info = {"id": "a3f5e8c9", "model_type": "centroid", "alias": "old-name"}
        temp_registry.register(model_info)

        # Update to new alias
        temp_registry.update("a3f5e8c9", {"alias": "new-name"})

        # Old alias should be removed
        assert "old-name" not in temp_registry._data["aliases"]
        # New alias should be mapped
        assert temp_registry._data["aliases"]["new-name"] == "a3f5e8c9"

    def test_delete_model(self, temp_registry):
        """Test deleting a model."""
        model_info = {"id": "a3f5e8c9", "model_type": "centroid", "alias": "test"}
        temp_registry.register(model_info)

        temp_registry.delete("a3f5e8c9")

        assert not temp_registry.exists("a3f5e8c9")
        assert "test" not in temp_registry._data["aliases"]

    def test_delete_nonexistent_model(self, temp_registry):
        """Test deleting a model that doesn't exist raises KeyError."""
        with pytest.raises(KeyError, match="not found in registry"):
            temp_registry.delete("nonexistent")

    def test_delete_model_with_files(self, temp_registry, tmp_path):
        """Test deleting a model with file deletion."""
        # Create a fake model directory
        model_dir = tmp_path / "test_model"
        model_dir.mkdir()
        (model_dir / "best.ckpt").write_text("fake checkpoint")

        model_info = {
            "id": "a3f5e8c9",
            "model_type": "centroid",
            "local_path": str(model_dir)
        }
        temp_registry.register(model_info)

        # Delete with file removal
        temp_registry.delete("a3f5e8c9", delete_files=True)

        assert not temp_registry.exists("a3f5e8c9")
        assert not model_dir.exists()

    def test_exists(self, temp_registry):
        """Test checking if a model exists."""
        assert not temp_registry.exists("a3f5e8c9")

        model_info = {"id": "a3f5e8c9", "model_type": "centroid"}
        temp_registry.register(model_info)

        assert temp_registry.exists("a3f5e8c9")


class TestFiltering:
    """Test list operations with filters."""

    def test_list_all_models(self, temp_registry):
        """Test listing all models."""
        temp_registry.register({"id": "model1", "model_type": "centroid"})
        temp_registry.register({"id": "model2", "model_type": "topdown"})

        models = temp_registry.list()
        assert len(models) == 2

    def test_filter_by_type(self, temp_registry):
        """Test filtering models by type."""
        temp_registry.register({"id": "model1", "model_type": "centroid"})
        temp_registry.register({"id": "model2", "model_type": "topdown"})
        temp_registry.register({"id": "model3", "model_type": "centroid"})

        centroid_models = temp_registry.list(filters={"model_type": "centroid"})
        assert len(centroid_models) == 2
        assert all(m["model_type"] == "centroid" for m in centroid_models)

    def test_filter_by_source(self, temp_registry):
        """Test filtering models by source."""
        temp_registry.register({"id": "model1", "model_type": "centroid", "source": "local-import"})
        temp_registry.register({"id": "model2", "model_type": "centroid", "source": "worker-training"})

        local_models = temp_registry.list(filters={"source": "local-import"})
        assert len(local_models) == 1
        assert local_models[0]["id"] == "model1"

    def test_filter_by_location(self, temp_registry):
        """Test filtering by worker location."""
        temp_registry.register({"id": "model1", "model_type": "centroid", "on_worker": False})
        temp_registry.register({"id": "model2", "model_type": "centroid", "on_worker": True})

        local_only = temp_registry.list(filters={"location": "local-only"})
        assert len(local_only) == 1
        assert local_only[0]["id"] == "model1"

    def test_filter_by_alias_presence(self, temp_registry):
        """Test filtering models that have aliases."""
        temp_registry.register({"id": "model1", "model_type": "centroid", "alias": "named"})
        temp_registry.register({"id": "model2", "model_type": "centroid"})

        with_aliases = temp_registry.list(filters={"has_alias": True})
        assert len(with_aliases) == 1
        assert with_aliases[0]["id"] == "model1"

    def test_list_sorting(self, temp_registry):
        """Test that models are sorted by timestamp (newest first)."""
        import time

        temp_registry.register({"id": "model1", "model_type": "centroid"})
        time.sleep(0.01)  # Small delay to ensure different timestamps
        temp_registry.register({"id": "model2", "model_type": "centroid"})
        time.sleep(0.01)
        temp_registry.register({"id": "model3", "model_type": "centroid"})

        models = temp_registry.list()
        # Newest should be first
        assert models[0]["id"] == "model3"
        assert models[2]["id"] == "model1"


class TestHelperMethods:
    """Test helper and utility methods."""

    def test_get_all_models(self, temp_registry):
        """Test retrieving all models as a dictionary."""
        temp_registry.register({"id": "model1", "model_type": "centroid"})
        temp_registry.register({"id": "model2", "model_type": "topdown"})

        all_models = temp_registry.get_all_models()
        assert len(all_models) == 2
        assert "model1" in all_models
        assert "model2" in all_models

    def test_get_all_aliases(self, temp_registry):
        """Test retrieving all alias mappings."""
        temp_registry.register({"id": "model1", "model_type": "centroid", "alias": "alias1"})
        temp_registry.register({"id": "model2", "model_type": "topdown", "alias": "alias2"})

        all_aliases = temp_registry.get_all_aliases()
        assert len(all_aliases) == 2
        assert all_aliases["alias1"] == "model1"
        assert all_aliases["alias2"] == "model2"

    def test_expand_path(self):
        """Test path expansion with tilde."""
        expanded = ClientModelRegistry._expand_path("~/test/path")
        assert "~" not in str(expanded)
        assert expanded.is_absolute()


class TestAtomicOperations:
    """Test atomic write operations and crash safety."""

    def test_atomic_write_creates_temp_file(self, temp_registry):
        """Test that save operation uses temporary file."""
        model_info = {"id": "test", "model_type": "centroid"}
        temp_registry.register(model_info)

        # Registry should exist, temp file should be cleaned up
        assert temp_registry.registry_path.exists()
        temp_file = temp_registry.registry_path.with_suffix(".json.tmp")
        assert not temp_file.exists()

    def test_registry_readable_json(self, temp_registry):
        """Test that saved registry is valid JSON."""
        model_info = {"id": "test", "model_type": "centroid"}
        temp_registry.register(model_info)

        # Read the file directly and ensure it's valid JSON
        with open(temp_registry.registry_path, "r") as f:
            data = json.load(f)

        assert data["version"] == "1.0"
        assert "test" in data["models"]

    def test_registry_persistence(self, tmp_path):
        """Test that registry persists across instances."""
        registry_path = tmp_path / "manifest.json"

        # Create first instance and add data
        registry1 = ClientModelRegistry(registry_path=registry_path)
        registry1.register({"id": "persist", "model_type": "centroid", "alias": "test"})

        # Create second instance and verify data persists
        registry2 = ClientModelRegistry(registry_path=registry_path)
        assert registry2.exists("persist")
        assert "test" in registry2.get_all_aliases()


class TestMetadata:
    """Test model metadata handling."""

    def test_timestamp_added_on_register(self, temp_registry):
        """Test that timestamp is added during registration."""
        model_info = {"id": "test", "model_type": "centroid", "source": "local-import"}
        temp_registry.register(model_info)

        retrieved = temp_registry.get("test")
        assert "imported_at" in retrieved

    def test_default_fields_set(self, temp_registry):
        """Test that default fields are set if not provided."""
        model_info = {"id": "test", "model_type": "centroid"}
        temp_registry.register(model_info)

        retrieved = temp_registry.get("test")
        assert retrieved["on_worker"] is False
        assert retrieved["worker_last_seen"] is None
        assert retrieved["worker_path"] is None

    def test_optional_metadata_preserved(self, temp_registry):
        """Test that optional metadata fields are preserved."""
        model_info = {
            "id": "test",
            "model_type": "centroid",
            "metrics": {"loss": 0.0234},
            "training_hyperparameters": {"lr": 0.001},
            "tags": ["production", "validated"],
            "notes": "Best model"
        }
        temp_registry.register(model_info)

        retrieved = temp_registry.get("test")
        assert retrieved["metrics"]["loss"] == 0.0234
        assert retrieved["training_hyperparameters"]["lr"] == 0.001
        assert "production" in retrieved["tags"]
        assert retrieved["notes"] == "Best model"
