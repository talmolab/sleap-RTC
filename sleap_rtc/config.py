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
from pathlib import Path
from typing import Optional

from loguru import logger


# Default production signaling server URLs
DEFAULT_SIGNALING_WEBSOCKET = (
    "ws://ec2-54-176-92-10.us-west-1.compute.amazonaws.com:8080"
)
DEFAULT_SIGNALING_HTTP = "http://ec2-54-176-92-10.us-west-1.compute.amazonaws.com:8001"

# Valid environment names
VALID_ENVIRONMENTS = {"development", "staging", "production"}


class Config:
    """Configuration manager for SLEAP-RTC."""

    def __init__(self):
        """Initialize configuration with defaults."""
        self.signaling_websocket: str = DEFAULT_SIGNALING_WEBSOCKET
        self.signaling_http: str = DEFAULT_SIGNALING_HTTP
        self.environment: str = "production"
        self._config_data: dict = {}

    def load(self) -> None:
        """Load configuration from all sources.

        Priority order:
        1. Environment variables (SLEAP_RTC_SIGNALING_WS, SLEAP_RTC_SIGNALING_HTTP)
        2. TOML configuration file
        3. Default values
        """
        # Determine environment
        self.environment = self._get_environment()

        # Try to load config file
        config_file = self._find_config_file()
        if config_file:
            self._load_config_file(config_file)

        # Apply environment variable overrides
        self._apply_env_overrides()

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
