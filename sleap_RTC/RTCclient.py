import asyncio
import uuid
import logging

from sleap_RTC.client.client_class import RTCClient


def run_RTCclient(session_string: str, pkg_path: str, zmq_ports: dict, **kwargs):
    """Standalone function to run the RTC client with CLI arguments.
    
    Args:
        session_string: Session string to connect to the worker
        pkg_path: Path to the SLEAP training/inference package  
        zmq_ports: Dict with 'controller' and 'publish' port numbers
        **kwargs: Additional arguments (currently unused)
    """
    # Create client instance
    client = RTCClient(
        DNS="ws://ec2-54-176-92-10.us-west-1.compute.amazonaws.com",
        port_number="8080",
        gui=False  # Indicate that this is running in CLI mode
    )
    
    # Map CLI arguments to method parameters
    # Note: session_string will be handled within run_client method
    # For now, we'll pass pkg_path as file_path
    method_kwargs = {
        'file_path': pkg_path,
        'output_dir': '.',
        'zmq_ports': [zmq_ports.get('controller', 9000), zmq_ports.get('publish', 9001)],  # Convert dict to list
        'config_info_list': None, # None since CLI (used for updating LossViewer)
        # 'win': None, # None since CLI
        'session_string': session_string # Pass session_string here (CLI)
    }
    
    # Run the async method
    try:
        asyncio.run(client.run_client(**method_kwargs))
    except KeyboardInterrupt:
        logging.info("Client interrupted by user. Shutting down...")
    except Exception as e:
        logging.error(f"Client error: {e}")
        raise

