"""Tests for model utility functions."""

import json
import pytest
from pathlib import Path
import yaml
import tempfile
from sleap_rtc.client.model_utils import (
    find_checkpoint_files,
    detect_model_type,
    calculate_model_size,
    validate_checkpoint_files,
    generate_model_id_from_config,
    format_size,
    resolve_model_path,
    resolve_model_paths,
)
from sleap_rtc.client.client_model_registry import ClientModelRegistry


class TestFindCheckpointFiles:
    """Test checkpoint file discovery."""

    def test_find_ckpt_files(self, tmp_path):
        """Test finding .ckpt files."""
        # Create test checkpoint files
        (tmp_path / "model.ckpt").touch()
        (tmp_path / "best.ckpt").touch()
        (tmp_path / "other.txt").touch()

        checkpoints = find_checkpoint_files(tmp_path)
        assert len(checkpoints) == 2
        assert all(p.suffix == ".ckpt" for p in checkpoints)

    def test_find_h5_files(self, tmp_path):
        """Test finding .h5 files."""
        (tmp_path / "model.h5").touch()
        (tmp_path / "weights.h5").touch()

        checkpoints = find_checkpoint_files(tmp_path)
        assert len(checkpoints) == 2
        assert all(p.suffix == ".h5" for p in checkpoints)

    def test_find_mixed_formats(self, tmp_path):
        """Test finding multiple checkpoint formats."""
        (tmp_path / "model.ckpt").touch()
        (tmp_path / "weights.h5").touch()
        (tmp_path / "state.pth").touch()
        (tmp_path / "model.pt").touch()

        checkpoints = find_checkpoint_files(tmp_path)
        assert len(checkpoints) == 4

    def test_find_in_subdirectories(self, tmp_path):
        """Test finding checkpoints in subdirectories."""
        subdir = tmp_path / "checkpoints"
        subdir.mkdir()
        (subdir / "best.ckpt").touch()
        (tmp_path / "model.ckpt").touch()

        checkpoints = find_checkpoint_files(tmp_path)
        assert len(checkpoints) == 2

    def test_no_checkpoint_files(self, tmp_path):
        """Test when no checkpoint files exist."""
        (tmp_path / "readme.txt").touch()

        checkpoints = find_checkpoint_files(tmp_path)
        assert len(checkpoints) == 0

    def test_nonexistent_path(self):
        """Test with nonexistent path."""
        with pytest.raises(ValueError, match="does not exist"):
            find_checkpoint_files(Path("/nonexistent/path"))

    def test_file_instead_of_directory(self, tmp_path):
        """Test with file instead of directory."""
        file_path = tmp_path / "file.txt"
        file_path.touch()

        with pytest.raises(ValueError, match="not a directory"):
            find_checkpoint_files(file_path)


