import asyncio
import uuid
import logging

from sleap_rtc.client.client_class import RTCClient


def run_RTCclient(
    session_string: str = None,
    pkg_path: str = None,
    zmq_ports: dict = None,
    room_id: str = None,
    token: str = None,
    worker_id: str = None,
    auto_select: bool = False,
    min_gpu_memory: int = None,
    **kwargs
):
    """Standalone function to run the RTC client with CLI arguments.

    Args:
        session_string: Session string for direct connection to specific worker
        pkg_path: Path to the SLEAP training/inference package
        zmq_ports: Dict with 'controller' and 'publish' port numbers
        room_id: Room ID for room-based worker discovery
        token: Room token for authentication
        worker_id: Specific worker peer-id to connect to (skips discovery)
        auto_select: Automatically select best worker by GPU memory
        min_gpu_memory: Minimum GPU memory in MB for worker filtering
        **kwargs: Additional arguments passed to run_client
    """
    # Create client instance (DNS will be loaded from config)
    client = RTCClient(
        DNS=None,  # Use config
        port_number="8080",
        gui=False  # Indicate that this is running in CLI mode
    )

    # Map CLI arguments to method parameters
    method_kwargs = {
        'file_path': pkg_path,
        'output_dir': '.',
        'zmq_ports': [zmq_ports.get('controller', 9000), zmq_ports.get('publish', 9001)],  # Convert dict to list
        'config_info_list': None, # None since CLI (used for updating LossViewer)
        'session_string': session_string,
        # Room-based connection parameters
        'room_id': room_id,
        'token': token,
        'worker_id': worker_id,
        'auto_select': auto_select,
        'min_gpu_memory': min_gpu_memory,
    }

    # Add any additional kwargs
    method_kwargs.update(kwargs)

    # Run the async method
    try:
        asyncio.run(client.run_client(**method_kwargs))
    except KeyboardInterrupt:
        logging.info("Client interrupted by user. Shutting down...")
    except Exception as e:
        logging.error(f"Client error: {e}")
        raise

