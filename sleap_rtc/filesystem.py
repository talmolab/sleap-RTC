"""Filesystem utilities for shared storage operations.

This module provides utilities for working with shared filesystems (NFS, local mounts)
using Python standard library. Future enhancement: Can integrate fsspec for cloud storage
(S3, GCS, Azure) support.

Key classes:
- StorageResolver: Bridge between StorageConfig and filesystem operations
- PathValidationError: Raised when path validation fails
- SharedStorageError: Raised when storage operations fail

Key functions:
- validate_path_in_root: Security check for path traversal
- safe_copy, safe_mkdir, safe_remove: Safe filesystem operations
"""

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from sleap_rtc.config import StorageConfig


class PathValidationError(Exception):
    """Raised when path validation fails (security or accessibility)."""

    pass


class SharedStorageError(Exception):
    """Raised when shared storage operations fail."""

    pass


def validate_path_in_root(
    path: Path, root: Path, resolve_symlinks: bool = True
) -> Path:
    """Validate that a path is within the shared storage root directory.

    This function prevents path traversal attacks by ensuring the resolved path
    stays within the allowed root directory.

    Args:
        path: The path to validate (can be relative or absolute).
        root: The root directory that path must be within.
        resolve_symlinks: If True, resolve symlinks before validation (recommended
            for security). Default: True.

    Returns:
        The resolved absolute path if validation succeeds.

    Raises:
        PathValidationError: If path is outside root or validation fails.

    Examples:
        >>> root = Path("/shared/storage")
        >>> path = Path("/shared/storage/jobs/job_123/data.zip")
        >>> validate_path_in_root(path, root)
        PosixPath('/shared/storage/jobs/job_123/data.zip')

        >>> path = Path("/shared/storage/../../etc/passwd")
        >>> validate_path_in_root(path, root)
        PathValidationError: Path outside shared storage root
    """
    try:
        if resolve_symlinks:
            resolved_path = path.resolve()
            resolved_root = root.resolve()
        else:
            resolved_path = path.absolute()
            resolved_root = root.absolute()

        # Check if path is within root using relative_to
        # This will raise ValueError if path is not relative to root
        resolved_path.relative_to(resolved_root)

        return resolved_path

    except ValueError as e:
        raise PathValidationError(
            f"Path '{path}' is outside shared storage root '{root}'. "
            f"Resolved to: '{resolved_path if 'resolved_path' in locals() else 'unknown'}'"
        ) from e


def to_relative_path(path: Path, root: Path) -> Path:
    """Convert an absolute path to a relative path from root.

    Args:
        path: The absolute path to convert.
        root: The root directory to compute relative path from.

    Returns:
        The relative path from root.

    Raises:
        ValueError: If path is not within root.

    Examples:
        >>> root = Path("/shared/storage")
        >>> path = Path("/shared/storage/jobs/job_123/data.zip")
        >>> to_relative_path(path, root)
        PosixPath('jobs/job_123/data.zip')
    """
    return path.relative_to(root)


def to_absolute_path(relative_path: Path, root: Path) -> Path:
    """Convert a relative path to an absolute path using root.

    Args:
        relative_path: The relative path to convert.
        root: The root directory to resolve from.

    Returns:
        The absolute path.

    Examples:
        >>> root = Path("/shared/storage")
        >>> rel_path = Path("jobs/job_123/data.zip")
        >>> to_absolute_path(rel_path, root)
        PosixPath('/shared/storage/jobs/job_123/data.zip')
    """
    return root / relative_path


def safe_exists(path: Path) -> bool:
    """Check if a path exists safely (handles permission errors).

    Args:
        path: The path to check.

    Returns:
        True if path exists and is accessible, False otherwise.

    Examples:
        >>> safe_exists(Path("/shared/storage/file.zip"))
        True
        >>> safe_exists(Path("/nonexistent/file.zip"))
        False
    """
    try:
        return path.exists()
    except (PermissionError, OSError):
        return False


