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


class TestAliasValidation:
    """Test alias validation logic."""

    def test_valid_aliases(self, temp_registry):
        """Test that valid aliases are accepted."""
        valid_aliases = [
            "good-model",
            "my_model",
            "Model123",
            "prod-v1",
            "a",  # min length
            "x" * 64,  # max length
        ]

        for alias in valid_aliases:
            is_valid, error = temp_registry._validate_alias(alias)
            assert is_valid, f"'{alias}' should be valid but got error: {error}"

    def test_invalid_aliases(self, temp_registry):
        """Test that invalid aliases are rejected."""
        invalid_cases = [
            ("", "empty"),
            ("   ", "whitespace"),
            ("my model", "spaces"),
            ("model@prod", "special char @"),
            ("my/model", "special char /"),
            ("-leading", "leading dash"),
            ("trailing-", "trailing dash"),
            ("_leading", "leading underscore"),
            ("trailing_", "trailing underscore"),
            ("x" * 65, "too long"),
            ("all", "reserved: all"),
            ("latest", "reserved: latest"),
            ("none", "reserved: none"),
            ("null", "reserved: null"),
            ("a3f5e8c9", "looks like model ID"),
        ]

        for alias, reason in invalid_cases:
            is_valid, error = temp_registry._validate_alias(alias)
            assert not is_valid, f"'{alias}' should be invalid ({reason})"
            assert error is not None

    def test_case_sensitivity(self, temp_registry):
        """Test that aliases are case-sensitive."""
        is_valid1, _ = temp_registry._validate_alias("Good-Model")
        is_valid2, _ = temp_registry._validate_alias("good-model")
        is_valid3, _ = temp_registry._validate_alias("GOOD-MODEL")

        assert is_valid1 and is_valid2 and is_valid3
        # These are all valid and should be treated as different

    def test_sanitize_alias(self, temp_registry):
        """Test alias sanitization."""
        test_cases = [
            ("my model", "my-model"),
            ("Good Model 2024", "Good-Model-2024"),
            ("model@prod!", "modelprod"),
            ("  -trim-  ", "trim"),
            ("x" * 70, "x" * 64),  # truncation
        ]

        for input_text, expected in test_cases:
            result = temp_registry._sanitize_alias(input_text)
            assert result == expected, f"Sanitize '{input_text}' expected '{expected}', got '{result}'"


class TestAliasAssignment:
    """Test setting and updating aliases."""

    def test_set_alias_basic(self, temp_registry):
        """Test basic alias assignment."""
        temp_registry.register({"id": "model1", "model_type": "centroid"})

        success = temp_registry.set_alias("model1", "my-alias")
        assert success

        # Check alias mapping exists
        assert "my-alias" in temp_registry._data["aliases"]
        assert temp_registry._data["aliases"]["my-alias"] == "model1"

        # Check model has alias field
        model = temp_registry.get("model1")
        assert model["alias"] == "my-alias"

    def test_set_alias_nonexistent_model(self, temp_registry):
        """Test setting alias for non-existent model raises error."""
        with pytest.raises(KeyError, match="not found in registry"):
            temp_registry.set_alias("nonexistent", "alias")

    def test_set_alias_invalid(self, temp_registry):
        """Test setting invalid alias raises error."""
        temp_registry.register({"id": "model1", "model_type": "centroid"})

        with pytest.raises(ValueError, match="Alias"):
            temp_registry.set_alias("model1", "invalid alias")  # has space

    def test_set_alias_collision_without_force(self, temp_registry):
        """Test alias collision without force flag."""
        temp_registry.register({"id": "model1", "model_type": "centroid"})
        temp_registry.register({"id": "model2", "model_type": "centroid"})

        # Set alias on first model
        temp_registry.set_alias("model1", "shared-alias")

        # Try to set same alias on second model (should fail)
        success = temp_registry.set_alias("model2", "shared-alias")
        assert not success

        # First model should still have the alias
        assert temp_registry._data["aliases"]["shared-alias"] == "model1"

    def test_set_alias_collision_with_force(self, temp_registry):
        """Test alias collision with force flag."""
        temp_registry.register({"id": "model1", "model_type": "centroid"})
        temp_registry.register({"id": "model2", "model_type": "centroid"})

        # Set alias on first model
        temp_registry.set_alias("model1", "shared-alias")

        # Force set same alias on second model
        success = temp_registry.set_alias("model2", "shared-alias", force=True)
        assert success

        # Second model should now have the alias
        assert temp_registry._data["aliases"]["shared-alias"] == "model2"

        # First model should not have alias anymore
        model1 = temp_registry.get("model1")
        assert model1["alias"] is None

    def test_rename_alias(self, temp_registry):
        """Test renaming a model's alias."""
        temp_registry.register({"id": "model1", "model_type": "centroid"})

        # Set initial alias
        temp_registry.set_alias("model1", "old-name")

        # Set new alias (should remove old one)
        temp_registry.set_alias("model1", "new-name")

        # Old alias should be gone
        assert "old-name" not in temp_registry._data["aliases"]

        # New alias should exist
        assert "new-name" in temp_registry._data["aliases"]
        assert temp_registry._data["aliases"]["new-name"] == "model1"

        # Model should have new alias
        model = temp_registry.get("model1")
        assert model["alias"] == "new-name"


