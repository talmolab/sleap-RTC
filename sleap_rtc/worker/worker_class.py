import asyncio
import base64
import subprocess
import stat
import sys
import uuid
import websockets
import json
import logging
import shutil
import os
import re
import requests
import zmq
import socket
import platform
import time

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel

# from run_training import run_all_training_jobs
from pathlib import Path
from websockets.client import ClientConnection

from sleap_rtc.config import get_config, SharedStorageConfig, SharedStorageConfigError
from sleap_rtc.filesystem import (
    safe_mkdir,
    to_relative_path,
    to_absolute_path,
    get_file_info,
    validate_path_in_root,
    PathValidationError,
    SharedStorageError,
)
from sleap_rtc.protocol import (
    MSG_JOB_ID,
    MSG_SHARED_INPUT_PATH,
    MSG_SHARED_OUTPUT_PATH,
    MSG_SHARED_STORAGE_JOB,
    MSG_PATH_VALIDATED,
    MSG_PATH_ERROR,
    MSG_JOB_COMPLETE,
    format_message,
    parse_message,
)
from sleap_rtc.worker.capabilities import WorkerCapabilities
from sleap_rtc.worker.job_executor import JobExecutor
from sleap_rtc.worker.file_manager import FileManager
from sleap_rtc.worker.job_coordinator import JobCoordinator
from sleap_rtc.worker.state_manager import StateManager
from sleap_rtc.worker.progress_reporter import ProgressReporter

# Setup logging.
logging.basicConfig(level=logging.INFO)

# Set chunk separator
SEP = re.compile(rb"[\r\n]")