class TestDetectModelType:
    """Test model type detection."""

    def test_detect_from_head_configs_centroid(self, tmp_path):
        """Test detection from SLEAP-NN head_configs for centroid."""
        config = {
            "model_config": {
                "head_configs": {
                    "single_instance": None,
                    "centroid": {
                        "confmaps": {
                            "sigma": 2.5,
                            "output_stride": 2,
                        }
                    },
                    "centered_instance": None,
                    "bottomup": None,
                }
            }
        }
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "centroid"

    def test_detect_from_head_configs_centered_instance(self, tmp_path):
        """Test detection from SLEAP-NN head_configs for centered_instance."""
        config = {
            "model_config": {
                "head_configs": {
                    "single_instance": None,
                    "centroid": None,
                    "centered_instance": {
                        "confmaps": {
                            "sigma": 2.5,
                            "output_stride": 4,
                        }
                    },
                    "bottomup": None,
                }
            }
        }
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "centered_instance"

    def test_detect_from_head_configs_bottomup(self, tmp_path):
        """Test detection from SLEAP-NN head_configs for bottomup."""
        config = {
            "model_config": {
                "head_configs": {
                    "single_instance": None,
                    "centroid": None,
                    "centered_instance": None,
                    "bottomup": {
                        "confmaps": {"sigma": 2.5},
                        "pafs": {"sigma": 10.0},
                    },
                }
            }
        }
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "bottomup"

    def test_detect_from_head_configs_single_instance(self, tmp_path):
        """Test detection from SLEAP-NN head_configs for single_instance."""
        config = {
            "model_config": {
                "head_configs": {
                    "single_instance": {
                        "confmaps": {"sigma": 2.5}
                    },
                    "centroid": None,
                    "centered_instance": None,
                    "bottomup": None,
                }
            }
        }
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "single_instance"

    def test_detect_from_model_type_field_fallback(self, tmp_path):
        """Test detection from model_type field (fallback)."""
        config = {"model_type": "centroid"}
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "centroid"

    def test_detect_from_model_dict_fallback(self, tmp_path):
        """Test detection from model.type field (fallback)."""
        config = {"model": {"type": "centered_instance"}}
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "centered_instance"

    def test_no_config_file(self, tmp_path):
        """Test when no config file exists."""
        model_type = detect_model_type(tmp_path)
        assert model_type is None

    def test_alternative_config_names(self, tmp_path):
        """Test detection with alternative config filenames."""
        config = {
            "model_config": {
                "head_configs": {
                    "bottomup": {"confmaps": {"sigma": 2.5}},
                    "centroid": None,
                }
            }
        }

        # Test config.yaml
        config_path = tmp_path / "config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "bottomup"

    def test_named_config_file(self, tmp_path):
        """Test detection from model-type-named config file."""
        config = {
            "model_config": {
                "head_configs": {
                    "centroid": {"confmaps": {"sigma": 2.5}},
                    "bottomup": None,
                }
            }
        }

        # Test centroid.yaml
        config_path = tmp_path / "centroid.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "centroid"

    def test_invalid_config(self, tmp_path):
        """Test with invalid YAML."""
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            f.write("invalid: yaml: content: {{")

        model_type = detect_model_type(tmp_path)
        assert model_type is None

    def test_detect_from_json_head_configs_centroid(self, tmp_path):
        """Test detection from JSON config with head_configs for centroid."""
        config = {
            "model_config": {
                "head_configs": {
                    "single_instance": None,
                    "centroid": {
                        "confmaps": {
                            "sigma": 2.5,
                            "output_stride": 2,
                        }
                    },
                    "centered_instance": None,
                    "bottomup": None,
                }
            }
        }
        config_path = tmp_path / "training_config.json"
        with open(config_path, 'w') as f:
            json.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "centroid"

    def test_detect_from_json_head_configs_centered_instance(self, tmp_path):
        """Test detection from JSON config with head_configs for centered_instance."""
        config = {
            "model_config": {
                "head_configs": {
                    "single_instance": None,
                    "centroid": None,
                    "centered_instance": {
                        "confmaps": {
                            "sigma": 2.5,
                            "output_stride": 4,
                        }
                    },
                    "bottomup": None,
                }
            }
        }
        config_path = tmp_path / "config.json"
        with open(config_path, 'w') as f:
            json.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "centered_instance"

    def test_detect_from_json_model_type_fallback(self, tmp_path):
        """Test detection from JSON with model_type field (older SLEAP format)."""
        config = {"model_type": "bottomup"}
        config_path = tmp_path / "training_config.json"
        with open(config_path, 'w') as f:
            json.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "bottomup"

    def test_invalid_json_config(self, tmp_path):
        """Test with invalid JSON."""
        config_path = tmp_path / "training_config.json"
        with open(config_path, 'w') as f:
            f.write("{invalid json content")

        model_type = detect_model_type(tmp_path)
        assert model_type is None


