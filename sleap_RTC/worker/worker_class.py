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
import hashlib

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
# from run_training import run_all_training_jobs
from pathlib import Path
from websockets.client import ClientConnection

from sleap_rtc.config import get_config
from sleap_rtc.worker.model_registry import ModelRegistry

# Setup logging.
logging.basicConfig(level=logging.INFO)

# Set chunk separator
SEP = re.compile(rb'[\r\n]')

class RTCWorkerClient:
    def __init__(self, chunk_size=32 * 1024, gpu_id=0):
        # Use /app/shared_data in production, current dir + shared_data in dev
        self.save_dir = "."
        self.chunk_size = chunk_size
        self.received_files = {}
        self.output_dir = ""
        self.ctrl_socket = None
        self.pc = None  # RTCPeerConnection will be set later
        self.websocket = None  # WebSocket connection will be set later
        self.package_type = "train"  # Default to training, can be "track" for inference

        # Worker state and capabilities (for v2.0 features)
        self.gpu_id = gpu_id
        self.status = "available"  # "available", "busy", "reserved", "maintenance"
        self.current_job = None
        self.gpu_memory_mb = self._detect_gpu_memory()
        self.gpu_model = self._detect_gpu_model()
        self.cuda_version = self._detect_cuda_version()
        self.supported_models = ["base", "centroid", "topdown"]
        self.supported_job_types = ["training", "inference"]
        self.max_concurrent_jobs = 1

        # Model registry for tracking trained models and checkpoints
        self.registry = ModelRegistry()
        self.current_model_id = None  # Track currently training model ID
        self.training_job_hash = None  # Track hash of uploaded training package
        self.is_query_only_connection = False  # Track if connection is only for registry queries

    def _detect_gpu_memory(self) -> int:
        """Detect GPU memory in MB.

        Returns:
            GPU memory in MB, or 0 if no GPU available
        """
        try:
            import torch
            if torch.cuda.is_available() and torch.cuda.device_count() > self.gpu_id:
                return torch.cuda.get_device_properties(self.gpu_id).total_memory // (1024 * 1024)
        except (ImportError, RuntimeError) as e:
            logging.warning(f"Failed to detect GPU memory: {e}")
        return 0

    def _detect_gpu_model(self) -> str:
        """Detect GPU model name.

        Returns:
            GPU model name, or "CPU" if no GPU available
        """
        try:
            import torch
            if torch.cuda.is_available() and torch.cuda.device_count() > self.gpu_id:
                return torch.cuda.get_device_properties(self.gpu_id).name
        except (ImportError, RuntimeError) as e:
            logging.warning(f"Failed to detect GPU model: {e}")
        return "CPU"

    def _detect_cuda_version(self) -> str:
        """Detect CUDA version.

        Returns:
            CUDA version string, or "N/A" if CUDA not available
        """
        try:
            import torch
            if torch.cuda.is_available():
                return torch.version.cuda if torch.version.cuda else "N/A"
        except (ImportError, RuntimeError) as e:
            logging.warning(f"Failed to detect CUDA version: {e}")
        return "N/A"

    async def update_status(self, status: str, **extra_properties):
        """Update worker status in signaling server.

        Args:
            status: "available", "busy", "reserved", or "maintenance"
            **extra_properties: Additional properties to update (e.g., current_job_id)
        """
        self.status = status

        metadata = {
            "properties": {
                "status": status,
                **extra_properties
            }
        }

        try:
            await self.websocket.send(json.dumps({
                "type": "update_metadata",
                "peer_id": self.cognito_username,
                "metadata": metadata
            }))

            # Wait for confirmation (optional but recommended)
            response = await asyncio.wait_for(self.websocket.recv(), timeout=2.0)
            response_data = json.loads(response)

            if response_data.get("type") == "metadata_updated":
                logging.info(f"Status updated to: {status}")
            else:
                logging.warning(f"Unexpected response to status update: {response_data}")
        except asyncio.TimeoutError:
            logging.warning(f"Timeout waiting for status update confirmation")
        except Exception as e:
            logging.error(f"Failed to update status: {e}")

    async def _send_peer_message(self, to_peer_id: str, payload: dict):
        """Send peer message via signaling server.

        Args:
            to_peer_id: Target peer ID
            payload: Application-specific message payload
        """
        try:
            await self.websocket.send(json.dumps({
                "type": "peer_message",
                "from_peer_id": self.cognito_username,
                "to_peer_id": to_peer_id,
                "payload": payload
            }))
            logging.info(f"Sent peer message to {to_peer_id}: {payload.get('app_message_type', 'unknown')}")
        except Exception as e:
            logging.error(f"Failed to send peer message: {e}")

    def _check_job_compatibility(self, request: dict) -> bool:
        """Check if this worker can handle the job.

        Args:
            request: Job request dictionary

        Returns:
            True if worker can handle the job, False otherwise
        """
        job_spec = request.get("config", {})
        requirements = request.get("requirements", {})

        # Check GPU memory
        min_gpu_mb = requirements.get("min_gpu_memory_mb", 0)
        if self.gpu_memory_mb < min_gpu_mb:
            logging.info(f"Job requires {min_gpu_mb}MB GPU memory, worker has {self.gpu_memory_mb}MB")
            return False

        # Check model support
        model_type = job_spec.get("model_type")
        if model_type and model_type not in self.supported_models:
            logging.info(f"Job requires model type '{model_type}', worker supports {self.supported_models}")
            return False

        # Check job type
        job_type = request.get("job_type")
        if job_type and job_type not in self.supported_job_types:
            logging.info(f"Job type '{job_type}' not supported, worker supports {self.supported_job_types}")
            return False

        return True

    def _estimate_job_duration(self, request: dict) -> int:
        """Estimate job duration in minutes.

        Args:
            request: Job request dictionary

        Returns:
            Estimated duration in minutes
        """
        # Simple estimation based on job type and config
        job_type = request.get("job_type", "training")
        config = request.get("config", {})

        if job_type == "training":
            epochs = config.get("epochs", 100)
            # Rough estimate: 0.5 minutes per epoch
            return int(epochs * 0.5)
        elif job_type == "inference":
            frame_count = request.get("dataset_info", {}).get("frame_count", 1000)
            # Rough estimate: 100 frames per minute
            return max(1, int(frame_count / 100))

        return 60  # Default 60 minutes

    def _get_gpu_utilization(self) -> float:
        """Get current GPU utilization percentage.

        Returns:
            GPU utilization as a float between 0.0 and 1.0
        """
        try:
            import torch
            if torch.cuda.is_available() and torch.cuda.device_count() > self.gpu_id:
                # Simple check: return 0.0 if available, 1.0 if busy
                return 0.0 if self.status == "available" else 0.9
        except (ImportError, RuntimeError):
            pass
        return 0.0

    def _get_available_memory(self) -> int:
        """Get available GPU memory in MB.

        Returns:
            Available memory in MB
        """
        try:
            import torch
            if torch.cuda.is_available() and torch.cuda.device_count() > self.gpu_id:
                free_memory = torch.cuda.mem_get_info(self.gpu_id)[0]
                return int(free_memory / (1024 * 1024))
        except (ImportError, RuntimeError):
            pass
        return self.gpu_memory_mb

    async def handle_peer_message(self, message: dict):
        """Handle incoming peer messages (job requests).

        Args:
            message: Peer message from signaling server
        """
        if message.get("type") != "peer_message":
            return

        payload = message.get("payload", {})
        app_message_type = payload.get("app_message_type")
        from_peer_id = message.get("from_peer_id")

        logging.info(f"Received peer message from {from_peer_id}: {app_message_type}")

        if app_message_type == "job_request":
            await self._handle_job_request(from_peer_id, payload)
        elif app_message_type == "job_assignment":
            await self._handle_job_assignment(from_peer_id, payload)
        elif app_message_type == "job_cancel":
            await self._handle_job_cancel(payload)
        else:
            logging.warning(f"Unhandled peer message type: {app_message_type}")

    async def _handle_job_request(self, client_id: str, request: dict):
        """Respond to job request from client.

        Args:
            client_id: Client peer ID
            request: Job request payload
        """
        job_id = request.get("job_id")
        job_type = request.get("job_type")

        logging.info(f"Handling job request {job_id} of type {job_type} from {client_id}")

        # Check if we can accept this job
        can_accept = (
            self.status == "available" and
            self._check_job_compatibility(request)
        )

        if not can_accept:
            # Send rejection
            reason = "busy" if self.status != "available" else "incompatible"
            logging.info(f"Rejecting job {job_id}: {reason}")

            await self._send_peer_message(client_id, {
                "app_message_type": "job_response",
                "job_id": job_id,
                "accepted": False,
                "reason": reason
            })
            return

        # Estimate job duration
        estimated_duration = self._estimate_job_duration(request)

        # Send acceptance
        logging.info(f"Accepting job {job_id}, estimated duration: {estimated_duration} minutes")

        await self._send_peer_message(client_id, {
            "app_message_type": "job_response",
            "job_id": job_id,
            "accepted": True,
            "estimated_start_time_sec": 0,
            "estimated_duration_minutes": estimated_duration,
            "worker_info": {
                "gpu_utilization": self._get_gpu_utilization(),
                "available_memory_mb": self._get_available_memory()
            }
        })

        # Update status to "reserved" (prevent other clients from requesting)
        await self.update_status("reserved", pending_job_id=job_id)

    async def _handle_job_assignment(self, client_id: str, assignment: dict):
        """Handle job assignment from client.

        Args:
            client_id: Client peer ID
            assignment: Job assignment payload
        """
        job_id = assignment.get("job_id")

        logging.info(f"Handling job assignment {job_id} from {client_id}")

        # Update status to busy
        await self.update_status("busy", current_job_id=job_id)

        # Store job info for execution
        self.current_job = {
            "job_id": job_id,
            "client_id": client_id,
            "assigned_at": time.time()
        }

        logging.info(f"Job {job_id} assigned and ready for WebRTC connection")

        # Note: WebRTC connection will be initiated by the client
        # The existing on_datachannel handler will handle the data transfer

    async def _handle_job_cancel(self, payload: dict):
        """Handle job cancellation request.

        Args:
            payload: Job cancel payload
        """
        job_id = payload.get("job_id")
        reason = payload.get("reason", "unknown")

        logging.info(f"Handling job cancellation for {job_id}: {reason}")

        if self.current_job and self.current_job.get("job_id") == job_id:
            # Cancel the current job
            client_id = self.current_job.get("client_id")

            # Send cancellation acknowledgment
            await self._send_peer_message(client_id, {
                "app_message_type": "job_cancelled",
                "job_id": job_id,
                "status": "cancelled",
                "cleanup_complete": True
            })

            # Reset job state
            self.current_job = None
            await self.update_status("available")

            logging.info(f"Job {job_id} cancelled successfully")
        else:
            logging.warning(f"Cannot cancel job {job_id}: not currently running")

    def generate_session_string(self, room_id: str, token: str, peer_id: str):
        """Generates an encoded session string for the room."""
        session_data = {
            "r": room_id, 
            "t": token, 
            "p": peer_id 
        }
        encoded = base64.urlsafe_b64encode(json.dumps(session_data).encode()).decode()

        return f"sleap-session:{encoded}"


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


    def request_create_room(self, id_token):
        """Requests the signaling server to create a room and returns the room ID and token.

        Args:
            id_token (str): Cognito ID token for authentication.
        Returns:
            dict: Contains room_id and token if successful, otherwise raises an exception.
        """

        config = get_config()
        url = config.get_http_endpoint("/create-room")
        headers = {"Authorization": f"Bearer {id_token}"} # Use the ID token string for authentication

        response = requests.post(url, headers=headers)
        
        if response.status_code == 200:
            return response.json()
        else:
            logging.error(f"Failed to create room: {response.status_code} - {response.text}")
            raise Exception("Failed to create room")


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


    def parse_training_script(self, train_script_path: str):
        jobs = []
        # Updated pattern to match 'sleap-nn train' and extract --config-name
        # Example: sleap-nn train --config-name centroid.yaml...
        pattern = re.compile(r"^\s*sleap-(?:nn\s+)?train\s+.*--config-name\s+(\S+)")

        with open(train_script_path, "r") as f:
            for line in f:
                match = pattern.match(line)
                if match:
                    config_name = match.group(1)
                    # For sleap-nn, we don't need separate labels file - it's configured in the YAML
                    jobs.append(config_name)
        return jobs


    def parse_track_script(self, track_script_path: str):
        """Parses track-script.sh and extracts sleap-nn track commands.

        Args:
            track_script_path: Path to track-script.sh

        Returns:
            List of command argument lists
        """
        commands = []
        pattern = re.compile(r"^\s*sleap-nn\s+track\s+(.+)")

        with open(track_script_path, "r") as f:
            script_content = f.read()

        # Handle multi-line commands with backslashes
        script_content = script_content.replace("\\\n", " ")

        for line in script_content.split("\n"):
            match = pattern.match(line)
            if match:
                # Split arguments while preserving quoted strings
                args_str = match.group(1)
                # Simple split (for more complex parsing, use shlex.split)
                args = ["sleap-nn", "track"] + args_str.split()
                commands.append(args)

        return commands


    async def run_all_training_jobs(self, channel: RTCDataChannel, train_script_path: str):
        """Execute training jobs with progress updates.

        Args:
            channel: RTC data channel for sending logs
            train_script_path: Path to training script
        """
        training_jobs = self.parse_training_script(train_script_path)

        # Get job ID and client ID from current_job if available
        job_id = self.current_job.get("job_id") if self.current_job else None
        client_id = self.current_job.get("client_id") if self.current_job else None

        try:
            for config_name in training_jobs:
                job_name = Path(config_name).stem

                # Load training configuration for registry
                config_path = Path(self.unzipped_dir) / config_name
                try:
                    import yaml
                    with open(config_path, 'r') as f:
                        training_config = yaml.safe_load(f)
                except Exception as e:
                    logging.warning(f"Could not load config {config_name} for registry: {e}")
                    training_config = {}

                # Find labels file for hashing
                labels_files = list(Path(self.unzipped_dir).glob("*.slp"))
                labels_path = str(labels_files[0]) if labels_files else ""

                # Generate unique model ID
                from datetime import datetime
                run_name = f"{job_name}_{datetime.now().strftime('%y%m%d_%H%M%S')}"

                model_id = self.registry.generate_model_id(
                    config=training_config,
                    labels_path=labels_path,
                    run_name=run_name
                )

                # Determine model type from config
                head_configs = training_config.get('model_config', {}).get('head_configs', {})
                model_types = [k for k, v in head_configs.items() if v is not None]
                model_type = model_types[0] if model_types else "unknown"

                # Create hash-based directory name
                model_dir_name = f"{model_type}_{model_id}"

                # Register model in registry
                model_info = {
                    "id": model_id,
                    "full_hash": hashlib.sha256(f"{model_id}{run_name}".encode()).hexdigest(),
                    "run_name": run_name,
                    "model_type": model_type,
                    "training_job_hash": self.training_job_hash or "",
                    "status": "training",
                    "checkpoint_path": f"{self.output_dir}/{model_dir_name}/best.ckpt",
                    "config_path": f"{self.output_dir}/{model_dir_name}/training_config.yaml",
                    "created_at": datetime.now().isoformat(),
                    "metadata": {
                        "dataset": os.path.basename(labels_path) if labels_path else "",
                        "gpu_model": self.gpu_model,
                        "original_config": config_name
                    }
                }

                registered_id = self.registry.register(model_info)
                self.current_model_id = registered_id
                current_training_model_id = registered_id  # Store locally for this training job
                logging.info(f"Registered model {registered_id} in registry")

                # Send RTC msg over channel to indicate job start.
                logging.info(f"Starting training job: {job_name} with config: {config_name}")
                channel.send(f"TRAIN_JOB_START::{job_name}")

                # Send starting status via peer message
                if job_id and client_id:
                    await self._send_peer_message(client_id, {
                        "app_message_type": "job_status",
                        "job_id": job_id,
                        "status": "starting",
                        "progress": 0.0,
                        "message": f"Initializing training: {job_name}"
                    })

                # Check for interrupted training to resume
                interrupted_jobs = self.registry.get_interrupted()
                resume_checkpoint = None
                for interrupted in interrupted_jobs:
                    interrupted_config = interrupted.get('metadata', {}).get('original_config', '')
                    if interrupted['model_type'] == model_type and interrupted_config == config_name:
                        resume_checkpoint = interrupted.get('checkpoint_path')
                        logging.info(f"Found interrupted job for {model_type}, will resume from {resume_checkpoint}")
                        # Use the same model ID to continue training
                        self.current_model_id = interrupted['id']
                        model_dir_name = f"{model_type}_{interrupted['id']}"
                        break

                # Use sleap-nn train command for newer SLEAP versions
                # Run directly in the extracted directory with config-dir from training script
                cmd = [
                    "sleap-nn", "train",
                    "--config-name", config_name,
                    "--config-dir", ".",
                    "trainer_config.ckpt_dir=models",
                    f"trainer_config.run_name={model_dir_name}",  # Use hash-based name
                    "trainer_config.zmq.controller_port=9000",
                    "trainer_config.zmq.publish_port=9001",
                    # "trainer_config.enable_progress_bar=false"
                ]

                # Add resumption if checkpoint found
                if resume_checkpoint:
                    # Convert to absolute path
                    resume_path = Path(self.unzipped_dir) / resume_checkpoint
                    if resume_path.exists():
                        cmd.append(f"trainer_config.resume_ckpt_path={resume_path}")
                        logging.info(f"Resuming from checkpoint: {resume_path}")
                    else:
                        logging.warning(f"Resume checkpoint not found: {resume_path}, starting fresh")

                logging.info(f"[RUNNING] {' '.join(cmd)} (cwd={self.unzipped_dir})")

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=self.unzipped_dir, # run this command in the extracted directory
                    env={**os.environ, "PYTHONUNBUFFERED": "1"}
                )

                assert process.stdout is not None
                logging.info(f"Process started with PID: {process.pid}")

                async def stream_logs(emit_on_cr=True, read_size=512, max_flush=64*1024):
                    buf = b""
                    try:
                        while True:
                            chunk = await process.stdout.read(read_size)
                            if not chunk:
                                # process ended; flush any remaining text as a final line
                                if buf:
                                    channel.send(buf.decode(errors="replace") + "\n")
                                break

                            buf += chunk

                            while True:
                                m = SEP.search(buf)
                                if not m:
                                    # If tqdm keeps extending one long line, occasionally flush
                                    if len(buf) > max_flush:
                                        text = buf.decode(errors="replace")
                                        if emit_on_cr and text:
                                            # send as a CR-style redraw
                                            channel.send("\r" + text)
                                        buf = b""
                                    break

                                sep = buf[m.start():m.end()]     # b'\n' or b'\r'
                                payload = buf[:m.start()]
                                buf = buf[m.end():]

                                text = payload.decode(errors="replace")
                                if not text:
                                    continue

                                if sep == b'\n':
                                    # NORMAL LOG LINE: preserve newline so your client appends it
                                    channel.send(text + "\n")
                                else:  # sep == b'\r'
                                    if emit_on_cr:
                                        # PROGRESS REDRAW: send a carriage-return update
                                        # Client should treat this as "replace current progress line"
                                        channel.send("\r" + text)

                    except Exception as e:
                        logging.exception("stream_logs failed: %s", e)
                        # Optional: notify client without crashing the emitter
                        try:
                            channel.send(f"[log-stream error] {e}\n")
                        except Exception:
                            pass

                # Track start time for duration calculation
                start_time = time.time()

                await stream_logs()
                logging.info("Waiting for process to complete...")
                await process.wait()
                logging.info(f"Process completed with return code: {process.returncode}")

                # Calculate training duration
                training_duration = (time.time() - start_time) / 60  # Convert to minutes

                if process.returncode == 0:
                    logging.info(f"[DONE] Job {job_name} completed successfully.")
                    if channel.readyState == "open":
                        channel.send(f"TRAIN_JOB_END::{job_name}")

                    # Mark model as completed in registry
                    if current_training_model_id:
                        logging.info(f"Marking model {current_training_model_id} as completed...")
                        # Try to extract final validation loss from training logs (simplified)
                        # In production, would parse actual training output
                        metrics = {
                            "training_duration_minutes": training_duration,
                            "epochs_completed": training_config.get('trainer_config', {}).get('max_epochs', 0),
                            "final_val_loss": 0.0  # TODO: Parse from training logs
                        }
                        try:
                            self.registry.mark_completed(current_training_model_id, metrics)
                            logging.info(f"Successfully marked model {current_training_model_id} as completed in registry")
                        except Exception as e:
                            logging.error(f"Failed to mark model {current_training_model_id} as completed: {e}")
                    else:
                        logging.warning("No model ID for this training job, cannot mark as completed")

                    # Send completion status via peer message
                    if job_id and client_id:
                        # Try to get model size if it exists
                        model_path = Path(self.unzipped_dir) / self.output_dir / model_dir_name
                        model_size_mb = 0
                        if model_path.exists():
                            # Check for model files
                            model_files = list(model_path.glob("*.ckpt")) + list(model_path.glob("*.h5"))
                            if model_files:
                                model_size_mb = sum(f.stat().st_size for f in model_files) / (1024 * 1024)

                        await self._send_peer_message(client_id, {
                            "app_message_type": "job_status",
                            "job_id": job_id,
                            "status": "running",
                            "progress": 1.0,
                            "message": f"Training completed: {job_name} (model_id: {self.current_model_id})"
                        })
                else:
                    logging.warning(f"[FAILED] Job {job_name} exited with code {process.returncode}.")
                    if channel.readyState == "open":
                        channel.send(f"TRAIN_JOB_ERROR::{job_name}::{process.returncode}")

                    # Send failure status via peer message
                    if job_id and client_id:
                        await self._send_peer_message(client_id, {
                            "app_message_type": "job_failed",
                            "job_id": job_id,
                            "status": "failed",
                            "error": {
                                "code": "TRAINING_FAILED",
                                "message": f"Training job {job_name} failed with exit code {process.returncode}",
                                "recoverable": False
                            }
                        })

            # All jobs completed - send final completion message
            channel.send("TRAINING_JOBS_DONE")

            # Send job completion message via peer message
            if job_id and client_id:
                await self._send_peer_message(client_id, {
                    "app_message_type": "job_complete",
                    "job_id": job_id,
                    "status": "completed",
                    "result": {
                        "training_duration_minutes": training_duration,
                        "total_jobs": len(training_jobs)
                    },
                    "transfer_method": "webrtc_datachannel",
                    "ready_for_download": True
                })

        except Exception as e:
            # Handle any unexpected errors during training
            logging.error(f"Error during training execution: {e}")
            if job_id and client_id:
                await self._send_peer_message(client_id, {
                    "app_message_type": "job_failed",
                    "job_id": job_id,
                    "status": "failed",
                    "error": {
                        "code": "EXECUTION_ERROR",
                        "message": str(e),
                        "recoverable": False
                    }
                })
            raise

        finally:
            # Update status back to available when done
            if self.current_job:
                await self.update_status("available")
                self.current_job = None


    async def run_track_workflow(self, channel: RTCDataChannel, track_script_path: str):
        """Executes inference workflow on received track package.

        Args:
            channel: RTC data channel for sending progress
            track_script_path: Path to track-script.sh
        """
        # Get job ID and client ID from current_job if available
        job_id = self.current_job.get("job_id") if self.current_job else None
        client_id = self.current_job.get("client_id") if self.current_job else None

        try:
            if not Path(track_script_path).exists():
                logging.error("No track-script.sh found in package")
                channel.send("INFERENCE_ERROR::No track script found")

                # Send error via peer message
                if job_id and client_id:
                    await self._send_peer_message(client_id, {
                        "app_message_type": "job_failed",
                        "job_id": job_id,
                        "status": "failed",
                        "error": {
                            "code": "TRACK_SCRIPT_NOT_FOUND",
                            "message": "No track script found in package",
                            "recoverable": False
                        }
                    })
                return

            logging.info("Starting inference workflow...")
            channel.send("INFERENCE_START")

            # Send starting status via peer message
            if job_id and client_id:
                await self._send_peer_message(client_id, {
                    "app_message_type": "job_status",
                    "job_id": job_id,
                    "status": "starting",
                    "progress": 0.0,
                    "message": "Initializing inference"
                })

            # Make script executable
            os.chmod(track_script_path, os.stat(track_script_path).st_mode | stat.S_IEXEC)

            # Parse and run track commands
            track_commands = self.parse_track_script(track_script_path)
            start_time = time.time()

            for cmd_args in track_commands:
                job_name = "inference"
                channel.send(f"INFERENCE_JOB_START::{job_name}")

                # Run sleap-nn track
                process = await asyncio.create_subprocess_exec(
                    *cmd_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=self.unzipped_dir,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"}
                )

                # Stream logs
                async for line in process.stdout:
                    decoded_line = line.decode().rstrip()
                    if channel.readyState == "open":
                        try:
                            channel.send(f"TRACK_LOG:{decoded_line}")
                        except Exception as e:
                            logging.error(f"Failed to send track log: {e}")

                await process.wait()

                if process.returncode == 0:
                    logging.info("Inference completed successfully")
                    channel.send(f"INFERENCE_JOB_END::{job_name}")
                else:
                    logging.error(f"Inference failed with code {process.returncode}")
                    channel.send(f"INFERENCE_JOB_ERROR::{job_name}::{process.returncode}")

                    # Send failure via peer message
                    if job_id and client_id:
                        await self._send_peer_message(client_id, {
                            "app_message_type": "job_failed",
                            "job_id": job_id,
                            "status": "failed",
                            "error": {
                                "code": "INFERENCE_FAILED",
                                "message": f"Inference failed with exit code {process.returncode}",
                                "recoverable": False
                            }
                        })
                    return

            # Calculate inference duration
            inference_duration = (time.time() - start_time) / 60  # Convert to minutes

            # Find and send predictions file
            predictions_files = list(Path(self.unzipped_dir).glob("*.predictions.slp"))

            if predictions_files:
                predictions_path = predictions_files[0]
                logging.info(f"Sending predictions: {predictions_path}")

                # Send predictions back to client
                await self.send_file(channel, str(predictions_path))

                # Send completion via peer message
                if job_id and client_id:
                    predictions_size_mb = predictions_path.stat().st_size / (1024 * 1024)
                    await self._send_peer_message(client_id, {
                        "app_message_type": "job_complete",
                        "job_id": job_id,
                        "status": "completed",
                        "result": {
                            "predictions_size_mb": predictions_size_mb,
                            "inference_duration_seconds": int((time.time() - start_time)),
                            "predictions_file": predictions_path.name
                        },
                        "transfer_method": "webrtc_datachannel",
                        "ready_for_download": True
                    })
            else:
                logging.warning("No predictions file found")

                # Send warning via peer message
                if job_id and client_id:
                    await self._send_peer_message(client_id, {
                        "app_message_type": "job_complete",
                        "job_id": job_id,
                        "status": "completed",
                        "result": {
                            "inference_duration_seconds": int((time.time() - start_time)),
                            "warning": "No predictions file generated"
                        },
                        "transfer_method": "webrtc_datachannel",
                        "ready_for_download": False
                    })

            channel.send("INFERENCE_JOBS_DONE")

        except Exception as e:
            # Handle any unexpected errors during inference
            logging.error(f"Error during inference execution: {e}")
            if job_id and client_id:
                await self._send_peer_message(client_id, {
                    "app_message_type": "job_failed",
                    "job_id": job_id,
                    "status": "failed",
                    "error": {
                        "code": "EXECUTION_ERROR",
                        "message": str(e),
                        "recoverable": False
                    }
                })
            raise

        finally:
            # Update status back to available when done
            if self.current_job:
                await self.update_status("available")
                self.current_job = None


    async def send_file(self, channel: RTCDataChannel, file_path: str):
        """Sends a file to the client via data channel.

        Args:
            channel: RTC data channel
            file_path: Path to file to send
        """
        if channel.readyState != "open":
            logging.error(f"Data channel not open: {channel.readyState}")
            return

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        # Send file metadata
        channel.send(f"FILE_META::{file_name}:{file_size}:{self.output_dir}")
        logging.info(f"Sending file: {file_name} ({file_size} bytes)")

        # Send file in chunks
        with open(file_path, "rb") as file:
            bytes_sent = 0
            while chunk := file.read(self.chunk_size):
                # Flow control
                while channel.bufferedAmount is not None and channel.bufferedAmount > 16 * 1024 * 1024:
                    await asyncio.sleep(0.1)

                channel.send(chunk)
                bytes_sent += len(chunk)

        # Signal end of file
        channel.send("END_OF_FILE")
        logging.info("File sent successfully")


    def start_zmq_control(self, zmq_address: str = "tcp://127.0.0.1:9000"):
        """Starts a ZMQ control PUB socket to send ZMQ commands to the Trainer.
    
        Args:
            zmq_address: Address of the ZMQ socket to connect to.
        Returns:
            None
        """
        # Initialize socket and event loop.
        logging.info("Starting ZMQ control socket...")
        context = zmq.Context()
        socket = context.socket(zmq.PUB)

        logging.info(f"Connecting to ZMQ address: {zmq_address}")
        socket.bind(zmq_address)

        # set PUB socket for use in other functions
        self.ctrl_socket = socket
        logging.info("ZMQ control socket initialized.")

    async def start_progress_listener(self, channel: RTCDataChannel, zmq_address: str = "tcp://127.0.0.1:9001"):
        """Starts a listener for ZMQ messages and sends progress updates to the client over the data channel.
    
        Args:
            channel: DataChannel object to send progress updates.
            zmq_address: Address of the ZMQ socket to connect to.
        Returns:
            None
        """

        # Initialize socket and event loop.
        logging.info("Starting ZMQ progress listener...")
        context = zmq.Context()
        socket = context.socket(zmq.SUB)

        logging.info(f"Connecting to ZMQ address: {zmq_address}")
        socket.bind(zmq_address) 
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
                    channel.send(f"PROGRESS_REPORT::{msg}")
                    # logging.info("Progress report sent to client.")
                except Exception as e:
                    logging.error(f"Failed to send ZMQ progress: {e}")
                    
            # Polling interval.
            await asyncio.sleep(0.05)

    async def zip_results(self, file_name: str, dir_path: str = None):
        """Zips the contents of the shared_data directory and saves it to a zip file.

        Args:
            file_name: Name of the zip file to be created.
            dir_path: Path to the directory to be zipped.
        Returns:
            None
        """

        logging.info("Zipping results...")
        if Path(dir_path):
            try:
                shutil.make_archive(file_name, 'zip', dir_path)
                self.zipped_file = f"{file_name}.zip"
                logging.info(f"Results zipped to {self.zipped_file}")
            except Exception as e:
                logging.error(f"Error zipping results: {e}")
                return
        else:
            logging.info(f"{dir_path} does not exist!")
            return

    async def unzip_results(self, file_path: str):
        """Unzips the contents of the given file path.

        Args:
            file_path: Path to the zip file to be unzipped.
        Returns:
            None
        """

        logging.info("Unzipping results...")
        if Path(file_path):
            try:
                shutil.unpack_archive(file_path, self.save_dir)
                logging.info(f"Results unzipped from {file_path} to {self.save_dir}")
                self.unzipped_dir = f"{self.save_dir}/{self.original_file_name[:-4]}"  # remove .zip extension
                logging.info(f"Unzipped contents to {self.unzipped_dir}")
            except Exception as e:
                logging.error(f"Error unzipping results: {e}")
                return
        else:
            logging.info(f"{file_path} does not exist!")
            return
        
    async def clean_exit(self):
        """Handles cleanup and shutdown of the worker.

        Args:
            pc: RTCPeerConnection object
            websocket: WebSocket connection object
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

    async def send_worker_messages(self, pc: RTCPeerConnection, channel: RTCDataChannel):
        """Handles typed messages from worker to be sent to client peer.

        Args:
            None
        Returns:
            None
        """

        message = input("Enter message to send (type 'file' to prompt file or type 'quit' to exit): ")
        data = None
        
        if message.lower() == "quit":
            logging.info("Quitting...")
            await pc.close()
            return

        if channel.readyState != "open":
            logging.info(f"Data channel not open. Ready state is: {channel.readyState}")
            return

        if message.lower() == "file":
            logging.info("Prompting file...")
            file_path = input("Enter file path: (or type 'quit' to exit): ")
            if not file_path:
                logging.info("No file path entered.")
                return
            if file_path.lower() == "quit":
                logging.info("Quitting...")
                await pc.close()
                return
            if not Path(file_path):
                logging.info("File does not exist.")
                return
            else:
                logging.info(f"Sending {self.zipped_file} to client...") # trained_labels.v001.slp.training_job.zip
                file_name = os.path.basename(self.zipped_file)
                file_size = os.path.getsize(self.zipped_file)
                file_save_dir = self.output_dir 
                
                # Send metadata first
                channel.send(f"FILE_META::{file_name}:{file_size}:{file_save_dir}")
                
                # Send file in chunks (32 KB)
                with open(file_path, "rb") as file:
                    logging.info(f"File opened: {file_path}")
                    while chunk := file.read(self.CHUNK_SIZE):
                        channel.send(chunk)
                
                channel.send("END_OF_FILE")
                logging.info(f"File sent to client.")
                        
                # Flag data to True to prevent reg msg from being sent
                data = True

        if not data:
            channel.send(message)
            logging.info(f"Message sent to client.")

    async def handle_connection(self, pc: RTCPeerConnection, websocket: ClientConnection, peer_id: str):
        """ Handles incoming messages from the signaling server and processes them accordingly.

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
                msg_type = data.get('type')

                # Receive offer SDP from client (forwarded by signaling server).
                if msg_type == "offer":
                    logging.info('Received offer SDP')

                    # Obtain the sender's peer ID (this is the new target)
                    target_pid = data.get('sender')

                    # SAFEGUARD: Check worker status before accepting connection
                    if self.status in ["busy", "reserved"]:
                        logging.warning(f"Rejecting connection from {target_pid} - worker is {self.status}")

                        # Send error response to client via signaling server
                        await self.websocket.send(json.dumps({
                            'type': 'error',
                            'target': target_pid,
                            'reason': 'worker_busy',
                            'message': f"Worker is currently {self.status}. Please use --room-id and --token to discover available workers.",
                            'current_status': self.status
                        }))

                        logging.info(f"Sent busy rejection to client {target_pid}")
                        continue  # Skip this offer, continue listening

                    # Status is "available" - proceed with connection
                    logging.info(f"Accepting connection from {target_pid} (status: {self.status})")

                    # Update status to "reserved" to prevent race conditions
                    await self.update_status("reserved")
                    logging.info("Worker status updated to 'reserved'")

                    # Set worker peer's remote description to the client's offer based on sdp data
                    await self.pc.setRemoteDescription(RTCSessionDescription(sdp=data.get('sdp'), type='offer'))

                    # Generate worker's answer SDP and set it as the local description
                    await self.pc.setLocalDescription(await self.pc.createAnswer())

                    # Send worker's answer SDP to client so they can set it as their remote description
                    await self.websocket.send(json.dumps({
                        'type': self.pc.localDescription.type, # 'answer'
                        'sender': peer_id, # worker's peer ID
                        'target': target_pid, # client's peer ID
                        'sdp': self.pc.localDescription.sdp # worker's answer SDP
                    }))

                    logging.info(f"Connection accepted - answer sent to {target_pid}")

                    # Reset received_files dictionary
                    self.received_files.clear()

                elif msg_type == 'registered_auth':
                    room_id = data.get('room_id')
                    token = data.get('token')
                    peer_id = data.get('peer_id')

                    # Print session string for direct worker connection (backward compatibility)
                    logging.info("=" * 80)
                    logging.info("Worker authenticated with server")
                    logging.info("=" * 80)
                    logging.info("")
                    logging.info("Session string for DIRECT connection to this worker:")
                    session_string = self.generate_session_string(room_id, token, peer_id)
                    logging.info(f"  {session_string}")
                    logging.info("")
                    logging.info("Room credentials for OTHER workers/clients to join this room:")
                    logging.info(f"  Room ID: {room_id}")
                    logging.info(f"  Token:   {token}")
                    logging.info("")
                    logging.info("Use session string with --session-string for direct connection")
                    logging.info("Use room credentials with --room-id and --token for worker discovery")
                    logging.info("=" * 80)

                # Handle "trickle ICE" for non-local ICE candidates (might be unnecessary)
                elif msg_type == 'candidate':
                    print("Received ICE candidate")
                    candidate = data.get('candidate')
                    await pc.addIceCandidate(candidate)

                elif msg_type == 'error':
                    logging.error(f"Error received from server: {data.get('reason')}")
                    await self.clean_exit()
                    return

                elif msg_type == 'quit': # NOT initiator, received quit request from worker
                    print("Received quit request from Client. Closing connection...")
                    await self.clean_exit()
                    return

                elif msg_type == 'peer_message':  # NEW: Handle peer messages (job requests, etc.)
                    await self.handle_peer_message(data)

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
        logging.info("channel(%s) %s" % (channel.label, "created by remote party & received."))
    
        async def send_worker_file(file_path: str):
            """Handles direct, one-way file transfer from client to be sent to client peer.
        
            Args:
                file_path: Path to the file to be sent.
            Returns:
                None
            """
            
            if channel.readyState != "open":
                logging.info(f"Data channel not open. Ready state is: {channel.readyState}")
                return 

            logging.info(f"Given file path {file_path}")
            if not file_path:
                logging.info("No file path entered.")
                return
            if not Path(file_path):
                logging.info("File does not exist.")
                return
            else: 
                logging.info(f"Sending {self.zipped_file} to client...")

                # Obtain metadata.
                file_name = os.path.basename(self.zipped_file) 
                file_size = os.path.getsize(self.zipped_file)
                file_save_dir = self.output_dir
                
                # Send metadata first.
                channel.send(f"FILE_META::{file_name}:{file_size}:{file_save_dir}")

                # Send file in chunks (32 KB).
                with open(self.zipped_file, "rb") as file:
                    logging.info(f"File opened: {self.zipped_file}")
                    while chunk := file.read(self.chunk_size):
                        while channel.bufferedAmount is not None and channel.bufferedAmount > 16 * 1024 * 1024: # Wait if buffer >16MB 
                            await asyncio.sleep(0.1)

                        channel.send(chunk)

                channel.send("END_OF_FILE")
                logging.info(f"File sent to client.")
                
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
            logging.info(f'{channel.label} channel is open')
        
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

                    # Generate hash of training package for registry tracking
                    if file_path.endswith(".zip"):
                        self.training_job_hash = hashlib.md5(file_data).hexdigest()[:8]
                        logging.info(f"Training package hash: {self.training_job_hash}")

                    # Unzip results if needed.
                    if file_path.endswith(".zip"):
                        await self.unzip_results(file_path)
                        logging.info(f"Unzipped results from {file_path}")

                    # Reset dictionary for next file
                    self.received_files.clear()

                    # Route to appropriate workflow based on package type
                    if self.package_type == "track":
                        # Inference workflow
                        track_script_path = os.path.join(self.unzipped_dir, "track-script.sh")

                        if Path(track_script_path).exists():
                            await self.run_track_workflow(channel, track_script_path)
                        else:
                            logging.error(f"No track script found in {self.unzipped_dir}. Skipping inference.")

                    else:
                        # Training workflow (default)
                        train_script_path = os.path.join(self.unzipped_dir, "train-script.sh")

                        if Path(train_script_path):
                            try:
                                logging.info("self.gui is: " + str(self.gui))
                                progress_listener_task = None
                                # if self.gui:
                                # Start ZMQ progress listener.
                                # Don't need to send ZMQ progress reports if User just using CLI sleap-rtc.
                                # (Will print sleap-nn train logs directly to terminal instead.)
                                if self.gui:
                                    progress_listener_task = asyncio.create_task(self.start_progress_listener(channel))
                                    logging.info(f'{channel.label} progress listener started')

                                    # Start ZMQ control socket.
                                    self.start_zmq_control()
                                    logging.info(f'{channel.label} ZMQ control socket started')

                                    # Give SUB socket time to connect.
                                    await asyncio.sleep(1)

                                logging.info(f"Running training script: {train_script_path}")

                                # Make the script executable
                                os.chmod(train_script_path, os.stat(train_script_path).st_mode | stat.S_IEXEC)

                                # Run the training script in the save directory
                                await self.run_all_training_jobs(channel, train_script_path=train_script_path)

                                # Finish training.
                                logging.info("Training completed successfully.")
                                if progress_listener_task:
                                    progress_listener_task.cancel()

                                # Zip the results.
                                logging.info("Zipping results...")
                                zipped_file_name = f"trained_{self.original_file_name[:-4]}"
                                await self.zip_results(zipped_file_name, f"{self.unzipped_dir}/{self.output_dir}") # normally, "./labels_dir/models"
                                # Zipped file saved to current directory.

                                # Send the zipped file to the client.
                                logging.info(f"Sending zipped file to client: {zipped_file_name}")
                                await send_worker_file(zipped_file_name)

                            except subprocess.CalledProcessError as e:
                                logging.error(f"Training failed with error:\n{e.stderr}")
                                await self.clean_exit()
                        else:
                            logging.info(f"No training script found in {self.save_dir}. Skipping training.")

                elif "OUTPUT_DIR::" in message:
                    logging.info(f"Output directory received: {message}")
                    _, self.output_dir = message.split("OUTPUT_DIR::", 1) # normally, "models"

                elif "FILE_META::" in message:
                    logging.info(f"File metadata received: {message}")
                    _, meta = message.split("FILE_META::", 1)
                    file_name, file_size, gui = meta.split(":")

                    logging.info("self.gui set to: " + gui)

                    # Convert string to boolean
                    self.gui = gui.lower() == 'true'
                    self.received_files[file_name] = bytearray()
                    logging.info(f"File name received: {file_name}, of size {file_size}")
                    logging.info(f"self.gui converted to boolean: {self.gui}")
                elif "ZMQ_CTRL::" in message:
                    logging.info(f"ZMQ LossViewer control message received: {message}")
                    _, zmq_msg = message.split("ZMQ_CTRL::", 1)

                    # Send LossViewer's control message to the Trainer client (listening on control port 9000).
                    # Remember, the Trainer printed: "ZMQ controller subscribed to: tcp://127.0.0.1:9000", so publish there.
                    if self.ctrl_socket != None:
                        self.ctrl_socket.send_string(zmq_msg)
                        logging.info(f"Sent control message to Trainer: {zmq_msg}")
                    else:
                        logging.error(f"ZMQ control socket not initialized {self.ctrl_socket}. Cannot send control message.")

                elif "REGISTRY_QUERY_LIST" in message:
                    logging.info("Registry query list request received")
                    # Mark this as a query-only connection (no training/inference)
                    self.is_query_only_connection = True

                    # Parse optional filters from message
                    try:
                        _, filters_json = message.split("REGISTRY_QUERY_LIST::", 1)
                        filters = json.loads(filters_json) if filters_json else {}
                    except (ValueError, json.JSONDecodeError):
                        filters = {}

                    # Query registry with filters
                    models = self.registry.list(filters=filters)
                    logging.info(f"Returning {len(models)} models to client")

                    # Send response
                    response = {
                        "type": "registry_list",
                        "models": models,
                        "count": len(models)
                    }
                    channel.send(f"REGISTRY_RESPONSE_LIST::{json.dumps(response)}")

                elif "REGISTRY_QUERY_INFO" in message:
                    logging.info("Registry query info request received")
                    # Mark this as a query-only connection (no training/inference)
                    self.is_query_only_connection = True

                    # Extract model ID from message
                    try:
                        _, model_id = message.split("REGISTRY_QUERY_INFO::", 1)
                        model_id = model_id.strip()
                    except ValueError:
                        logging.error("Invalid REGISTRY_QUERY_INFO message format")
                        channel.send("REGISTRY_RESPONSE_ERROR::Invalid message format")
                        return

                    # Get model info from registry
                    model_info = self.registry.get(model_id)

                    if model_info:
                        logging.info(f"Returning info for model {model_id}")
                        response = {
                            "type": "registry_info",
                            "model": model_info
                        }
                        channel.send(f"REGISTRY_RESPONSE_INFO::{json.dumps(response)}")
                    else:
                        logging.warning(f"Model {model_id} not found in registry")
                        response = {
                            "type": "registry_error",
                            "error": f"Model '{model_id}' not found"
                        }
                        channel.send(f"REGISTRY_RESPONSE_ERROR::{json.dumps(response)}")

                else:
                    logging.info(f"Client sent: {message}")
                    await self.send_worker_messages(channel, self.pc, self.websocket)

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
        logging.info(f"ICE connection state is now {self.pc.iceConnectionState}")

        # Check the ICE connection state and handle accordingly.
        if self.pc.iceConnectionState == "failed":
            logging.error('ICE connection failed')

            # Mark current training as interrupted if active
            if self.current_model_id and self.status == "busy":
                logging.info(f"Marking model {self.current_model_id} as interrupted due to connection failure")
                # Find best checkpoint in model directory
                model_info = self.registry.get(self.current_model_id)
                if model_info:
                    checkpoint_path = model_info.get('checkpoint_path', '')
                    # Estimate current epoch (would need actual tracking in production)
                    epoch = 0  # TODO: Track actual epoch during training
                    self.registry.mark_interrupted(self.current_model_id, checkpoint_path, epoch)
                    logging.info(f"Model {self.current_model_id} marked as interrupted and can be resumed")

            await self.clean_exit()
            return
        elif self.pc.iceConnectionState in ["failed", "disconnected", "closed"]:
            # Check if this is a query-only connection (registry queries only)
            if self.is_query_only_connection:
                logging.info(f"Query-only connection closed. Returning to available state.")
                # Reset the flag for next connection
                self.is_query_only_connection = False
                # Don't call pc.close() - connection already closed by client
                # Calling close() on an already-closed connection corrupts its state
                # Status should return to available
                if self.status == "reserved":
                    self.status = "available"
                    logging.info("Worker status returned to 'available'")
                return

            logging.info(f"ICE connection {self.pc.iceConnectionState}. Waiting for reconnect...")

            # Mark training as interrupted before waiting
            if self.current_model_id and self.status == "busy":
                logging.info(f"Marking model {self.current_model_id} as interrupted due to disconnection")
                model_info = self.registry.get(self.current_model_id)
                if model_info:
                    checkpoint_path = model_info.get('checkpoint_path', '')
                    epoch = 0  # TODO: Track actual epoch
                    self.registry.mark_interrupted(self.current_model_id, checkpoint_path, epoch)

            # Wait up to 90 seconds.
            for i in range(90):
                await asyncio.sleep(1)
                if self.pc.iceConnectionState in ["connected", "completed"]:
                    logging.info("ICE reconnected!")
                    return

            logging.error("Reconnection timed out. Closing connection.")
            await self.clean_exit()

        elif self.pc.iceConnectionState == "checking":
            logging.info("ICE connection is checking...")
            

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
            sign_in_json = self.request_anonymous_signin()
            id_token = sign_in_json['id_token']
            peer_id = sign_in_json['username']
            self.cognito_username = peer_id
            
            if not id_token:
                logging.error("Failed to sign in anonymously. No ID token given. Exiting...")
                return
            
            logging.info(f"Anonymous sign-in successful. ID token: {id_token}")

            # Create the room or use existing room credentials
            if room_id and token:
                # Join existing room
                logging.info(f"Joining existing room with ID: {room_id}")
                room_json = {
                    'room_id': room_id,
                    'token': token
                }
            else:
                # Create the room and get the room ID, token, and cognito username.
                room_json = self.request_create_room(id_token)

                if not room_json or 'room_id' not in room_json or 'token' not in room_json:
                    logging.error("Failed to create room or get room ID/token. Exiting...")
                    return
                logging.info(f"Room created with ID: {room_json['room_id']} and token: {room_json['token']}")

            # Establish a WebSocket connection to the signaling server.
            logging.info(f"Connecting to signaling server at {DNS}...")
            async with websockets.connect(DNS) as websocket:

                # Set the WebSocket connection for the worker.
                self.websocket = websocket

                # Register the worker with the server (with v2.0 metadata).
                logging.info(f"Registering {peer_id} with signaling server...")

                # Try to get SLEAP version
                try:
                    import sleap
                    sleap_version = sleap.__version__
                except (ImportError, AttributeError):
                    sleap_version = "unknown"

                await self.websocket.send(json.dumps({
                    'type': 'register',
                    'peer_id': peer_id, # identify a peer uniquely in the room (Zoom username)
                    'room_id': room_json['room_id'], # from backend API call for room identification (Zoom meeting ID)
                    'token': room_json['token'], # from backend API call for room joining (Zoom meeting password)
                    'id_token': id_token, # from anon. Cognito sign-in (Prevent peer spoofing, even anonymously)
                    'role': 'worker',  # NEW: worker role for discovery
                    'metadata': {  # NEW: worker capabilities and status
                        'tags': ['sleap-rtc', 'training-worker', 'inference-worker'],
                        'properties': {
                            'gpu_memory_mb': self.gpu_memory_mb,
                            'gpu_model': self.gpu_model,
                            'sleap_version': sleap_version,
                            'cuda_version': self.cuda_version,
                            'hostname': socket.gethostname(),
                            'status': self.status,
                            'max_concurrent_jobs': self.max_concurrent_jobs,
                            'supported_models': self.supported_models,
                            'supported_job_types': self.supported_job_types
                        }
                    }
                }))
                logging.info(f"{peer_id} sent to signaling server for registration with metadata!")
                logging.info(f"Worker capabilities: GPU={self.gpu_model}, Memory={self.gpu_memory_mb}MB, CUDA={self.cuda_version}")

                # Handle incoming messages from server (e.g. answers).
                await self.handle_connection(self.pc, self.websocket, peer_id)
                logging.info(f"{peer_id} connected with client!")
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
                port_number=8080
            )
        )
    except KeyboardInterrupt:
        logging.info("Worker interrupted by user. Shutting down...")
    finally:
        logging.info("Worker exiting...")

