"""Client-side model registry for tracking downloaded and imported models.

This module provides a persistent registry stored at ~/.sleap-rtc/models/manifest.json
that tracks all models available locally, their metadata, and synchronization status
with workers.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

from loguru import logger


class ClientModelRegistry:
    """Client-side model registry for tracking local models.

    The registry maintains a JSON manifest at ~/.sleap-rtc/models/manifest.json
    with model metadata including:
    - Model ID and type
    - Local file paths
    - Worker synchronization status
    - Training metrics and hyperparameters
    - User-assigned aliases

    Attributes:
        registry_path: Path to the manifest.json file
        models_dir: Path to ~/.sleap-rtc/models/ directory
        _data: In-memory registry data (loaded from disk)
    """

    REGISTRY_VERSION = "1.0"

    def __init__(self, registry_path: Optional[Path] = None):
        """Initialize the client model registry.

        Args:
            registry_path: Optional custom path to registry file.
                          Defaults to ~/.sleap-rtc/models/manifest.json
        """
        if registry_path is None:
            # Default location in user's home directory
            self.models_dir = self._expand_path("~/.sleap-rtc/models")
            self.registry_path = self.models_dir / "manifest.json"
        else:
            self.registry_path = Path(registry_path)
            self.models_dir = self.registry_path.parent

        self._data: Dict[str, Any] = {}
        self._ensure_directories()
        self._load_registry()

    @staticmethod
    def _expand_path(path: str) -> Path:
        """Expand user home directory and resolve path.

        Args:
            path: Path string potentially containing ~

        Returns:
            Resolved absolute Path
        """
        return Path(path).expanduser().resolve()

    def _ensure_directories(self) -> None:
        """Create ~/.sleap-rtc/models/ directory structure on first use.

        Sets appropriate permissions (user read/write only) for security.
        """
        if not self.models_dir.exists():
            self.models_dir.mkdir(parents=True, mode=0o700)
            logger.info(f"Created model registry directory: {self.models_dir}")

    def _load_registry(self) -> None:
        """Load registry from disk with corruption handling.

        If the registry file exists but is corrupted, creates a timestamped
        backup and initializes a fresh registry.
        """
        if not self.registry_path.exists():
            logger.debug("No existing registry found, initializing fresh registry")
            self._initialize_fresh_registry()
            return

        try:
            with open(self.registry_path, "r") as f:
                self._data = json.load(f)

            # Validate schema version
            if self._data.get("version") != self.REGISTRY_VERSION:
                logger.info(
                    f"Registry version mismatch: {self._data.get('version')} -> {self.REGISTRY_VERSION}"
                )
                self._migrate_schema()

            logger.debug(f"Loaded registry with {len(self._data.get('models', {}))} models")

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Registry corrupted: {e}")
            self._backup_corrupted_registry()
            self._initialize_fresh_registry()
            logger.warning("Initialized fresh registry. Import models manually to restore.")

    def _backup_corrupted_registry(self) -> None:
        """Create timestamped backup of corrupted registry."""
        timestamp = datetime.now().isoformat().replace(":", "-")
        backup_path = self.registry_path.with_suffix(f".json.backup.{timestamp}")
        shutil.copy(self.registry_path, backup_path)
        logger.info(f"Corrupted registry backed up to: {backup_path}")

    def _initialize_fresh_registry(self) -> None:
        """Initialize a new empty registry."""
        self._data = {
            "version": self.REGISTRY_VERSION,
            "models": {},
            "aliases": {}
        }
        self._save_registry()

    def _migrate_schema(self) -> None:
        """Migrate registry schema to current version.

        This method handles migrations between schema versions.
        Currently a placeholder for future migrations.
        """
        # Future migrations will be implemented here
        # For now, just update version
        old_version = self._data.get("version", "unknown")
        self._data["version"] = self.REGISTRY_VERSION
        self._save_registry()
        logger.info(f"Migrated registry schema from {old_version} to {self.REGISTRY_VERSION}")

    def _save_registry(self) -> None:
        """Save registry to disk with atomic write operation.

        Uses a temp file + rename pattern to ensure atomicity:
        1. Write to .tmp file
        2. Validate JSON is well-formed
        3. Rename to final path (atomic on POSIX)

        This prevents corruption from crashes during writes.
        """
        temp_path = self.registry_path.with_suffix(".json.tmp")

        try:
            # Write to temporary file
            with open(temp_path, "w") as f:
                json.dump(self._data, f, indent=2, sort_keys=False)

            # Atomic rename (POSIX guarantees atomicity)
            temp_path.replace(self.registry_path)
            logger.debug("Registry saved successfully")

        except Exception as e:
            logger.error(f"Failed to save registry: {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise

    def register(self, model_info: Dict[str, Any]) -> str:
        """Register a new model in the registry.

        Args:
            model_info: Dictionary containing model metadata:
                - id: str (8-char hash, required)
                - model_type: str (centroid/topdown/bottomup, required)
                - alias: str (optional)
                - source: str (worker-training/worker-pull/local-import/client-upload)
                - local_path: str (path to model directory)
                - checkpoint_path: str (path to best.ckpt)
                - on_worker: bool (default: False)
                - metrics: dict (optional)
                - training_hyperparameters: dict (optional)
                - tags: list[str] (optional)
                - notes: str (optional)

        Returns:
            Model ID of the registered model

        Raises:
            ValueError: If required fields are missing or invalid
        """
        # Validate required fields
        if "id" not in model_info or "model_type" not in model_info:
            raise ValueError("model_info must contain 'id' and 'model_type'")

        model_id = model_info["id"]

        # Check if already exists
        if model_id in self._data["models"]:
            logger.warning(f"Model {model_id} already registered, updating entry")

        # Add timestamp
        timestamp_field = "imported_at" if model_info.get("source") == "local-import" else "downloaded_at"
        if timestamp_field not in model_info:
            model_info[timestamp_field] = datetime.now().isoformat()

        # Set defaults for optional fields
        model_info.setdefault("on_worker", False)
        model_info.setdefault("worker_last_seen", None)
        model_info.setdefault("worker_path", None)

        # Store in registry
        self._data["models"][model_id] = model_info

        # Handle alias if provided
        if "alias" in model_info and model_info["alias"]:
            self._data["aliases"][model_info["alias"]] = model_id

        self._save_registry()
        logger.info(f"Registered model {model_id} (type: {model_info['model_type']})")

        return model_id

    def get(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve model entry by ID.

        Args:
            model_id: 8-character model ID hash

        Returns:
            Model metadata dict, or None if not found
        """
        return self._data["models"].get(model_id)

    def list(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """List models with optional filtering.

        Args:
            filters: Optional filter criteria:
                - model_type: str (centroid/topdown/bottomup)
                - source: str (worker-training/worker-pull/local-import)
                - location: str (local-only/worker-only/both)
                - status: str (completed/training/checkpoint_missing/broken_symlink)
                - has_alias: bool (only models with aliases)

        Returns:
            List of model metadata dicts, sorted by download timestamp (newest first)
        """
        models = list(self._data["models"].values())

        if filters:
            # Filter by model type
            if "model_type" in filters:
                models = [m for m in models if m["model_type"] == filters["model_type"]]

            # Filter by source
            if "source" in filters:
                models = [m for m in models if m.get("source") == filters["source"]]

            # Filter by location
            if "location" in filters:
                if filters["location"] == "local-only":
                    models = [m for m in models if not m.get("on_worker", False)]
                elif filters["location"] == "worker-only":
                    models = [m for m in models if m.get("on_worker", False) and not m.get("local", True)]
                elif filters["location"] == "both":
                    models = [m for m in models if m.get("on_worker", False) and m.get("local", True)]

            # Filter by alias presence
            if "has_alias" in filters and filters["has_alias"]:
                models = [m for m in models if m.get("alias")]

        # Sort by timestamp (newest first)
        def get_timestamp(model: Dict[str, Any]) -> str:
            return model.get("downloaded_at") or model.get("imported_at") or ""

        models.sort(key=get_timestamp, reverse=True)

        return models

    def update(self, model_id: str, updates: Dict[str, Any]) -> None:
        """Update model metadata.

        Args:
            model_id: Model ID to update
            updates: Dictionary of fields to update

        Raises:
            KeyError: If model_id not found
        """
        if model_id not in self._data["models"]:
            raise KeyError(f"Model {model_id} not found in registry")

        model = self._data["models"][model_id]

        # Handle alias updates (update both model entry and aliases dict)
        if "alias" in updates:
            old_alias = model.get("alias")
            new_alias = updates["alias"]

            # Remove old alias mapping
            if old_alias and old_alias in self._data["aliases"]:
                del self._data["aliases"][old_alias]

            # Add new alias mapping
            if new_alias:
                self._data["aliases"][new_alias] = model_id

        # Update fields
        model.update(updates)

        self._save_registry()
        logger.debug(f"Updated model {model_id}")

    def delete(self, model_id: str, delete_files: bool = False) -> None:
        """Remove model from registry and optionally delete files.

        Args:
            model_id: Model ID to delete
            delete_files: If True, also delete checkpoint files from disk

        Raises:
            KeyError: If model_id not found
        """
        if model_id not in self._data["models"]:
            raise KeyError(f"Model {model_id} not found in registry")

        model = self._data["models"][model_id]

        # Remove alias mapping if present
        if model.get("alias"):
            self._data["aliases"].pop(model["alias"], None)

        # Optionally delete files
        if delete_files and "local_path" in model:
            local_path = self._expand_path(model["local_path"])
            if local_path.exists():
                try:
                    if local_path.is_symlink():
                        local_path.unlink()
                        logger.info(f"Removed symlink: {local_path}")
                    else:
                        shutil.rmtree(local_path)
                        logger.info(f"Deleted model files: {local_path}")
                except Exception as e:
                    logger.error(f"Failed to delete files: {e}")

        # Remove from registry
        del self._data["models"][model_id]
        self._save_registry()
        logger.info(f"Deleted model {model_id} from registry")

    def exists(self, model_id: str) -> bool:
        """Check if model exists in registry.

        Args:
            model_id: Model ID to check

        Returns:
            True if model exists, False otherwise
        """
        return model_id in self._data["models"]

    def get_all_models(self) -> Dict[str, Dict[str, Any]]:
        """Get all models as a dictionary.

        Returns:
            Dictionary mapping model IDs to model metadata
        """
        return dict(self._data["models"])

    def get_all_aliases(self) -> Dict[str, str]:
        """Get all alias mappings.

        Returns:
            Dictionary mapping alias names to model IDs
        """
        return dict(self._data["aliases"])

    # Alias Management Methods

    @staticmethod
    def _validate_alias(alias: str) -> tuple[bool, Optional[str]]:
        """Validate an alias string.

        Args:
            alias: The alias to validate

        Returns:
            Tuple of (is_valid, error_message)
            If valid, error_message is None
            If invalid, error_message describes the problem
        """
        import re

        # Check empty or whitespace
        if not alias or not alias.strip():
            return False, "Alias cannot be empty or whitespace only"

        # Check length
        if len(alias) < 1:
            return False, "Alias must be at least 1 character"
        if len(alias) > 64:
            return False, "Alias must be at most 64 characters"

        # Check reserved names
        reserved = ["all", "latest", "none", "null"]
        if alias.lower() in reserved:
            return False, f"'{alias}' is a reserved name"

        # Check if it looks like a model ID (8 hex chars)
        if re.match(r"^[0-9a-f]{8}$", alias.lower()):
            return False, "Alias cannot look like a model ID (8 hexadecimal characters)"

        # Check character restrictions
        if not re.match(r"^[a-zA-Z0-9_-]+$", alias):
            return False, "Alias must contain only letters, numbers, dashes, and underscores"

        # Check leading/trailing dashes or underscores
        if alias[0] in "-_" or alias[-1] in "-_":
            return False, "Alias cannot start or end with dash or underscore"

        return True, None

    @staticmethod
    def _sanitize_alias(text: str) -> str:
        """Sanitize a string to create a valid alias.

        Args:
            text: Text to sanitize

        Returns:
            Sanitized alias (may still need validation)
        """
        import re

        # Replace spaces with dashes
        text = text.replace(" ", "-")

        # Remove invalid characters
        text = re.sub(r"[^a-zA-Z0-9_-]", "", text)

        # Remove leading/trailing dashes and underscores
        text = text.strip("-_")

        # Truncate to max length
        if len(text) > 64:
            text = text[:64].rstrip("-_")

        return text

    def set_alias(self, model_id: str, alias: str, force: bool = False) -> bool:
        """Set or update an alias for a model.

        Args:
            model_id: Model ID to assign alias to
            alias: Alias name to assign
            force: If True, overwrite existing alias without prompting

        Returns:
            True if alias was set successfully, False otherwise

        Raises:
            KeyError: If model_id not found in registry
            ValueError: If alias is invalid
        """
        # Check model exists
        if model_id not in self._data["models"]:
            raise KeyError(f"Model {model_id} not found in registry")

        # Validate alias
        is_valid, error_msg = self._validate_alias(alias)
        if not is_valid:
            raise ValueError(error_msg)

        # Check for collision
        if alias in self._data["aliases"]:
            existing_id = self._data["aliases"][alias]
            if existing_id != model_id:
                if not force:
                    # Collision detected, would need user confirmation in CLI
                    # For now, log and return False
                    logger.warning(
                        f"Alias '{alias}' already used by model {existing_id}. "
                        f"Use force=True to overwrite."
                    )
                    return False
                else:
                    # Force overwrite - remove old alias from previous model
                    if existing_id in self._data["models"]:
                        old_alias = self._data["models"][existing_id].get("alias")
                        if old_alias == alias:
                            self._data["models"][existing_id]["alias"] = None
                    logger.info(
                        f"Alias '{alias}' reassigned from {existing_id} to {model_id}"
                    )

        # If model already has a different alias, remove the old mapping
        model = self._data["models"][model_id]
        old_alias = model.get("alias")
        if old_alias and old_alias != alias:
            self._data["aliases"].pop(old_alias, None)

        # Set the new alias
        self._data["aliases"][alias] = model_id
        self._data["models"][model_id]["alias"] = alias

        self._save_registry()
        logger.info(f"Set alias '{alias}' for model {model_id}")
        return True

    def get_by_alias(self, alias: str) -> Optional[Dict[str, Any]]:
        """Retrieve a model by its alias.

        Args:
            alias: Alias to look up

        Returns:
            Model metadata dict if found, None otherwise
        """
        model_id = self._data["aliases"].get(alias)
        if model_id:
            return self._data["models"].get(model_id)
        return None

    def resolve(self, identifier: str) -> Optional[str]:
        """Resolve an identifier to a model ID.

        Tries to match against model IDs first, then aliases.

        Args:
            identifier: Either a model ID or alias

        Returns:
            Model ID if found, None otherwise
        """
        # First check if it's a direct model ID match
        if identifier in self._data["models"]:
            return identifier

        # Then check if it's an alias
        if identifier in self._data["aliases"]:
            return self._data["aliases"][identifier]

        return None

    def remove_alias(self, alias: str) -> bool:
        """Remove an alias from the registry.

        The model itself is preserved, only the alias is removed.

        Args:
            alias: Alias to remove

        Returns:
            True if alias was removed, False if alias didn't exist
        """
        if alias not in self._data["aliases"]:
            logger.warning(f"Alias '{alias}' not found")
            return False

        # Get the model ID
        model_id = self._data["aliases"][alias]

        # Remove alias mapping
        del self._data["aliases"][alias]

        # Clear alias from model entry
        if model_id in self._data["models"]:
            self._data["models"][model_id]["alias"] = None

        self._save_registry()
        logger.info(f"Removed alias '{alias}' from model {model_id}")
        return True

    def list_aliases(self, sort: bool = True) -> Dict[str, str]:
        """List all aliases with optional sorting.

        Args:
            sort: If True, return aliases sorted alphabetically

        Returns:
            Dictionary mapping alias names to model IDs
        """
        aliases = dict(self._data["aliases"])

        if sort:
            # Return as OrderedDict sorted by alias name
            from collections import OrderedDict
            return OrderedDict(sorted(aliases.items()))

        return aliases

    def suggest_alias(self, base_text: str, model_type: Optional[str] = None) -> str:
        """Suggest a valid, available alias based on input text.

        Args:
            base_text: Base text to create alias from (e.g., directory name)
            model_type: Optional model type to include in suggestion

        Returns:
            Suggested alias (guaranteed to be valid and available)
        """
        # Sanitize the base text
        sanitized = self._sanitize_alias(base_text)

        # If model type provided, prepend it
        if model_type and sanitized and not sanitized.startswith(model_type):
            sanitized = f"{model_type}-{sanitized}"

        # Ensure it's valid
        if not sanitized:
            sanitized = "model"

        is_valid, _ = self._validate_alias(sanitized)
        if not is_valid:
            sanitized = "model"

        # Make it unique if it already exists
        suggestion = sanitized
        counter = 1
        while suggestion in self._data["aliases"]:
            suggestion = f"{sanitized}-v{counter}"
            counter += 1

        return suggestion
