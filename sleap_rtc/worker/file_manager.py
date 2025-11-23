"""File transfer and storage management for worker nodes.

This module handles file transfer via RTC data channels, file compression,
and shared storage path validation for SLEAP-RTC workers.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from aiortc import RTCDataChannel

from sleap_rtc.config import SharedStorageConfig, SharedStorageConfigError
from sleap_rtc.filesystem import (
    safe_mkdir,
    to_relative_path,
    to_absolute_path,
    validate_path_in_root,
    PathValidationError,
)
from sleap_rtc.protocol import (
    MSG_PATH_VALIDATED,
    MSG_PATH_ERROR,
    format_message,
)


class FileManager:
    """Manages file transfer, compression, and shared storage operations.

    This class handles file transfer via RTC data channels, compression of
    training results, and validation of shared storage paths for security.

    Attributes:
        chunk_size: Size of chunks for file transfer (default 32KB).
        shared_storage_root: Path to shared storage root directory.
        shared_jobs_dir: Directory for shared storage jobs.
        zipped_file: Path to most recently created zip file.
        save_dir: Local directory for saving files.
        output_dir: Output directory for job results.
    """

    def __init__(
        self,
        chunk_size: int = 32 * 1024,
        shared_storage_root: Optional[Path] = None,
    ):
        """Initialize file manager.

        Args:
            chunk_size: Size of chunks for file transfer (default 32KB).
            shared_storage_root: Optional path to shared storage root.
        """
        self.chunk_size = chunk_size
        self.save_dir = "."
        self.zipped_file = ""
        self.output_dir = ""

        # Initialize shared storage configuration
        try:
            self.shared_storage_root = shared_storage_root or SharedStorageConfig.get_shared_storage_root()
            if self.shared_storage_root:
                # Create jobs directory for shared storage transfers
                self.shared_jobs_dir = self.shared_storage_root / "jobs"
                safe_mkdir(self.shared_jobs_dir)
                logging.info(
                    f"FileManager shared storage enabled: {self.shared_storage_root}"
                )
            else:
                self.shared_jobs_dir = None
                logging.info(
                    "FileManager shared storage not configured, will use RTC transfer"
                )
        except SharedStorageConfigError as e:
            logging.error(f"FileManager shared storage configuration error: {e}")
            logging.info("Falling back to RTC transfer")
            self.shared_storage_root = None
            self.shared_jobs_dir = None

    async def send_file(self, channel: RTCDataChannel, file_path: str, output_dir: str = ""):
        """Send a file to client via RTC data channel.

        Args:
            channel: RTC data channel for sending file.
            file_path: Path to file to send.
            output_dir: Output directory hint for client (where to save file).
        """
        if channel.readyState != "open":
            logging.error(f"Data channel not open: {channel.readyState}")
            return

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        # Send file metadata
        output_hint = output_dir or self.output_dir
        channel.send(f"FILE_META::{file_name}:{file_size}:{output_hint}")
        logging.info(f"Sending file: {file_name} ({file_size} bytes)")

        # Send file in chunks
        with open(file_path, "rb") as file:
            bytes_sent = 0
            while chunk := file.read(self.chunk_size):
                # Flow control: wait if buffer is too full
                while (
                    channel.bufferedAmount is not None
                    and channel.bufferedAmount > 16 * 1024 * 1024
                ):
                    await asyncio.sleep(0.1)

                channel.send(chunk)
                bytes_sent += len(chunk)

        # Signal end of file
        channel.send("END_OF_FILE")
        logging.info("File sent successfully")

    async def zip_results(self, file_name: str, dir_path: Optional[str] = None):
        """Zip directory contents into archive.

        Args:
            file_name: Name of the zip file to create (without .zip extension).
            dir_path: Path to directory to zip.

        Returns:
            Path to created zip file, or None if failed.
        """
        logging.info("Zipping results...")
        if not dir_path or not Path(dir_path).exists():
            logging.error(f"Directory does not exist: {dir_path}")
            return None

        try:
            shutil.make_archive(file_name, "zip", dir_path)
            self.zipped_file = f"{file_name}.zip"
            logging.info(f"Results zipped to {self.zipped_file}")
            return self.zipped_file
        except Exception as e:
            logging.error(f"Error zipping results: {e}")
            return None

    async def unzip_results(self, file_path: str):
        """Unzip archive to save directory.

        Args:
            file_path: Path to zip file to extract.

        Returns:
            Path to extracted directory, or None if failed.
        """
        logging.info("Unzipping results...")
        if not Path(file_path).exists():
            logging.error(f"File does not exist: {file_path}")
            return None

        try:
            shutil.unpack_archive(file_path, self.save_dir)
            logging.info(f"Results unzipped from {file_path} to {self.save_dir}")

            # Calculate unzipped directory path
            original_name = Path(file_path).stem  # Remove .zip extension
            unzipped_dir = f"{self.save_dir}/{original_name}"
            logging.info(f"Unzipped contents to {unzipped_dir}")
            return unzipped_dir
        except Exception as e:
            logging.error(f"Error unzipping results: {e}")
            return None

    async def validate_shared_input_path(
        self,
        relative_path_str: str,
        channel: RTCDataChannel
    ) -> Optional[Path]:
        """Validate shared storage input path and send confirmation.

        Args:
            relative_path_str: Relative path from client.
            channel: RTC data channel for sending validation response.

        Returns:
            Validated absolute path, or None if validation failed.
        """
        if not self.shared_storage_root:
            error_msg = "Shared storage not configured on worker"
            logging.error(error_msg)
            channel.send(format_message(MSG_PATH_ERROR, error_msg))
            return None

        try:
            # Convert relative path to absolute
            relative_path = Path(relative_path_str)
            absolute_path = to_absolute_path(
                relative_path, self.shared_storage_root
            )

            # Validate path exists
            if not absolute_path.exists():
                error_msg = f"Input path does not exist: {absolute_path}"
                logging.error(error_msg)
                channel.send(format_message(MSG_PATH_ERROR, error_msg))
                return None

            # Security: Validate path is within shared root
            validated_path = validate_path_in_root(
                absolute_path, self.shared_storage_root
            )

            logging.info(f"✓ Input path validated: {validated_path}")
            channel.send(format_message(MSG_PATH_VALIDATED, "input"))
            return validated_path

        except PathValidationError as e:
            error_msg = f"Path validation failed: {e}"
            logging.error(error_msg)
            channel.send(format_message(MSG_PATH_ERROR, error_msg))
            return None
        except Exception as e:
            error_msg = f"Error processing input path: {e}"
            logging.error(error_msg)
            channel.send(format_message(MSG_PATH_ERROR, error_msg))
            return None

    async def validate_shared_output_path(
        self,
        relative_path_str: str,
        channel: RTCDataChannel
    ) -> Optional[Path]:
        """Validate shared storage output path and create directory.

        Args:
            relative_path_str: Relative path from client.
            channel: RTC data channel for sending validation response.

        Returns:
            Validated absolute path, or None if validation failed.
        """
        if not self.shared_storage_root:
            error_msg = "Shared storage not configured on worker"
            logging.error(error_msg)
            channel.send(format_message(MSG_PATH_ERROR, error_msg))
            return None

        try:
            # Convert relative path to absolute
            relative_path = Path(relative_path_str)
            absolute_path = to_absolute_path(
                relative_path, self.shared_storage_root
            )

            # Security: Validate path is within shared root
            validated_path = validate_path_in_root(
                absolute_path, self.shared_storage_root
            )

            # Create output directory
            safe_mkdir(validated_path)

            # Update output_dir for file transfer metadata
            self.output_dir = str(validated_path)

            logging.info(f"✓ Output path validated and created: {validated_path}")
            channel.send(format_message(MSG_PATH_VALIDATED, "output"))
            return validated_path

        except PathValidationError as e:
            error_msg = f"Path validation failed: {e}"
            logging.error(error_msg)
            channel.send(format_message(MSG_PATH_ERROR, error_msg))
            return None
        except Exception as e:
            error_msg = f"Error processing output path: {e}"
            logging.error(error_msg)
            channel.send(format_message(MSG_PATH_ERROR, error_msg))
            return None

    def is_shared_storage_available(self) -> bool:
        """Check if shared storage is configured and available.

        Returns:
            True if shared storage is available, False otherwise.
        """
        return self.shared_storage_root is not None

    def get_relative_path(self, absolute_path: Path) -> Path:
        """Convert absolute path to relative path within shared storage.

        Args:
            absolute_path: Absolute path to convert.

        Returns:
            Relative path within shared storage root.

        Raises:
            ValueError: If shared storage not configured.
        """
        if not self.shared_storage_root:
            raise ValueError("Shared storage not configured")

        return to_relative_path(absolute_path, self.shared_storage_root)
