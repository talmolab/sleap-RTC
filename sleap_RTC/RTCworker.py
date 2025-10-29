"""Entry point for sleap_RTC worker CLI."""
import asyncio
import uuid
import logging

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
from pathlib import Path
from sleap_RTC.worker.worker_class import RTCWorkerClient
from sleap_RTC.config import get_config

def run_RTCworker():
    """Create RTCWorkerClient and start it."""
    # Create the worker instance.
    worker = RTCWorkerClient()

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
                port_number=8080
            )
        )
    except KeyboardInterrupt:
        logging.info("Worker interrupted by user. Shutting down...")
    finally:
        logging.info("Worker exiting...")
