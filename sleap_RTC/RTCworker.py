"""Entry point for sleap_RTC worker CLI."""
import asyncio
import uuid
import logging

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
from pathlib import Path
from sleap_RTC.worker.worker_class import RTCWorkerClient

def run_worker():
    """Create RTCWorkerClient and start it."""
    # Create the worker instance.
    worker = RTCWorkerClient()

    # Create the RTCPeerConnection object.
    pc = RTCPeerConnection()

    # Generate a unique peer ID for the worker.
    peer_id = f"worker-{uuid.uuid4()}"

    # Run the worker.
    try:
        asyncio.run(
            worker.run_worker(
                pc=pc,
                peer_id=peer_id,
                DNS="ws://ec2-54-176-92-10.us-west-1.compute.amazonaws.com",
                port_number=8080
            )
        )
    except KeyboardInterrupt:
        logging.info("Worker interrupted by user. Shutting down...")
    finally:
        logging.info("Worker exiting...")
