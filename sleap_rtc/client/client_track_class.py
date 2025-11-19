"""Remote inference client for sleap-RTC.

Similar structure to RTCClient in client_class.py but specialized for inference.
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import List, Optional

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
from websockets.client import ClientConnection
import websockets
import requests

from sleap_rtc.config import get_config

logging.basicConfig(level=logging.INFO)

# Global constants
CHUNK_SIZE = 64 * 1024
MAX_RECONNECT_ATTEMPTS = 5
RETRY_DELAY = 5  # seconds


class RTCTrackClient:
    """Client for running remote inference via WebRTC.

    Mirrors structure of RTCClient but specialized for inference workflow.
    """

    def __init__(
        self,
        DNS: Optional[str] = None,
        port_number: str = "8080",
    ):
        # Initialize RTC peer connection and websocket
        self.pc = RTCPeerConnection()
        self.websocket: ClientConnection = None
        self.data_channel: RTCDataChannel = None
        self.pc.on("iceconnectionstatechange", self.on_iceconnectionstatechange)

        # Initialize given parameters
        config = get_config()
        self.DNS = DNS if DNS is not None else config.signaling_websocket
        self.port_number = port_number

        # Inference-specific variables
        self.chunk_size = CHUNK_SIZE
        self.received_files = {}
        self.predictions_data = bytearray()
        self.cognito_username = None
        self.target_worker = None
        self.reconnecting = False
        self.reconnect_attempts = 0

    def create_track_package(
        self,
        data_path: str,
        model_paths: List[str],
        output: str,
        only_suggested_frames: bool,
    ) -> str:
        """Creates a track package with data + models + track-script.sh

        Args:
            data_path: Path to .slp file with data
            model_paths: List of paths to trained model directories
            output: Output filename for predictions
            only_suggested_frames: Whether to track only suggested frames

        Returns:
            Path to created .zip package
        """
        temp_dir = tempfile.mkdtemp(prefix="sleap_track_")
        pkg_dir = Path(temp_dir) / "track_package"
        pkg_dir.mkdir()

        # 1. Copy data file
        data_file = Path(data_path)
        if not data_file.exists():
            raise FileNotFoundError(f"Data file not found: {data_path}")

        shutil.copy(data_file, pkg_dir / data_file.name)
        logging.info(f"Copied data file: {data_file.name}")

        # 2. Copy model directories
        models_dir = pkg_dir / "models"
        models_dir.mkdir()

        for model_path in model_paths:
            model_dir = Path(model_path)
            if not model_dir.exists():
                raise FileNotFoundError(f"Model directory not found: {model_path}")

            # Copy entire model directory
            dest_model_dir = models_dir / model_dir.name
            shutil.copytree(model_dir, dest_model_dir)
            logging.info(f"Copied model: {model_dir.name}")

        # 3. Generate track-script.sh
        track_script = self._generate_track_script(
            data_filename=data_file.name,
            model_names=[Path(p).name for p in model_paths],
            output=output,
            only_suggested_frames=only_suggested_frames,
        )

        track_script_path = pkg_dir / "track-script.sh"
        track_script_path.write_text(track_script)
        track_script_path.chmod(track_script_path.stat().st_mode | stat.S_IEXEC)
        logging.info("Generated track-script.sh")

        # 4. Zip the package
        zip_path = Path(temp_dir) / "track_package.zip"
        shutil.make_archive(str(zip_path.with_suffix("")), "zip", pkg_dir)
        logging.info(f"Created track package: {zip_path}")

        return str(zip_path)

    def _generate_track_script(
        self,
        data_filename: str,
        model_names: List[str],
        output: str,
        only_suggested_frames: bool,
    ) -> str:
        """Generates track-script.sh for the worker to execute.

        Args:
            data_filename: Name of the data .slp file
            model_names: List of model directory names
            output: Output predictions filename
            only_suggested_frames: Whether to track only suggested frames

        Returns:
            Shell script content
        """
        model_paths_args = " \\\n  ".join(
            [f"--model_paths models/{name}" for name in model_names]
        )

        suggested_flag = (
            "--only_suggested_frames \\\n  " if only_suggested_frames else ""
        )

        script = f"""#!/bin/bash
# Auto-generated inference script for sleap-nn track

sleap-nn track \\
  --data_path {data_filename} \\
  {suggested_flag}{model_paths_args} \\
  -o {output}
