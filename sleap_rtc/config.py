"""Configuration management for SLEAP-RTC.

This module handles loading configuration from multiple sources with the following priority:
1. CLI arguments (handled by caller)
2. Environment variables (SLEAP_RTC_SIGNALING_WS, SLEAP_RTC_SIGNALING_HTTP)
3. TOML configuration file
4. Default values (production environment)

Configuration files are loaded from:
- sleap-rtc.toml in current working directory
- ~/.sleap-rtc/config.toml

Environment selection via SLEAP_RTC_ENV (development, staging, production).
Defaults to production if not set.
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


# Default production signaling server URLs
DEFAULT_SIGNALING_WEBSOCKET = (
    "ws://ec2-52-9-213-137.us-west-1.compute.amazonaws.com:8080"
)
DEFAULT_SIGNALING_HTTP = "http://ec2-52-9-213-137.us-west-1.compute.amazonaws.com:8001"

# Valid environment names
VALID_ENVIRONMENTS = {"development", "staging", "production"}


class Config:
    """Configuration manager for SLEAP-RTC.

    This class manages all SLEAP-RTC configuration including:
    - Signaling server URLs (WebSocket and HTTP)
    - Environment selection (development, staging, production)
    - Storage backends for shared filesystem access

    Configuration is loaded from multiple sources with the following priority:
    1. CLI arguments (handled by caller)
    2. Environment variables
    3. TOML configuration file
    4. Default values
    """

    def __init__(self):
        """Initialize configuration with defaults."""
        self.signaling_websocket: str = DEFAULT_SIGNALING_WEBSOCKET
        self.signaling_http: str = DEFAULT_SIGNALING_HTTP
        self.environment: str = "production"
        self._config_data: dict = {}
        # Storage backends - initialized empty, populated by load()
        self._storage: Optional["StorageConfig"] = None

    def load(self) -> None:
        """Load configuration from all sources.

        Priority order:
        1. Environment variables (SLEAP_RTC_SIGNALING_WS, SLEAP_RTC_SIGNALING_HTTP)
        2. TOML configuration file
        3. Default values

        Storage backends are loaded from:
        1. TOML [storage.*] sections
        2. SHARED_STORAGE_ROOT environment variable (creates "default" backend)
        3. Empty config (no backends, will use RTC transfer)
        """
        # Determine environment
        self.environment = self._get_environment()

        # Try to load config file
        config_file = self._find_config_file()
        if config_file:
            self._load_config_file(config_file)

        # Apply environment variable overrides
        self._apply_env_overrides()

        # Load storage backends
        self._load_storage_config()

    def _get_environment(self) -> str:
        """Get the current environment from SLEAP_RTC_ENV.

        Returns:
            Environment name (development, staging, or production).
            Defaults to production if not set or invalid.
        """
        env = os.getenv("SLEAP_RTC_ENV", "production").lower()
        if env not in VALID_ENVIRONMENTS:
            logger.warning(
                f"Invalid SLEAP_RTC_ENV value '{env}'. "
                f"Valid values are: {', '.join(VALID_ENVIRONMENTS)}. "
                f"Defaulting to 'production'."
            )
            env = "production"
        return env

    def _find_config_file(self) -> Optional[Path]:
        """Find the configuration file.

        Searches in order:
        1. sleap-rtc.toml in current working directory
        2. ~/.sleap-rtc/config.toml

        Returns:
            Path to config file if found, None otherwise.
        """
        # Check current working directory
        cwd_config = Path.cwd() / "sleap-rtc.toml"
        if cwd_config.exists():
            logger.info(f"Loading config from {cwd_config}")
            return cwd_config

        # Check home directory
        home_config = Path.home() / ".sleap-rtc" / "config.toml"
        if home_config.exists():
            logger.info(f"Loading config from {home_config}")
            return home_config

        logger.debug("No config file found, using defaults")
        return None

    def _load_config_file(self, config_file: Path) -> None:
        """Load configuration from TOML file.

        Args:
            config_file: Path to the TOML configuration file.
        """
        try:
            with open(config_file, "rb") as f:
                self._config_data = tomllib.load(f)

            # Apply default section settings
            if "default" in self._config_data:
                # Default section for shared settings (future use)
                pass

            # Apply environment-specific settings
            environments = self._config_data.get("environments", {})
            env_config = environments.get(self.environment, {})

            if not env_config:
                logger.debug(
                    f"No configuration found for environment '{self.environment}' "
                    f"in {config_file}, using defaults"
                )
                return

            # Load signaling server URLs
            if "signaling_websocket" in env_config:
                self.signaling_websocket = env_config["signaling_websocket"]
                logger.debug(
                    f"Loaded signaling_websocket from config: {self.signaling_websocket}"
                )

            if "signaling_http" in env_config:
                self.signaling_http = env_config["signaling_http"]
                logger.debug(
                    f"Loaded signaling_http from config: {self.signaling_http}"
                )

        except Exception as e:
            logger.warning(
                f"Failed to load config file {config_file}: {e}. Using defaults."
            )

    def _apply_env_overrides(self) -> None:
        """Apply environment variable overrides.

        Environment variables take precedence over config file settings
        but are overridden by CLI arguments (handled by caller).
        """
        # Override WebSocket URL
        ws_override = os.getenv("SLEAP_RTC_SIGNALING_WS")
        if ws_override:
            self.signaling_websocket = ws_override
            logger.info(
                f"Overriding signaling_websocket from env: {self.signaling_websocket}"
            )

        # Override HTTP URL
        http_override = os.getenv("SLEAP_RTC_SIGNALING_HTTP")
        if http_override:
            self.signaling_http = http_override
            logger.info(f"Overriding signaling_http from env: {self.signaling_http}")

    def get_websocket_url(self, port: int = 8080) -> str:
        """Get the WebSocket signaling server URL.

        Args:
            port: Port number to use if not specified in URL (default: 8080).

        Returns:
            WebSocket URL with port.
        """
        url = self.signaling_websocket
        # Add port if not present
        if ":" not in url.split("//")[-1]:
            url = f"{url}:{port}"
        return url

    def get_http_url(self, port: int = 8001) -> str:
        """Get the HTTP signaling server base URL.

        Args:
            port: Port number to use if not specified in URL (default: 8001).

        Returns:
            HTTP base URL with port.
        """
        url = self.signaling_http
        # Add port if not present
        if ":" not in url.split("//")[-1]:
            url = f"{url}:{port}"
        return url

    def get_http_endpoint(self, endpoint: str) -> str:
        """Get a full HTTP API endpoint URL.

        Args:
            endpoint: API endpoint path (e.g., '/create-room', '/delete-peers-and-room').

        Returns:
            Full endpoint URL.
        """
        base_url = self.get_http_url()
        # Remove leading slash from endpoint if present
        endpoint = endpoint.lstrip("/")
        return f"{base_url}/{endpoint}"

    def _load_storage_config(self) -> None:
        """Load storage backend configuration.

        Loads storage backends from TOML [storage.*] sections if available,
        otherwise falls back to SHARED_STORAGE_ROOT environment variable.
        """
        # Import here to avoid circular dependency (StorageConfig is defined later)
        # Actually StorageConfig is in the same file, so we can reference it directly
        self._storage = StorageConfig.load(self._config_data)

    @property
    def storage(self) -> "StorageConfig":
        """Get the storage configuration.

        Returns:
            StorageConfig instance with configured backends.
            Returns empty StorageConfig if no backends are configured.

        Example:
            >>> config = get_config()
            >>> if config.storage.has_backend("vast"):
            ...     path = config.storage.resolve_path("vast", "amick", "data.zip")
        """
        if self._storage is None:
            # Lazy initialization if load() wasn't called
            self._storage = StorageConfig()
        return self._storage

    def has_storage_backend(self, name: str) -> bool:
        """Check if a storage backend is configured.

        Convenience method that delegates to storage.has_backend().

        Args:
            name: Backend name to check.

        Returns:
            True if backend exists, False otherwise.

        Example:
            >>> config = get_config()
            >>> if config.has_storage_backend("vast"):
            ...     print("Shared storage available")
        """
        return self.storage.has_backend(name)

    def list_storage_backends(self) -> List[str]:
        """List all configured storage backend names.

        Convenience method that delegates to storage.list_backends().

        Returns:
            List of backend names, sorted alphabetically.

        Example:
            >>> config = get_config()
            >>> print(f"Available backends: {config.list_storage_backends()}")
        """
        return self.storage.list_backends()


# Global configuration instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance.

    Loads configuration on first call.

    Returns:
        Global Config instance.
    """
    global _config
    if _config is None:
        _config = Config()
        _config.load()
    return _config