class TestCalculateModelSize:
    """Test model size calculation."""

    def test_single_checkpoint(self, tmp_path):
        """Test size calculation for single checkpoint."""
        checkpoint = tmp_path / "model.ckpt"
        checkpoint.write_bytes(b"x" * 1024)  # 1 KB

        size = calculate_model_size(tmp_path)
        assert size == 1024

    def test_multiple_checkpoints(self, tmp_path):
        """Test size calculation for multiple checkpoints."""
        (tmp_path / "model1.ckpt").write_bytes(b"x" * 1024)
        (tmp_path / "model2.ckpt").write_bytes(b"x" * 2048)

        size = calculate_model_size(tmp_path)
        assert size == 3072

    def test_no_checkpoints(self, tmp_path):
        """Test size when no checkpoints exist."""
        size = calculate_model_size(tmp_path)
        assert size == 0


class TestValidateCheckpointFiles:
    """Test checkpoint file validation."""

    def test_valid_checkpoints(self, tmp_path):
        """Test validation of valid checkpoints."""
        (tmp_path / "model.ckpt").write_bytes(b"valid checkpoint data")

        assert validate_checkpoint_files(tmp_path) is True

    def test_no_checkpoints(self, tmp_path):
        """Test validation when no checkpoints exist."""
        assert validate_checkpoint_files(tmp_path) is False

    def test_readable_checkpoint(self, tmp_path):
        """Test that checkpoints are readable."""
        checkpoint = tmp_path / "model.ckpt"
        checkpoint.write_bytes(b"x" * 2048)

        assert validate_checkpoint_files(tmp_path) is True


class TestGenerateModelId:
    """Test model ID generation."""

    def test_generate_from_config(self, tmp_path):
        """Test ID generation from config hash."""
        config = {"model_type": "centroid", "learning_rate": 0.001}
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_id = generate_model_id_from_config(tmp_path)
        assert len(model_id) == 8
        assert all(c in "0123456789abcdef" for c in model_id)

    def test_deterministic_id(self, tmp_path):
        """Test that same config produces same ID."""
        config = {"model_type": "centroid"}
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        id1 = generate_model_id_from_config(tmp_path)
        id2 = generate_model_id_from_config(tmp_path)
        assert id1 == id2

    def test_generate_from_json_config(self, tmp_path):
        """Test ID generation from JSON config hash."""
        config = {"model_type": "centroid", "learning_rate": 0.001}
        config_path = tmp_path / "training_config.json"
        with open(config_path, 'w') as f:
            json.dump(config, f)

        model_id = generate_model_id_from_config(tmp_path)
        assert len(model_id) == 8
        assert all(c in "0123456789abcdef" for c in model_id)

    def test_json_deterministic_id(self, tmp_path):
        """Test that same JSON config produces same ID."""
        config = {"model_type": "bottomup"}
        config_path = tmp_path / "config.json"
        with open(config_path, 'w') as f:
            json.dump(config, f)

        id1 = generate_model_id_from_config(tmp_path)
        id2 = generate_model_id_from_config(tmp_path)
        assert id1 == id2

    def test_random_id_without_config(self, tmp_path):
        """Test random ID generation when no config exists."""
        model_id = generate_model_id_from_config(tmp_path)
        assert len(model_id) == 8
        assert all(c in "0123456789abcdef" for c in model_id)


class TestFormatSize:
    """Test size formatting."""

    def test_bytes(self):
        """Test formatting bytes."""
        assert format_size(512) == "512.0 B"

    def test_kilobytes(self):
        """Test formatting kilobytes."""
        assert format_size(1024) == "1.0 KB"
        assert format_size(1536) == "1.5 KB"

    def test_megabytes(self):
        """Test formatting megabytes."""
        assert format_size(1024 * 1024) == "1.0 MB"
        assert format_size(int(1.5 * 1024 * 1024)) == "1.5 MB"

    def test_gigabytes(self):
        """Test formatting gigabytes."""
        assert format_size(1024 * 1024 * 1024) == "1.0 GB"
        assert format_size(int(2.5 * 1024 * 1024 * 1024)) == "2.5 GB"


