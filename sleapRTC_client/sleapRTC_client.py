import asyncio
import argparse
import base64
import uuid
import boto3
import requests
import websockets
import json
import jsonpickle
import logging
import os
import zmq

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
from functools import partial
from pathlib import Path
from sleap.gui.widgets.monitor import LossViewer
from sleap.gui.learning.configs import ConfigFileInfo
from sleap.nn.config.training_job import TrainingJobConfig
from typing import List, Optional, Text, Tuple
from websockets.client import ClientConnection

# Setup logging.
logging.basicConfig(level=logging.INFO)

# Global constants.
CHUNK_SIZE = 64 * 1024
MAX_RECONNECT_ATTEMPTS = 5
RETRY_DELAY = 5  # seconds

class RTCCLIClient:
    def __init__(
        self, 
        peer_id: str = f"client-{uuid.uuid4()}",
        DNS: str = "ws://ec2-54-176-92-10.us-west-1.compute.amazonaws.com",
        port_number: str = "8080",
    ):
        # Initialize RTC peer connection and websocket.
        self.pc = RTCPeerConnection()
        self.websocket: ClientConnection = None
        self.data_channel: RTCDataChannel = None
        self.pc.on("iceconnectionstatechange", self.on_iceconnectionstatechange)

        # Initialize given parameters.
        self.peer_id = peer_id
        self.DNS = DNS
        self.port_number = port_number

        # Other variables.
        self.received_files = {}
        self.target_worker = None
        self.reconnecting = False


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


    def request_anonymous_signin(self) -> str:
        """Request an anonymous token from Signaling Server."""

        # Send POST request to the anonymous sign-in endpoint.
        # CHANGE TO EC2 DNS LATER
        url = "http://ec2-54-176-92-10.us-west-1.compute.amazonaws.com:8001/anonymous-signin"
        response = requests.post(url)

        if response.status_code == 200:
            return response.json()["id_token"]
        else:
            logging.error(f"Failed to get anonymous token: {response.text}")
            return None 
        

    async def start_zmq_listener(self, channel: RTCDataChannel, zmq_address: str = "tcp://127.0.0.1:9000"): # Control Port
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
                    logging.info(f"Sending progress report to client: {msg}")
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
        await self.pc.close()

        logging.info("Closing websocket connection...")
        await self.websocket.close()

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

                # Unhandled message types.
                else:
                    logging.debug(f"Unhandled message: {data}")
                    logging.debug("exiting...")
                    break
        
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

            # Send output directory.
            output_dir = "models"
            if self.config_info_list:
                output_dir = self.config_info_list[0].config.outputs.runs_folder

            self.data_channel.send(f"OUTPUT_DIR::{output_dir}")

            # Obtain metadata.
            file_name = Path(file_path).name
            file_size = Path(file_path).stat().st_size

            # Send metadata next.
            self.data_channel.send(f"FILE_META::{file_name}:{file_size}")

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
                file_name, file_data = list(self.received_files.items())[0]

                try:
                    os.makedirs(self.output_dir, exist_ok=True)
                    file_path = Path.join(self.output_dir, file_name)
    
                    with open(file_path, "wb") as file:
                        file.write(file_data)
                    logging.info(f"File saved as: {file_path}") 
                except PermissionError:
                    logging.error(f"Permission denied when writing to: {output_dir}")
                except Exception as e:
                    logging.error(f"Failed to save file: {e}")
                
                self.received_files.clear()

                # Update monitor window with file transfer and training completion.
                # Can close LossViewer window NOW since EOF received.
                # self.win.close()
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
                if self.win:
                    rtc_progress_msg = {
                        "event": "rtc_update_monitor",
                        "rtc_msg": progress
                    }
                    self.ctrl_socket.send_string(jsonpickle.encode(rtc_progress_msg))

                    # QtCore.QTimer.singleShot(0, partial(self.win._check_messages, rtc_msg=progress))
                    # win._check_messages(
                    #     # Progress should be result from jsonpickle.decode(msg_str)
                    #     rtc_msg=progress 
                    # )
                else:
                    logging.info(f"No monitor window available! win is {self.win}")


                # print("Resetting monitor window.")
                # plateau_patience = config_info.optimization.early_stopping.plateau_patience
                # plateau_min_delta = config_info.optimization.early_stopping.plateau_min_delta
                # win.reset(
                #     what=str(model_type),
                #     plateau_patience=plateau_patience,
                #     plateau_min_delta=plateau_min_delta,
                # )
                # win.setWindowTitle(f"Training Model - {str(model_type)}")
                # win.set_message(f"Preparing to run training...")
                # if save_viz:
                #     viz_window = QtImageDirectoryWidget.make_training_vizualizer(
                #         job.outputs.run_path
                #     )
                #     viz_window.move(win.x() + win.width() + 20, win.y())
                #     win.on_epoch.connect(viz_window.poll)

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

                        # Trained job paths not needed because no remote inference (remote training only).

                    # Otherwise, prepare to run training job.
                    else:
                        job = config_info.config
                        model_type = config_info.head_name

                        if self.win:
                            # Reset (clear) LossViewer window for new training job.
                            logging.info("Resetting monitor window.")
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

                            # self.win.reset(
                            #     what=str(model_type),
                            #     plateau_patience=plateau_patience,
                            #     plateau_min_delta=plateau_min_delta,
                            # )
                            # self.win.setWindowTitle(f"Training Model - {str(model_type)}")
                            # self.win.set_message(f"Preparing to run training...")
                            
                            # Further updates to the LossViewer window handled by PROGRESS_REPORT messages when training starts remotely.

                        logging.info(f"Start training {str(model_type)} job with config: {job}")
            
                except Exception as e:
                    logging.error(f"Failed to parse training job config: {e}")

            elif "TRAIN_JOB_END::" in message: # ONLY TO SIGNAL TRAINING JOB END, NOT WHOLE TRAINING SESSION END (resets handled by TRAIN_JOB_START)
                # Training job end message received.
                _, job_info = message.split("TRAIN_JOB_END::", 1)
                logging.info(f"Train job completed: {job_info}, checking for next job...")

                # Update LossViewer window to indicate training completion based on how many training jobs left.
                if len(self.config_info_list) == 0:
                    logging.info("No more training jobs to run. Closing LossViewer window once trained zip is sent.")
                    # close_msg = {
                    #     "event": "rtc_close_monitor"
                    # }
                    # self.ctrl_socket.send_string(jsonpickle.encode(close_msg)) # sent to port 9001
                    # await self.clean_exit() <- DON'T WANT TO EXIT YET, WAITING FOR Training zip still
                else:
                    logging.info(f"More training jobs to run: {len(self.config_info_list)} remaining.")
                    # Handle next job with TRAIN_JOB_START message.
                    
                    # next_job = self.config_info_list[0]
                    # if self.win:
                    #     # Send reset ZMQ message to LossViewer window.
                    #     reset_msg = {
                    #         "event": "rtc_reset_monitor",
                    #         "what": str(next_job.head_name),
                    #         "plateau_patience": next_job.config.optimization.early_stopping.plateau_patience,
                    #         "plateau_min_delta": next_job.config.optimization.early_stopping.plateau_min_delta,
                    #         "window_title": f"Training Model - {str(next_job.head_name)}",
                    #         "message": "Preparing to run training..."
                    #     }
                    #     self.ctrl_socket.send_string(jsonpickle.encode(reset_msg))
                

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
                if self.win:
                    rtc_progress_msg = {
                        "event": "rtc_update_monitor",
                        "rtc_msg": progress
                    }
                    self.ctrl_socket.send_string(jsonpickle.encode(rtc_progress_msg))

                    # QtCore.QTimer.singleShot(0, partial(self.win._check_messages, rtc_msg=progress))
                    # win._check_messages(
                    #     # Progress should be result from jsonpickle.decode(msg_str)
                    #     rtc_msg=progress 
                    # )
                else:
                    logging.info(f"No monitor window available! win is {self.win}")

            file_name = list(self.received_files.keys())[0]
            if file_name not in self.received_files:
                self.received_files[file_name] = bytearray()
            self.received_files[file_name].extend(message)


    # @pc.on("iceconnectionstatechange")
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
            
            reconnection_success = await self.reconnect(self.pc, self.websocket)
            self.reconnecting = False
            if not reconnection_success:
                logging.info("Reconnection failed. Closing connection...")
                await self.clean_exit()
                return


    async def run_client(
            self, 
            file_path: str = None, 
            output_dir: str = "", 
            zmq_ports: list = None, 
            config_info_list: List[ConfigFileInfo] = None,
        ):
        """Sends initial SDP offer to worker peer and establishes both connection & datachannel to be used by both parties.
        
        Args:
            peer_id: Unique str identifier for client
            DNS: DNS address of the signaling server
            port_number: Port number of the signaling server
            file_path: Path to a file to be sent to worker peer (usually zip file)
            CLI: Boolean indicating if the client is running in CLI mode
            output_dir: Directory to save files received from worker peer
        Returns:
            None
        """

        # Initialize data channel.
        channel = self.pc.createDataChannel("my-data-channel")
        self.data_channel = channel
        logging.info("channel(%s) %s" % (channel.label, "created by local party."))

        # Set local variable
        self.file_path = file_path
        self.output_dir = output_dir
        self.zmq_ports = zmq_ports
        self.config_info_list = config_info_list

        # Register event handlers for the data channel.
        channel.on("open", self.on_channel_open)
        channel.on("message", self.on_message)

        # CLI so no LossViewer functions/updates.

        # Sign-in anonymously with Cognito to get an ID token.
        id_token = self.request_anonymous_signin()

        if not id_token:
            logging.error("Failed to sign in anonymously. Exiting client.")
            return
        
        logging.info(f"Anonymous sign-in successful. ID token: {id_token}")

        # No room creation needed for GUI Client since using Worker credentials.
        # Only needs its own Firebase ID token and peer ID 
        # Create the room and get the room ID and token.
        # room_json = self.request_create_room(id_token)
        # logging.info(f"Room created with ID: {room_json['room_id']} and token: {room_json['token']}")

        # Initate the WebSocket connection to the signaling server.
        async with websockets.connect(f"{self.DNS}:{self.port_number}") as websocket:

            # Initate the websocket for the GUI client (so other functions can use). 
            self.websocket = websocket

            # Prompt for session string.
            while True:
                session_string = input("Please enter RTC session string (or type 'exit' to quit): ")
                if session_string.lower() == "exit":
                    print("Exiting client.")
                    return  # <-- This exits the current function (e.g., run_client)
                try:
                    session_str_json = self.parse_session_string(session_string)
                    break  # Exit loop if parsing succeeds
                except ValueError as e:
                    print(f"Error: {e}")
                    print("Please try again or type 'exit' to quit.")
            
            # Extract worker credentials from session string.
            worker_room_id = session_str_json.get("room_id")
            worker_token = session_str_json.get("token")
            worker_peer_id = session_str_json.get("worker_peer_id")

            # Register the client with the signaling server.
            logging.info(f"Registering {self.peer_id} with signaling server...")
            await self.websocket.send(json.dumps({
                'type': 'register', 
                'peer_id': self.peer_id, # should be own peer_id (Zoom username)
                'room_id': worker_room_id, # should match Worker's room_id (Zoom meeting ID)
                'token': worker_token, # should match Worker's token (Zoom meeting password)
                'id_token': id_token, # should be own Cognito ID token
            }))
            # await websocket.send(json.dumps({'type': 'register', 'peer_id': self.peer_id}))
            logging.info(f"{self.peer_id} sent to signaling server for registration!")

            # # Query for available workers.
            # await websocket.send(json.dumps({'type': 'query'}))
            # response = await websocket.recv()
            # available_workers = json.loads(response)["peers"]
            # logging.info(f"Available workers: {available_workers}")

            # Select a worker to connect to.
            # target_worker = available_workers[0] if available_workers else None

            # self.peer_id should match Worker's peer_id (Zoom username).
            target_worker = worker_peer_id
            logging.info(f"Selected worker: {target_worker}")

            if not target_worker:
                logging.info("No target worker given. Cannot connect.")
                return
            
            # Create and send SDP offer to worker peer.
            await self.pc.setLocalDescription(await self.pc.createOffer())
            await websocket.send(json.dumps({
                'type': self.pc.localDescription.type, # type: 'offer'
                'sender': self.peer_id, # should be own peer_id (Zoom username)
                'target': target_worker, # should match Worker's peer_id (Zoom username)
                'sdp': self.pc.localDescription.sdp
            }))
            logging.info('Offer sent to worker')

            # Handle incoming messages from server (e.g. answer from worker).
            await self.handle_connection()

        # Exit.
        await self.pc.close()
        await websocket.close()


def create_remote_trainer_using_cli(args: Optional[List] = None):
    """Create CLI for remote training"""
    import argparse
    parser = argparse.ArgumentParser(description="SLEAP Remote Training CLI Client")

    pass