class TestAliasRetrieval:
    """Test retrieving models by alias."""

    def test_get_by_alias(self, temp_registry):
        """Test retrieving model by alias."""
        temp_registry.register({"id": "model1", "model_type": "centroid", "alias": "test-alias"})

        model = temp_registry.get_by_alias("test-alias")
        assert model is not None
        assert model["id"] == "model1"

    def test_get_by_alias_not_found(self, temp_registry):
        """Test retrieving non-existent alias."""
        model = temp_registry.get_by_alias("nonexistent")
        assert model is None

    def test_resolve_model_id(self, temp_registry):
        """Test resolving a model ID."""
        temp_registry.register({"id": "model1", "model_type": "centroid"})

        resolved = temp_registry.resolve("model1")
        assert resolved == "model1"

    def test_resolve_alias(self, temp_registry):
        """Test resolving an alias to model ID."""
        temp_registry.register({"id": "model1", "model_type": "centroid", "alias": "my-alias"})

        resolved = temp_registry.resolve("my-alias")
        assert resolved == "model1"

    def test_resolve_priority(self, temp_registry):
        """Test that model ID takes priority over alias in resolution."""
        temp_registry.register({"id": "model1", "model_type": "centroid"})
        temp_registry.register({"id": "model2", "model_type": "centroid", "alias": "model1"})

        # "model1" is both a model ID and an alias
        # Should resolve to the model ID first
        resolved = temp_registry.resolve("model1")
        assert resolved == "model1"

    def test_resolve_not_found(self, temp_registry):
        """Test resolving non-existent identifier."""
        resolved = temp_registry.resolve("nonexistent")
        assert resolved is None


class TestAliasManagement:
    """Test alias management operations."""

    def test_remove_alias(self, temp_registry):
        """Test removing an alias."""
        temp_registry.register({"id": "model1", "model_type": "centroid", "alias": "test-alias"})

        success = temp_registry.remove_alias("test-alias")
        assert success

        # Alias should be gone
        assert "test-alias" not in temp_registry._data["aliases"]

        # Model should not have alias
        model = temp_registry.get("model1")
        assert model["alias"] is None

        # Model itself should still exist
        assert temp_registry.exists("model1")

    def test_remove_nonexistent_alias(self, temp_registry):
        """Test removing non-existent alias."""
        success = temp_registry.remove_alias("nonexistent")
        assert not success

    def test_list_aliases(self, temp_registry):
        """Test listing all aliases."""
        temp_registry.register({"id": "model1", "model_type": "centroid", "alias": "alias-c"})
        temp_registry.register({"id": "model2", "model_type": "centroid", "alias": "alias-a"})
        temp_registry.register({"id": "model3", "model_type": "centroid", "alias": "alias-b"})

        aliases = temp_registry.list_aliases(sort=True)

        # Should be sorted alphabetically
        alias_list = list(aliases.keys())
        assert alias_list == ["alias-a", "alias-b", "alias-c"]

        # Check mappings are correct
        assert aliases["alias-a"] == "model2"
        assert aliases["alias-b"] == "model3"
        assert aliases["alias-c"] == "model1"

    def test_list_aliases_unsorted(self, temp_registry):
        """Test listing aliases without sorting."""
        temp_registry.register({"id": "model1", "model_type": "centroid", "alias": "alias-z"})
        temp_registry.register({"id": "model2", "model_type": "centroid", "alias": "alias-a"})

        aliases = temp_registry.list_aliases(sort=False)

        # Should be a dict (order may vary)
        assert isinstance(aliases, dict)
        assert len(aliases) == 2

    def test_suggest_alias_basic(self, temp_registry):
        """Test basic alias suggestion."""
        suggestion = temp_registry.suggest_alias("my model")
        assert suggestion == "my-model"

        # Should be valid
        is_valid, _ = temp_registry._validate_alias(suggestion)
        assert is_valid

    def test_suggest_alias_with_model_type(self, temp_registry):
        """Test alias suggestion with model type."""
        suggestion = temp_registry.suggest_alias("mouse", model_type="centroid")
        assert suggestion == "centroid-mouse"

    def test_suggest_alias_uniqueness(self, temp_registry):
        """Test that suggestions are unique."""
        temp_registry.register({"id": "model1", "model_type": "centroid", "alias": "my-model"})

        # Should suggest versioned name
        suggestion = temp_registry.suggest_alias("my model")
        assert suggestion == "my-model-v1"

        # Add that too
        temp_registry.register({"id": "model2", "model_type": "centroid", "alias": "my-model-v1"})

        # Should suggest v2
        suggestion = temp_registry.suggest_alias("my model")
        assert suggestion == "my-model-v2"

    def test_suggest_alias_invalid_input(self, temp_registry):
        """Test alias suggestion with invalid input."""
        # Empty input should default to "model"
        suggestion = temp_registry.suggest_alias("")
        assert suggestion == "model"

        # Special characters should be sanitized
        suggestion = temp_registry.suggest_alias("@#$%")
        assert suggestion == "model"


class TestAliasPersistence:
    """Test alias persistence across registry instances."""

    def test_alias_persists(self, tmp_path):
        """Test that aliases persist when registry is reloaded."""
        registry_path = tmp_path / "manifest.json"

        # Create registry and set alias
        registry1 = ClientModelRegistry(registry_path=registry_path)
        registry1.register({"id": "model1", "model_type": "centroid"})
        registry1.set_alias("model1", "persistent-alias")

        # Create new instance (reload from disk)
        registry2 = ClientModelRegistry(registry_path=registry_path)

        # Alias should still exist
        assert "persistent-alias" in registry2.list_aliases()
        assert registry2.resolve("persistent-alias") == "model1"

        model = registry2.get_by_alias("persistent-alias")
        assert model is not None
        assert model["id"] == "model1"
