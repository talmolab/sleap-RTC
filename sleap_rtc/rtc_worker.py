"""Entry point for sleap_rtc worker CLI."""

import asyncio
import uuid
import logging

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
from pathlib import Path
from sleap_rtc.worker.worker_class import RTCWorkerClient
from sleap_rtc.config import get_config


def run_RTCworker(room_id=None, token=None, shared_storage_root=None):
    """Create RTCWorkerClient and start it.

    Args:
        room_id: Optional room ID to join. If not provided, a new room will be created.
        token: Optional room token for authentication. Required if room_id is provided.
        shared_storage_root: Optional path to shared storage root. If not provided,
            uses SHARED_STORAGE_ROOT env var or falls back to RTC transfer.
    """
    # Create the worker instance.
    worker = RTCWorkerClient(shared_storage_root=shared_storage_root)

    # Create the RTCPeerConnection object.
    pc = RTCPeerConnection()

    # Get configuration
    config = get_config()

    # Run the worker.
    try:
        asyncio.run(
            worker.run_worker(
                pc=pc,
                DNS=config.signaling_websocket,
                port_number=8080,
                room_id=room_id,
                token=token,
            )
        )
    except KeyboardInterrupt:
        logging.info("Worker interrupted by user. Shutting down...")
    finally:
        logging.info("Worker exiting...")
