"""Model registry for tracking trained SLEAP models and checkpoints.

This module provides a ModelRegistry class for managing trained model metadata,
enabling model identification, checkpoint recovery, and lifecycle tracking.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

try:
    import yaml
    YAML_AVAILABLE = True
    YAMLError = yaml.YAMLError
except ImportError:
    YAML_AVAILABLE = False
    # Create placeholder exception for environments without PyYAML
    class YAMLError(Exception):
        pass
    logging.warning("PyYAML not available. Registry will only support JSON format.")


class ModelRegistry:
    """Registry for tracking trained models and their metadata.

    The registry maintains a persistent record of all trained models, including
    their configuration, status, checkpoints, and metrics. It supports both JSON
    and YAML formats for the registry file.

    Attributes:
        registry_dir: Directory containing the registry file.
        registry_format: Format of the registry file ('json' or 'yaml').
        _data: In-memory registry data.
    """

    def __init__(
        self,
        registry_dir: Path = Path("models/.registry"),
        registry_format: str = "json"
    ):
        """Initialize the model registry.

        Args:
            registry_dir: Directory to store the registry file. Defaults to models/.registry
            registry_format: Format for the registry file ('json' or 'yaml'). Defaults to 'json'.
                            If 'yaml' is specified but PyYAML is not available, falls back to JSON.
        """
        self.registry_dir = Path(registry_dir)

        # Validate format and availability
        if registry_format == "yaml" and not YAML_AVAILABLE:
            logging.warning("YAML format requested but PyYAML not installed. Using JSON instead.")
            registry_format = "json"

        self.registry_format = registry_format
        self._data = None

        # Create registry directory if it doesn't exist
        self.registry_dir.mkdir(parents=True, exist_ok=True)

        # Load existing registry or create new one
        self._load_registry()

    @property
    def registry_path(self) -> Path:
        """Get the path to the registry file based on current format.

        Returns:
            Path to the registry file (manifest.json or manifest.yaml).
        """
        extension = "yaml" if self.registry_format == "yaml" else "json"
        return self.registry_dir / f"manifest.{extension}"

    def _detect_existing_format(self) -> Optional[str]:
        """Detect the format of existing registry file.

        Checks for both JSON and YAML registry files. If both exist,
        returns the format of the most recently modified file.

        Returns:
            'json', 'yaml', or None if no registry exists.
        """
        json_path = self.registry_dir / "manifest.json"
        yaml_path = self.registry_dir / "manifest.yaml"

        json_exists = json_path.exists()
        yaml_exists = yaml_path.exists() and YAML_AVAILABLE

        if not json_exists and not yaml_exists:
            return None

        if json_exists and not yaml_exists:
            return "json"

        if yaml_exists and not json_exists:
            return "yaml"

        # Both exist - use most recently modified
        json_mtime = json_path.stat().st_mtime
        yaml_mtime = yaml_path.stat().st_mtime

        if yaml_mtime > json_mtime:
            logging.info(f"Multiple registry formats found. Using YAML (newer: {yaml_path})")
            return "yaml"
        else:
            logging.info(f"Multiple registry formats found. Using JSON (newer: {json_path})")
            return "json"

    def _load_registry(self) -> None:
        """Load the registry from disk or create a new one.

        Attempts to load an existing registry file, auto-detecting the format.
        If no registry exists, creates a new empty registry. Handles corruption
        by backing up the corrupted file and creating a fresh registry.
        """
        # Auto-detect format from existing files
        detected_format = self._detect_existing_format()

        if detected_format:
            # Use detected format instead of initialized format
            self.registry_format = detected_format
            registry_path = self.registry_path

            try:
                with open(registry_path, 'r') as f:
                    if self.registry_format == "yaml":
                        self._data = yaml.safe_load(f)
                    else:
                        self._data = json.load(f)

                logging.info(f"Loaded registry from {registry_path}")

                # Validate registry structure
                if not isinstance(self._data, dict):
                    raise ValueError("Registry root must be a dictionary")
                if 'models' not in self._data:
                    self._data['models'] = {}
                if 'interrupted' not in self._data:
                    self._data['interrupted'] = []

            except (json.JSONDecodeError, YAMLError, ValueError) as e:
                logging.error(f"Registry file corrupted: {e}")

                # Backup corrupted file
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = registry_path.with_suffix(f".corrupted.{timestamp}{registry_path.suffix}")
                shutil.copy2(registry_path, backup_path)
                logging.info(f"Backed up corrupted registry to {backup_path}")

                # Create fresh registry
                self._data = self._create_empty_registry()
                self._save_registry()
        else:
            # No existing registry - create new one
            logging.info("No existing registry found. Creating new registry.")
            self._data = self._create_empty_registry()
            self._save_registry()

    def _create_empty_registry(self) -> dict:
        """Create an empty registry structure.

        Returns:
            Dictionary with version, models, and interrupted fields.
        """
        return {
            "version": "1.0",
            "models": {},
            "interrupted": []
        }

    def _save_registry(self) -> None:
        """Save the registry to disk using atomic write operations.

        Writes to a temporary file first, then atomically renames it to the
        final location to prevent corruption from partial writes.
        """
        registry_path = self.registry_path
        temp_path = registry_path.with_suffix(f".tmp{registry_path.suffix}")

        try:
            with open(temp_path, 'w') as f:
                if self.registry_format == "yaml":
                    yaml.dump(self._data, f, default_flow_style=False, sort_keys=False)
                else:
                    json.dump(self._data, f, indent=2, sort_keys=False)

            # Atomic rename
            temp_path.replace(registry_path)
            logging.debug(f"Registry saved to {registry_path}")

        except Exception as e:
            logging.error(f"Failed to save registry: {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise

    def generate_model_id(
        self,
        config: dict,
        labels_path: str,
        run_name: str
    ) -> str:
        """Generate a deterministic 8-character model ID.

        Creates a unique identifier based on the training configuration,
        dataset, and run name. Uses SHA256 hashing for determinism.

        Args:
            config: Training configuration dictionary (YAML config loaded as dict).
            labels_path: Path to the labels file used for training.
            run_name: Run name for this training job (typically timestamp-based).

        Returns:
            8-character hexadecimal model ID.
        """
        # Extract model type from head configs
        head_configs = config.get('model_config', {}).get('head_configs', {})
        model_types = [k for k, v in head_configs.items() if v is not None]

        # Compute MD5 hash of labels file for data versioning
        try:
            with open(labels_path, 'rb') as f:
                labels_md5 = hashlib.md5(f.read()).hexdigest()
        except (FileNotFoundError, IOError) as e:
            logging.warning(f"Could not hash labels file {labels_path}: {e}. Using filename instead.")
            labels_md5 = hashlib.md5(labels_path.encode()).hexdigest()

        # Create hash input dictionary
        hash_input = {
            "model_type": model_types,
            "backbone": config.get('model_config', {}).get('backbone_config'),
            "run_name": run_name,
            "labels_md5": labels_md5
        }

        # Generate SHA256 hash
        content = json.dumps(hash_input, sort_keys=True)
        full_hash = hashlib.sha256(content.encode()).hexdigest()

        # Return first 8 characters
        return full_hash[:8]

    def _ensure_unique_id(self, base_id: str) -> str:
        """Ensure model ID is unique by appending suffix if needed.

        Args:
            base_id: Base model ID to check.

        Returns:
            Unique model ID (may have -2, -3, etc. suffix).
        """
        if base_id not in self._data['models']:
            return base_id

        # Collision detected - append numeric suffix
        logging.warning(f"Model ID collision detected for {base_id}")

        suffix = 2
        while f"{base_id}-{suffix}" in self._data['models']:
            suffix += 1

        unique_id = f"{base_id}-{suffix}"
        logging.info(f"Using unique ID: {unique_id}")
        return unique_id

    def register(self, model_info: dict) -> str:
        """Register a new model in the registry.

        Args:
            model_info: Dictionary containing model metadata:
                - id: Model ID (will be made unique if collision detected)
                - full_hash: Full 64-character SHA256 hash
                - run_name: Training run name
                - model_type: Type of model (centroid, centered_instance, etc.)
                - training_job_hash: Hash of training package
                - status: Current status (training, completed, interrupted, failed)
                - checkpoint_path: Path to checkpoint file (relative to models dir)
                - config_path: Path to config file (relative to models dir)
                - created_at: ISO 8601 timestamp
                - metadata: Additional metadata dict

        Returns:
            Final model ID (may differ from input if collision occurred).
        """
        base_id = model_info['id']
        unique_id = self._ensure_unique_id(base_id)

        # Update model info with unique ID
        model_info['id'] = unique_id

        # Add to registry
        self._data['models'][unique_id] = model_info

        # Save to disk
        self._save_registry()

        logging.info(f"Registered model {unique_id} with status {model_info.get('status')}")
        return unique_id

    def get(self, model_id: str) -> Optional[Dict]:
        """Retrieve model metadata by ID.

        Args:
            model_id: 8-character model ID.

        Returns:
            Model metadata dictionary, or None if not found.
        """
        return self._data['models'].get(model_id)

    def list(self, filters: Optional[Dict] = None) -> List[Dict]:
        """List models with optional filtering.

        Args:
            filters: Optional dictionary with filter criteria:
                - status: Filter by status (training, completed, interrupted, failed)
                - model_type: Filter by model type (centroid, centered_instance, etc.)

        Returns:
            List of model metadata dictionaries, sorted by creation time (newest first).
        """
        models = list(self._data['models'].values())

        # Apply filters if provided
        if filters:
            if 'status' in filters:
                models = [m for m in models if m.get('status') == filters['status']]

            if 'model_type' in filters:
                models = [m for m in models if m.get('model_type') == filters['model_type']]

        # Sort by creation timestamp (newest first)
        models.sort(
            key=lambda m: m.get('created_at', ''),
            reverse=True
        )

        return models

    def mark_completed(self, model_id: str, metrics: dict) -> None:
        """Mark a model as completed and store final metrics.

        Args:
            model_id: Model ID to update.
            metrics: Dictionary of final metrics (validation loss, epochs, etc.).

        Raises:
            KeyError: If model ID not found in registry.
        """
        if model_id not in self._data['models']:
            raise KeyError(f"Model {model_id} not found in registry")

        model = self._data['models'][model_id]
        model['status'] = 'completed'
        model['completed_at'] = datetime.now().isoformat()
        model['metrics'] = metrics

        # Remove from interrupted list if present
        if model_id in self._data['interrupted']:
            self._data['interrupted'].remove(model_id)

        self._save_registry()
        logging.info(f"Marked model {model_id} as completed")

    def mark_interrupted(
        self,
        model_id: str,
        checkpoint_path: str,
        epoch: int
    ) -> None:
        """Mark a model as interrupted with resumable state.

        Args:
            model_id: Model ID to update.
            checkpoint_path: Path to last saved checkpoint.
            epoch: Last completed epoch number.

        Raises:
            KeyError: If model ID not found in registry.
        """
        if model_id not in self._data['models']:
            raise KeyError(f"Model {model_id} not found in registry")

        model = self._data['models'][model_id]
        model['status'] = 'interrupted'
        model['checkpoint_path'] = checkpoint_path
        model['last_epoch'] = epoch
        model['interrupted_at'] = datetime.now().isoformat()

        # Add to interrupted list if not already present
        if model_id not in self._data['interrupted']:
            self._data['interrupted'].append(model_id)

        self._save_registry()
        logging.info(f"Marked model {model_id} as interrupted at epoch {epoch}")

    def get_interrupted(self) -> List[Dict]:
        """Get all interrupted jobs that can be resumed.

        Returns:
            List of model metadata for interrupted jobs.
        """
        interrupted_ids = self._data.get('interrupted', [])
        return [
            self._data['models'][model_id]
            for model_id in interrupted_ids
            if model_id in self._data['models']
        ]

    def get_checkpoint_path(self, model_id: str) -> Path:
        """Resolve model ID to checkpoint file path.

        Args:
            model_id: Model ID to look up.

        Returns:
            Absolute path to the checkpoint file.

        Raises:
            KeyError: If model ID not found in registry.
        """
        if model_id not in self._data['models']:
            raise KeyError(f"Model {model_id} not found in registry")

        model = self._data['models'][model_id]
        checkpoint_rel_path = model.get('checkpoint_path')

        if not checkpoint_rel_path:
            raise ValueError(f"Model {model_id} has no checkpoint path")

        # Resolve to absolute path
        checkpoint_path = Path(checkpoint_rel_path)

        # Warn if checkpoint doesn't exist
        if not checkpoint_path.exists():
            logging.warning(f"Checkpoint file not found: {checkpoint_path}")

        return checkpoint_path