def reload_config() -> Config:
    """Reload configuration from sources.

    Useful for testing or runtime reconfiguration.

    Returns:
        Reloaded Config instance.
    """
    global _config
    _config = Config()
    _config.load()
    return _config


class SharedStorageConfigError(Exception):
    """Raised when shared storage configuration is invalid."""

    pass


class StorageBackendError(Exception):
    """Raised when storage backend operations fail."""

    pass


class StorageBackendNotFoundError(StorageBackendError):
    """Raised when a requested storage backend is not configured."""

    pass


class StoragePathValidationError(StorageBackendError):
    """Raised when path validation fails (security or accessibility)."""

    pass


@dataclass
class StorageBackend:
    """Configuration for a single named storage backend.

    A storage backend represents a shared filesystem mount point that can be
    accessed by both clients and workers. Each backend has a logical name
    (e.g., "vast", "gdrive", "scratch") that maps to a local filesystem path.

    The same logical name should be used across all machines that share access
    to the same physical storage, even if the local mount paths differ:
    - Mac client: vast -> /Volumes/talmo
    - Linux worker: vast -> /mnt/vast
    - Run:AI container: vast -> /home/jovyan/vast

    Attributes:
        name: Logical identifier for this backend (e.g., "vast", "gdrive").
        base_path: Local filesystem path where this storage is mounted.
        type: Storage type hint (e.g., "nfs", "local", "google_shared_drive").
            Used for documentation and future optimizations.
        description: Human-readable description for logs and UI.

    Example:
        >>> backend = StorageBackend(
        ...     name="vast",
        ...     base_path=Path("/mnt/vast"),
        ...     type="nfs",
        ...     description="Institutional VAST storage"
        ... )
        >>> backend.resolve_path("user", "project/labels.slp")
        PosixPath('/mnt/vast/user/project/labels.slp')
    """

    name: str
    base_path: Path
    type: str = "local"
    description: str = ""

    def __post_init__(self) -> None:
        """Validate and normalize fields after initialization."""
        # Ensure base_path is a Path object
        if isinstance(self.base_path, str):
            self.base_path = Path(self.base_path)

        # Expand user home directory (~) and resolve to absolute path
        self.base_path = self.base_path.expanduser().resolve()

    def resolve_path(
        self,
        user_subdir: str,
        relative_path: str | Path,
    ) -> Path:
        """Resolve a logical path to an absolute local filesystem path.

        Constructs the full path by combining:
        base_path / user_subdir / relative_path

        Args:
            user_subdir: User's subdirectory within the storage backend
                (e.g., "amick", "sam"). Can be empty string for backends
                without user separation.
            relative_path: Path relative to the user's directory
                (e.g., "project/labels.slp").

        Returns:
            Absolute path on the local filesystem.

        Example:
            >>> backend = StorageBackend(name="vast", base_path=Path("/mnt/vast"))
            >>> backend.resolve_path("user", "project/labels.slp")
            PosixPath('/mnt/vast/user/project/labels.slp')

            >>> backend.resolve_path("", "shared/dataset.zip")
            PosixPath('/mnt/vast/shared/dataset.zip')
        """
        if isinstance(relative_path, str):
            relative_path = Path(relative_path)

        # Build path components, filtering out empty strings
        components = [self.base_path]
        if user_subdir:
            components.append(Path(user_subdir))
        components.append(relative_path)

        # Combine all components
        result = components[0]
        for component in components[1:]:
            result = result / component

        return result

    def validate_path(self, resolved_path: Path) -> Path:
        """Validate that a resolved path is within this backend's base_path.

        This security check prevents path traversal attacks where malicious
        input like "../../../etc/passwd" could escape the storage root.

        Args:
            resolved_path: The path to validate (should be output of resolve_path).

        Returns:
            The validated absolute path (with symlinks resolved).

        Raises:
            StoragePathValidationError: If path escapes the base_path.

        Example:
            >>> backend = StorageBackend(name="vast", base_path=Path("/mnt/vast"))
            >>> path = backend.resolve_path("user", "data.zip")
            >>> backend.validate_path(path)
            PosixPath('/mnt/vast/user/data.zip')

            >>> bad_path = backend.resolve_path("../../../etc", "passwd")
            >>> backend.validate_path(bad_path)
            StoragePathValidationError: Path escapes storage backend root
        """
        try:
            # Resolve symlinks to get the true path
            actual_path = resolved_path.resolve()
            base_resolved = self.base_path.resolve()

            # Check if the resolved path is within the base path
            actual_path.relative_to(base_resolved)

            return actual_path

        except ValueError as e:
            raise StoragePathValidationError(
                f"Path '{resolved_path}' escapes storage backend '{self.name}' "
                f"root '{self.base_path}'. Resolved to: '{actual_path}'"
            ) from e

    def validate_and_resolve(
        self,
        user_subdir: str,
        relative_path: str | Path,
    ) -> Path:
        """Resolve a path and validate it in one step.

        Convenience method that combines resolve_path() and validate_path().

        Args:
            user_subdir: User's subdirectory within the storage backend.
            relative_path: Path relative to the user's directory.

        Returns:
            Validated absolute path on the local filesystem.

        Raises:
            StoragePathValidationError: If the resolved path escapes base_path.

        Example:
            >>> backend = StorageBackend(name="vast", base_path=Path("/mnt/vast"))
            >>> backend.validate_and_resolve("user", "project/labels.slp")
            PosixPath('/mnt/vast/user/project/labels.slp')
        """
        resolved = self.resolve_path(user_subdir, relative_path)
        return self.validate_path(resolved)

    def exists(self) -> bool:
        """Check if the base_path exists and is accessible.

        Returns:
            True if base_path exists and is a directory.
        """
        try:
            return self.base_path.exists() and self.base_path.is_dir()
        except (PermissionError, OSError):
            return False

    def is_readable(self) -> bool:
        """Check if the base_path is readable.

        Returns:
            True if base_path has read permissions.
        """
        return os.access(self.base_path, os.R_OK)

    def is_writable(self) -> bool:
        """Check if the base_path is writable.

        Returns:
            True if base_path has write permissions.
        """
        return os.access(self.base_path, os.W_OK)

    def validate_backend(self) -> None:
        """Validate that this storage backend is properly configured and accessible.

        Checks that base_path exists, is a directory, and has read/write permissions.

        Raises:
            StorageBackendError: If validation fails.
        """
        if not self.base_path.exists():
            raise StorageBackendError(
                f"Storage backend '{self.name}' base path does not exist: "
                f"{self.base_path}"
            )

        if not self.base_path.is_dir():
            raise StorageBackendError(
                f"Storage backend '{self.name}' base path is not a directory: "
                f"{self.base_path}"
            )

        if not self.is_readable():
            raise StorageBackendError(
                f"Storage backend '{self.name}' base path is not readable: "
                f"{self.base_path}"
            )

        if not self.is_writable():
            raise StorageBackendError(
                f"Storage backend '{self.name}' base path is not writable: "
                f"{self.base_path}"
            )

        logger.debug(
            f"Storage backend '{self.name}' validated: {self.base_path} "
            f"(type={self.type})"
        )

    def __repr__(self) -> str:
        """Return a detailed string representation."""
        return (
            f"StorageBackend(name={self.name!r}, base_path={self.base_path}, "
            f"type={self.type!r})"
        )


