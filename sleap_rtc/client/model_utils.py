"""Utilities for model file detection and management."""

import hashlib
import yaml
from pathlib import Path
from typing import Optional, List
from loguru import logger


def find_checkpoint_files(path: Path) -> List[Path]:
    """Find checkpoint files in a directory.

    Args:
        path: Directory path to search for checkpoint files

    Returns:
        List of paths to checkpoint files (.ckpt, .h5, .pth, .pt)

    Raises:
        ValueError: If path doesn't exist or isn't a directory
    """
    if not path.exists():
        raise ValueError(f"Path does not exist: {path}")

    if not path.is_dir():
        raise ValueError(f"Path is not a directory: {path}")

    # Supported checkpoint extensions
    checkpoint_extensions = [".ckpt", ".h5", ".pth", ".pt"]

    checkpoint_files = []
    for ext in checkpoint_extensions:
        checkpoint_files.extend(path.glob(f"*{ext}"))
        # Also search in subdirectories (one level deep)
        checkpoint_files.extend(path.glob(f"*/*{ext}"))

    # Sort by name for consistent ordering
    checkpoint_files = sorted(set(checkpoint_files))

    logger.debug(f"Found {len(checkpoint_files)} checkpoint files in {path}")
    return checkpoint_files


def detect_model_type(path: Path) -> Optional[str]:
    """Detect model type from training_config.yaml if present.

    Args:
        path: Directory path to search for training configuration

    Returns:
        Model type string (e.g., "centroid", "topdown", "bottomup") or None if not detected
    """
    # Look for training_config.yaml in the directory
    config_files = [
        path / "training_config.yaml",
        path / "training_config.yml",
        path / "config.yaml",
        path / "config.yml",
    ]

    for config_file in config_files:
        if config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    config = yaml.safe_load(f)

                # Try different possible keys for model type
                # SLEAP training configs typically have a 'model' or 'model_type' field
                if isinstance(config, dict):
                    # Check common model type keys
                    if 'model_type' in config:
                        model_type = config['model_type']
                        logger.info(f"Detected model type from config: {model_type}")
                        return model_type

                    # Check for model configuration structure
                    if 'model' in config and isinstance(config['model'], dict):
                        if 'type' in config['model']:
                            model_type = config['model']['type']
                            logger.info(f"Detected model type from config: {model_type}")
                            return model_type

                    # Check for backbone type (common in SLEAP models)
                    if 'backbone' in config:
                        backbone = config['backbone']
                        if isinstance(backbone, dict) and 'type' in backbone:
                            # Infer from backbone type
                            backbone_type = backbone['type'].lower()
                            if 'centroid' in backbone_type:
                                return 'centroid'
                            elif 'topdown' in backbone_type or 'top_down' in backbone_type:
                                return 'topdown'
                            elif 'bottomup' in backbone_type or 'bottom_up' in backbone_type:
                                return 'bottomup'

                    logger.debug(f"Config found but could not determine model type from: {config_file}")

            except Exception as e:
                logger.warning(f"Error reading config file {config_file}: {e}")
                continue

    logger.debug(f"Could not detect model type for {path}")
    return None


def calculate_model_size(path: Path) -> int:
    """Calculate total size of model checkpoint files.

    Args:
        path: Directory path containing model files

    Returns:
        Total size in bytes of all checkpoint files
    """
    checkpoint_files = find_checkpoint_files(path)

    total_size = 0
    for file_path in checkpoint_files:
        try:
            total_size += file_path.stat().st_size
        except OSError as e:
            logger.warning(f"Could not get size of {file_path}: {e}")

    logger.debug(f"Total model size: {total_size} bytes ({total_size / (1024**2):.2f} MB)")
    return total_size


def validate_checkpoint_files(path: Path) -> bool:
    """Validate that checkpoint files exist and are readable.

    Args:
        path: Directory path containing model files

    Returns:
        True if all checkpoint files are valid, False otherwise
    """
    checkpoint_files = find_checkpoint_files(path)

    if not checkpoint_files:
        logger.error(f"No checkpoint files found in {path}")
        return False

    all_valid = True
    for file_path in checkpoint_files:
        try:
            # Try to open the file to ensure it's readable
            with open(file_path, 'rb') as f:
                # Read first few bytes to verify file is not corrupted
                f.read(1024)
            logger.debug(f"Validated checkpoint: {file_path}")
        except Exception as e:
            logger.error(f"Invalid checkpoint file {file_path}: {e}")
            all_valid = False

    return all_valid


def generate_model_id_from_config(path: Path) -> str:
    """Generate a model ID from the training config file hash.

    Args:
        path: Directory path containing training configuration

    Returns:
        8-character hex string model ID
    """
    # Look for training_config.yaml
    config_files = [
        path / "training_config.yaml",
        path / "training_config.yml",
        path / "config.yaml",
        path / "config.yml",
    ]

    for config_file in config_files:
        if config_file.exists():
            try:
                with open(config_file, 'rb') as f:
                    config_hash = hashlib.md5(f.read()).hexdigest()[:8]
                logger.debug(f"Generated model ID from config: {config_hash}")
                return config_hash
            except Exception as e:
                logger.warning(f"Error hashing config file {config_file}: {e}")

    # If no config found, generate random ID
    import secrets
    random_id = secrets.token_hex(4)
    logger.debug(f"Generated random model ID: {random_id}")
    return random_id


def format_size(size_bytes: int) -> str:
    """Format file size in human-readable format.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted string (e.g., "125.5 MB", "1.2 GB")
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"