class RTCWorkerClient:
    def __init__(self, chunk_size=32 * 1024, gpu_id=0, shared_storage_root=None):
        # Use /app/shared_data in production, current dir + shared_data in dev
        self.save_dir = "."
        self.chunk_size = chunk_size
        self.received_files = {}
        self.output_dir = ""
        self.ctrl_socket = None
        self.pc = None  # RTCPeerConnection will be set later
        self.websocket = None  # WebSocket connection will be set later
        self.package_type = "train"  # Default to training, can be "track" for inference

        # Initialize shared storage configuration
        try:
            self.shared_storage_root = SharedStorageConfig.get_shared_storage_root(
                cli_override=shared_storage_root
            )
            if self.shared_storage_root:
                # Create jobs directory for shared storage transfers
                self.shared_jobs_dir = self.shared_storage_root / "jobs"
                safe_mkdir(self.shared_jobs_dir)
                logging.info(
                    f"Worker shared storage enabled: {self.shared_storage_root}"
                )
            else:
                self.shared_jobs_dir = None
                logging.info(
                    "Worker shared storage not configured, will use RTC transfer"
                )
        except SharedStorageConfigError as e:
            logging.error(f"Worker shared storage configuration error: {e}")
            logging.info("Falling back to RTC transfer")
            self.shared_storage_root = None
            self.shared_jobs_dir = None

        # Shared storage job tracking
        self.current_job_id = None
        self.current_input_path = None
        self.current_output_path = None

        # Worker state and capabilities (for v2.0 features)
        self.capabilities = WorkerCapabilities(gpu_id=gpu_id)
        self.job_executor = JobExecutor(
            worker=self,
            capabilities=self.capabilities,
            shared_storage_root=self.shared_storage_root,
        )
        self.file_manager = FileManager(
            chunk_size=chunk_size,
            shared_storage_root=self.shared_storage_root,
        )
        self.job_coordinator = None  # Initialized in run_worker after authentication
        self.state_manager = None  # Initialized in run_worker after authentication
        self.progress_reporter = ProgressReporter()  # ZMQ progress reporting
        self.status = "available"  # "available", "busy", "reserved", "maintenance"
        self.current_job = None
        self.max_concurrent_jobs = 1
        self.shutting_down = False

        # Expose capabilities as properties for backward compatibility
        self.gpu_id = gpu_id
        self.gpu_memory_mb = self.capabilities.gpu_memory_mb
        self.gpu_model = self.capabilities.gpu_model
        self.cuda_version = self.capabilities.cuda_version
        self.supported_models = self.capabilities.supported_models
        self.supported_job_types = self.capabilities.supported_job_types

        # Room credentials (set during run_worker, needed for re-registration)
        self.room_id = None
        self.room_token = None
        self.id_token = None  # Cognito ID token for authentication


    async def clean_exit(self):
        """Handles cleanup and shutdown of the worker.

        Args:
            pc: RTCPeerConnection object
            websocket: WebSocket connection object
        Returns:
            None
        """
        # Set flag BEFORE closing connection & iceConnection state change triggers
        self.shutting_down = True

        logging.info("Closing WebRTC connection...")
        if self.pc:
            await self.pc.close()

        logging.info("Closing websocket connection...")
        if self.websocket:
            await self.websocket.close()

        logging.info("Cleaning up ZMQ sockets...")
        if self.progress_reporter:
            self.progress_reporter.cleanup()

        logging.info("Cleaning up Cognito and DynamoDB entries...")
        if self.cognito_username and self.state_manager:
            self.state_manager.request_peer_room_deletion(self.cognito_username)
            self.cognito_username = None

        logging.info("Client shutdown complete. Exiting...")

    async def handle_connection(
        self, pc: RTCPeerConnection, websocket: ClientConnection, peer_id: str
    ):
        """Handles incoming messages from the signaling server and processes them accordingly.

        Args:
            pc: RTCPeerConnection object
            websocket: WebSocket connection object
            peer_id: The ID of the peer
        Returns:
            None
        """
        if websocket is None:
            logging.info("Given websocket is None. Using self.websocket instead.")

        if pc is None:
            logging.info("Given PeerConnection object is None. Using self.pc instead.")

        try:
            async for message in self.websocket:
                data = json.loads(message)
                msg_type = data.get("type")

                # Receive offer SDP from client (forwarded by signaling server).
                if msg_type == "offer":
                    logging.info("Received offer SDP")

                    # Obtain the sender's peer ID (this is the new target)
                    target_pid = data.get("sender")

                    # SAFEGUARD: Check worker status before accepting connection
                    if self.status in ["busy", "reserved"]:
                        logging.warning(
                            f"Rejecting connection from {target_pid} - worker is {self.status}"
                        )

                        # Send error response to client via signaling server
                        await self.websocket.send(
                            json.dumps(
                                {
                                    "type": "error",
                                    "target": target_pid,
                                    "reason": "worker_busy",
                                    "message": f"Worker is currently {self.status}. Please use --room-id and --token to discover available workers.",
                                    "current_status": self.status,
                                }
                            )
                        )

                        logging.info(f"Sent busy rejection to client {target_pid}")
                        continue  # Skip this offer, continue listening

                    # Status is "available" - proceed with connection
                    logging.info(
                        f"Accepting connection from {target_pid} (status: {self.status})"
                    )

                    # Update status to "reserved" to prevent race conditions
                    await self.state_manager.update_status("reserved")
                    logging.info("Worker status updated to 'reserved'")

                    # Set worker peer's remote description to the client's offer based on sdp data
                    await self.pc.setRemoteDescription(
                        RTCSessionDescription(sdp=data.get("sdp"), type="offer")
                    )

                    # Generate worker's answer SDP and set it as the local description
                    await self.pc.setLocalDescription(await self.pc.createAnswer())

                    # Send worker's answer SDP to client so they can set it as their remote description
                    await self.websocket.send(
                        json.dumps(
                            {
                                "type": self.pc.localDescription.type,  # 'answer'
                                "sender": peer_id,  # worker's peer ID
                                "target": target_pid,  # client's peer ID
                                "sdp": self.pc.localDescription.sdp,  # worker's answer SDP
                            }
                        )
                    )

                    logging.info(f"Connection accepted - answer sent to {target_pid}")

                    # Reset received_files dictionary
                    self.received_files.clear()

                elif msg_type == "registered_auth":
                    room_id = data.get("room_id")
                    token = data.get("token")
                    peer_id = data.get("peer_id")

                    # Print session string for direct worker connection (backward compatibility)
                    logging.info("=" * 80)
                    logging.info("Worker authenticated with server")
                    logging.info("=" * 80)
                    logging.info("")
                    logging.info("Session string for DIRECT connection to this worker:")
                    session_string = self.state_manager.generate_session_string(
                        room_id, token, peer_id
                    )
                    logging.info(f"  {session_string}")
                    logging.info("")
                    logging.info(
                        "Room credentials for OTHER workers/clients to join this room:"
                    )
                    logging.info(f"  Room ID: {room_id}")
                    logging.info(f"  Token:   {token}")
                    logging.info("")
                    logging.info(
                        "Use session string with --session-string for direct connection"
                    )
                    logging.info(
                        "Use room credentials with --room-id and --token for worker discovery"
                    )
                    logging.info("=" * 80)

                # Handle "trickle ICE" for non-local ICE candidates (might be unnecessary)
                elif msg_type == "candidate":
                    print("Received ICE candidate")
                    candidate = data.get("candidate")
                    await pc.addIceCandidate(candidate)

                elif msg_type == "error":
                    logging.error(f"Error received from server: {data.get('reason')}")
                    await self.clean_exit()
                    return

                elif (
                    msg_type == "quit"
                ):  # NOT initiator, received quit request from worker
                    print("Received quit request from Client. Closing connection...")
                    await self.clean_exit()
                    return

                elif (
                    msg_type == "peer_message"
                ):  # NEW: Handle peer messages (job requests, etc.)
                    await self.job_coordinator.handle_peer_message(data)

                elif msg_type == "metadata_updated":
                    logging.info(
                        f"Status updated to: {data.get('metadata').get('properties').get('status')}"
                    )

                # Error handling
                else:
                    logging.warning(f"Unhandled message type: {msg_type}")

        except json.JSONDecodeError:
            logging.ERROR("Invalid JSON received")

        except Exception as e:
            logging.ERROR(f"Error handling message: {e}")

    async def keep_ice_alive(self, channel: RTCDataChannel):
        """Sends periodic keep-alive messages to the client to maintain the connection.

        Args:
            channel: DataChannel object
        Returns:
            None
        """
        while True:
            await asyncio.sleep(15)
            if channel.readyState == "open":
                channel.send(b"KEEP_ALIVE")

    # Websockets are only necessary here for setting up exchange of SDP & ICE candidates to each other.
    # Listen for incoming data channel messages on channel established by the client.
    def on_datachannel(self, channel: RTCDataChannel):
        """Handles incoming data channel messages from the client.

        Args:
            channel: DataChannel object
        Returns:
            None
        """
        # Listen for incoming messages on the channel.
        logging.info(
            "channel(%s) %s" % (channel.label, "created by remote party & received.")
        )

        async def send_worker_file(file_path: str):
            """Handles direct, one-way file transfer from client to be sent to client peer.

            Args:
                file_path: Path to the file to be sent.

            Returns:
                None
            """
            if not file_path or not Path(file_path).exists():
                logging.info(f"Invalid file path: {file_path}")
                return

            # Use FileManager to send the file
            await self.file_manager.send_file(channel, file_path, self.output_dir)
            return

        @channel.on("open")
        def on_channel_open():
            """Logs the channel open event.

            Args:
                None
            Returns:
                None
            """
            asyncio.create_task(self.keep_ice_alive(channel))
            logging.info(f"{channel.label} channel is open")

        @channel.on("message")
        async def on_message(message):
            """Handles incoming messages from the client.

            Args:
                message: The message received from the client (can be string or bytes)

            Returns:
                None
            """
            # Log Client's message.
            logging.info(f"Worker received: {message}")

            if isinstance(message, str):
                # Parse message type and arguments
                msg_type, msg_args = parse_message(message)

                # Handle shared storage job messages
                if msg_type == MSG_JOB_ID:
                    self.current_job_id = msg_args[0] if msg_args else None
                    logging.info(
                        f"Received shared storage job ID: {self.current_job_id}"
                    )
                    return

                elif msg_type == MSG_SHARED_INPUT_PATH:
                    # Receive relative path from client
                    relative_path_str = msg_args[0] if msg_args else ""
                    logging.info(f"Received shared input path: {relative_path_str}")

                    # Validate and store path using FileManager
                    validated_path = await self.file_manager.validate_shared_input_path(
                        relative_path_str, channel
                    )
                    if validated_path:
                        self.current_input_path = validated_path
                    return

                elif msg_type == MSG_SHARED_OUTPUT_PATH:
                    # Receive relative output path from client
                    relative_path_str = msg_args[0] if msg_args else ""
                    logging.info(f"Received shared output path: {relative_path_str}")

                    # Validate and store path using FileManager
                    validated_path = await self.file_manager.validate_shared_output_path(
                        relative_path_str, channel
                    )
                    if validated_path:
                        self.current_output_path = validated_path
                        self.output_dir = str(validated_path)
                    return

                elif (
                    msg_type == MSG_SHARED_STORAGE_JOB
                    and msg_args
                    and msg_args[0] == "start"
                ):
                    logging.info("Starting shared storage job processing")
                    # Process the job using shared storage paths
                    if self.current_input_path and self.current_output_path:
                        try:
                            await self.job_executor.process_shared_storage_job(channel)
                        except Exception as e:
                            logging.error(f"Error processing shared storage job: {e}")
                            channel.send(f"JOB_FAILED::{self.current_job_id}::{str(e)}")
                    else:
                        error_msg = (
                            "Missing input or output path for shared storage job"
                        )
                        logging.error(error_msg)
                        channel.send(format_message(MSG_PATH_ERROR, error_msg))
                    return

                if message == b"KEEP_ALIVE":
                    logging.info("Keep alive message received.")
                    return

                # Detect package type (track or train)
                if "PACKAGE_TYPE::track" in message:
                    self.package_type = "track"
                    logging.info("Received track package (inference mode)")
                    return
                elif "PACKAGE_TYPE::train" in message:
                    self.package_type = "train"
                    logging.info("Received train package (training mode)")
                    return

                if message == "END_OF_FILE":
                    logging.info("End of file transfer received.")

                    # File transfer complete, save to disk.
                    file_name, file_data = list(self.received_files.items())[0]
                    self.original_file_name = file_name
                    file_path = os.path.join(self.save_dir, file_name)

                    with open(file_path, "wb") as file:
                        file.write(file_data)
                    logging.info(f"File saved as: {file_path}")

                    # Unzip results if needed.
                    if file_path.endswith(".zip"):
                        self.unzipped_dir = await self.file_manager.unzip_results(file_path)
                        logging.info(f"Unzipped results from {file_path}")

                    # Reset dictionary for next file
                    self.received_files.clear()

                    # Route to appropriate workflow based on package type
                    if self.package_type == "track":
                        # Inference workflow
                        track_script_path = os.path.join(
                            self.unzipped_dir, "track-script.sh"
                        )

                        if Path(track_script_path).exists():
                            self.job_executor.unzipped_dir = self.unzipped_dir
                            self.job_executor.output_dir = self.output_dir
                            await self.job_executor.run_track_workflow(channel, track_script_path)
                        else:
                            logging.error(
                                f"No track script found in {self.unzipped_dir}. Skipping inference."
                            )

                    else:
                        # Training workflow (default)
                        train_script_path = os.path.join(
                            self.unzipped_dir, "train-script.sh"
                        )

                        if Path(train_script_path):
                            try:
                                logging.info("self.gui is: " + str(self.gui))
                                progress_listener_task = None
                                # if self.gui:
                                # Start ZMQ progress listener.
                                # Don't need to send ZMQ progress reports if User just using CLI sleap-rtc.
                                # (Will print sleap-nn train logs directly to terminal instead.)
                                if self.gui:
                                    # Start ZMQ control socket
                                    self.progress_reporter.start_control_socket()
                                    logging.info(
                                        f"{channel.label} ZMQ control socket started"
                                    )

                                    # Start ZMQ progress listener
                                    progress_listener_task = self.progress_reporter.start_progress_listener_task(channel)
                                    logging.info(
                                        f"{channel.label} progress listener started"
                                    )

                                    # Give SUB socket time to connect.
                                    await asyncio.sleep(1)

                                logging.info(
                                    f"Running training script: {train_script_path}"
                                )

                                # Make the script executable
                                os.chmod(
                                    train_script_path,
                                    os.stat(train_script_path).st_mode | stat.S_IEXEC,
                                )

                                # Run the training script in the save directory
                                self.job_executor.unzipped_dir = self.unzipped_dir
                                self.job_executor.output_dir = self.output_dir
                                await self.job_executor.run_all_training_jobs(
                                    channel, train_script_path=train_script_path
                                )

                                # Finish training.
                                logging.info("Training completed successfully.")
                                if progress_listener_task:
                                    progress_listener_task.cancel()

                                # Zip the results.
                                logging.info("Zipping results...")
                                zipped_file_name = (
                                    f"trained_{self.original_file_name[:-4]}"
                                )
                                zipped_file = await self.file_manager.zip_results(
                                    zipped_file_name,
                                    f"{self.unzipped_dir}/{self.output_dir}",
                                )  # normally, "./labels_dir/models"
                                # Zipped file saved to current directory.

                                # Send the zipped file to the client.
                                if zipped_file:
                                    logging.info(
                                        f"Sending zipped file to client: {zipped_file}"
                                    )
                                    await send_worker_file(zipped_file)

                            except subprocess.CalledProcessError as e:
                                logging.error(
                                    f"Training failed with error:\n{e.stderr}"
                                )
                                await self.clean_exit()
                        else:
                            logging.info(
                                f"No training script found in {self.save_dir}. Skipping training."
                            )

                elif "OUTPUT_DIR::" in message:
                    logging.info(f"Output directory received: {message}")
                    _, self.output_dir = message.split(
                        "OUTPUT_DIR::", 1
                    )  # normally, "models"

                elif "FILE_META::" in message:
                    logging.info(f"File metadata received: {message}")
                    _, meta = message.split("FILE_META::", 1)
                    file_name, file_size, gui = meta.split(":")

                    logging.info("self.gui set to: " + gui)

                    # Convert string to boolean
                    self.gui = gui.lower() == "true"
                    self.received_files[file_name] = bytearray()
                    logging.info(
                        f"File name received: {file_name}, of size {file_size}"
                    )
                    logging.info(f"self.gui converted to boolean: {self.gui}")
                elif "ZMQ_CTRL::" in message:
                    logging.info(f"ZMQ LossViewer control message received: {message}")
                    _, zmq_msg = message.split("ZMQ_CTRL::", 1)

                    # Send LossViewer's control message to the Trainer client (listening on control port 9000).
                    # Remember, the Trainer printed: "ZMQ controller subscribed to: tcp://127.0.0.1:9000", so publish there.
                    self.progress_reporter.send_control_message(zmq_msg)
                else:
                    logging.info(f"Client sent: {message}")
                    # await self.send_worker_messages(self.pc, channel)

            elif isinstance(message, bytes):
                if message == b"KEEP_ALIVE":
                    logging.info("Keep alive message received.")
                    return

                file_name = list(self.received_files.keys())[0]
                self.received_files.get(file_name).extend(message)

    async def on_iceconnectionstatechange(self):
        """Handles ICE connection state changes.

        Args:
            None
        Returns:
            None
        """
        # Log the ICE connection state.
        iceState = self.pc.iceConnectionState
        logging.info(f"ICE connection state is now {iceState}")

        # Let clean_exit() finish without restarting
        if self.shutting_down:
            logging.info("Worker is shutting down - ignoring ICE state change.")
            return

        # Success States
        if iceState in ["connected", "completed"]:
            logging.info(f"ICE connection established ({iceState})")
            # Reset any reconnection tracking if you add it later
            # self.reconnect_attempts = 0
            # Connection is now active and ready
            return

        # Negotiation States
        if iceState == "checking":
            logging.info("ICE connection is checking...")
            return

        if iceState == "new":
            logging.info("ICE connection is new...")
            return

        # Check the ICE connection state and reset the Worker.
        if iceState == "closed":
            # Client explicitly closed the connection.
            logging.error("Client closed connection - resetting for new client")

            # Close old peer connection
            await self.pc.close()

            # Create new peer connection
            self.pc = RTCPeerConnection()
            self.pc.on("datachannel", self.on_datachannel)
            self.pc.on("iceconnectionstatechange", self.on_iceconnectionstatechange)

            # Clear the job state
            if self.job_coordinator:
                self.job_coordinator.clear_current_job()
            self.received_files.clear()

            # Re-register with signaling server (not just update metadata)
            # This ensures the worker appears in discovery queries again
            self.status = "available"
            await self.state_manager.reregister_worker()

            logging.info("Ready for new client connection!")

        elif iceState == "disconnected":
            # Temporary network issue - wait for recovery
            logging.info("ICE connection disconnected -wainting for recovery...")

            # Wait up to 90 seconds.
            for i in range(90):
                await asyncio.sleep(1)
                if self.pc.iceConnectionState in ["connected", "completed"]:
                    logging.info("ICE reconnected!")
                    return
                if self.pc.iceConnectionState in ["closed", "failed"]:
                    break

            logging.error("Reconnection timed out. Closing connection.")
            await self.pc.close()

            # Create new peer connection
            self.pc = RTCPeerConnection()
            self.pc.on("datachannel", self.on_datachannel)
            self.pc.on("iceconnectionstatechange", self.on_iceconnectionstatechange)

            # Clear the job state
            if self.job_coordinator:
                self.job_coordinator.clear_current_job()
            self.received_files.clear()

            # Re-register with signaling server (not just update metadata)
            self.status = "available"
            await self.state_manager.reregister_worker()

            logging.info("Ready for new client connection!")

        elif iceState == "failed":
            # Connection permanently failed
            logging.info("Connection failed - resetting")

            # Close old peer connection
            await self.pc.close()

            # Create new peer connection
            self.pc = RTCPeerConnection()
            self.pc.on("datachannel", self.on_datachannel)
            self.pc.on("iceconnectionstatechange", self.on_iceconnectionstatechange)

            # Clear the job state
            if self.job_coordinator:
                self.job_coordinator.clear_current_job()
            self.received_files.clear()

            # Re-register with signaling server (not just update metadata)
            self.status = "available"
            await self.state_manager.reregister_worker()

            logging.info("Ready for new client connection!")

    async def run_worker(self, pc, DNS: str, port_number, room_id=None, token=None):
        """Main function to run the worker. Contains several event handlers for the WebRTC connection and data channel.

        Args:
            pc: RTCPeerConnection object
            DNS: DNS address of the signaling server
            port_number: Port number of the signaling server
            room_id: Optional room ID to join. If not provided, a new room will be created.
            token: Optional room token for authentication. Required if room_id is provided.

        Returns:
            None
        """
        try:
            # Set the RTCPeerConnection object for the worker.
            logging.info(f"Setting RTCPeerConnection...")
            self.pc = pc

            # Register PeerConnection functions with PC object.
            logging.info(f"Registering PeerConnection functions...")
            self.pc.on("datachannel", self.on_datachannel)
            self.pc.on("iceconnectionstatechange", self.on_iceconnectionstatechange)

            # Sign-in anonymously with AWS Cognito to get an ID token (str).
            sign_in_json = StateManager.request_anonymous_signin()
            id_token = sign_in_json["id_token"]
            peer_id = sign_in_json["username"]
            self.cognito_username = peer_id

            if not id_token:
                logging.error(
                    "Failed to sign in anonymously. No ID token given. Exiting..."
                )
                return

            logging.info(f"Anonymous sign-in successful. ID token: {id_token}")

            # Create the room or use existing room credentials
            if room_id and token:
                # Join existing room
                logging.info(f"Joining existing room with ID: {room_id}")
                room_json = {"room_id": room_id, "token": token}
            else:
                # Create the room and get the room ID, token, and cognito username.
                room_json = StateManager.request_create_room(id_token)

                if (
                    not room_json
                    or "room_id" not in room_json
                    or "token" not in room_json
                ):
                    logging.error(
                        "Failed to create room or get room ID/token. Exiting..."
                    )
                    return
                logging.info(
                    f"Room created with ID: {room_json['room_id']} and token: {room_json['token']}"
                )

            # Store credentials for re-registration after reset
            self.id_token = id_token
            self.room_id = room_json["room_id"]
            self.room_token = room_json["token"]

            # Establish a WebSocket connection to the signaling server.
            logging.info(f"Connecting to signaling server at {DNS}...")
            async with websockets.connect(DNS) as websocket:

                # Set the WebSocket connection for the worker.
                self.websocket = websocket

                # Initialize state manager now that we have websocket and worker_id
                self.state_manager = StateManager(
                    worker_id=peer_id,
                    websocket=self.websocket,
                    capabilities=self.capabilities,
                    max_concurrent_jobs=self.max_concurrent_jobs,
                )
                # Set room credentials in state manager
                self.state_manager.set_room_credentials(
                    room_id=self.room_id,
                    token=self.room_token,
                    id_token=self.id_token,
                )
                logging.info("StateManager initialized")

                # Initialize job coordinator now that we have websocket and worker_id
                self.job_coordinator = JobCoordinator(
                    worker_id=peer_id,
                    websocket=self.websocket,
                    capabilities=self.capabilities,
                    update_status_callback=self.state_manager.update_status,
                    get_status_callback=self.state_manager.get_status,
                )
                logging.info("JobCoordinator initialized")

                # Register the worker with the server (with v2.0 metadata).
                logging.info(f"Registering {peer_id} with signaling server...")

                # Try to get SLEAP version
                try:
                    import sleap

                    sleap_version = sleap.__version__
                except (ImportError, AttributeError):
                    sleap_version = "unknown"

                await self.websocket.send(
                    json.dumps(
                        {
                            "type": "register",
                            "peer_id": peer_id,  # identify a peer uniquely in the room (Zoom username)
                            "room_id": room_json[
                                "room_id"
                            ],  # from backend API call for room identification (Zoom meeting ID)
                            "token": room_json[
                                "token"
                            ],  # from backend API call for room joining (Zoom meeting password)
                            "id_token": id_token,  # from anon. Cognito sign-in (Prevent peer spoofing, even anonymously)
                            "role": "worker",  # NEW: worker role for discovery
                            "metadata": {  # NEW: worker capabilities and status
                                "tags": [
                                    "sleap-rtc",
                                    "training-worker",
                                    "inference-worker",
                                ],
                                "properties": {
                                    "gpu_memory_mb": self.gpu_memory_mb,
                                    "gpu_model": self.gpu_model,
                                    "sleap_version": sleap_version,
                                    "cuda_version": self.cuda_version,
                                    "hostname": socket.gethostname(),
                                    "status": self.status,
                                    "max_concurrent_jobs": self.max_concurrent_jobs,
                                    "supported_models": self.supported_models,
                                    "supported_job_types": self.supported_job_types,
                                },
                            },
                        }
                    )
                )
                logging.info(
                    f"{peer_id} sent to signaling server for registration with metadata!"
                )
                logging.info(
                    f"Worker capabilities: GPU={self.gpu_model}, Memory={self.gpu_memory_mb}MB, CUDA={self.cuda_version}"
                )

                # Handle incoming messages from server (e.g. answers).
                try:
                    await self.handle_connection(self.pc, self.websocket, peer_id)
                    logging.info(f"{peer_id} connected with client!")
                except KeyboardInterrupt:
                    logging.info("Worker shut down by user, shutting down...")
                    await self.clean_exit()

        except KeyboardInterrupt:
            logging.info("Worker interrupted by user during setup")
            await self.clean_exit()
        except Exception as e:
            logging.error(f"Error in run_worker: {e}")
        finally:
            await self.clean_exit()


if __name__ == "__main__":
    # Create the worker instance.
    worker = RTCWorkerClient()

    # Create the RTCPeerConnection object.
    pc = RTCPeerConnection()

    # Generate a unique peer ID for the worker.
    # peer_id = f"worker-{uuid.uuid4()}"

    # Run the worker
    try:
        config = get_config()
        asyncio.run(
            worker.run_worker(
                pc=pc,
                # peer_id=peer_id,
                DNS=config.signaling_websocket,
                port_number=8080,
            )
        )
    except KeyboardInterrupt:
        logging.info("Worker interrupted by user. Shutting down...")
    finally:
        logging.info("Worker exiting...")