class StorageConfig:
    """Manages multiple named storage backends.

    This class handles configuration for multiple storage backends, allowing
    workers and clients to access shared filesystems using logical names
    that map to different local paths on each machine.

    Storage backends can be configured via:
    1. TOML configuration file with [storage.<name>] sections
    2. Legacy SHARED_STORAGE_ROOT environment variable (creates "default" backend)

    Example TOML configuration:
        [storage.vast]
        base_path = "/home/jovyan/vast"
        type = "nfs"
        description = "Institutional VAST storage"

        [storage.scratch]
        base_path = "/scratch"
        type = "local"

    Example usage:
        >>> config = StorageConfig.from_toml(toml_data)
        >>> config.has_backend("vast")
        True
        >>> config.resolve_path("vast", "user", "project/data.zip")
        PosixPath('/mnt/vast/user/project/data.zip')
        >>> config.list_backends()
        ['vast', 'scratch']
    """

    def __init__(self, backends: Optional[Dict[str, StorageBackend]] = None) -> None:
        """Initialize StorageConfig with optional backends.

        Args:
            backends: Dictionary mapping backend names to StorageBackend instances.
                If None, an empty dict is used.
        """
        self._backends: Dict[str, StorageBackend] = backends or {}

    @classmethod
    def from_toml(
        cls,
        config_data: dict,
        validate: bool = False,
    ) -> "StorageConfig":
        """Create StorageConfig from parsed TOML configuration data.

        Parses [storage.<name>] sections from the TOML data and creates
        corresponding StorageBackend instances.

        Args:
            config_data: Parsed TOML configuration dictionary.
            validate: If True, validate each backend after creation
                (checks that paths exist and are accessible).

        Returns:
            New StorageConfig instance with configured backends.

        Raises:
            StorageBackendError: If validate=True and a backend fails validation.
            ValueError: If a storage section is missing required 'base_path'.

        Example:
            >>> toml_data = {
            ...     "storage": {
            ...         "vast": {"base_path": "/mnt/vast", "type": "nfs"},
            ...         "scratch": {"base_path": "/scratch"}
            ...     }
            ... }
            >>> config = StorageConfig.from_toml(toml_data)
            >>> config.list_backends()
            ['vast', 'scratch']
        """
        backends: Dict[str, StorageBackend] = {}

        storage_section = config_data.get("storage", {})

        for name, settings in storage_section.items():
            # Validate required fields
            if "base_path" not in settings:
                raise ValueError(
                    f"Storage backend '{name}' is missing required 'base_path' field"
                )

            backend = StorageBackend(
                name=name,
                base_path=Path(settings["base_path"]),
                type=settings.get("type", "local"),
                description=settings.get("description", ""),
            )

            if validate:
                backend.validate_backend()

            backends[name] = backend
            logger.debug(
                f"Loaded storage backend '{name}': {backend.base_path} "
                f"(type={backend.type})"
            )

        return cls(backends=backends)

    @classmethod
    def from_env(cls, validate: bool = False) -> "StorageConfig":
        """Create StorageConfig from SHARED_STORAGE_ROOT environment variable.

        This provides backward compatibility with the legacy single-root
        configuration. If SHARED_STORAGE_ROOT is set, creates a "default"
        backend pointing to that path.

        Args:
            validate: If True, validate the backend after creation.

        Returns:
            New StorageConfig instance. Empty if env var not set.

        Example:
            >>> os.environ["SHARED_STORAGE_ROOT"] = "/mnt/storage"
            >>> config = StorageConfig.from_env()
            >>> config.has_backend("default")
            True
            >>> config.get_backend("default").base_path
            PosixPath('/mnt/storage')
        """
        backends: Dict[str, StorageBackend] = {}

        env_root = os.getenv("SHARED_STORAGE_ROOT")
        if env_root:
            backend = StorageBackend(
                name="default",
                base_path=Path(env_root),
                type="legacy",
                description="Legacy SHARED_STORAGE_ROOT configuration",
            )

            if validate:
                backend.validate_backend()

            backends["default"] = backend
            logger.debug(
                f"Created 'default' backend from SHARED_STORAGE_ROOT: {env_root}"
            )

        return cls(backends=backends)

    @classmethod
    def load(
        cls,
        config_data: Optional[dict] = None,
        validate: bool = False,
    ) -> "StorageConfig":
        """Load storage configuration with automatic fallback.

        Priority order:
        1. TOML [storage.*] sections (if present in config_data)
        2. SHARED_STORAGE_ROOT environment variable (creates "default" backend)
        3. Empty config (no backends)

        Args:
            config_data: Parsed TOML configuration dictionary. Can be None.
            validate: If True, validate backends after creation.

        Returns:
            StorageConfig with backends from highest-priority source.

        Example:
            >>> # With TOML config
            >>> config = StorageConfig.load(toml_data)

            >>> # Without TOML, falls back to env var
            >>> config = StorageConfig.load(None)
        """
        # Try TOML config first
        if config_data and "storage" in config_data:
            config = cls.from_toml(config_data, validate=validate)
            if config._backends:
                logger.info(
                    f"Loaded {len(config._backends)} storage backend(s) from config: "
                    f"{', '.join(config.list_backends())}"
                )
                return config

        # Fall back to environment variable
        config = cls.from_env(validate=validate)
        if config._backends:
            logger.info(
                "Using legacy SHARED_STORAGE_ROOT for storage backend 'default'"
            )
            return config

        # No storage configured
        logger.debug(
            "No storage backends configured. "
            "Set [storage.*] in config or SHARED_STORAGE_ROOT env var."
        )
        return cls()

    def has_backend(self, name: str) -> bool:
        """Check if a storage backend is configured.

        Args:
            name: Backend name to check.

        Returns:
            True if backend exists, False otherwise.

        Example:
            >>> config.has_backend("vast")
            True
            >>> config.has_backend("nonexistent")
            False
        """
        return name in self._backends

    def get_backend(self, name: str) -> StorageBackend:
        """Get a storage backend by name.

        Args:
            name: Backend name to retrieve.

        Returns:
            The StorageBackend instance.

        Raises:
            StorageBackendNotFoundError: If backend doesn't exist.

        Example:
            >>> backend = config.get_backend("vast")
            >>> backend.base_path
            PosixPath('/mnt/vast')
        """
        if name not in self._backends:
            available = ", ".join(self.list_backends()) or "(none)"
            raise StorageBackendNotFoundError(
                f"Storage backend '{name}' not found. Available backends: {available}"
            )
        return self._backends[name]

    def resolve_path(
        self,
        backend_name: str,
        user_subdir: str,
        relative_path: str,
    ) -> Path:
        """Resolve a logical path to an absolute local filesystem path.

        Combines: backend.base_path / user_subdir / relative_path

        Args:
            backend_name: Name of the storage backend to use.
            user_subdir: User's subdirectory within the backend.
            relative_path: Path relative to user's directory.

        Returns:
            Absolute path on the local filesystem.

        Raises:
            StorageBackendNotFoundError: If backend doesn't exist.

        Example:
            >>> config.resolve_path("vast", "user", "project/data.zip")
            PosixPath('/mnt/vast/user/project/data.zip')
        """
        backend = self.get_backend(backend_name)
        return backend.resolve_path(user_subdir, relative_path)

    def validate_and_resolve_path(
        self,
        backend_name: str,
        user_subdir: str,
        relative_path: str,
    ) -> Path:
        """Resolve and validate a path in one step.

        Args:
            backend_name: Name of the storage backend to use.
            user_subdir: User's subdirectory within the backend.
            relative_path: Path relative to user's directory.

        Returns:
            Validated absolute path on the local filesystem.

        Raises:
            StorageBackendNotFoundError: If backend doesn't exist.
            StoragePathValidationError: If path escapes backend root.

        Example:
            >>> config.validate_and_resolve_path("vast", "user", "data.zip")
            PosixPath('/mnt/vast/user/data.zip')
        """
        backend = self.get_backend(backend_name)
        return backend.validate_and_resolve(user_subdir, relative_path)

    def list_backends(self) -> List[str]:
        """List all configured storage backend names.

        Returns:
            List of backend names, sorted alphabetically.

        Example:
            >>> config.list_backends()
            ['gdrive', 'scratch', 'vast']
        """
        return sorted(self._backends.keys())

    def add_backend(self, backend: StorageBackend) -> None:
        """Add a storage backend to the configuration.

        Args:
            backend: StorageBackend instance to add.

        Example:
            >>> backend = StorageBackend(name="new", base_path="/mnt/new")
            >>> config.add_backend(backend)
        """
        self._backends[backend.name] = backend
        logger.debug(f"Added storage backend '{backend.name}': {backend.base_path}")

    def remove_backend(self, name: str) -> None:
        """Remove a storage backend from the configuration.

        Args:
            name: Name of backend to remove.

        Raises:
            StorageBackendNotFoundError: If backend doesn't exist.
        """
        if name not in self._backends:
            raise StorageBackendNotFoundError(f"Storage backend '{name}' not found")
        del self._backends[name]
        logger.debug(f"Removed storage backend '{name}'")

    def validate_all(self) -> None:
        """Validate all configured storage backends.

        Checks that each backend's base_path exists and is accessible.

        Raises:
            StorageBackendError: If any backend fails validation.
        """
        for backend in self._backends.values():
            backend.validate_backend()

    def __len__(self) -> int:
        """Return number of configured backends."""
        return len(self._backends)

    def __contains__(self, name: str) -> bool:
        """Check if backend exists (supports 'in' operator)."""
        return name in self._backends

    def __iter__(self):
        """Iterate over backend names."""
        return iter(self._backends)

    def __repr__(self) -> str:
        """Return string representation."""
        backends = ", ".join(self.list_backends())
        return f"StorageConfig(backends=[{backends}])"