def safe_copy(src: Path, dst: Path, preserve_metadata: bool = True) -> None:
    """Copy a file safely with error handling.

    Args:
        src: Source file path.
        dst: Destination file path.
        preserve_metadata: If True, preserve file metadata (timestamps, permissions).
            Default: True.

    Raises:
        SharedStorageError: If copy operation fails.

    Examples:
        >>> src = Path("/local/training.zip")
        >>> dst = Path("/shared/storage/jobs/job_123/training.zip")
        >>> safe_copy(src, dst)
    """
    try:
        if preserve_metadata:
            shutil.copy2(src, dst)
        else:
            shutil.copy(src, dst)
    except PermissionError as e:
        raise SharedStorageError(
            f"Permission denied copying '{src}' to '{dst}'. " f"Check file permissions."
        ) from e
    except OSError as e:
        # Includes disk full, I/O errors, etc.
        if "No space left on device" in str(e):
            raise SharedStorageError(
                f"Insufficient disk space to copy '{src}' to '{dst}'."
            ) from e
        else:
            raise SharedStorageError(f"Failed to copy '{src}' to '{dst}': {e}") from e


def safe_mkdir(path: Path, parents: bool = True, exist_ok: bool = True) -> None:
    """Create a directory safely with error handling.

    Args:
        path: Directory path to create.
        parents: If True, create parent directories as needed. Default: True.
        exist_ok: If True, don't raise error if directory exists. Default: True.

    Raises:
        SharedStorageError: If directory creation fails.

    Examples:
        >>> safe_mkdir(Path("/shared/storage/jobs/job_123/output"))
    """
    try:
        path.mkdir(parents=parents, exist_ok=exist_ok)
    except PermissionError as e:
        raise SharedStorageError(
            f"Permission denied creating directory '{path}'. "
            f"Check parent directory permissions."
        ) from e
    except OSError as e:
        if "No space left on device" in str(e):
            raise SharedStorageError(
                f"Insufficient disk space to create directory '{path}'."
            ) from e
        else:
            raise SharedStorageError(f"Failed to create directory '{path}': {e}") from e


def safe_remove(path: Path, recursive: bool = False) -> None:
    """Remove a file or directory safely with error handling.

    Args:
        path: Path to remove.
        recursive: If True, remove directories recursively. Default: False.

    Raises:
        SharedStorageError: If removal fails.

    Examples:
        >>> safe_remove(Path("/shared/storage/temp/file.zip"))
        >>> safe_remove(Path("/shared/storage/temp/"), recursive=True)
    """
    try:
        if recursive and path.is_dir():
            shutil.rmtree(path)
        else:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
    except PermissionError as e:
        raise SharedStorageError(
            f"Permission denied removing '{path}'. Check permissions."
        ) from e
    except OSError as e:
        raise SharedStorageError(f"Failed to remove '{path}': {e}") from e


def get_file_info(path: Path) -> dict:
    """Get file metadata (size, modification time, etc.).

    Args:
        path: Path to file or directory.

    Returns:
        Dictionary containing file metadata:
            - size: File size in bytes
            - mtime: Modification time (timestamp)
            - is_dir: True if directory, False if file
            - is_file: True if regular file
            - exists: True if path exists

    Examples:
        >>> info = get_file_info(Path("/shared/storage/data.zip"))
        >>> print(f"Size: {info['size'] / 1e9:.2f} GB")
        Size: 5.23 GB
    """
    try:
        if not path.exists():
            return {"exists": False}

        stat = path.stat()
        return {
            "exists": True,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "is_dir": path.is_dir(),
            "is_file": path.is_file(),
        }
    except (PermissionError, OSError):
        return {"exists": False, "error": "Permission denied or inaccessible"}


def list_directory(path: Path, pattern: Optional[str] = None) -> list[Path]:
    """List contents of a directory.

    Args:
        path: Directory path to list.
        pattern: Optional glob pattern to filter results (e.g., "*.zip").

    Returns:
        List of Path objects for directory contents.

    Raises:
        SharedStorageError: If directory cannot be listed.

    Examples:
        >>> files = list_directory(Path("/shared/storage/jobs/job_123"))
        >>> zip_files = list_directory(Path("/shared/storage"), pattern="*.zip")
    """
    try:
        if pattern:
            return list(path.glob(pattern))
        else:
            return list(path.iterdir())
    except PermissionError as e:
        raise SharedStorageError(
            f"Permission denied listing directory '{path}'."
        ) from e
    except OSError as e:
        raise SharedStorageError(f"Failed to list directory '{path}': {e}") from e


