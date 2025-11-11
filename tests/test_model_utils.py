"""Tests for model utility functions."""

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
)


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

    def test_detect_from_model_type_field(self, tmp_path):
        """Test detection from model_type field."""
        config = {"model_type": "centroid"}
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "centroid"

    def test_detect_from_model_dict(self, tmp_path):
        """Test detection from model.type field."""
        config = {"model": {"type": "topdown"}}
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "topdown"

    def test_detect_from_backbone(self, tmp_path):
        """Test detection from backbone type."""
        config = {"backbone": {"type": "centroid_resnet"}}
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "centroid"

    def test_no_config_file(self, tmp_path):
        """Test when no config file exists."""
        model_type = detect_model_type(tmp_path)
        assert model_type is None

    def test_alternative_config_names(self, tmp_path):
        """Test detection with alternative config filenames."""
        config = {"model_type": "bottomup"}

        # Test config.yaml
        config_path = tmp_path / "config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        model_type = detect_model_type(tmp_path)
        assert model_type == "bottomup"

    def test_invalid_config(self, tmp_path):
        """Test with invalid YAML."""
        config_path = tmp_path / "training_config.yaml"
        with open(config_path, 'w') as f:
            f.write("invalid: yaml: content: {{")

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
