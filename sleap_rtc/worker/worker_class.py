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
from typing import Optional

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
from sleap_rtc.worker.crdt_state import RoomStateCRDT
from sleap_rtc.worker.admin_controller import AdminController
from sleap_rtc.worker.mesh_coordinator import MeshCoordinator
from sleap_rtc.worker.mesh_messages import (
    MessageType,
    deserialize_message,
    serialize_message,
    validate_message,
)

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

        # Mesh networking attributes (Phase 3-6)
        self.cognito_username = None  # Set during run_worker
        self.peer_id = None  # Our peer_id, set from signaling server
        self.mesh_initialized = False
        self.signaling_dns = None  # Signaling server DNS for mesh connections
        self.admin_peer_id = None  # Current admin's peer_id
        self.mesh_coordinator = None  # MeshCoordinator instance
        self.admin_controller = None  # AdminController instance
        self.room_state_crdt = None  # RoomStateCRDT instance
        self.worker_connections = {}  # peer_id -> RTCPeerConnection for workers
        self.client_connections = {}  # peer_id -> RTCPeerConnection for clients
        self.data_channels = {}  # peer_id -> RTCDataChannel for mesh messaging

        # Heartbeat attributes (Phase 4)
        self.heartbeat_sequence = 0  # Sequence number for heartbeat messages
        self.heartbeat_task = None  # Background task for sending heartbeats
        self.heartbeat_check_task = None  # Background task for checking heartbeat timeouts
        self.heartbeat_interval = 5.0  # Seconds between heartbeats
        self.heartbeat_timeout = 15.0  # Seconds before peer is considered dead (3x interval)
        self.last_heartbeat = {}  # peer_id -> last heartbeat timestamp

        # Network partition recovery attributes (Phase 5)
        self.partition_check_task = None  # Background task for partition detection
        self.partition_check_interval = 30.0  # Seconds between partition checks
        self.is_partitioned = False  # Whether we're currently in a network partition
        self.partition_detected_at = None  # Timestamp when partition was detected

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

    # ===== Connection Registry Methods (Phase 2) =====

    def _get_connection_type(self, peer_id: str) -> str:
        """Get the type of connection for a peer_id.

        Returns:
            "client", "worker", "admin", or "unknown"
        """
        if peer_id in self.worker_connections:
            if peer_id == self.admin_peer_id:
                return "admin"
            return "worker"
        elif peer_id in self.client_connections:
            return "client"
        return "unknown"

    async def _handle_admin_disconnect(self, peer_id: str):
        """Handle admin worker disconnect - trigger re-election.

        Args:
            peer_id: Admin worker's peer_id
        """
        logging.warning(f"Admin connection closed: {peer_id}")

        # Remove from worker connections
        if peer_id in self.worker_connections:
            del self.worker_connections[peer_id]

        # Track if we were admin before re-election
        was_admin = self.admin_controller.is_admin if self.admin_controller else False

        # Trigger re-election via AdminController (if available)
        if self.admin_controller:
            await self.admin_controller.handle_admin_departure(peer_id)

            # Update admin_peer_id from election result
            self.admin_peer_id = self.admin_controller.admin_peer_id
            logging.info(f"New admin after re-election: {self.admin_peer_id}")

            # Check if our admin status changed after re-election
            is_now_admin = self.admin_controller.is_admin

            # Handle admin promotion (non-admin -> admin)
            if not was_admin and is_now_admin:
                logging.info("Promoted to admin after re-election")
                if self.mesh_coordinator:
                    await self.mesh_coordinator.on_admin_promotion()

            # Handle admin demotion (admin -> non-admin) - shouldn't happen in this flow
            elif was_admin and not is_now_admin:
                logging.info("Demoted from admin after re-election")
                if self.mesh_coordinator:
                    await self.mesh_coordinator.on_admin_demotion()
        else:
            logging.warning("AdminController not initialized, cannot trigger re-election")
            self.admin_peer_id = None  # Only reset if no controller to handle re-election

    async def _handle_worker_disconnect(self, peer_id: str):
        """Handle non-admin worker disconnect.

        Args:
            peer_id: Worker's peer_id
        """
        logging.info(f"Worker peer disconnected: {peer_id}")

        # Remove from worker connections
        if peer_id in self.worker_connections:
            del self.worker_connections[peer_id]

        # Track if we were admin before handling departure
        was_admin = self.admin_controller.is_admin if self.admin_controller else False

        # Update CRDT and potentially trigger re-election
        if self.admin_controller:
            await self.admin_controller.handle_worker_departure(peer_id)

            # Sync admin_peer_id with controller (may have changed if departed was admin)
            self.admin_peer_id = self.admin_controller.admin_peer_id

            # Check if our admin status changed (in case departed was misidentified as non-admin)
            is_now_admin = self.admin_controller.is_admin
            if not was_admin and is_now_admin:
                logging.info("Promoted to admin after worker departure")
                if self.mesh_coordinator:
                    await self.mesh_coordinator.on_admin_promotion()
        else:
            logging.warning("AdminController not initialized, cannot handle worker departure")

    async def _handle_client_disconnect(self, peer_id: str):
        """Handle client disconnect - existing behavior.

        Args:
            peer_id: Client's peer_id
        """
        logging.info(f"Client disconnected: {peer_id}")

        # Remove from client connections
        if peer_id in self.client_connections:
            del self.client_connections[peer_id]

        # TODO: Add existing cleanup logic for client jobs
        # (update status, clean up files, etc.)
        # This will be integrated when we refactor existing client connection code

    async def on_mesh_iceconnectionstatechange(self, peer_id: str, pc: RTCPeerConnection):
        """Handle ICE connection state changes for mesh connections.

        This is separate from on_iceconnectionstatechange which handles
        the main client connection.

        Args:
            peer_id: The peer_id of the mesh connection
            pc: The RTCPeerConnection for this mesh connection
        """
        ice_state = pc.iceConnectionState
        logging.info(f"Mesh ICE state with {peer_id}: {ice_state}")

        if self.shutting_down:
            logging.info("Worker shutting down - ignoring mesh ICE state change")
            return

        # Success states
        if ice_state in ["connected", "completed"]:
            logging.info(f"Mesh connection established with {peer_id}")
            return

        # Negotiation states
        if ice_state in ["checking", "new"]:
            logging.debug(f"Mesh ICE {ice_state} with {peer_id}")
            return

        # Failure states - handle disconnect
        if ice_state in ["closed", "failed", "disconnected"]:
            logging.warning(f"Mesh connection {ice_state} with {peer_id}")

            # Determine connection type and handle appropriately
            conn_type = self._get_connection_type(peer_id)

            if conn_type == "admin":
                await self._handle_admin_disconnect(peer_id)
            elif conn_type == "worker":
                await self._handle_worker_disconnect(peer_id)
            else:
                logging.debug(f"Unknown peer {peer_id} disconnected")

            # Clean up data channel
            if peer_id in self.data_channels:
                del self.data_channels[peer_id]

            # Clean up heartbeat tracking
            if peer_id in self.last_heartbeat:
                del self.last_heartbeat[peer_id]

    # ===== End Connection Registry Methods =====

    # ===== Mesh Message Handling (Phase 4) =====

    async def _handle_mesh_message(self, message_str: str, from_peer_id: str):
        """Handle incoming mesh protocol message.

        Routes message to appropriate handler based on type.

        Args:
            message_str: JSON string message
            from_peer_id: Sender's peer_id
        """
        try:
            # Deserialize message
            message = deserialize_message(message_str)

            # Validate message
            if not validate_message(message):
                logging.warning(f"Invalid mesh message from {from_peer_id}")
                return

            # Route by message type
            msg_type = message.type

            if msg_type == MessageType.STATUS_UPDATE.value:
                await self._handle_status_update(message, from_peer_id)
            elif msg_type == MessageType.STATE_BROADCAST.value:
                await self._handle_state_broadcast(message, from_peer_id)
            elif msg_type == MessageType.HEARTBEAT.value:
                await self._handle_heartbeat(message, from_peer_id)
            elif msg_type == MessageType.HEARTBEAT_RESPONSE.value:
                await self._handle_heartbeat_response(message, from_peer_id)
            elif msg_type == MessageType.QUERY_WORKERS.value:
                await self._handle_query_workers(message, from_peer_id)
            elif msg_type == MessageType.WORKER_LIST.value:
                await self._handle_worker_list(message, from_peer_id)
            elif msg_type == MessageType.PEER_JOINED.value:
                await self._handle_peer_joined_notification(message, from_peer_id)
            elif msg_type == MessageType.PEER_LEFT.value:
                await self._handle_peer_left_notification(message, from_peer_id)
            else:
                logging.warning(f"Unknown mesh message type: {msg_type}")

        except Exception as e:
            logging.error(f"Error handling mesh message from {from_peer_id}: {e}")
            logging.exception(e)

    async def _handle_status_update(self, message, from_peer_id: str):
        """Handle status update from worker (admin only).

        Args:
            message: StatusUpdateMessage
            from_peer_id: Sender's peer_id
        """
        if not self.admin_controller or not self.admin_controller.is_admin:
            logging.warning(f"Received status update but not admin: {from_peer_id}")
            return

        logging.info(
            f"Received status update from {from_peer_id}: {message.status}"
        )

        # Update CRDT with new status
        if self.room_state_crdt:
            worker = self.room_state_crdt.get_worker(from_peer_id)
            if worker:
                # Update worker status in CRDT
                self.room_state_crdt.update_worker_status(
                    from_peer_id, message.status, message.current_job
                )

                # Broadcast updated state to all workers
                await self._broadcast_state()
            else:
                logging.warning(f"Worker not found in CRDT: {from_peer_id}")

    async def _handle_state_broadcast(self, message, from_peer_id: str):
        """Handle state broadcast from admin (non-admin workers).

        Args:
            message: StateBroadcastMessage
            from_peer_id: Admin's peer_id
        """
        if from_peer_id != self.admin_peer_id:
            logging.warning(
                f"Received state broadcast from non-admin: {from_peer_id}"
            )
            return

        logging.debug(f"Received state broadcast from admin (version {message.version})")

        # Merge CRDT snapshot
        if self.room_state_crdt and message.crdt_snapshot:
            try:
                self.room_state_crdt.merge(message.crdt_snapshot)
                logging.debug("CRDT state merged successfully")
            except Exception as e:
                logging.error(f"Failed to merge CRDT state: {e}")

    async def _handle_heartbeat(self, message, from_peer_id: str):
        """Handle heartbeat from peer.

        Args:
            message: HeartbeatMessage
            from_peer_id: Sender's peer_id
        """
        # Update last heartbeat timestamp
        self.last_heartbeat[from_peer_id] = message.timestamp
        logging.debug(f"Heartbeat from {from_peer_id} (seq {message.sequence})")

        # TODO: Send heartbeat response (optional, for RTT measurement)

    async def _handle_heartbeat_response(self, message, from_peer_id: str):
        """Handle heartbeat response from peer.

        Args:
            message: HeartbeatResponseMessage
            from_peer_id: Sender's peer_id
        """
        # Calculate RTT (optional)
        if message.original_timestamp:
            rtt = message.timestamp - message.original_timestamp
            logging.debug(f"RTT to {from_peer_id}: {rtt*1000:.2f}ms")

    async def _handle_query_workers(self, message, from_peer_id: str):
        """Handle worker query from client (admin only).

        Args:
            message: QueryWorkersMessage
            from_peer_id: Client's peer_id
        """
        if not self.admin_controller or not self.admin_controller.is_admin:
            logging.warning(f"Received query_workers but not admin: {from_peer_id}")
            return

        logging.info(f"Client {from_peer_id} querying workers with filters: {message.filters}")

        # Use AdminController to handle query
        if self.admin_controller:
            # This will be implemented in AdminController
            result = await self.admin_controller.handle_client_query(message.filters)
            # TODO: Send response back to client
            logging.debug(f"Query result: {result}")

    async def _handle_worker_list(self, message, from_peer_id: str):
        """Handle worker list response from admin (client side).

        Args:
            message: WorkerListMessage
            from_peer_id: Admin's peer_id
        """
        logging.info(f"Received worker list from admin: {len(message.workers)} workers")
        # This would be handled by client code
        # Workers don't typically need to process this

    async def _handle_peer_joined_notification(self, message, from_peer_id: str):
        """Handle notification that peer joined (from admin).

        Args:
            message: PeerJoinedMessage
            from_peer_id: Admin's peer_id
        """
        logging.info(f"Peer joined: {message.peer_id}")

        # Add to CRDT if we have it
        if self.room_state_crdt and message.metadata:
            self.room_state_crdt.add_worker(message.peer_id, message.metadata)

        # Optionally: establish connection to new peer
        # (handled by MeshCoordinator in Phase 3)

    async def _handle_peer_left_notification(self, message, from_peer_id: str):
        """Handle notification that peer left (from admin).

        Args:
            message: PeerLeftMessage
            from_peer_id: Admin's peer_id
        """
        logging.info(f"Peer left: {message.peer_id}")

        # Remove from CRDT
        if self.room_state_crdt:
            self.room_state_crdt.remove_worker(message.peer_id)

    async def _broadcast_state(self):
        """Broadcast CRDT state to all workers (admin only).

        Called when admin updates CRDT and needs to sync with workers.
        """
        if not self.admin_controller or not self.admin_controller.is_admin:
            logging.warning("Cannot broadcast state: not admin")
            return

        if not self.room_state_crdt:
            logging.warning("Cannot broadcast state: no CRDT")
            return

        logging.debug("Broadcasting state to all workers")

        # Use AdminController to broadcast
        await self.admin_controller.broadcast_state_update()

    def _send_mesh_message_to_peer(self, peer_id: str, message):
        """Send mesh message to specific peer via data channel.

        Args:
            peer_id: Target peer_id
            message: Message object to send
        """
        # Check if we have data channel for peer
        if peer_id not in self.data_channels:
            logging.warning(f"Cannot send message to {peer_id}: no data channel")
            return

        data_channel = self.data_channels[peer_id]

        # Check data channel state
        if data_channel.readyState != "open":
            logging.warning(
                f"Cannot send message to {peer_id}: channel state is {data_channel.readyState}"
            )
            return

        try:
            # Serialize and send message
            message_str = serialize_message(message)
            data_channel.send(message_str)
            logging.debug(f"Sent {message.type} to {peer_id}")
        except Exception as e:
            logging.error(f"Failed to send message to {peer_id}: {e}")

    async def send_status_update(self, status: str, current_job=None):
        """Send status update to admin.

        If partitioned, queues update for later (read-only mode).

        Args:
            status: New status (available, busy, reserved, maintenance)
            current_job: Current job info (optional)
        """
        # Phase 5: Read-only mode during partition
        if self.is_partitioned:
            logging.warning(
                f"Partitioned: Queueing status update ({status}) for later"
            )
            self.pending_status_updates.append({
                "status": status,
                "current_job": current_job
            })
            return

        if not self.admin_peer_id:
            logging.debug("No admin to send status update to")
            return

        from sleap_rtc.worker.mesh_messages import create_status_update

        message = create_status_update(self.peer_id, status, current_job)
        self._send_mesh_message_to_peer(self.admin_peer_id, message)
        logging.info(f"Sent status update to admin: {status}")

    # ===== End Mesh Message Handling =====

    # ===== Heartbeat Mechanism (Phase 4) =====

    async def _heartbeat_loop(self):
        """Send periodic heartbeats to all connected peers.

        Runs in background, sends heartbeat every heartbeat_interval seconds.
        """
        logging.info(f"Starting heartbeat loop (interval: {self.heartbeat_interval}s)")

        while not self.shutting_down:
            try:
                # Send heartbeat to all connected workers
                for peer_id in list(self.worker_connections.keys()):
                    await self._send_heartbeat(peer_id)

                # Wait for next heartbeat
                await asyncio.sleep(self.heartbeat_interval)

            except asyncio.CancelledError:
                logging.info("Heartbeat loop cancelled")
                break
            except Exception as e:
                logging.error(f"Error in heartbeat loop: {e}")
                await asyncio.sleep(self.heartbeat_interval)

    async def _send_heartbeat(self, peer_id: str):
        """Send heartbeat to specific peer.

        Args:
            peer_id: Target peer_id
        """
        from sleap_rtc.worker.mesh_messages import create_heartbeat

        self.heartbeat_sequence += 1
        message = create_heartbeat(self.peer_id, self.heartbeat_sequence)
        self._send_mesh_message_to_peer(peer_id, message)
        logging.debug(f"Sent heartbeat to {peer_id} (seq {self.heartbeat_sequence})")

    async def _heartbeat_check_loop(self):
        """Check for missing heartbeats and detect failed peers.

        Runs in background, checks every heartbeat_interval seconds.
        """
        logging.info(f"Starting heartbeat check loop (timeout: {self.heartbeat_timeout}s)")

        while not self.shutting_down:
            try:
                await self._check_heartbeat_timeouts()
                await asyncio.sleep(self.heartbeat_interval)

            except asyncio.CancelledError:
                logging.info("Heartbeat check loop cancelled")
                break
            except Exception as e:
                logging.error(f"Error in heartbeat check loop: {e}")
                await asyncio.sleep(self.heartbeat_interval)

    async def _check_heartbeat_timeouts(self):
        """Check if any peers have timed out (3 missed heartbeats)."""
        import time

        current_time = time.time()

        # Check each peer's last heartbeat
        for peer_id in list(self.last_heartbeat.keys()):
            last_beat = self.last_heartbeat.get(peer_id, 0)
            time_since_beat = current_time - last_beat

            # Timeout if no heartbeat for heartbeat_timeout seconds
            if time_since_beat > self.heartbeat_timeout:
                logging.warning(
                    f"Heartbeat timeout for {peer_id}: {time_since_beat:.1f}s since last beat"
                )

                # Determine connection type and handle appropriately
                connection_type = self._get_connection_type(peer_id)

                if connection_type == "admin":
                    logging.warning(f"Admin heartbeat timeout: {peer_id}")
                    await self._handle_admin_disconnect(peer_id)
                elif connection_type == "worker":
                    logging.warning(f"Worker heartbeat timeout: {peer_id}")
                    await self._handle_worker_disconnect(peer_id)

                # Remove from heartbeat tracking
                if peer_id in self.last_heartbeat:
                    del self.last_heartbeat[peer_id]

    def start_heartbeat_tasks(self):
        """Start background heartbeat tasks.

        Starts both heartbeat sending and heartbeat checking tasks.
        """
        if self.heartbeat_task is None or self.heartbeat_task.done():
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            logging.info("Heartbeat sending task started")

        if self.heartbeat_check_task is None or self.heartbeat_check_task.done():
            self.heartbeat_check_task = asyncio.create_task(self._heartbeat_check_loop())
            logging.info("Heartbeat checking task started")

    async def stop_heartbeat_tasks(self):
        """Stop background heartbeat tasks."""
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass
            logging.info("Heartbeat sending task stopped")

        if self.heartbeat_check_task:
            self.heartbeat_check_task.cancel()
            try:
                await self.heartbeat_check_task
            except asyncio.CancelledError:
                pass
            logging.info("Heartbeat checking task stopped")

    # ===== End Heartbeat Mechanism =====

    # ===== Partition Detection and Recovery (Phase 5) =====

    def _detect_partition(self) -> bool:
        """Detect if worker is partitioned from mesh.

        Returns True if worker has lost connection to admin AND
        connection to majority of workers.

        Returns:
            True if partitioned, False otherwise
        """
        # Can't be partitioned if no mesh initialized
        if not self.room_state_crdt:
            return False

        # Get total workers from CRDT
        all_workers = self.room_state_crdt.get_all_workers()
        total_workers = len(all_workers)

        if total_workers <= 1:
            # Solo worker or just us - not partitioned
            return False

        # Count connected workers (excluding self)
        connected_count = len([
            peer_id for peer_id in self.worker_connections.keys()
            if peer_id != self.peer_id
        ])

        # Check admin connection
        has_admin = (
            self.admin_peer_id
            and self.admin_peer_id in self.worker_connections
        )

        # Calculate connectivity ratio
        # Subtract 1 from total to exclude self
        other_workers = total_workers - 1
        connectivity = connected_count / other_workers if other_workers > 0 else 1.0

        # Partitioned if:
        # 1. Lost admin connection AND
        # 2. Connected to less than 50% of workers
        is_partitioned = not has_admin and connectivity < 0.5

        if is_partitioned:
            logging.warning(
                f"Partition detected: admin={'present' if has_admin else 'lost'}, "
                f"connectivity={connectivity:.1%} ({connected_count}/{other_workers})"
            )

        return is_partitioned

    async def _partition_check_loop(self):
        """Periodically check for network partition.

        Runs in background, checks every partition_check_interval seconds.
        """
        logging.info(
            f"Starting partition check loop (interval: {self.partition_check_interval}s)"
        )

        while not self.shutting_down:
            try:
                await self._check_partition_status()
                await asyncio.sleep(self.partition_check_interval)

            except asyncio.CancelledError:
                logging.info("Partition check loop cancelled")
                break
            except Exception as e:
                logging.error(f"Error in partition check loop: {e}")
                await asyncio.sleep(self.partition_check_interval)

    async def _check_partition_status(self):
        """Check current partition status and handle transitions."""
        was_partitioned = self.is_partitioned
        is_now_partitioned = self._detect_partition()

        # Partition state changed
        if was_partitioned != is_now_partitioned:
            if is_now_partitioned:
                # Entered partition
                await self._on_partition_detected()
            else:
                # Partition healed
                await self._on_partition_recovered()

        self.is_partitioned = is_now_partitioned

    async def _on_partition_detected(self):
        """Handle detection of network partition.

        Enters read-only mode and starts retry attempts.
        """
        import time

        self.partition_detected_at = time.time()
        logging.warning(
            "PARTITION DETECTED: Entering read-only mode. "
            "Will use stale state until reconnection."
        )

        # Log partition details
        self._log_connection_health()

        # Start retry attempts for lost connections
        await self._start_reconnection_attempts()

    async def _on_partition_recovered(self):
        """Handle partition recovery.

        Requests state reconciliation and resumes normal operation.
        """
        import time

        partition_duration = (
            time.time() - self.partition_detected_at
            if self.partition_detected_at
            else 0
        )

        logging.info(
            f"PARTITION RECOVERED after {partition_duration:.1f}s. "
            "Reconciling state..."
        )

        # Request CRDT snapshot from admin
        await self._request_crdt_snapshot()

        # Flush pending status updates
        await self._flush_pending_updates()

        # Reset partition state
        self.partition_detected_at = None

        logging.info("State reconciliation complete. Resuming normal operation.")

    async def _request_crdt_snapshot(self):
        """Request full CRDT snapshot from admin for state reconciliation."""
        if not self.admin_peer_id or self.admin_peer_id not in self.worker_connections:
            logging.warning("Cannot request CRDT snapshot: admin not connected")
            return

        # TODO: Implement snapshot request message type
        # For now, admin will send state_broadcast which we'll merge
        logging.debug(f"Requesting CRDT snapshot from admin: {self.admin_peer_id}")

    async def _flush_pending_updates(self):
        """Send queued status updates after partition recovery."""
        if not self.pending_status_updates:
            return

        logging.info(
            f"Flushing {len(self.pending_status_updates)} pending status updates"
        )

        for update in self.pending_status_updates:
            try:
                await self.send_status_update(
                    update.get("status"), update.get("current_job")
                )
            except Exception as e:
                logging.error(f"Failed to send pending update: {e}")

        self.pending_status_updates.clear()

    async def _start_reconnection_attempts(self):
        """Start reconnection attempts for lost connections."""
        # Get list of workers we should be connected to
        if not self.room_state_crdt:
            return

        all_workers = self.room_state_crdt.get_all_workers()

        for peer_id in all_workers.keys():
            if peer_id == self.peer_id:
                continue  # Skip self

            # Check if we're missing connection
            if peer_id not in self.worker_connections:
                logging.info(f"Starting reconnection attempts for {peer_id}")
                # Start retry task
                task = asyncio.create_task(
                    self._retry_connection_with_backoff(peer_id)
                )
                self.retry_tasks[peer_id] = task

    async def _retry_connection_with_backoff(
        self, peer_id: str, max_attempts: int = 10
    ):
        """Retry connection to peer with exponential backoff.

        Args:
            peer_id: Peer to reconnect to
            max_attempts: Maximum retry attempts
        """
        for attempt in range(max_attempts):
            # Check if already reconnected
            if peer_id in self.worker_connections:
                logging.info(f"Already reconnected to {peer_id}")
                return True

            # Check if shutting down
            if self.shutting_down:
                return False

            # Calculate delay with exponential backoff (cap at 32s)
            delay = min(2**attempt, 32)

            logging.info(
                f"Reconnection attempt {attempt + 1}/{max_attempts} "
                f"for {peer_id} in {delay}s"
            )

            await asyncio.sleep(delay)

            # Attempt reconnection
            try:
                success = await self._attempt_reconnection(peer_id)
                if success:
                    logging.info(f"Successfully reconnected to {peer_id}")
                    # Remove from retry tasks
                    if peer_id in self.retry_tasks:
                        del self.retry_tasks[peer_id]
                    return True
            except Exception as e:
                logging.error(f"Reconnection attempt failed for {peer_id}: {e}")

        logging.error(
            f"Failed to reconnect to {peer_id} after {max_attempts} attempts"
        )
        # Remove from retry tasks
        if peer_id in self.retry_tasks:
            del self.retry_tasks[peer_id]
        return False

    async def _attempt_reconnection(self, peer_id: str) -> bool:
        """Attempt to reconnect to a specific peer.

        Args:
            peer_id: Peer to reconnect to

        Returns:
            True if reconnection successful
        """
        if not self.mesh_coordinator:
            return False

        # Use MeshCoordinator to establish connection
        if peer_id == self.admin_peer_id:
            # Reconnecting to admin
            return await self.mesh_coordinator.connect_to_admin(peer_id)
        else:
            # Reconnecting to worker
            return await self.mesh_coordinator._connect_to_worker(peer_id)

    def _log_connection_health(self):
        """Log connection health metrics."""
        if not self.room_state_crdt:
            return

        all_workers = self.room_state_crdt.get_all_workers()
        total_peers = len(all_workers) - 1  # Exclude self

        connected = len(self.worker_connections)
        connectivity = connected / total_peers if total_peers > 0 else 1.0

        has_admin = (
            self.admin_peer_id and self.admin_peer_id in self.worker_connections
        )

        logging.info("=" * 60)
        logging.info("MESH CONNECTION HEALTH")
        logging.info("=" * 60)
        logging.info(f"Total workers in room: {len(all_workers)}")
        logging.info(f"Connected workers: {connected}/{total_peers} ({connectivity:.1%})")
        logging.info(
            f"Admin connection: {'✓ Connected' if has_admin else '✗ Disconnected'}"
        )
        logging.info(f"Partition status: {'✗ PARTITIONED' if self.is_partitioned else '✓ Healthy'}")

        if self.is_partitioned and self.partition_detected_at:
            import time

            duration = time.time() - self.partition_detected_at
            logging.info(f"Partition duration: {duration:.1f}s")

        logging.info("=" * 60)

    def start_partition_check_task(self):
        """Start background partition check task."""
        if self.partition_check_task is None or self.partition_check_task.done():
            self.partition_check_task = asyncio.create_task(
                self._partition_check_loop()
            )
            logging.info("Partition check task started")

    async def stop_partition_check_task(self):
        """Stop background partition check task."""
        if self.partition_check_task:
            self.partition_check_task.cancel()
            try:
                await self.partition_check_task
            except asyncio.CancelledError:
                pass
            logging.info("Partition check task stopped")

    # ===== End Partition Detection and Recovery =====

    async def _notify_admin_status(self) -> None:
        """Notify signaling server that this worker is now admin.

        Sends a register message with is_admin=True so the signaling server
        can track this worker as the admin and provide discovery info to
        future workers joining the room.
        """
        if not self.websocket:
            logging.warning("Cannot notify admin status: WebSocket not connected")
            return

        logging.info("Notifying signaling server of admin status...")

        try:
            # Get SLEAP version
            try:
                import sleap
                sleap_version = sleap.__version__
            except (ImportError, AttributeError):
                sleap_version = "unknown"

            # Send register message with is_admin=True
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "register",
                        "peer_id": self.peer_id,
                        "room_id": self.room_id,
                        "token": self.room_token,
                        "id_token": self.id_token,
                        "role": "worker",
                        "is_admin": True,  # Declare admin status
                        "metadata": {
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
            logging.info("Admin status notification sent to signaling server")

        except Exception as e:
            logging.error(f"Failed to notify admin status: {e}")

    async def initialize_mesh(
        self,
        dns: str,
        discovered_admin: Optional[str] = None,
        discovered_peers: Optional[list] = None,
        peer_metadata: Optional[dict] = None,
    ):
        """Initialize mesh networking components after registration.

        This method:
        1. Creates CRDT for room state
        2. Adds discovered workers to CRDT (if provided by signaling server)
        3. Initializes AdminController
        4. Runs admin election
        5. Initializes MeshCoordinator
        6. Connects to mesh based on role (admin keeps WebSocket, non-admin connects then closes)

        Args:
            dns: Signaling server DNS for WebSocket reconnection
            discovered_admin: Admin peer_id from signaling server (optional)
            discovered_peers: List of peer_ids from signaling server (optional)
            peer_metadata: Metadata dict for discovered peers (optional)
        """
        logging.info("Initializing mesh networking...")

        try:
            # 1. Create CRDT for room state
            self.room_state_crdt = RoomStateCRDT.create(self.room_id)
            logging.info(f"CRDT initialized for room: {self.room_id}")

            # 2. Add discovered workers to CRDT first (from signaling server)
            if discovered_peers and peer_metadata:
                logging.info(f"Adding {len(discovered_peers)} discovered workers to CRDT")
                for peer_id in discovered_peers:
                    if peer_id != self.peer_id:  # Don't add ourselves yet
                        metadata = peer_metadata.get(peer_id, {})
                        is_admin = peer_id == discovered_admin
                        self.room_state_crdt.add_worker(peer_id, metadata, is_admin=is_admin)
                        logging.info(f"Added discovered worker: {peer_id} (admin: {is_admin})")

            # 3. Add ourselves to CRDT
            self.room_state_crdt.add_worker(
                self.peer_id,
                metadata={
                    "tags": ["sleap-rtc", "training-worker", "inference-worker"],
                    "properties": {
                        "gpu_memory_mb": self.gpu_memory_mb,
                        "gpu_model": self.gpu_model,
                        "status": self.status,
                    },
                },
            )
            logging.info(f"Added self ({self.peer_id}) to CRDT")

            # 3. Initialize AdminController
            self.admin_controller = AdminController(self, self.room_state_crdt)
            logging.info("AdminController initialized")

            # 4. Run admin election
            await self.admin_controller.run_election()
            admin_peer_id = self.admin_controller.admin_peer_id
            is_admin = self.admin_controller.is_admin
            logging.info(
                f"Admin election complete: {admin_peer_id} is admin (this worker: {is_admin})"
            )

            # 4b. Set admin status callback for real-time admin checks during re-registration
            if self.state_manager:
                self.state_manager.set_admin_callback(
                    lambda: self.admin_controller.is_admin if self.admin_controller else False
                )

            # 5. Initialize MeshCoordinator
            self.mesh_coordinator = MeshCoordinator(
                worker=self, admin_controller=self.admin_controller, batch_size=3
            )
            await self.mesh_coordinator.initialize(self.websocket, dns)
            logging.info("MeshCoordinator initialized")

            # 6. Connect to mesh based on role
            if is_admin:
                logging.info(
                    "This worker is admin - keeping WebSocket open for discovery"
                )
                # Admin keeps WebSocket open (already open from registration)
                # Note: Admin status already declared in initial registration
            else:
                logging.info("This worker is non-admin - connecting to admin")
                # Non-admin: connect to admin, then close WebSocket
                if admin_peer_id != self.peer_id:
                    success = await self.mesh_coordinator.connect_to_admin(admin_peer_id)
                    if success:
                        logging.info(f"Successfully connected to admin: {admin_peer_id}")
                        # Request list of other workers from admin
                        # TODO: Implement peer list request
                    else:
                        logging.error(f"Failed to connect to admin: {admin_peer_id}")

            # 7. Start heartbeat tasks (Phase 4)
            self.start_heartbeat_tasks()

            # 8. Start partition check task (Phase 5)
            self.start_partition_check_task()

            logging.info("Mesh networking initialization complete")

        except Exception as e:
            logging.error(f"Failed to initialize mesh networking: {e}")
            logging.exception(e)

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

                    # Set peer_id on this worker instance
                    self.peer_id = peer_id

                    # Extract discovery info from signaling server (Phase 6)
                    discovered_admin = data.get("admin_peer_id")
                    discovered_peers = data.get("peer_list", [])
                    peer_metadata = data.get("peer_metadata", {})

                    # Log discovery info
                    if discovered_admin:
                        logging.info(f"Discovered admin from signaling server: {discovered_admin}")
                    if discovered_peers:
                        logging.info(f"Discovered {len(discovered_peers)} peers from signaling server: {discovered_peers}")

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

                    # Initialize mesh networking (Phase 3)
                    if not self.mesh_initialized and self.signaling_dns:
                        await self.initialize_mesh(
                            self.signaling_dns,
                            discovered_admin=discovered_admin,
                            discovered_peers=discovered_peers,
                            peer_metadata=peer_metadata,
                        )
                        self.mesh_initialized = True

                # Phase 6: Handle mesh connection messages from signaling server
                elif msg_type == "mesh_offer":
                    # Admin receives connection offer from non-admin via signaling server
                    if self.mesh_coordinator:
                        await self.mesh_coordinator.handle_signaling_offer(data)
                    else:
                        logging.warning("Received mesh_offer but mesh not initialized")

                elif msg_type == "mesh_answer":
                    # Non-admin receives connection answer from admin via signaling server
                    if self.mesh_coordinator:
                        await self.mesh_coordinator.handle_signaling_answer(data)
                    else:
                        logging.warning("Received mesh_answer but mesh not initialized")

                elif msg_type == "ice_candidate":
                    # Relay ICE candidates for mesh connections
                    if self.mesh_coordinator:
                        await self.mesh_coordinator.handle_signaling_ice_candidate(data)
                    else:
                        logging.warning("Received ice_candidate but mesh not initialized")

                elif msg_type == "admin_conflict":
                    # Handle race condition: another worker is already admin
                    current_admin = data.get("current_admin")
                    logging.warning(
                        f"Admin conflict: {current_admin} is already admin, "
                        f"demoting ourselves to non-admin"
                    )
                    if self.admin_controller:
                        self.admin_controller.is_admin = False
                        self.admin_controller.admin_peer_id = current_admin
                        # Close WebSocket if we opened it (we're not admin)
                        if self.mesh_coordinator and self.mesh_coordinator.websocket:
                            await self.mesh_coordinator.on_admin_demotion()

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
            self.state_manager.status = "available"  # Sync state_manager status!
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
            self.state_manager.status = "available"  # Sync state_manager status!
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
            self.state_manager.status = "available"  # Sync state_manager status!
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

            # Store signaling server DNS for mesh networking
            self.signaling_dns = DNS

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
                            "is_admin": True,  # NEW: Optimistically claim admin (server will resolve conflicts)
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