def check_disk_space(path: Path, required_bytes: int) -> bool:
    """Check if sufficient disk space is available at path.

    Args:
        path: Path to check disk space for.
        required_bytes: Number of bytes required.

    Returns:
        True if sufficient space available, False otherwise.

    Examples:
        >>> # Check if 5GB available
        >>> if check_disk_space(Path("/shared/storage"), 5 * 1024**3):
        ...     print("Enough space for 5GB file")
    """
    try:
        stat = shutil.disk_usage(path)
        return stat.free >= required_bytes
    except OSError:
        # If we can't check, assume insufficient space (fail-safe)
        return False


def get_disk_usage(path: Path) -> dict:
    """Get disk usage statistics for the filesystem containing path.

    Args:
        path: Path to check disk usage for.

    Returns:
        Dictionary with keys:
            - total: Total disk space in bytes
            - used: Used disk space in bytes
            - free: Free disk space in bytes
            - percent_used: Percentage of disk used

    Examples:
        >>> usage = get_disk_usage(Path("/shared/storage"))
        >>> print(f"Free: {usage['free'] / 1e9:.1f} GB ({100 - usage['percent_used']:.1f}% free)")
        Free: 423.5 GB (42.3% free)
    """
    try:
        stat = shutil.disk_usage(path)
        return {
            "total": stat.total,
            "used": stat.used,
            "free": stat.free,
            "percent_used": (stat.used / stat.total * 100) if stat.total > 0 else 0,
        }
    except OSError as e:
        raise SharedStorageError(f"Failed to get disk usage for '{path}': {e}") from e


