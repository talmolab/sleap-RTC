"""File transfer and storage management for worker nodes.

This module handles file transfer via RTC data channels, file compression,
and Worker I/O paths validation for SLEAP-RTC workers.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from aiortc import RTCDataChannel

if TYPE_CHECKING:
    from sleap_rtc.config import WorkerIOConfig

from sleap_rtc.filesystem import safe_mkdir
from sleap_rtc.protocol import (
    MSG_FILE_EXISTS,
    MSG_FILE_NOT_FOUND,
    MSG_JOB_OUTPUT_PATH,
    format_message,
)


class FileManager:
    """Manages file transfer, compression, and Worker I/O paths operations.

    This class handles file transfer via RTC data channels, compression of
    training results, and validation of Worker I/O paths for security.

    Attributes:
        chunk_size: Size of chunks for file transfer (default 32KB).
        zipped_file: Path to most recently created zip file.
        save_dir: Local directory for saving files.
        output_dir: Output directory for job results.
        io_config: Worker I/O configuration (input/output paths).
        current_input_file: Resolved input file path for current job.
    """

    def __init__(
        self,
        chunk_size: int = 32 * 1024,
        io_config: Optional["WorkerIOConfig"] = None,
    ):
        """Initialize file manager.

        Args:
            chunk_size: Size of chunks for file transfer (default 32KB).
            io_config: Optional Worker I/O configuration for I/O paths mode.
        """
        self.chunk_size = chunk_size
        self.save_dir = "."
        self.zipped_file = ""
        self.output_dir = ""
        self.io_config = io_config
        self.current_input_file: Optional[Path] = None  # Resolved input file path

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

    def validate_input_file(
        self,
        filename: str,
        job_id: str,
        channel: RTCDataChannel,
    ) -> Optional[Path]:
        """Validate that a file exists in the worker's input directory.

        This method is used for Worker I/O Paths mode, where the client sends
        just a filename and the worker validates it exists in input_path.

        Args:
            filename: Filename provided by client (no path components allowed).
            job_id: Job ID for output directory creation.
            channel: RTC data channel for sending validation response.

        Returns:
            Validated absolute path to the input file, or None if validation failed.
        """
        if not self.io_config:
            error_msg = "Worker I/O paths not configured"
            logging.error(error_msg)
            channel.send(format_message(MSG_FILE_NOT_FOUND, filename, error_msg))
            return None

        # Security: Reject filenames with path separators or parent references
        if os.sep in filename or (os.altsep and os.altsep in filename):
            error_msg = "Invalid filename: contains path separator"
            logging.error(f"{error_msg}: {filename}")
            channel.send(format_message(MSG_FILE_NOT_FOUND, filename, error_msg))
            return None

        if ".." in filename:
            error_msg = "Invalid filename: contains parent reference"
            logging.error(f"{error_msg}: {filename}")
            channel.send(format_message(MSG_FILE_NOT_FOUND, filename, error_msg))
            return None

        # Resolve full path
        input_path = self.io_config.input_path / filename

        # Check if file exists
        if not input_path.exists():
            error_msg = "File does not exist"
            logging.error(f"{error_msg}: {input_path}")
            channel.send(format_message(MSG_FILE_NOT_FOUND, filename, error_msg))
            return None

        # Check if it's a file (not a directory)
        if not input_path.is_file():
            error_msg = "Path is not a file"
            logging.error(f"{error_msg}: {input_path}")
            channel.send(format_message(MSG_FILE_NOT_FOUND, filename, error_msg))
            return None

        # Check if file is readable
        if not os.access(input_path, os.R_OK):
            error_msg = "File is not readable"
            logging.error(f"{error_msg}: {input_path}")
            channel.send(format_message(MSG_FILE_NOT_FOUND, filename, error_msg))
            return None

        # Store the resolved input file path for later use
        self.current_input_file = input_path
        logging.info(f"✓ Input file validated: {input_path}")

        # Create job output directory: {output_path}/jobs/{job_id}/
        job_output_dir = self.io_config.output_path / "jobs" / job_id
        try:
            safe_mkdir(job_output_dir)
            self.output_dir = str(job_output_dir)
            logging.info(f"✓ Job output directory created: {job_output_dir}")
        except Exception as e:
            error_msg = f"Failed to create output directory: {e}"
            logging.error(error_msg)
            channel.send(format_message(MSG_FILE_NOT_FOUND, filename, error_msg))
            return None

        # Send success response
        channel.send(format_message(MSG_FILE_EXISTS, filename))

        # Send the output path to the client so they know where to find results
        channel.send(format_message(MSG_JOB_OUTPUT_PATH, str(job_output_dir)))

        return input_path
