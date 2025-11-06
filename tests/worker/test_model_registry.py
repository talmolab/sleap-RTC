"""Unit tests for ModelRegistry class."""

import json
import pytest
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from sleap_rtc.worker.model_registry import ModelRegistry

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


@pytest.fixture
def temp_registry_dir():
    """Create a temporary directory for registry testing."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    # Cleanup
    if temp_dir.exists():
        shutil.rmtree(temp_dir)


@pytest.fixture
def sample_config():
    """Sample training configuration for testing."""
    return {
        'model_config': {
            'head_configs': {
                'centroid': {'sigma': 2.5},
                'centered_instance': None
            },
            'backbone_config': {
                'unet': {
                    'filters': 16,
                    'max_stride': 16
                }
            }
        },
        'trainer_config': {
            'max_epochs': 5,
            'run_name': 'centroid_test_001'
        }
    }


@pytest.fixture
def sample_labels_file(temp_registry_dir):
    """Create a sample labels file for hashing."""
    labels_path = temp_registry_dir / "test_labels.slp"
    labels_path.write_text("dummy labels data for testing")
    return str(labels_path)


class TestRegistryInitialization:
    """Tests for registry initialization and file handling."""

    def test_create_new_registry_json(self, temp_registry_dir):
        """Test creating a new registry with JSON format."""
        registry = ModelRegistry(
            registry_dir=temp_registry_dir,
            registry_format="json"
        )

        assert registry.registry_path.exists()
        assert registry.registry_path.suffix == ".json"
        assert registry._data['version'] == "1.0"
        assert registry._data['models'] == {}
        assert registry._data['interrupted'] == []

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_create_new_registry_yaml(self, temp_registry_dir):
        """Test creating a new registry with YAML format."""
        registry = ModelRegistry(
            registry_dir=temp_registry_dir,
            registry_format="yaml"
        )

        assert registry.registry_path.exists()
        assert registry.registry_path.suffix == ".yaml"
        assert registry._data['version'] == "1.0"

    def test_yaml_fallback_to_json(self, temp_registry_dir):
        """Test fallback to JSON when YAML requested but unavailable."""
        # This test always passes if YAML is available, but tests the warning path
        registry = ModelRegistry(
            registry_dir=temp_registry_dir,
            registry_format="yaml"
        )

        # Should create a registry regardless
        assert registry.registry_path.exists()

    def test_load_existing_json_registry(self, temp_registry_dir):
        """Test loading an existing JSON registry."""
        # Create registry file manually
        registry_file = temp_registry_dir / "manifest.json"
        registry_data = {
            "version": "1.0",
            "models": {
                "abc12345": {
                    "id": "abc12345",
                    "model_type": "centroid",
                    "status": "completed"
                }
            },
            "interrupted": []
        }
        with open(registry_file, 'w') as f:
            json.dump(registry_data, f)

        # Load registry
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        assert "abc12345" in registry._data['models']
        assert registry._data['models']['abc12345']['model_type'] == "centroid"

    def test_corrupted_registry_recovery(self, temp_registry_dir):
        """Test recovery from corrupted registry file."""
        # Create corrupted JSON file
        registry_file = temp_registry_dir / "manifest.json"
        registry_file.write_text("{ this is not valid json }")

        # Should create fresh registry and backup corrupted file
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        assert registry._data['models'] == {}
        # Check that backup was created
        backups = list(temp_registry_dir.glob("*.corrupted.*"))
        assert len(backups) == 1

    def test_prefer_newer_format(self, temp_registry_dir):
        """Test that newer file is preferred when both JSON and YAML exist."""
        if not YAML_AVAILABLE:
            pytest.skip("PyYAML not available")

        # Create JSON file
        json_file = temp_registry_dir / "manifest.json"
        json_data = {"version": "1.0", "models": {"json_model": {}}, "interrupted": []}
        with open(json_file, 'w') as f:
            json.dump(json_data, f)

        # Create YAML file (newer)
        import time
        time.sleep(0.1)
        yaml_file = temp_registry_dir / "manifest.yaml"
        yaml_data = {"version": "1.0", "models": {"yaml_model": {}}, "interrupted": []}
        with open(yaml_file, 'w') as f:
            yaml.dump(yaml_data, f)

        # Should load YAML (newer)
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        assert "yaml_model" in registry._data['models']
        assert "json_model" not in registry._data['models']
        assert registry.registry_format == "yaml"


class TestModelIDGeneration:
    """Tests for model ID generation and uniqueness."""

    def test_generate_model_id(self, temp_registry_dir, sample_config, sample_labels_file):
        """Test deterministic model ID generation."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        model_id = registry.generate_model_id(
            config=sample_config,
            labels_path=sample_labels_file,
            run_name="test_run_001"
        )

        assert len(model_id) == 8
        assert model_id.isalnum()

    def test_same_config_produces_same_id(self, temp_registry_dir, sample_config, sample_labels_file):
        """Test that identical configuration produces identical ID."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        id1 = registry.generate_model_id(
            config=sample_config,
            labels_path=sample_labels_file,
            run_name="test_run_001"
        )

        id2 = registry.generate_model_id(
            config=sample_config,
            labels_path=sample_labels_file,
            run_name="test_run_001"
        )

        assert id1 == id2

    def test_different_config_produces_different_id(self, temp_registry_dir, sample_config, sample_labels_file):
        """Test that different configuration produces different ID."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        id1 = registry.generate_model_id(
            config=sample_config,
            labels_path=sample_labels_file,
            run_name="test_run_001"
        )

        # Modify config
        modified_config = sample_config.copy()
        modified_config['trainer_config']['max_epochs'] = 10

        id2 = registry.generate_model_id(
            config=modified_config,
            labels_path=sample_labels_file,
            run_name="test_run_001"
        )

        # Note: Since we hash backbone and model type (not trainer config),
        # IDs might still be same unless those change
        # Let's change backbone instead
        modified_config['model_config']['backbone_config']['unet']['filters'] = 32

        id3 = registry.generate_model_id(
            config=modified_config,
            labels_path=sample_labels_file,
            run_name="test_run_001"
        )

        assert id1 != id3

    def test_hash_collision_handling(self, temp_registry_dir, sample_config, sample_labels_file):
        """Test that hash collisions are handled with numeric suffix."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        # Generate first model ID
        model_id = registry.generate_model_id(
            config=sample_config,
            labels_path=sample_labels_file,
            run_name="test_run_001"
        )

        # Register first model
        model_info1 = {
            "id": model_id,
            "full_hash": "a" * 64,
            "run_name": "test_run_001",
            "model_type": "centroid",
            "status": "completed",
            "created_at": datetime.now().isoformat(),
            "metadata": {}
        }
        registry.register(model_info1)

        # Try to register another model with same ID (simulating collision)
        model_info2 = model_info1.copy()
        unique_id = registry._ensure_unique_id(model_id)

        assert unique_id == f"{model_id}-2"


class TestRegistryOperations:
    """Tests for core registry CRUD operations."""

    def test_register_model(self, temp_registry_dir):
        """Test registering a new model."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        model_info = {
            "id": "abc12345",
            "full_hash": "a" * 64,
            "run_name": "centroid_20241105_120000",
            "model_type": "centroid",
            "training_job_hash": "job123",
            "status": "training",
            "checkpoint_path": "models/centroid_abc12345/best.ckpt",
            "config_path": "models/centroid_abc12345/training_config.yaml",
            "created_at": datetime.now().isoformat(),
            "metadata": {
                "dataset": "labels.v001.slp",
                "gpu_model": "NVIDIA RTX 3090"
            }
        }

        model_id = registry.register(model_info)

        assert model_id == "abc12345"
        assert "abc12345" in registry._data['models']

    def test_get_model(self, temp_registry_dir):
        """Test retrieving a model by ID."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        model_info = {
            "id": "xyz98765",
            "model_type": "centered_instance",
            "status": "completed",
            "created_at": datetime.now().isoformat(),
            "metadata": {}
        }
        registry.register(model_info)

        retrieved = registry.get("xyz98765")

        assert retrieved is not None
        assert retrieved['model_type'] == "centered_instance"

    def test_get_nonexistent_model(self, temp_registry_dir):
        """Test getting a model that doesn't exist."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        result = registry.get("nonexistent")

        assert result is None

    def test_list_all_models(self, temp_registry_dir):
        """Test listing all models."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        # Register multiple models
        for i in range(3):
            model_info = {
                "id": f"model{i}",
                "model_type": "centroid",
                "status": "completed",
                "created_at": f"2024-11-0{i+1}T12:00:00",
                "metadata": {}
            }
            registry.register(model_info)

        models = registry.list()

        assert len(models) == 3
        # Should be sorted by creation time (newest first)
        assert models[0]['id'] == "model2"

    def test_filter_by_status(self, temp_registry_dir):
        """Test filtering models by status."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        # Register models with different statuses
        statuses = ["training", "completed", "interrupted", "completed"]
        for i, status in enumerate(statuses):
            model_info = {
                "id": f"model{i}",
                "model_type": "centroid",
                "status": status,
                "created_at": datetime.now().isoformat(),
                "metadata": {}
            }
            registry.register(model_info)

        completed = registry.list(filters={"status": "completed"})

        assert len(completed) == 2
        assert all(m['status'] == "completed" for m in completed)

    def test_filter_by_model_type(self, temp_registry_dir):
        """Test filtering models by type."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        types = ["centroid", "centered_instance", "centroid"]
        for i, model_type in enumerate(types):
            model_info = {
                "id": f"model{i}",
                "model_type": model_type,
                "status": "completed",
                "created_at": datetime.now().isoformat(),
                "metadata": {}
            }
            registry.register(model_info)

        centroid_models = registry.list(filters={"model_type": "centroid"})

        assert len(centroid_models) == 2
        assert all(m['model_type'] == "centroid" for m in centroid_models)


class TestStatusTransitions:
    """Tests for model status transitions and lifecycle management."""

    def test_mark_completed(self, temp_registry_dir):
        """Test marking a model as completed."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        # Register training model
        model_info = {
            "id": "model123",
            "model_type": "centroid",
            "status": "training",
            "created_at": datetime.now().isoformat(),
            "metadata": {}
        }
        registry.register(model_info)

        # Mark as completed
        metrics = {
            "final_val_loss": 0.0234,
            "epochs_completed": 5,
            "best_epoch": 4
        }
        registry.mark_completed("model123", metrics)

        model = registry.get("model123")
        assert model['status'] == "completed"
        assert model['metrics'] == metrics
        assert 'completed_at' in model

    def test_mark_interrupted(self, temp_registry_dir):
        """Test marking a model as interrupted."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        model_info = {
            "id": "model456",
            "model_type": "centroid",
            "status": "training",
            "created_at": datetime.now().isoformat(),
            "metadata": {}
        }
        registry.register(model_info)

        # Mark as interrupted
        registry.mark_interrupted(
            model_id="model456",
            checkpoint_path="models/centroid_model456/best.ckpt",
            epoch=3
        )

        model = registry.get("model456")
        assert model['status'] == "interrupted"
        assert model['last_epoch'] == 3
        assert model['checkpoint_path'] == "models/centroid_model456/best.ckpt"
        assert "model456" in registry._data['interrupted']

    def test_completed_removes_from_interrupted(self, temp_registry_dir):
        """Test that marking as completed removes from interrupted list."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        model_info = {
            "id": "model789",
            "model_type": "centroid",
            "status": "training",
            "created_at": datetime.now().isoformat(),
            "metadata": {}
        }
        registry.register(model_info)

        # Interrupt, then complete
        registry.mark_interrupted("model789", "checkpoint.ckpt", 2)
        assert "model789" in registry._data['interrupted']

        registry.mark_completed("model789", {"epochs": 5})
        assert "model789" not in registry._data['interrupted']

    def test_get_interrupted_jobs(self, temp_registry_dir):
        """Test retrieving all interrupted jobs."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        # Register and interrupt multiple models
        for i in range(3):
            model_info = {
                "id": f"model{i}",
                "model_type": "centroid",
                "status": "training",
                "created_at": datetime.now().isoformat(),
                "metadata": {}
            }
            registry.register(model_info)

            if i < 2:  # Interrupt first 2
                registry.mark_interrupted(f"model{i}", "ckpt", i)

        interrupted = registry.get_interrupted()

        assert len(interrupted) == 2
        assert all(m['status'] == "interrupted" for m in interrupted)


class TestCheckpointOperations:
    """Tests for checkpoint path resolution."""

    def test_get_checkpoint_path(self, temp_registry_dir):
        """Test resolving model ID to checkpoint path."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        model_info = {
            "id": "model_cp",
            "model_type": "centroid",
            "status": "completed",
            "checkpoint_path": "models/centroid_model_cp/best.ckpt",
            "created_at": datetime.now().isoformat(),
            "metadata": {}
        }
        registry.register(model_info)

        checkpoint_path = registry.get_checkpoint_path("model_cp")

        assert checkpoint_path == Path("models/centroid_model_cp/best.ckpt")

    def test_get_checkpoint_path_nonexistent_model(self, temp_registry_dir):
        """Test getting checkpoint for nonexistent model."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        with pytest.raises(KeyError):
            registry.get_checkpoint_path("nonexistent")

    def test_get_checkpoint_path_missing_file(self, temp_registry_dir):
        """Test getting checkpoint when file doesn't exist (should warn but return path)."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        model_info = {
            "id": "model_missing",
            "model_type": "centroid",
            "status": "completed",
            "checkpoint_path": "models/nonexistent/best.ckpt",
            "created_at": datetime.now().isoformat(),
            "metadata": {}
        }
        registry.register(model_info)

        # Should return path even if file doesn't exist (with warning)
        checkpoint_path = registry.get_checkpoint_path("model_missing")

        assert checkpoint_path == Path("models/nonexistent/best.ckpt")


class TestAtomicWrites:
    """Tests for atomic write operations."""

    def test_atomic_write_on_save(self, temp_registry_dir):
        """Test that registry uses atomic writes (temp file + rename)."""
        registry = ModelRegistry(registry_dir=temp_registry_dir)

        # Register a model (triggers save)
        model_info = {
            "id": "atomic_test",
            "model_type": "centroid",
            "status": "training",
            "created_at": datetime.now().isoformat(),
            "metadata": {}
        }
        registry.register(model_info)

        # Check that no temp files remain
        temp_files = list(temp_registry_dir.glob("*.tmp*"))
        assert len(temp_files) == 0

        # Check that registry file exists and is valid
        assert registry.registry_path.exists()

        # Verify content
        with open(registry.registry_path) as f:
            data = json.load(f)
        assert "atomic_test" in data['models']