"""
        return script

    async def send_track_package(
        self, channel: RTCDataChannel, package_path: str, output_dir: str
    ):
        """Sends the track package to the worker.

        Args:
            channel: WebRTC data channel
            package_path: Path to the .zip package
            output_dir: Output directory for predictions
        """
        if channel.readyState != "open":
            logging.error(f"Data channel not open: {channel.readyState}")
            return

        # Send package type indicator
        channel.send("PACKAGE_TYPE::track")
        await asyncio.sleep(0.1)

        # Send output directory (where predictions will be saved)
        channel.send(f"OUTPUT_DIR::{output_dir}")
        await asyncio.sleep(0.1)

        # Send file metadata
        file_name = os.path.basename(package_path)
        file_size = os.path.getsize(package_path)

        channel.send(
            f"FILE_META::{file_name}:{file_size}:false"
        )  # gui=false for inference
        logging.info(f"Sending track package: {file_name} ({file_size} bytes)")

        # Send file in chunks
        with open(package_path, "rb") as file:
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

                if bytes_sent % (1024 * 1024) == 0:  # Log every 1MB
                    progress = (bytes_sent / file_size) * 100
                    logging.info(f"Upload progress: {progress:.1f}%")

        # Signal end of file
        channel.send("END_OF_FILE")
        logging.info("Track package sent successfully")

    def parse_session_string(self, session_string: str):
        """Parse session string to extract worker credentials."""
        prefix = "sleap-session:"
        if not session_string.startswith(prefix):
            raise ValueError(f"Session string must start with '{prefix}'")

        encoded = session_string[len(prefix) :]
        try:
            json_str = base64.urlsafe_b64decode(encoded).decode()
            data = json.loads(json_str)
            return {
                "room_id": data.get("r"),
                "token": data.get("t"),
                "peer_id": data.get("p"),
            }
        except Exception as e:
            raise ValueError(f"Failed to decode session string: {e}")

    def request_anonymous_signin(self) -> str:
        """Request an anonymous token from Signaling Server."""
        config = get_config()
        url = config.get_http_endpoint("/anonymous-signin")
        response = requests.post(url)

        if response.status_code == 200:
            return response.json()
        else:
            logging.error(f"Failed to get anonymous token: {response.text}")
            return None

    def request_peer_room_deletion(self, peer_id: str):
        """Requests the signaling server to delete the room and associated user/worker."""
        config = get_config()
        url = config.get_http_endpoint("/delete-peers-and-room")
        json_data = {
            "peer_id": peer_id,
        }

        response = requests.post(url, json=json_data)

        if response.status_code == 200:
            return  # Success
        else:
            logging.error(f"Failed to delete room and peer: {response.text}")
            return None

    async def clean_exit(self):
        """Cleans up the client connection and closes the peer connection and websocket."""
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

    async def keep_ice_alive(self):
        """Sends periodic keep-alive messages to the worker peer to maintain the connection."""
        while True:
            await asyncio.sleep(15)
            if self.data_channel and self.data_channel.readyState == "open":
                self.data_channel.send(b"KEEP_ALIVE")

    async def on_channel_open(self):
        """Event handler function for when the datachannel is open."""
        # Initiate keep-alive task
        asyncio.create_task(self.keep_ice_alive())
        logging.info(f"{self.data_channel.label} is open")

        # Send track package to worker
        await self.send_track_package(
            self.data_channel, self.file_path, self.output_dir
        )

    async def on_message(self, message):
        """Handles incoming messages from worker during inference.

        Args:
            message: Message from worker (string or bytes)
        """
        if isinstance(message, str):
            if message == "END_OF_FILE":
                # Save received predictions file
                if self.predictions_data:
                    output_path = Path(self.output_dir) / self.predictions_filename
                    output_path.write_bytes(self.predictions_data)
                    logging.info(f"Predictions saved to: {output_path}")
                    self.predictions_data = bytearray()

            elif "FILE_META::" in message:
                # Predictions file metadata
                _, meta = message.split("FILE_META::", 1)
                file_name, file_size, _ = meta.split(":")
                logging.info(f"Receiving predictions: {file_name} ({file_size} bytes)")
                self.predictions_data = bytearray()
                self.predictions_filename = file_name

            elif "TRACK_LOG:" in message:
                # Inference log from sleap-nn track
                _, log = message.split("TRACK_LOG:", 1)
                print(log)  # Print to console

            elif "INFERENCE_START" in message:
                logging.info("Worker started inference...")

            elif "INFERENCE_JOBS_DONE" in message:
                logging.info("Inference completed on worker")

            elif message.startswith("INFERENCE_JOB_"):
                logging.info(message)

        elif isinstance(message, bytes):
            # Accumulate predictions file data
            if message != b"KEEP_ALIVE":
                self.predictions_data.extend(message)

    async def on_iceconnectionstatechange(self):
        """Event handler function for when the ICE connection state changes."""
        logging.info(f"ICE connection state is now {self.pc.iceConnectionState}")

        if self.pc.iceConnectionState in ["connected", "completed"]:
            self.reconnect_attempts = 0
            logging.info("ICE connection established.")

        elif (
            self.pc.iceConnectionState in ["failed", "disconnected", "closed"]
            and not self.reconnecting
        ):
            logging.warning(f"ICE connection {self.pc.iceConnectionState}. Exiting...")
            await self.clean_exit()

    async def handle_connection(self):
        """Handles receiving SDP answer from Worker and ICE candidates from Worker."""
        try:
            async for message in self.websocket:
                if type(message) == int:
                    logging.info(f"Received int message: {message}")

                data = json.loads(message)

                # Receive answer SDP from worker
                if data.get("type") == "answer":
                    logging.info(f"Received answer from worker: {data}")
                    await self.pc.setRemoteDescription(
                        RTCSessionDescription(
                            sdp=data.get("sdp"), type=data.get("type")
                        )
                    )

                # Handle "trickle ICE" for non-local ICE candidates
                elif data.get("type") == "candidate":
                    logging.info("Received ICE candidate")
                    candidate = data.get("candidate")
                    await self.pc.addIceCandidate(candidate)

                # Worker quit
                elif data.get("type") == "quit":
                    logging.info("Worker has quit. Closing connection...")
                    await self.clean_exit()
                    break

                # Client authenticated
                elif data.get("type") == "registered_auth":
                    logging.info(f"Client authenticated with server.")

                else:
                    logging.debug(f"Unhandled message: {data}")

        except json.JSONDecodeError:
            logging.error("Invalid JSON received")
        except Exception as e:
            logging.error(f"Error handling message: {e}")

    async def run_client(
        self, file_path: str = None, output_dir: str = ".", session_string: str = None
    ):
        """Connects to worker and runs inference workflow.

        Args:
            file_path: Path to track package zip file
            output_dir: Directory to save predictions
            session_string: Session string from worker
        """
        try:
            # Initialize data channel
            channel = self.pc.createDataChannel("my-data-channel")
            self.data_channel = channel
            logging.info("channel(%s) %s" % (channel.label, "created by local party."))

            # Set local variables
            self.file_path = file_path
            self.output_dir = output_dir

            # Register event handlers for the data channel
            channel.on("open", self.on_channel_open)
            channel.on("message", self.on_message)

            # Initialize reconnect attempts
            self.reconnect_attempts = 0

            # Sign-in anonymously with Cognito
            sign_in_json = self.request_anonymous_signin()
            id_token = sign_in_json["id_token"]
            self.peer_id = sign_in_json["username"]
            self.cognito_username = self.peer_id

            if not id_token:
                logging.error("Failed to get anonymous ID token. Exiting client.")
                return

            logging.info(f"Anonymous ID token received: {id_token}")

            # Connect to signaling server
            async with websockets.connect(self.DNS) as websocket:
                self.websocket = websocket

                # Parse session string
                if not session_string:
                    session_str_json = None
                    while True:
                        session_string = input(
                            "Please enter RTC session string (or type 'exit' to quit): "
                        )
                        if session_string.lower() == "exit":
                            print("Exiting client.")
                            return
                        try:
                            session_str_json = self.parse_session_string(session_string)
                            break
                        except ValueError as e:
                            print(f"Error: {e}")
                            print("Please try again or type 'exit' to quit.")
                else:
                    session_str_json = self.parse_session_string(session_string)

                # Extract worker credentials
                worker_room_id = session_str_json.get("room_id")
                worker_token = session_str_json.get("token")
                worker_peer_id = session_str_json.get("peer_id")

                # Register with signaling server
                logging.info(f"Registering {self.peer_id} with signaling server...")
                await self.websocket.send(
                    json.dumps(
                        {
                            "type": "register",
                            "peer_id": self.peer_id,
                            "room_id": worker_room_id,
                            "token": worker_token,
                            "id_token": id_token,
                        }
                    )
                )
                logging.info(
                    f"{self.peer_id} sent to signaling server for registration!"
                )

                # Set target worker
                self.target_worker = worker_peer_id
                logging.info(f"Selected worker: {self.target_worker}")

                if not self.target_worker:
                    logging.info("No target worker given. Cannot connect.")
                    return

                # Create and send SDP offer to worker
                await self.pc.setLocalDescription(await self.pc.createOffer())
                await websocket.send(
                    json.dumps(
                        {
                            "type": self.pc.localDescription.type,
                            "sender": self.peer_id,
                            "target": self.target_worker,
                            "sdp": self.pc.localDescription.sdp,
                        }
                    )
                )
                logging.info("Offer sent to worker")

                # Handle incoming messages from server
                await self.handle_connection()

            # Cleanup
            logging.info("Cleaning up Cognito and DynamoDB entries...")
            self.request_peer_room_deletion(self.peer_id)

        except Exception as e:
            logging.error(f"Error in run_client: {e}")
        finally:
            await self.clean_exit()