class TestResolveModelPath:
    """Test model path resolution."""

    def test_resolve_direct_path(self, tmp_path):
        """Test resolving a direct filesystem path."""
        model_dir = tmp_path / "my_model"
        model_dir.mkdir()
        (model_dir / "model.ckpt").touch()

        resolved, source = resolve_model_path(str(model_dir))
        assert resolved == model_dir.resolve()
        assert source == "path"

    def test_resolve_relative_path(self, tmp_path):
        """Test resolving a relative filesystem path."""
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            model_dir = tmp_path / "my_model"
            model_dir.mkdir()
            (model_dir / "model.ckpt").touch()

            resolved, source = resolve_model_path("my_model")
            assert resolved == model_dir.resolve()
            assert source == "path"
        finally:
            os.chdir(original_cwd)

    def test_resolve_by_model_id(self, tmp_path):
        """Test resolving a model by ID through registry."""
        # Create a model directory
        model_dir = tmp_path / "models" / "centroid_a3b4c5d6"
        model_dir.mkdir(parents=True)
        (model_dir / "model.ckpt").touch()

        # Create registry
        registry_path = tmp_path / "manifest.json"
        registry = ClientModelRegistry(registry_path=registry_path)

        # Register model
        registry.register({
            "id": "a3b4c5d6",
            "model_type": "centroid",
            "local_path": str(model_dir),
            "checkpoint_path": str(model_dir / "model.ckpt"),
        })

        # Resolve by ID
        resolved, source = resolve_model_path("a3b4c5d6", registry_path=registry_path)
        assert resolved == model_dir
        assert source == "id"

    def test_resolve_by_alias(self, tmp_path):
        """Test resolving a model by alias through registry."""
        # Create a model directory
        model_dir = tmp_path / "models" / "centroid_a3b4c5d6"
        model_dir.mkdir(parents=True)
        (model_dir / "model.ckpt").touch()

        # Create registry
        registry_path = tmp_path / "manifest.json"
        registry = ClientModelRegistry(registry_path=registry_path)

        # Register model with alias
        registry.register({
            "id": "a3b4c5d6",
            "model_type": "centroid",
            "local_path": str(model_dir),
            "checkpoint_path": str(model_dir / "model.ckpt"),
        })
        registry.set_alias("a3b4c5d6", "production-v1", force=True)

        # Resolve by alias
        resolved, source = resolve_model_path("production-v1", registry_path=registry_path)
        assert resolved == model_dir
        assert source == "alias"

    def test_resolve_nonexistent_path(self, tmp_path):
        """Test resolving a nonexistent path."""
        resolved, source = resolve_model_path("/nonexistent/path")
        assert resolved is None
        assert source is None

    def test_resolve_nonexistent_id(self, tmp_path):
        """Test resolving a nonexistent model ID."""
        registry_path = tmp_path / "manifest.json"
        ClientModelRegistry(registry_path=registry_path)  # Create empty registry

        resolved, source = resolve_model_path("12345678", registry_path=registry_path)
        assert resolved is None
        assert source is None

    def test_resolve_nonexistent_alias(self, tmp_path):
        """Test resolving a nonexistent alias."""
        registry_path = tmp_path / "manifest.json"
        ClientModelRegistry(registry_path=registry_path)  # Create empty registry

        resolved, source = resolve_model_path("nonexistent-alias", registry_path=registry_path)
        assert resolved is None
        assert source is None

    def test_resolve_registry_path_missing(self, tmp_path):
        """Test resolving when registry path doesn't exist."""
        model_dir = tmp_path / "models" / "centroid_a3b4c5d6"
        model_dir.mkdir(parents=True)

        # Register model in registry but model path is deleted
        registry_path = tmp_path / "manifest.json"
        registry = ClientModelRegistry(registry_path=registry_path)
        registry.register({
            "id": "a3b4c5d6",
            "model_type": "centroid",
            "local_path": str(tmp_path / "deleted_model"),
            "checkpoint_path": str(tmp_path / "deleted_model" / "model.ckpt"),
        })

        # Try to resolve
        resolved, source = resolve_model_path("a3b4c5d6", registry_path=registry_path)
        assert resolved is None
        assert source is None

    def test_resolve_path_priority_over_registry(self, tmp_path):
        """Test that filesystem paths have priority over registry."""
        import os
        original_cwd = os.getcwd()
        try:
            # Change to tmp_path so relative path will work
            os.chdir(tmp_path)

            # Create an actual directory with the same name as a potential alias
            model_dir = tmp_path / "production-v1"
            model_dir.mkdir()
            (model_dir / "model.ckpt").touch()

            # Also create a registry entry with the same name as alias
            registry_path = tmp_path / "manifest.json"
            registry = ClientModelRegistry(registry_path=registry_path)
            other_model = tmp_path / "other_model"
            other_model.mkdir()
            registry.register({
                "id": "a3b4c5d6",
                "model_type": "centroid",
                "local_path": str(other_model),
                "checkpoint_path": str(other_model / "model.ckpt"),
            })
            registry.set_alias("a3b4c5d6", "production-v1", force=True)

            # Resolve - should prefer the filesystem path over the alias
            resolved, source = resolve_model_path("production-v1", registry_path=registry_path)
            assert resolved == model_dir.resolve()
            assert source == "path"
        finally:
            os.chdir(original_cwd)