class StorageResolver:
    """Bridge between StorageConfig and filesystem operations.

    This class provides utilities for resolving paths between logical
    (backend_name, user_subdir, relative_path) and absolute filesystem paths.
    It's useful for:
    - Converting user-provided paths to absolute paths for file operations
    - Converting absolute paths back to logical paths for protocol messages
    - Finding which backend an absolute path belongs to

    Example:
        >>> from sleap_rtc.config import get_config
        >>> resolver = StorageResolver(get_config().storage)
        >>>
        >>> # Resolve logical path to absolute
        >>> abs_path = resolver.resolve("vast", "user", "project/data.zip")
        >>> # -> /mnt/vast/user/project/data.zip
        >>>
        >>> # Convert absolute path back to logical
        >>> backend, user, rel = resolver.to_relative(abs_path)
        >>> # -> ("vast", "user", "project/data.zip")
    """

    def __init__(self, storage_config: "StorageConfig") -> None:
        """Initialize StorageResolver with a StorageConfig.

        Args:
            storage_config: The StorageConfig instance to use for resolution.
        """
        self._config = storage_config

    @property
    def config(self) -> "StorageConfig":
        """Get the underlying StorageConfig."""
        return self._config

    def resolve(
        self,
        backend_name: str,
        user_subdir: str,
        relative_path: str,
        validate: bool = True,
    ) -> Path:
        """Resolve a logical path to an absolute filesystem path.

        Combines: backend.base_path / user_subdir / relative_path

        Args:
            backend_name: Name of the storage backend (e.g., "vast").
            user_subdir: User's subdirectory (e.g., "amick").
            relative_path: Path relative to user's directory (e.g., "project/data.zip").
            validate: If True, validate that the path stays within the backend's
                base_path (prevents path traversal). Default: True.

        Returns:
            Absolute path on the local filesystem.

        Raises:
            StorageBackendNotFoundError: If backend doesn't exist.
            PathValidationError: If validate=True and path escapes base_path.

        Example:
            >>> path = resolver.resolve("vast", "user", "project/data.zip")
            PosixPath('/mnt/vast/user/project/data.zip')
        """
        # Import here to avoid circular dependency
        from sleap_rtc.config import StoragePathValidationError

        if validate:
            path = self._config.validate_and_resolve_path(
                backend_name, user_subdir, relative_path
            )
        else:
            path = self._config.resolve_path(backend_name, user_subdir, relative_path)

        return path

    def to_relative(
        self,
        absolute_path: Path,
        backend_name: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """Convert an absolute path to logical components.

        Decomposes an absolute path into (backend_name, user_subdir, relative_path).
        If backend_name is not provided, attempts to find the matching backend
        by checking which backend's base_path contains the absolute path.

        Args:
            absolute_path: The absolute path to decompose.
            backend_name: Optional backend name. If not provided, will attempt
                to auto-detect by finding the backend whose base_path contains
                the path.

        Returns:
            Tuple of (backend_name, user_subdir, relative_path).
            Note: user_subdir is the first path component after base_path,
            and relative_path is everything after that.

        Raises:
            PathValidationError: If path doesn't belong to any backend or
                the specified backend.
            StorageBackendNotFoundError: If specified backend doesn't exist.

        Example:
            >>> backend, user, rel = resolver.to_relative(
            ...     Path("/mnt/vast/user/project/data.zip")
            ... )
            >>> print(f"{backend=}, {user=}, {rel=}")
            backend='vast', user='user', rel='project/data.zip'
        """
        resolved_path = absolute_path.resolve()

        # If backend is specified, use it
        if backend_name:
            backend = self._config.get_backend(backend_name)
            return self._decompose_path(resolved_path, backend_name, backend.base_path)

        # Auto-detect backend by finding which base_path contains this path
        for name in self._config.list_backends():
            backend = self._config.get_backend(name)
            try:
                return self._decompose_path(resolved_path, name, backend.base_path)
            except PathValidationError:
                continue  # Try next backend

        # No backend found
        available = ", ".join(self._config.list_backends()) or "(none)"
        raise PathValidationError(
            f"Path '{absolute_path}' does not belong to any configured storage backend. "
            f"Available backends: {available}"
        )

    def _decompose_path(
        self,
        absolute_path: Path,
        backend_name: str,
        base_path: Path,
    ) -> Tuple[str, str, str]:
        """Decompose an absolute path relative to a base_path.

        Args:
            absolute_path: The resolved absolute path.
            backend_name: Name of the backend.
            base_path: The backend's base path.

        Returns:
            Tuple of (backend_name, user_subdir, relative_path).

        Raises:
            PathValidationError: If path is not within base_path.
        """
        resolved_base = base_path.resolve()

        try:
            # Get path relative to base
            rel_to_base = absolute_path.relative_to(resolved_base)
        except ValueError as e:
            raise PathValidationError(
                f"Path '{absolute_path}' is not within backend '{backend_name}' "
                f"base path '{base_path}'"
            ) from e

        # Split into parts
        parts = rel_to_base.parts

        if len(parts) == 0:
            # Path is exactly the base path
            return (backend_name, "", "")
        elif len(parts) == 1:
            # Just user_subdir, no relative path
            return (backend_name, parts[0], "")
        else:
            # user_subdir + relative_path
            user_subdir = parts[0]
            relative_path = str(Path(*parts[1:]))
            return (backend_name, user_subdir, relative_path)

    def find_backend_for_path(self, absolute_path: Path) -> Optional[str]:
        """Find which backend (if any) contains the given absolute path.

        Args:
            absolute_path: The path to check.

        Returns:
            Backend name if path is within a configured backend, None otherwise.

        Example:
            >>> backend = resolver.find_backend_for_path(
            ...     Path("/mnt/vast/user/data.zip")
            ... )
            >>> print(backend)
            'vast'
        """
        resolved_path = absolute_path.resolve()

        for name in self._config.list_backends():
            backend = self._config.get_backend(name)
            try:
                resolved_path.relative_to(backend.base_path.resolve())
                return name
            except ValueError:
                continue

        return None

    def is_path_in_backend(self, absolute_path: Path, backend_name: str) -> bool:
        """Check if an absolute path is within a specific backend.

        Args:
            absolute_path: The path to check.
            backend_name: The backend to check against.

        Returns:
            True if path is within the backend's base_path.

        Example:
            >>> resolver.is_path_in_backend(
            ...     Path("/mnt/vast/user/data.zip"), "vast"
            ... )
            True
        """
        backend = self._config.get_backend(backend_name)
        resolved_path = absolute_path.resolve()

        try:
            resolved_path.relative_to(backend.base_path.resolve())
            return True
        except ValueError:
            return False

    def validate_path(self, absolute_path: Path, backend_name: str) -> Path:
        """Validate that a path is within a backend's base_path.

        Uses the existing validate_path_in_root function for security validation.

        Args:
            absolute_path: The path to validate.
            backend_name: The backend to validate against.

        Returns:
            The validated resolved path.

        Raises:
            StorageBackendNotFoundError: If backend doesn't exist.
            PathValidationError: If path escapes base_path.
        """
        backend = self._config.get_backend(backend_name)
        return validate_path_in_root(absolute_path, backend.base_path)

    def __repr__(self) -> str:
        """Return string representation."""
        backends = ", ".join(self._config.list_backends())
        return f"StorageResolver(backends=[{backends}])"
