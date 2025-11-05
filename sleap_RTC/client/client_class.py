import asyncio
import argparse
import base64
import uuid
import requests
import websockets
import json
import jsonpickle
import logging
import os
import re
import zmq
import platform
import time

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
from functools import partial
from pathlib import Path
from typing import List, Optional, Text, Tuple
from websockets.client import ClientConnection

from sleap_rtc.config import get_config
from sleap_rtc.exceptions import (
    NoWorkersAvailableError,
    NoWorkersAcceptedError,
    JobFailedError,
    WorkerDiscoveryError
)

# Setup logging.
logging.basicConfig(level=logging.INFO)

# Global constants.
CHUNK_SIZE = 64 * 1024
MAX_RECONNECT_ATTEMPTS = 5
RETRY_DELAY = 5  # seconds

class RTCClient:
    def __init__(
        self,
        DNS: Optional[str] = None,
        port_number: str = "8080",
        gui: bool = False
    ):
        # Initialize RTC peer connection and websocket.
        self.pc = RTCPeerConnection()
        self.websocket: ClientConnection = None
        self.data_channel: RTCDataChannel = None
        self.pc.on("iceconnectionstatechange", self.on_iceconnectionstatechange)

        # Initialize given parameters.
        # Use config if DNS not provided via CLI
        config = get_config()
        self.DNS = DNS if DNS is not None else config.signaling_websocket
        self.port_number = port_number
        self.gui = gui

        # Other variables.
        self.received_files = {}
        self.target_worker = None
        self.reconnecting = False
        self.current_job_id = None  # For tracking active job submissions
    

    def parse_session_string(self, session_string: str):
        prefix = "sleap-session:"
        if not session_string.startswith(prefix):
            raise ValueError(f"Session string must start with '{prefix}'")
        
        encoded = session_string[len(prefix):]
        try:
            json_str = base64.urlsafe_b64decode(encoded).decode()
            data = json.loads(json_str)
            return {
                "room_id": data.get("r"),
                "token": data.get("t"),
                "peer_id": data.get("p"),
            }
        except jsonpickle.UnpicklingError as e:
            raise ValueError(f"Failed to decode session string: {e}")
        

    def request_peer_room_deletion(self, peer_id: str):
        """Requests the signaling server to delete the room and associated user/worker."""

        config = get_config()
        url = config.get_http_endpoint("/delete-peers-and-room")
        json = {
            "peer_id": peer_id,
        }

        # Pass the Cognito usernmae (peer_id) to identify which room/peers to delete.
        response = requests.post(url, json=json)

        if response.status_code == 200:
            return # Success
        else:
            logging.error(f"Failed to delete room and peer: {response.text}")
            return None


    def request_anonymous_signin(self) -> str:
        """Request an anonymous token from Signaling Server."""

        config = get_config()
        url = config.get_http_endpoint("/anonymous-signin")
        response = requests.post(url)

        if response.status_code == 200:
            return response.json() # should be string type
        else:
            logging.error(f"Failed to get anonymous token: {response.text}")
            return None
        
    
    async def start_zmq_listener(self, channel: RTCDataChannel, zmq_address: str = "tcp://127.0.0.1:9000"):
        """Starts a ZMQ ctrl SUB socket to listen for ZMQ commands from the LossViewer.
        
        Args:
            channel: The RTCDataChannel to send progress reports to.
            zmq_address: Address of the ZMQ socket to connect to.
        Returns:
            None
        """
        # Use LossViewer's already initialized ZMQ control socket.
        # Initialize SUB socket.
        logging.info("Starting new ZMQ listener socket...")
        context = zmq.Context()
        socket = context.socket(zmq.SUB)

        logging.info(f"Connecting to ZMQ address: {zmq_address}")
        socket.connect(zmq_address)
        socket.setsockopt_string(zmq.SUBSCRIBE, "")

        loop = asyncio.get_event_loop()

        def recv_msg():
            """Receives a message from the ZMQ socket in a non-blocking way.
            
            Returns:
                The received message as a JSON object, or None if no message is available.
            """
            
            try:
                # logging.info("Receiving message from ZMQ...")
                return socket.recv_string(flags=zmq.NOBLOCK)  # or jsonpickle.decode(msg_str) if needed
            except zmq.Again:
                return None

        while True:
            # Send progress as JSON string with prefix.
            msg = await loop.run_in_executor(None, recv_msg)

            if msg:
                try:
                    logging.info(f"Sending ZMQ command to worker: {msg}")
                    channel.send(f"ZMQ_CTRL::{msg}")
                    # logging.info("Progress report sent to client.")
                except Exception as e:
                    logging.error(f"Failed to send ZMQ progress: {e}")
                    
            # Polling interval.
            await asyncio.sleep(0.05)


    def start_zmq_control(self, zmq_address: str = "tcp://127.0.0.1:9001"): # Publish Port
        """Starts a ZMQ ctrl PUB socket to forward ZMQ commands to the LossViewer.
    
        Args:
            zmq_address: Address of the ZMQ socket to connect to.
        Returns:
            None
        """
        # Use LossViewer's already initialized ZMQ control socket.
        # Initialize PUB socket.
        logging.info("Starting new ZMQ control socket...")
        context = zmq.Context()
        socket = context.socket(zmq.PUB)

        logging.info(f"Connecting to ZMQ address: {zmq_address}")
        socket.connect(zmq_address)

        # Set PUB socket for use in other functions.
        self.ctrl_socket = socket
        logging.info("ZMQ control socket initialized.")


    async def clean_exit(self):
        """Cleans up the client connection and closes the peer connection and websocket.
        
        Args:
            pc: RTCPeerConnection object
            websocket: ClientConnection object
        Returns:
            None
        """

        logging.info("Closing WebRTC connection...")
        if self.pc:
            await self.pc.close()

        logging.info("Closing websocket connection...")
        if self.websocket:
            await self.websocket.close()

        logging.info("Cleaning up Cognito and DynamoDB entries...")
        if self.cognito_username:
            self.request_peer_room_deletion(self.cognito_username)
            self.cognito_username = None
        
        logging.info("Client shutdown complete. Exiting...")


    async def reconnect(self):
        """Attempts to reconnect the client to the worker peer by creating a new offer with ICE restart flag.

        Args:
            pc: RTCPeerConnection object
            websocket: ClientConnection object
        Returns:
            bool: True if reconnection was successful, False otherwise
        """

        # Attempt to reconnect.
        while self.reconnect_attempts < MAX_RECONNECT_ATTEMPTS:
            try:
                self.reconnect_attempts += 1
                logging.info(f"Reconnection attempt {self.reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS}...")

                # Create new offer with ICE restart flag.
                logging.info("Creating new offer with manual ICE restart...")
                await self.pc.setLocalDescription(await self.pc.createOffer())

                # Send new offer to the worker via signaling.
                logging.info(f"Sending new offer to worker: {self.target_worker}")
                await self.websocket.send(json.dumps({
                    'type': self.pc.localDescription.type,
                    'sender': self.peer_id, # should be own peer_id (Zoom username)
                    'target': self.target_worker, # should be Worker's peer_id
                    'sdp': self.pc.localDescription.sdp
                }))

                # Wait for connection to complete.
                for _ in range(30):
                    await asyncio.sleep(1)
                    if self.pc.iceConnectionState in ["connected", "completed"]:
                        logging.info("Reconnection successful.")

                        # Clear received files on reconnection.
                        logging.info("Clearing received files on reconnection...")
                        self.received_files.clear()

                        return True

                logging.warning("Reconnection timed out. Retrying...")
        
            except Exception as e:
                logging.error(f"Reconnection failed with error: {e}")

            await asyncio.sleep(RETRY_DELAY)
        
        # Maximum reconnection attempts reached.
        logging.error("Maximum reconnection attempts reached. Exiting...")
        await self.clean_exit()
        return False


    async def handle_connection(self):
        """Handles receiving SDP answer from Worker and ICE candidates from Worker.

        Args:
            pc: RTCPeerConnection object
            websocket: websocket connection object 
        Returns:
            None
        Raises:
            JSONDecodeError: Invalid JSON received
            Exception: An error occurred while handling the message
        """

        # Handle incoming websocket messages.
        try:
            async for message in self.websocket:
                if type(message) == int:
                    logging.info(f"Received int message: {message}")

                data = json.loads(message)

                # Receive answer SDP from worker and set it as this peer's remote description.
                if data.get('type') == 'answer':
                    logging.info(f"Received answer from worker: {data}")

                    await self.pc.setRemoteDescription(RTCSessionDescription(sdp=data.get('sdp'), type=data.get('type')))

                # Handle "trickle ICE" for non-local ICE candidates.
                elif data.get('type') == 'candidate':
                    logging.info("Received ICE candidate")
                    candidate = data.get('candidate')
                    await self.pc.addIceCandidate(candidate)

                # NOT initiator, received quit request from worker.
                elif data.get('type') == 'quit': 
                    logging.info("Worker has quit. Closing connection...")
                    await self.clean_exit()
                    break

                # Handle the "registered_auth" message from the signaling server.
                elif data.get('type') == 'registered_auth':
                    logging.info(f"Client authenticated with server.")

                # Handle peer messages (job responses, status updates, etc.)
                elif data.get('type') == 'peer_message':
                    # Peer messages are handled by _collect_job_responses and _monitor_job_progress
                    # They should be processed by those methods, not here
                    # Just log for debugging
                    payload = data.get('payload', {})
                    logging.debug(f"Received peer message: {payload.get('app_message_type', 'unknown')}")

                # Unhandled message types.
                else:
                    logging.debug(f"Unhandled message: {data}")
        
        except json.JSONDecodeError:
            logging.DEBUG("Invalid JSON received")

        except Exception as e:
            logging.DEBUG(f"Error handling message: {e}")


    async def keep_ice_alive(self):
        """Sends periodic keep-alive messages to the worker peer to maintain the connection."""

        while True:
            await asyncio.sleep(15)
            if self.data_channel.readyState == "open":
                self.data_channel.send(b"KEEP_ALIVE")


    async def send_client_file(self, file_path: str = None, output_dir: str = ""):
        """Handles direct, one-way file transfer from client to be sent to worker peer.

        Args:
            None
        Returns:
            None
        """
        
        # Check channel state before sending file.
        if self.data_channel.readyState != "open":
            logging.info(f"Data channel not open. Ready state is: {self.data_channel.readyState}")
            return 

        # Send file to worker.
        logging.info(f"Given file path {file_path}")
        if not file_path:
            logging.info("No file path entered.")
            return
        if not Path(file_path).exists():
            logging.info("File does not exist.")
            return
        else:
            logging.info(f"Sending {file_path} to worker...")

            # Send package type indicator
            self.data_channel.send("PACKAGE_TYPE::train")

            # Send output directory (where models will be saved).
            output_dir = "models"
            if self.config_info_list:
                output_dir = self.config_info_list[0].config.outputs.runs_folder

            self.data_channel.send(f"OUTPUT_DIR::{output_dir}")

            # Obtain file metadata.
            file_name = Path(file_path).name
            file_size = Path(file_path).stat().st_size

            # Send metadata next.
            self.data_channel.send(f"FILE_META::{file_name}:{file_size}:{self.gui}")

            # Send file in chunks (32 KB).
            with open(file_path, "rb") as file:
                logging.info(f"File opened: {file_path}")
                while chunk := file.read(CHUNK_SIZE):
                    while self.data_channel.bufferedAmount is not None and self.data_channel.bufferedAmount > 16 * 1024 * 1024: # Wait if buffer >16MB
                        await asyncio.sleep(0.1)

                    self.data_channel.send(chunk)

            self.data_channel.send("END_OF_FILE")
            logging.info(f"File sent to worker.")

        # Start ZMQ control socket.
        if self.gui:
            self.start_zmq_control()
            asyncio.create_task(self.start_zmq_listener(self.data_channel))
            logging.info(f'{self.data_channel.label} ZMQ control socket started')
            
        return
    

    async def on_channel_open(self):
        """Event handler function for when the datachannel is open.

        Args:
            None
        Returns:
            None
        """

        # Initiate keep-alive task.
        asyncio.create_task(self.keep_ice_alive())
        logging.info(f"{self.data_channel.label} is open")

        # Initiate file upload to send entire training zip file to worker peer.
        await self.send_client_file(self.file_path, self.output_dir)


    async def on_message(self, message):
        """Event handler function for when a message is received on the datachannel from Worker.

        Args:
            message: The received message, either as a string or bytes.
        Returns:
            None
        """

        # Log the received message.
        logging.info(f"Client received: {message}")
        
        # Handle string and bytes messages differently.
        if isinstance(message, str):
            # if message == b"KEEP_ALIVE":
            #     logging.info("Keep alive message received.")
            #     return
            
            if message == "END_OF_FILE":
                # File transfer complete, save to disk.
                logging.info("File transfer complete. Saving file to disk...")
                file_name, file_data = list(self.received_files.items())[0]

                try:
                    os.makedirs(self.output_dir, exist_ok=True)
                    file_path = Path(self.output_dir).joinpath(file_name)
    
                    with open(file_path, "wb") as file:
                        file.write(file_data)
                    logging.info(f"File saved as: {file_path}") 
                except PermissionError:
                    logging.error(f"Permission denied when writing to: {output_dir}")
                except Exception as e:
                    logging.error(f"Failed to save file: {e}")
                
                self.received_files.clear()

                # Update monitor window with file transfer and training completion.
                if self.gui:
                    close_msg = {
                        "event": "rtc_close_monitor",
                    }
                    self.ctrl_socket.send_string(jsonpickle.encode(close_msg))
                    logging.info("Sent ZMQ close message to LossViewer window.")

            elif "PROGRESS_REPORT::" in message:
                # Progress report received from worker.
                logging.info(message)
                _, progress = message.split("PROGRESS_REPORT::", 1)
                
                # Update LossViewer window with received progress report.
                if self.gui:
                    rtc_progress_msg = {
                        "event": "rtc_update_monitor",
                        "rtc_msg": progress
                    }
                    self.ctrl_socket.send_string(jsonpickle.encode(rtc_progress_msg))

            elif "FILE_META::" in message: 
                # Metadata received (file name & size).
                _, meta = message.split("FILE_META::", 1)
                file_name, file_size, output_dir = meta.split(":")

                # Initialize received_files with file name as key and empty bytearray as value.
                if file_name not in self.received_files:
                    self.received_files[file_name] = bytearray()
                logging.info(f"File name received: {file_name}, of size {file_size}, saving to {output_dir}")

            elif "ZMQ_CTRL::" in message:
                # ZMQ control message received.
                _, zmq_ctrl = message.split("ZMQ_CTRL::", 1)
                
                # Handle ZMQ control messages (i.e. STOP or CANCEL).

            elif "TRAIN_JOB_START::" in message:
                # Training job start message received.
                _, job_info = message.split("TRAIN_JOB_START::", 1)
                logging.info(f"Training job started with info: {job_info}")

                # Parse the job info and update the LossViewer window.
                if self.gui:
                    try:
                        # Get next config info.
                        config_info = self.config_info_list.pop(0)

                        # Check for retraining flag.
                        if config_info.dont_retrain:
                            if not config_info.has_trained_model:
                                raise ValueError(
                                    "Config is set to not retrain but no trained model found: "
                                    f"{config_info.path}"
                                )

                            logging.info(
                                f"Using already trained model for {config_info.head_name}: "
                                f"{config_info.path}"
                            )

                            # Trained job paths not needed because no remote inference (remote training only) so far.

                        # Otherwise, prepare to run training job.
                        else:
                            logging.info("Resetting monitor window.")
                            job = config_info.config
                            model_type = config_info.head_name
                            plateau_patience = job.optimization.early_stopping.plateau_patience
                            plateau_min_delta = job.optimization.early_stopping.plateau_min_delta

                            # Send reset ZMQ message to LossViewer window.
                            # In separate thread from LossViewer, must use ZMQ PUB socket to update.
                            reset_msg = {
                                "event": "rtc_reset_monitor",
                                "what": str(model_type),
                                "plateau_patience": plateau_patience,
                                "plateau_min_delta": plateau_min_delta,
                                "window_title": f"Training Model - {str(model_type)}",
                                "message": "Preparing to run training..."
                            }
                            self.ctrl_socket.send_string(jsonpickle.encode(reset_msg))
                            
                            # Further updates to the LossViewer window handled by PROGRESS_REPORT messages when training starts remotely.

                            logging.info(f"Start training {str(model_type)} job with config: {job}")
                
                    except Exception as e:
                        logging.error(f"Failed to parse training job config: {e}")

            elif "TRAIN_JOB_END::" in message: # ONLY TO SIGNAL TRAINING JOB END, NOT WHOLE TRAINING SESSION END.
                # Training job end message received.
                _, job_info = message.split("TRAIN_JOB_END::", 1)
                logging.info(f"Train job completed: {job_info}, checking for next job...")

                # Update LossViewer window to indicate training completion based on how many training jobs left.
                if self.gui:
                    if len(self.config_info_list) == 0:
                        logging.info("No more training jobs to run. Closing LossViewer window.")
                    else:
                        logging.info(f"More training jobs to run: {len(self.config_info_list)} remaining.")
    
                

            elif "TRAIN_JOB_ERROR::" in message:
                # Training job error message received.
                _, error_info = message.split("TRAIN_JOB_ERROR::", 1)
                logging.error(f"Training job encountered an error: {error_info}")
                
        elif isinstance(message, bytes):
            if message == b"KEEP_ALIVE":
                logging.info("Keep alive message received.")
                return
            
            elif b"PROGRESS_REPORT::" in message:
                # Progress report received from worker as bytes.
                logging.info(message.decode())
                _, progress = message.decode().split("PROGRESS_REPORT::", 1)
                
                # Update LossViewer window with received progress report.
                if self.gui:
                    rtc_progress_msg = {
                        "event": "rtc_update_monitor",
                        "rtc_msg": progress
                    }
                    self.ctrl_socket.send_string(jsonpickle.encode(rtc_progress_msg))

            file_name = list(self.received_files.keys())[0]
            if file_name not in self.received_files:
                self.received_files[file_name] = bytearray()
            self.received_files[file_name].extend(message)


    # @pc.on("iceconnectionstatechange")
    async def _send_peer_message(self, to_peer_id: str, payload: dict):
        """Send peer message via signaling server.

        Args:
            to_peer_id: Target peer ID
            payload: Application-specific message payload
        """
        try:
            await self.websocket.send(json.dumps({
                "type": "peer_message",
                "from_peer_id": self.peer_id,
                "to_peer_id": to_peer_id,
                "payload": payload
            }))
            logging.info(f"Sent peer message to {to_peer_id}: {payload.get('app_message_type', 'unknown')}")
        except Exception as e:
            logging.error(f"Failed to send peer message: {e}")

    async def discover_workers(self, room_id: str = None, **filter_requirements) -> list:
        """Discover available workers matching requirements (DEPRECATED: use _discover_workers_in_room).

        NOTE: This method is deprecated. For room-based discovery, use _discover_workers_in_room() instead.
        This method is kept for backward compatibility but now requires room_id for security.

        Args:
            room_id: Room ID to scope discovery (REQUIRED for security)
            **filter_requirements: Keyword arguments for filtering
                - min_gpu_memory_mb: Minimum GPU memory
                - model_type: Required model support
                - job_type: "training" or "inference"

        Returns:
            List of worker peer info dicts
        """
        # SECURITY: Require room_id to prevent global discovery
        if not room_id:
            logging.error("SECURITY: room_id required for worker discovery. Global discovery is disabled.")
            return []

        # Build filters with room_id for security
        filters = {
            "role": "worker",
            "room_id": room_id,  # SECURITY: Always scope to room
            "tags": ["sleap-rtc"],
            "properties": {
                "status": "available"
            }
        }

        # Add GPU memory requirement
        if "min_gpu_memory_mb" in filter_requirements:
            filters["properties"]["gpu_memory_mb"] = {
                "$gte": filter_requirements["min_gpu_memory_mb"]
            }

        # Add job type requirement
        if "job_type" in filter_requirements:
            job_type = filter_requirements["job_type"]
            if job_type == "training":
                filters["tags"].append("training-worker")
            elif job_type == "inference":
                filters["tags"].append("inference-worker")

        # Try new discovery API
        try:
            # Send discovery request
            await self.websocket.send(json.dumps({
                "type": "discover_peers",
                "from_peer_id": self.peer_id,
                "filters": filters
            }))

            # Wait for response
            response = await asyncio.wait_for(self.websocket.recv(), timeout=5.0)
            response_data = json.loads(response)

            if response_data.get("type") == "peer_list":
                workers = response_data.get("peers", [])
                logging.info(f"Discovered {len(workers)} available workers in room {room_id}")
                return workers
            elif response_data.get("type") == "error" and response_data.get("code") == "UNKNOWN_MESSAGE_TYPE":
                # Old signaling server, fall back to manual peer_id
                logging.warning("Signaling server doesn't support discovery, using manual peer_id")
                return await self._fallback_manual_peer_selection()
            else:
                logging.error(f"Unexpected response: {response_data}")
                return []

        except asyncio.TimeoutError:
            logging.warning("Discovery timed out, falling back to manual peer_id")
            return await self._fallback_manual_peer_selection()
        except Exception as e:
            logging.error(f"Discovery error: {e}, falling back to manual peer_id")
            return await self._fallback_manual_peer_selection()

    async def _fallback_manual_peer_selection(self) -> list:
        """Fallback: Use manually configured worker peer_id.

        Returns:
            List with single worker entry if configured, empty list otherwise
        """
        # Check if we have a target worker already set (from session string)
        if self.target_worker:
            logging.info(f"Using target worker from session: {self.target_worker}")
            return [{
                "peer_id": self.target_worker,
                "role": "worker",
                "metadata": {}
            }]

        # Check environment variable
        worker_peer_id = os.environ.get("SLEAP_RTC_WORKER_PEER_ID")

        if worker_peer_id:
            logging.info(f"Using worker from environment: {worker_peer_id}")
            return [{
                "peer_id": worker_peer_id,
                "role": "worker",
                "metadata": {}
            }]

        # No fallback available
        logging.error("No workers discovered and no fallback peer_id available")
        return []

    async def _register_with_room(self, room_id: str, token: str, id_token: str):
        """Register client with room on signaling server.

        Args:
            room_id: Room ID to join
            token: Room authentication token
            id_token: Cognito ID token for this client
        """
        # Try to get SLEAP version
        try:
            import sleap
            sleap_version = sleap.__version__
        except (ImportError, AttributeError):
            sleap_version = "unknown"

        logging.info(f"Registering {self.peer_id} with room {room_id}...")

        await self.websocket.send(json.dumps({
            'type': 'register',
            'peer_id': self.peer_id,
            'room_id': room_id,
            'token': token,
            'id_token': id_token,
            'role': 'client',
            'metadata': {
                'tags': ['sleap-rtc', 'training-client'],
                'properties': {
                    'sleap_version': sleap_version,
                    'platform': platform.system(),
                    'user_id': os.environ.get('USER', 'unknown')
                }
            }
        }))

        # Wait for registration confirmation
        try:
            response = await asyncio.wait_for(self.websocket.recv(), timeout=5.0)
            response_data = json.loads(response)
            if response_data.get("type") == "registered_auth":
                logging.info(f"Client {self.peer_id} successfully registered with room {room_id}")
            else:
                logging.warning(f"Unexpected registration response: {response_data}")
        except asyncio.TimeoutError:
            logging.error("Registration confirmation timeout")
        except Exception as e:
            logging.error(f"Registration error: {e}")

    async def _discover_workers_in_room(self, room_id: str, min_gpu_memory: int = None) -> list:
        """Discover available workers in the specified room.

        Args:
            room_id: Room ID to search for workers
            min_gpu_memory: Minimum GPU memory in MB (optional filter)

        Returns:
            List of worker peer info dicts
        """
        # Build filters for room-scoped discovery
        filters = {
            "role": "worker",
            "room_id": room_id,  # CRITICAL: Scope discovery to this room only
            "tags": ["sleap-rtc"],
            "properties": {
                "status": "available"
            }
        }

        # Add GPU memory filter if specified
        if min_gpu_memory:
            filters["properties"]["gpu_memory_mb"] = {
                "$gte": min_gpu_memory
            }

        logging.info(f"Discovering workers in room {room_id}...")

        try:
            # Send discovery request
            await self.websocket.send(json.dumps({
                "type": "discover_peers",
                "from_peer_id": self.peer_id,
                "filters": filters
            }))

            # Wait for response
            response = await asyncio.wait_for(self.websocket.recv(), timeout=5.0)
            response_data = json.loads(response)

            if response_data.get("type") == "peer_list":
                workers = response_data.get("peers", [])
                logging.info(f"Discovered {len(workers)} available workers in room")
                return workers
            else:
                logging.error(f"Unexpected discovery response: {response_data}")
                return []

        except asyncio.TimeoutError:
            logging.error("Worker discovery timed out")
            return []
        except Exception as e:
            logging.error(f"Worker discovery error: {e}")
            return []

    def _auto_select_worker(self, workers: list) -> str:
        """Automatically select best worker based on GPU memory.

        Args:
            workers: List of worker peer info dicts

        Returns:
            Selected worker peer_id
        """
        if not workers:
            raise ValueError("No workers available for auto-selection")

        # Sort by GPU memory (descending)
        sorted_workers = sorted(
            workers,
            key=lambda w: w.get('metadata', {}).get('properties', {}).get('gpu_memory_mb', 0),
            reverse=True
        )

        selected = sorted_workers[0]
        peer_id = selected['peer_id']
        gpu_memory = selected.get('metadata', {}).get('properties', {}).get('gpu_memory_mb', 'unknown')

        logging.info(f"Auto-selected worker {peer_id} (GPU memory: {gpu_memory}MB)")
        return peer_id

    async def _prompt_worker_selection(self, workers: list) -> str:
        """Display workers and prompt user to select one.

        Args:
            workers: List of worker peer info dicts

        Returns:
            Selected worker peer_id
        """
        while True:
            print("\n" + "=" * 80)
            print("Available Workers:")
            print("=" * 80)

            for i, worker in enumerate(workers, 1):
                peer_id = worker['peer_id']
                metadata = worker.get('metadata', {}).get('properties', {})
                gpu_model = metadata.get('gpu_model', 'Unknown')
                gpu_memory = metadata.get('gpu_memory_mb', 0)
                cuda_version = metadata.get('cuda_version', 'Unknown')
                hostname = metadata.get('hostname', 'Unknown')

                print(f"\n  {i}. {peer_id}")
                print(f"     GPU Model:    {gpu_model}")
                print(f"     GPU Memory:   {gpu_memory} MB")
                print(f"     CUDA Version: {cuda_version}")
                print(f"     Hostname:     {hostname}")

            print("\n" + "=" * 80)
            print("Commands: Enter worker number (1-{}), or 'refresh' to update list".format(len(workers)))
            print("=" * 80)

            choice = input("\nSelect worker: ").strip().lower()

            if choice == 'refresh':
                logging.info("Refreshing worker list...")
                # Re-query workers
                new_workers = await self._discover_workers_in_room(
                    room_id=self.current_room_id,
                    min_gpu_memory=None
                )
                if new_workers:
                    workers = new_workers
                    continue
                else:
                    print("No workers found. Showing previous list.")
                    continue

            try:
                idx = int(choice) - 1
                if 0 <= idx < len(workers):
                    selected_worker = workers[idx]['peer_id']
                    print(f"\nSelected: {selected_worker}")
                    return selected_worker
                else:
                    print(f"Invalid selection. Please enter a number between 1 and {len(workers)}")
            except ValueError:
                print("Invalid input. Please enter a number or 'refresh'")

    async def _collect_job_responses(self, job_id: str, timeout: float) -> list:
        """Collect job responses from workers.

        Args:
            job_id: Job ID to collect responses for
            timeout: Timeout in seconds

        Returns:
            List of job response dicts
        """
        responses = []
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                remaining = deadline - time.time()
                msg = await asyncio.wait_for(
                    self.websocket.recv(),
                    timeout=max(0.1, remaining)
                )

                data = json.loads(msg)

                if data.get("type") == "peer_message":
                    payload = data.get("payload", {})
                    if (payload.get("app_message_type") == "job_response" and
                        payload.get("job_id") == job_id):

                        responses.append({
                            "worker_id": data.get("from_peer_id"),
                            "accepted": payload.get("accepted"),
                            "reason": payload.get("reason", ""),
                            "estimated_duration_minutes": payload.get("estimated_duration_minutes", 0),
                            "estimated_start_time_sec": payload.get("estimated_start_time_sec", 0),
                            "worker_info": payload.get("worker_info", {})
                        })

            except asyncio.TimeoutError:
                break

        return responses

    async def _monitor_job_progress(self, job_id: str):
        """Monitor job progress and display updates.

        Args:
            job_id: Job ID to monitor
        """
        while True:
            msg = await self.websocket.recv()
            data = json.loads(msg)

            if data.get("type") != "peer_message":
                continue

            payload = data.get("payload", {})

            if payload.get("job_id") != job_id:
                continue

            app_msg_type = payload.get("app_message_type")

            if app_msg_type == "job_status":
                # Display progress
                progress = payload.get("progress", 0)
                message = payload.get("message", "")
                details = payload.get("details", {})

                logging.info(f"Job progress: {progress*100:.1f}% - {message}")
                if details:
                    logging.info(f"Details: {details}")

            elif app_msg_type == "job_complete":
                logging.info("Job completed successfully!")
                result = payload.get("result", {})
                logging.info(f"Result: {result}")
                break

            elif app_msg_type == "job_failed":
                error = payload.get("error", {})
                logging.error(f"Job failed: {error.get('message', 'Unknown error')}")
                raise JobFailedError(
                    f"Job failed with code {error.get('code', 'UNKNOWN')}: {error.get('message', 'Unknown error')}"
                )

    async def submit_training_job(self, dataset_path: str, config: dict, room_id: str = None, **job_requirements):
        """Submit training job to available workers (DEPRECATED: use room-based connection flow).

        NOTE: This method is deprecated. Use the room-based connection flow with
        _discover_workers_in_room() instead.

        Args:
            dataset_path: Path to training dataset
            config: Training configuration
            room_id: Room ID to scope worker discovery (REQUIRED for security)
            **job_requirements: Job requirements (min_gpu_memory_mb, etc.)

        Returns:
            Selected worker peer_id

        Raises:
            NoWorkersAvailableError: No workers found matching requirements
            NoWorkersAcceptedError: No workers accepted the job request
        """
        # 1. Discover available workers (room-scoped for security)
        workers = await self.discover_workers(
            room_id=room_id,
            job_type="training",
            **job_requirements
        )

        if not workers:
            raise NoWorkersAvailableError(
                f"No workers found matching requirements: {job_requirements}"
            )

        logging.info(f"Found {len(workers)} workers, sending job requests...")

        # 2. Create job request
        job_id = str(uuid.uuid4())

        # Get dataset info
        dataset_size_mb = 0
        if os.path.exists(dataset_path):
            dataset_size_mb = os.path.getsize(dataset_path) / (1024 * 1024)

        job_request = {
            "app_message_type": "job_request",
            "job_id": job_id,
            "job_type": "training",
            "dataset_info": {
                "format": "slp",
                "path": dataset_path,
                "estimated_size_mb": dataset_size_mb
            },
            "config": config,
            "requirements": job_requirements
        }

        # 3. Send job request to all discovered workers
        for worker in workers:
            await self._send_peer_message(worker["peer_id"], job_request)

        # 4. Collect responses (with timeout)
        responses = await self._collect_job_responses(job_id, timeout=5.0)

        if not responses:
            raise NoWorkersAcceptedError(
                "No workers responded to job request (timeout)"
            )

        # Filter accepted responses
        accepted = [r for r in responses if r["accepted"]]

        if not accepted:
            reasons = [r["reason"] for r in responses if not r["accepted"]]
            raise NoWorkersAcceptedError(
                f"All workers rejected job. Reasons: {reasons}"
            )

        # 5. Select best worker (e.g., fastest estimated time)
        selected = min(accepted, key=lambda r: r.get("estimated_duration_minutes", 999999))

        logging.info(f"Selected worker: {selected['worker_id']} "
                     f"(estimate: {selected['estimated_duration_minutes']} min)")

        # 6. Send job assignment to selected worker
        await self._send_peer_message(selected["worker_id"], {
            "app_message_type": "job_assignment",
            "job_id": job_id,
            "initiate_connection": True
        })

        # Store job info for monitoring
        self.current_job_id = job_id
        self.target_worker = selected["worker_id"]

        # 7. Monitor job progress in background
        asyncio.create_task(self._monitor_job_progress(job_id))

        return selected["worker_id"]

    async def on_iceconnectionstatechange(self):
        """Event handler function for when the ICE connection state changes.

        Args:
            None
        Returns:
            None
        """

        # Log the current ICE connection state.
        logging.info(f"ICE connection state is now {self.pc.iceConnectionState}")

        # Check the ICE connection state and handle reconnection logic.
        if self.pc.iceConnectionState in ["connected", "completed"]:
            self.reconnect_attempts = 0
            logging.info("ICE connection established.")
            logging.info(f"reconnect attempts reset to {self.reconnect_attempts}")

        elif self.pc.iceConnectionState in ["failed", "disconnected", "closed"] and not self.reconnecting:
            logging.warning(f"ICE connection {self.pc.iceConnectionState}. Attempting reconnect...")
            self.reconnecting = True

            if self.target_worker is None:
                logging.info(f"No target worker available for reconnection. target_worker is {self.target_worker}.")
                await self.clean_exit()
                return

            reconnection_success = await self.reconnect()
            self.reconnecting = False
            if not reconnection_success:
                logging.info("Reconnection failed. Closing connection...")
                await self.clean_exit()
                return


    async def run_client(
            self,
            file_path: str = None,
            output_dir: str = ".",
            zmq_ports: list = None,
            config_info_list: list = None,
            session_string: str = None,
            room_id: str = None,
            token: str = None,
            worker_id: str = None,
            auto_select: bool = False,
            min_gpu_memory: int = None
        ):
        """Sends initial SDP offer to worker peer and establishes both connection & datachannel to be used by both parties.

        Args:
            file_path: Path to a file to be sent to worker peer (usually zip file)
            output_dir: Directory to save files received from worker peer
            zmq_ports: List of ZMQ ports [controller, publish]
            config_info_list: Config info for updating GUI (None for CLI)
            session_string: Session string for direct connection to specific worker
            room_id: Room ID for room-based worker discovery
            token: Room token for authentication
            worker_id: Specific worker peer-id to connect to (skips discovery)
            auto_select: Automatically select best worker by GPU memory
            min_gpu_memory: Minimum GPU memory in MB for worker filtering
        Returns:
            None
        """
        try:
            # Initialize data channel.
            channel = self.pc.createDataChannel("my-data-channel")
            self.data_channel = channel
            logging.info("channel(%s) %s" % (channel.label, "created by local party."))

            # Set local variable
            # self.win = win
            self.file_path = file_path
            self.output_dir = output_dir
            self.zmq_ports = zmq_ports
            self.config_info_list = config_info_list # only passed if not CLI

            # Register event handlers for the data channel.
            channel.on("open", self.on_channel_open)
            channel.on("message", self.on_message)

            # Initialize reconnect attempts.
            logging.info("Setting up RTC data channel reconnect attempts...")
            self.reconnect_attempts = 0 

            # Sign-in anonymously with Cognito to get an ID token.
            sign_in_json = self.request_anonymous_signin()
            id_token = sign_in_json['id_token']
            self.peer_id = sign_in_json['username']
            self.cognito_username = self.peer_id

            if not id_token:
                logging.error("Failed to get anonymous ID token. Exiting client.")
                return
            
            logging.info(f"Anonymous ID token received: {id_token}")

            # No room creation needed for GUI Client since using Worker credentials.
            # Only needs its own Cognito ID token and peer ID 
            # Create the room and get the room ID and token.
            # room_json = self.request_create_room(id_token)
            # logging.info(f"Room created with ID: {room_json['room_id']} and token: {room_json['token']}")

            # Initate the WebSocket connection to the signaling server.
            async with websockets.connect(self.DNS) as websocket:

                # Initate the websocket for the GUI client (so other functions can use).
                self.websocket = websocket

                # BRANCH 1: Session string (direct connection to specific worker)
                if session_string:
                    logging.info("Using session string for direct worker connection")

                    # Prompt for session string if not provided
                    if not session_string:
                        session_str_json = None
                        while True:
                            session_string = input("Please enter RTC session string (or type 'exit' to quit): ")
                            if session_string.lower() == "exit":
                                print("Exiting client.")
                                return
                            try:
                                session_str_json = self.parse_session_string(session_string)
                                break  # Exit loop if parsing succeeds
                            except ValueError as e:
                                print(f"Error: {e}")
                                print("Please try again or type 'exit' to quit.")
                    else:
                        session_str_json = self.parse_session_string(session_string)

                    # Extract worker credentials from session string.
                    worker_room_id = session_str_json.get("room_id")
                    worker_token = session_str_json.get("token")
                    worker_peer_id = session_str_json.get("peer_id")

                    # Register with room
                    await self._register_with_room(
                        room_id=worker_room_id,
                        token=worker_token,
                        id_token=id_token
                    )

                    # Set target worker from session string
                    self.target_worker = worker_peer_id
                    logging.info(f"Selected worker from session string: {self.target_worker}")

                # BRANCH 2: Room-based discovery
                elif room_id:
                    logging.info(f"Using room-based worker discovery: room_id={room_id}")

                    # Store room info for discovery
                    self.current_room_id = room_id

                    # Register with room
                    await self._register_with_room(
                        room_id=room_id,
                        token=token,
                        id_token=id_token
                    )

                    # Discover workers in room
                    workers = await self._discover_workers_in_room(
                        room_id=room_id,
                        min_gpu_memory=min_gpu_memory
                    )

                    if not workers:
                        logging.error("No available workers found in room")
                        return

                    # Select worker based on mode
                    if worker_id:
                        # Direct worker selection
                        self.target_worker = worker_id
                        logging.info(f"Using specified worker: {worker_id}")
                    elif auto_select:
                        # Auto-select best worker
                        self.target_worker = self._auto_select_worker(workers)
                        logging.info(f"Auto-selected worker: {self.target_worker}")
                    else:
                        # Interactive worker selection
                        self.target_worker = await self._prompt_worker_selection(workers)
                        logging.info(f"User selected worker: {self.target_worker}")

                else:
                    logging.error("No connection method provided (session_string or room_id required)")
                    return

                if not self.target_worker:
                    logging.info("No target worker given. Cannot connect.")
                    return
                
                # Create and send SDP offer to worker peer.
                await self.pc.setLocalDescription(await self.pc.createOffer())
                await websocket.send(json.dumps({
                    'type': self.pc.localDescription.type, # type: 'offer'
                    'sender': self.peer_id, # should be own peer_id (Zoom username)
                    'target': self.target_worker, # should match Worker's peer_id (Zoom username)
                    'sdp': self.pc.localDescription.sdp
                }))
                logging.info('Offer sent to worker')

                # Handle incoming messages from server (e.g. answer from worker).
                await self.handle_connection()

            # Send POST req to server to delete this User, Worker, and associated Room.
            logging.info("Cleaning up Cognito and DynamoDB entries...")
            self.request_peer_room_deletion(self.peer_id)

            # # Exit.
            # await self.pc.close()
            # await websocket.close()
        except Exception as e:
            logging.error(f"Error in run_client: {e}")
        finally:
            await self.clean_exit()