class TestResolveModelPaths:
    """Test resolving multiple model paths."""

    def test_resolve_multiple_paths(self, tmp_path):
        """Test resolving multiple filesystem paths."""
        model1 = tmp_path / "model1"
        model2 = tmp_path / "model2"
        model1.mkdir()
        model2.mkdir()

        resolved, errors = resolve_model_paths([str(model1), str(model2)])
        assert len(resolved) == 2
        assert len(errors) == 0
        assert model1.resolve() in resolved
        assert model2.resolve() in resolved

    def test_resolve_mixed_identifiers(self, tmp_path):
        """Test resolving mix of paths, IDs, and aliases."""
        # Create filesystem model
        model_dir = tmp_path / "fs_model"
        model_dir.mkdir()

        # Create registry with ID and alias
        registry_path = tmp_path / "manifest.json"
        registry = ClientModelRegistry(registry_path=registry_path)

        id_model = tmp_path / "models" / "centroid_a3b4c5d6"
        id_model.mkdir(parents=True)
        registry.register({
            "id": "a3b4c5d6",
            "model_type": "centroid",
            "local_path": str(id_model),
            "checkpoint_path": str(id_model / "model.ckpt"),
        })

        alias_model = tmp_path / "models" / "centroid_b7c8d9e0"
        alias_model.mkdir(parents=True)
        registry.register({
            "id": "b7c8d9e0",
            "model_type": "centroid",
            "local_path": str(alias_model),
            "checkpoint_path": str(alias_model / "model.ckpt"),
        })
        registry.set_alias("b7c8d9e0", "prod-v1", force=True)

        # Resolve all three
        identifiers = [str(model_dir), "a3b4c5d6", "prod-v1"]
        resolved, errors = resolve_model_paths(identifiers, registry_path=registry_path)

        assert len(resolved) == 3
        assert len(errors) == 0
        assert model_dir.resolve() in resolved
        assert id_model in resolved
        assert alias_model in resolved

    def test_resolve_with_errors(self, tmp_path):
        """Test resolving when some identifiers fail."""
        model_dir = tmp_path / "valid_model"
        model_dir.mkdir()

        identifiers = [str(model_dir), "nonexistent-id", "nonexistent-alias"]
        resolved, errors = resolve_model_paths(identifiers)

        assert len(resolved) == 1
        assert len(errors) == 2
        assert model_dir.resolve() in resolved
        assert any("nonexistent-id" in err[0] for err in errors)
        assert any("nonexistent-alias" in err[0] for err in errors)

    def test_resolve_empty_list(self):
        """Test resolving an empty list."""
        resolved, errors = resolve_model_paths([])
        assert len(resolved) == 0
        assert len(errors) == 0

    def test_resolve_all_failures(self, tmp_path):
        """Test when all resolutions fail."""
        identifiers = ["nonexistent1", "nonexistent2", "nonexistent3"]
        resolved, errors = resolve_model_paths(identifiers)

        assert len(resolved) == 0
        assert len(errors) == 3