class SharedStorageConfig:
    """Configuration for shared filesystem access.

    This class handles shared storage configuration using explicit paths only.
    No auto-detection is performed - configuration must be provided via:
    1. CLI argument (--shared-storage-root, passed as cli_override)
    2. Environment variable (SHARED_STORAGE_ROOT)
    3. If neither is set, returns None (will use RTC transfer fallback)

    The configured path is validated to ensure it exists, is a directory,
    and has read/write permissions.

    Example:
        # Via environment variable
        export SHARED_STORAGE_ROOT="/Volumes/talmo/amick"

        # In code
        config = SharedStorageConfig()
        root = config.get_shared_storage_root()
        if root:
            print(f"Using shared storage: {root}")
        else:
            print("Shared storage not configured, using RTC transfer")
    """

    @staticmethod
    def get_shared_storage_root(cli_override: Optional[str] = None) -> Optional[Path]:
        """Get shared storage root path from explicit configuration.

        Priority order:
        1. CLI argument (cli_override parameter)
        2. Environment variable (SHARED_STORAGE_ROOT)
        3. None (not configured, will use RTC transfer)

        Args:
            cli_override: Optional path from CLI argument (--shared-storage-root).
                Takes precedence over environment variable.

        Returns:
            Path to shared storage root if configured and valid, None otherwise.

        Raises:
            SharedStorageConfigError: If path is configured but invalid
                (doesn't exist, not a directory, or insufficient permissions).

        Examples:
            >>> # With environment variable set
            >>> os.environ['SHARED_STORAGE_ROOT'] = '/shared/storage'
            >>> root = SharedStorageConfig.get_shared_storage_root()
            >>> print(root)
            /shared/storage

            >>> # With CLI override
            >>> root = SharedStorageConfig.get_shared_storage_root('/custom/path')
            >>> print(root)
            /custom/path

            >>> # Not configured
            >>> os.environ.pop('SHARED_STORAGE_ROOT', None)
            >>> root = SharedStorageConfig.get_shared_storage_root()
            >>> print(root)
            None
        """
        # Priority 1: CLI argument override
        if cli_override:
            path_str = cli_override
            source = "CLI argument"
        # Priority 2: Environment variable
        elif env_root := os.getenv("SHARED_STORAGE_ROOT"):
            path_str = env_root
            source = "environment variable SHARED_STORAGE_ROOT"
        # Priority 3: Not configured
        else:
            logger.debug(
                "Shared storage not configured. "
                "Set SHARED_STORAGE_ROOT or use --shared-storage-root to enable. "
                "Falling back to RTC transfer."
            )
            return None

        # Validate the configured path
        try:
            root = Path(path_str).expanduser().resolve()
            SharedStorageConfig._validate_path(root, source)
            logger.info(f"✓ Using shared storage from {source}: {root}")
            return root
        except SharedStorageConfigError:
            # Re-raise validation errors with context
            raise
        except Exception as e:
            raise SharedStorageConfigError(
                f"Failed to process shared storage path '{path_str}' from {source}: {e}"
            ) from e

    @staticmethod
    def _validate_path(path: Path, source: str) -> None:
        """Validate that a path is suitable for shared storage.

        Args:
            path: The path to validate.
            source: Description of where the path came from (for error messages).

        Raises:
            SharedStorageConfigError: If path is invalid.
        """
        # Check existence
        if not path.exists():
            raise SharedStorageConfigError(
                f"Shared storage path does not exist: {path}\n"
                f"  Source: {source}\n"
                f"  Please verify the path is correct and accessible.\n"
                f"  To disable shared storage, unset SHARED_STORAGE_ROOT."
            )

        # Check is directory
        if not path.is_dir():
            raise SharedStorageConfigError(
                f"Shared storage path is not a directory: {path}\n"
                f"  Source: {source}\n"
                f"  Please provide a directory path, not a file."
            )

        # Check read permission
        if not os.access(path, os.R_OK):
            raise SharedStorageConfigError(
                f"Shared storage path is not readable: {path}\n"
                f"  Source: {source}\n"
                f"  Please check directory permissions."
            )

        # Check write permission
        if not os.access(path, os.W_OK):
            raise SharedStorageConfigError(
                f"Shared storage path is not writable: {path}\n"
                f"  Source: {source}\n"
                f"  Please check directory permissions."
            )

    @staticmethod
    def has_shared_storage(cli_override: Optional[str] = None) -> bool:
        """Check if shared storage is configured and available.

        Args:
            cli_override: Optional path from CLI argument.

        Returns:
            True if shared storage is configured and valid, False otherwise.

        Examples:
            >>> if SharedStorageConfig.has_shared_storage():
            ...     print("Will use shared storage transfer")
            ... else:
            ...     print("Will use RTC transfer")
        """
        try:
            root = SharedStorageConfig.get_shared_storage_root(cli_override)
            return root is not None
        except SharedStorageConfigError:
            # Configuration exists but is invalid - already logged
            return False
