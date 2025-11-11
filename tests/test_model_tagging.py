"""Tests for model tagging and metadata commands - Step 5."""

import pytest
from pathlib import Path
from sleap_rtc.client.client_model_registry import ClientModelRegistry


@pytest.fixture
def temp_registry(tmp_path):
    """Create a temporary registry with test models."""
    registry_path = tmp_path / "manifest.json"
    registry = ClientModelRegistry(registry_path=registry_path)

    # Register test models
    registry.register({
        "id": "aaaa1111",
        "model_type": "centroid",
        "source": "local-import",
        "local_path": str(tmp_path / "models" / "centroid_aaaa1111"),
        "checkpoint_path": str(tmp_path / "models" / "centroid_aaaa1111" / "best.ckpt"),
    })

    registry.register({
        "id": "bbbb2222",
        "model_type": "centered_instance",
        "source": "local-import",
        "local_path": str(tmp_path / "models" / "centered_instance_bbbb2222"),
        "checkpoint_path": str(tmp_path / "models" / "centered_instance_bbbb2222" / "best.ckpt"),
    })

    # Set initial alias on first model
    registry.set_alias("aaaa1111", "test-model", force=True)

    return registry


class TestModelMetadata:
    """Test metadata tracking in registry."""

    def test_notes_default(self, temp_registry):
        """Test that notes field defaults to empty string."""
        model = temp_registry.get("aaaa1111")
        assert "notes" in model
        assert model["notes"] == ""

    def test_tags_default(self, temp_registry):
        """Test that tags field defaults to empty list."""
        model = temp_registry.get("aaaa1111")
        assert "tags" in model
        assert model["tags"] == []

    def test_update_notes(self, temp_registry):
        """Test updating model notes."""
        temp_registry.update("aaaa1111", {"notes": "Test notes"})
        model = temp_registry.get("aaaa1111")
        assert model["notes"] == "Test notes"

    def test_update_tags(self, temp_registry):
        """Test updating model tags."""
        temp_registry.update("aaaa1111", {"tags": ["validated", "production"]})
        model = temp_registry.get("aaaa1111")
        assert set(model["tags"]) == {"validated", "production"}

    def test_update_notes_and_tags(self, temp_registry):
        """Test updating both notes and tags."""
        temp_registry.update("aaaa1111", {
            "notes": "Production model",
            "tags": ["validated", "v1.0"]
        })
        model = temp_registry.get("aaaa1111")
        assert model["notes"] == "Production model"
        assert set(model["tags"]) == {"validated", "v1.0"}

    def test_clear_notes(self, temp_registry):
        """Test clearing notes."""
        temp_registry.update("aaaa1111", {"notes": "Old notes"})
        temp_registry.update("aaaa1111", {"notes": ""})
        model = temp_registry.get("aaaa1111")
        assert model["notes"] == ""

    def test_clear_tags(self, temp_registry):
        """Test clearing tags."""
        temp_registry.update("aaaa1111", {"tags": ["tag1", "tag2"]})
        temp_registry.update("aaaa1111", {"tags": []})
        model = temp_registry.get("aaaa1111")
        assert model["tags"] == []


class TestAliasManagement:
    """Test alias management operations."""

    def test_set_alias_new(self, temp_registry):
        """Test setting a new alias."""
        success = temp_registry.set_alias("bbbb2222", "new-alias", force=True)
        assert success
        assert temp_registry.resolve("new-alias") == "bbbb2222"

    def test_update_alias(self, temp_registry):
        """Test updating an existing alias."""
        temp_registry.set_alias("aaaa1111", "updated-alias", force=True)
        assert temp_registry.resolve("updated-alias") == "aaaa1111"
        assert temp_registry.resolve("test-model") is None

    def test_alias_collision(self, temp_registry):
        """Test alias collision handling."""
        # Try to assign existing alias to different model without force
        success = temp_registry.set_alias("bbbb2222", "test-model", force=False)
        assert not success
        # Original assignment should be unchanged
        assert temp_registry.resolve("test-model") == "aaaa1111"

    def test_alias_force_overwrite(self, temp_registry):
        """Test forcing alias reassignment."""
        success = temp_registry.set_alias("bbbb2222", "test-model", force=True)
        assert success
        assert temp_registry.resolve("test-model") == "bbbb2222"

    def test_resolve_by_alias(self, temp_registry):
        """Test resolving model by alias."""
        model_id = temp_registry.resolve("test-model")
        assert model_id == "aaaa1111"

    def test_resolve_by_id(self, temp_registry):
        """Test resolving model by ID (returns same ID)."""
        model_id = temp_registry.resolve("aaaa1111")
        assert model_id == "aaaa1111"

    def test_resolve_nonexistent(self, temp_registry):
        """Test resolving nonexistent identifier."""
        result = temp_registry.resolve("nonexistent")
        assert result is None
