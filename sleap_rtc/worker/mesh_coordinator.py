"""Mesh coordinator for worker-to-worker peer connections.

This module implements the mesh networking layer for workers in a room,
enabling direct worker-to-worker WebRTC connections for state synchronization
and coordination.

Key responsibilities:
- Admin WebSocket persistence (admin keeps WebSocket open for discovery)
- Non-admin WebSocket closure (after connecting to admin)
- Mesh signaling via RTC data channels (relay SDP/ICE through admin)
- Batched parallel connection establishment (batch_size configurable)
- Admin promotion handling (open WebSocket when becoming admin)

Architecture:
1. New worker joins, connects to signaling server
2. Signaling server identifies admin worker
3. New worker negotiates WebRTC with admin via signaling server
4. New worker closes WebSocket (non-admin)
5. Admin relays connection requests to other workers via RTC data channels
6. Subsequent peer connections happen via mesh signaling (no signaling server)

Admin role:
- Admin keeps WebSocket open for new worker discovery
- Admin relays SDP offers/answers/ICE candidates between workers
- If admin disconnects, re-election happens, new admin opens WebSocket
"""

import asyncio
import logging
import json
from typing import Dict, Optional, Any, List, TYPE_CHECKING
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate

if TYPE_CHECKING:
    from websockets.client import ClientConnection
    from sleap_rtc.worker.worker_class import RTCWorkerClient
    from sleap_rtc.worker.admin_controller import AdminController

logger = logging.getLogger(__name__)


# Mesh signaling message types
MSG_MESH_PEER_JOINED = "mesh_peer_joined"
MSG_MESH_OFFER = "mesh_offer"
MSG_MESH_ANSWER = "mesh_answer"
MSG_MESH_ICE_CANDIDATE = "mesh_ice_candidate"
MSG_MESH_PEER_LIST = "mesh_peer_list"

# Admin verification message types (for election confirmation)
MSG_ADMIN_VERIFY = "admin_verify"
MSG_ADMIN_VERIFY_ACK = "admin_verify_ack"


class MeshCoordinator:
    """Coordinates worker-to-worker mesh connections.

    The MeshCoordinator handles:
    - Initial connection to admin worker via signaling server
    - Mesh signaling protocol (relay SDP/ICE via existing connections)
    - Batched parallel connection establishment to other workers
    - Admin role awareness and WebSocket persistence
    - Admin promotion (becoming admin opens WebSocket)

    Attributes:
        worker: Reference to RTCWorkerClient instance
        admin_controller: Reference to AdminController instance
        websocket: WebSocket connection to signaling server (admin only)
        batch_size: Max concurrent connections during mesh formation (default 3)
        pending_offers: Track pending connection offers
        pending_ice_candidates: Buffer ICE candidates during negotiation
    """

    def __init__(
        self,
        worker: "RTCWorkerClient",
        admin_controller: "AdminController",
        batch_size: int = 3,
    ):
        """Initialize MeshCoordinator.

        Args:
            worker: RTCWorkerClient instance
            admin_controller: AdminController instance
            batch_size: Max concurrent connections during mesh formation
        """
        self.worker = worker
        self.admin_controller = admin_controller
        self.batch_size = batch_size

        # WebSocket connection (only kept open if admin)
        self.websocket: Optional["ClientConnection"] = None
        self.websocket_dns: Optional[str] = None  # Store DNS for reconnection

        # Connection negotiation state
        self.pending_offers: Dict[str, Dict[str, Any]] = {}  # peer_id -> offer_data
        self.pending_ice_candidates: Dict[str, List[Dict[str, Any]]] = (
            {}
        )  # peer_id -> [candidates]

        # Mesh formation state
        self.mesh_formation_in_progress = False
        self.connected_peers: set = set()  # Track successfully connected peers

        # Admin WebSocket handler task
        self._admin_handler_task: Optional[asyncio.Task] = None

        # Non-admin WebSocket handler task (for post-demotion signaling)
        self._non_admin_handler_task: Optional[asyncio.Task] = None

    async def initialize(self, websocket: "ClientConnection", dns: str):
        """Initialize mesh coordinator with initial WebSocket connection.

        Called after worker registers with signaling server. Determines if
        this worker should keep WebSocket open (admin) or close it (non-admin).

        Args:
            websocket: Initial WebSocket connection from registration
            dns: Signaling server DNS for reconnection
        """
        self.websocket = websocket
        self.websocket_dns = dns

        logger.info("MeshCoordinator initialized")

        # Check if we're admin - if so, keep WebSocket open
        if self.admin_controller.is_admin:
            logger.info("This worker is admin - keeping WebSocket open for discovery")
            self._start_admin_websocket_handler()
        else:
            logger.info(
                "This worker is not admin - will close WebSocket after connecting to admin"
            )

    def _start_admin_websocket_handler(self):
        """Start background task to handle WebSocket messages (admin only)."""
        if not self.admin_controller.is_admin:
            logger.warning("Cannot start admin WebSocket handler: not admin")
            return

        # Cancel existing handler if any
        if self._admin_handler_task and not self._admin_handler_task.done():
            self._admin_handler_task.cancel()

        # Start new handler task
        self._admin_handler_task = asyncio.create_task(
            self._admin_websocket_handler_loop()
        )
        logger.info("Admin WebSocket handler started")

    async def _admin_websocket_handler_loop(self):
        """Background task that handles WebSocket messages for admin.

        This runs continuously while this worker is admin, processing:
        - mesh_offer: New workers connecting via signaling server
        - ice_candidate: ICE candidates for mesh connections
        - offer: Client connection offers
        - admin_conflict: Another worker claiming admin
        - registered_auth: Registration confirmation
        """
        logger.info("Starting admin WebSocket handler loop")

        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type")

                    logger.debug(f"Admin received message: {msg_type}")

                    if msg_type == "registered_auth":
                        # Registration confirmed after admin promotion
                        peer_id = data.get("peer_id")
                        room_id = data.get("room_id")
                        logger.info(
                            f"Admin re-registration confirmed: {peer_id} in room {room_id}"
                        )

                    elif msg_type == "mesh_offer":
                        # New worker connecting via signaling server
                        logger.info(f"Admin received mesh_offer from {data.get('from_peer_id')}")
                        await self.handle_signaling_offer(data)

                    elif msg_type == "ice_candidate":
                        # ICE candidate for mesh connection
                        await self.handle_signaling_ice_candidate(data)

                    elif msg_type == "offer":
                        # Client connection offer - delegate to worker
                        logger.info(f"Admin received client offer from {data.get('sender')}")
                        await self._handle_client_offer(data)

                    elif msg_type == "admin_conflict":
                        # Another worker is already admin
                        current_admin = data.get("current_admin")
                        logger.warning(
                            f"Admin conflict: {current_admin} is already admin, "
                            f"demoting ourselves"
                        )
                        self.admin_controller.is_admin = False
                        self.admin_controller.admin_peer_id = current_admin
                        self.worker.admin_peer_id = current_admin
                        # Close WebSocket since we're not admin
                        await self.on_admin_demotion()
                        return  # Exit handler loop

                    elif msg_type == "error":
                        logger.error(f"Admin received error: {data.get('reason')}")

                    else:
                        logger.debug(f"Admin ignoring message type: {msg_type}")

                except json.JSONDecodeError:
                    logger.error("Invalid JSON received in admin handler")
                except Exception as e:
                    logger.error(f"Error processing admin message: {e}")

        except asyncio.CancelledError:
            logger.info("Admin WebSocket handler cancelled")
        except Exception as e:
            logger.error(f"Admin WebSocket handler error: {e}")

    async def _handle_client_offer(self, data: Dict[str, Any]):
        """Handle client connection offer received on admin WebSocket.

        Delegates to the worker's existing client handling logic.

        Args:
            data: Offer data from signaling server
        """
        from aiortc import RTCSessionDescription

        sender = data.get("sender")
        sdp = data.get("sdp")

        logger.info(f"Admin handling client offer from {sender}")

        # Check worker status before accepting
        if self.worker.status in ["busy", "reserved"]:
            logger.warning(f"Rejecting client {sender} - worker is {self.worker.status}")
            await self.websocket.send(
                json.dumps({
                    "type": "error",
                    "target": sender,
                    "reason": "worker_busy",
                    "message": f"Worker is currently {self.worker.status}",
                    "current_status": self.worker.status,
                })
            )
            return

        # Update status to reserved
        await self.worker.state_manager.update_status("reserved")
        logger.info("Worker status updated to 'reserved'")

        # Set remote description and create answer
        await self.worker.pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp, type="offer")
        )
        await self.worker.pc.setLocalDescription(await self.worker.pc.createAnswer())

        # Send answer back to client
        await self.websocket.send(
            json.dumps({
                "type": self.worker.pc.localDescription.type,
                "sender": self.worker.peer_id,
                "target": sender,
                "sdp": self.worker.pc.localDescription.sdp,
            })
        )

        logger.info(f"Admin sent answer to client {sender}")

        # Clear received files for new connection
        self.worker.received_files.clear()

    async def connect_to_admin(self, admin_peer_id: str) -> bool:
        """Connect to admin worker via signaling server.

        This is the initial connection for non-admin workers. Uses the
        WebSocket signaling server to negotiate WebRTC connection with admin.

        Args:
            admin_peer_id: peer_id of the admin worker

        Returns:
            True if connection successful, False otherwise
        """
        if not self.websocket:
            logger.error("Cannot connect to admin: no WebSocket connection")
            return False

        logger.info(f"Connecting to admin worker: {admin_peer_id}")

        try:
            # Create peer connection for admin
            pc = RTCPeerConnection()

            # Set up data channel for mesh signaling
            data_channel = pc.createDataChannel("mesh_signaling")

            # Store data channel IMMEDIATELY - don't wait for on_open
            # The sending code will check readyState before sending
            self.worker.data_channels[admin_peer_id] = data_channel
            logger.info(f"Created data channel for admin {admin_peer_id} (state: {data_channel.readyState})")

            @data_channel.on("open")
            def on_open():
                logger.info(f"Mesh signaling channel open with admin: {admin_peer_id}")

            @data_channel.on("close")
            def on_close():
                logger.warning(f"Mesh signaling channel closed with admin: {admin_peer_id}")
                # Remove data channel reference
                if admin_peer_id in self.worker.data_channels:
                    del self.worker.data_channels[admin_peer_id]

            @data_channel.on("error")
            def on_error(error):
                logger.error(f"Data channel error with admin {admin_peer_id}: {error}")

            @data_channel.on("message")
            async def on_message(message):
                await self._handle_mesh_message(message, admin_peer_id)

            # Set ICE connection state handler
            @pc.on("iceconnectionstatechange")
            async def on_ice_state_change():
                await self.worker.on_mesh_iceconnectionstatechange(admin_peer_id, pc)

            # Set ICE candidate handler - send candidates to signaling server
            @pc.on("icecandidate")
            async def on_ice_candidate(event):
                if event.candidate and self.websocket:
                    await self.websocket.send(
                        json.dumps(
                            {
                                "type": "ice_candidate",
                                "from_peer_id": self.worker.peer_id,
                                "target_peer_id": admin_peer_id,
                                "candidate": {
                                    "candidate": event.candidate.candidate,
                                    "sdpMLineIndex": event.candidate.sdpMLineIndex,
                                    "sdpMid": event.candidate.sdpMid,
                                },
                            }
                        )
                    )
                    logger.debug(
                        f"Sent ICE candidate to admin {admin_peer_id} via signaling server"
                    )

            # Create offer
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)

            # Send offer to admin via signaling server
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "mesh_connect",
                        "from_peer_id": self.worker.peer_id,  # Added for clarity
                        "target_peer_id": admin_peer_id,
                        "offer": {
                            "sdp": pc.localDescription.sdp,
                            "type": pc.localDescription.type,
                        },
                    }
                )
            )

            logger.info(f"Sent connection offer to admin: {admin_peer_id}")

            # Store connection in registry
            self.worker.worker_connections[admin_peer_id] = pc
            self.worker.admin_peer_id = admin_peer_id
            self.connected_peers.add(admin_peer_id)

            return True

        except Exception as e:
            logger.error(f"Failed to connect to admin: {e}")
            return False

    async def connect_to_workers_batched(self, worker_peer_ids: List[str]):
        """Connect to multiple workers using batched parallel approach.

        Establishes connections in batches to avoid overwhelming the admin's
        data channel with simultaneous negotiations.

        Args:
            worker_peer_ids: List of worker peer_ids to connect to
        """
        if not worker_peer_ids:
            logger.info("No workers to connect to")
            return

        logger.info(
            f"Connecting to {len(worker_peer_ids)} workers in batches of {self.batch_size}"
        )

        self.mesh_formation_in_progress = True

        # Split into batches
        for i in range(0, len(worker_peer_ids), self.batch_size):
            batch = worker_peer_ids[i : i + self.batch_size]
            logger.info(
                f"Connecting to batch {i // self.batch_size + 1}: {len(batch)} workers"
            )

            # Connect to all workers in batch concurrently
            tasks = [self._connect_to_worker(peer_id) for peer_id in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Log results
            for peer_id, result in zip(batch, results):
                if isinstance(result, Exception):
                    logger.error(f"Failed to connect to {peer_id}: {result}")
                elif result:
                    logger.info(f"Successfully connected to {peer_id}")
                else:
                    logger.warning(f"Connection to {peer_id} failed")

            # Small delay between batches to avoid overwhelming admin
            if i + self.batch_size < len(worker_peer_ids):
                await asyncio.sleep(0.5)

        self.mesh_formation_in_progress = False
        logger.info(
            f"Mesh formation complete: {len(self.connected_peers)} / {len(worker_peer_ids) + 1} workers connected"
        )

    async def _connect_to_worker(self, peer_id: str) -> bool:
        """Connect to a single worker via mesh signaling (through admin).

        Args:
            peer_id: peer_id of the worker to connect to

        Returns:
            True if connection successful, False otherwise
        """
        logger.info(f"Initiating connection to worker: {peer_id}")

        try:
            # Create peer connection
            pc = RTCPeerConnection()

            # Set up data channel
            data_channel = pc.createDataChannel("mesh_signaling")

            # Store data channel IMMEDIATELY - don't wait for on_open
            # The sending code will check readyState before sending
            self.worker.data_channels[peer_id] = data_channel
            logger.info(f"Created data channel for worker {peer_id} (state: {data_channel.readyState})")

            @data_channel.on("open")
            def on_open():
                logger.info(f"Mesh signaling channel open with worker: {peer_id}")

            @data_channel.on("close")
            def on_close():
                logger.warning(f"Mesh signaling channel closed with worker: {peer_id}")
                # Remove data channel reference
                if peer_id in self.worker.data_channels:
                    del self.worker.data_channels[peer_id]

            @data_channel.on("error")
            def on_error(error):
                logger.error(f"Data channel error with worker {peer_id}: {error}")

            @data_channel.on("message")
            async def on_message(message):
                await self._handle_mesh_message(message, peer_id)

            # Set ICE connection state handler
            @pc.on("iceconnectionstatechange")
            async def on_ice_state_change():
                await self.worker.on_mesh_iceconnectionstatechange(peer_id, pc)

            # Set ICE candidate handler - send candidates via mesh signaling (through admin)
            @pc.on("icecandidate")
            async def on_ice_candidate(event):
                if event.candidate:
                    await self._send_mesh_message(
                        {
                            "type": MSG_MESH_ICE_CANDIDATE,
                            "from_peer_id": self.worker.peer_id,
                            "to_peer_id": peer_id,
                            "candidate": {
                                "candidate": event.candidate.candidate,
                                "sdpMLineIndex": event.candidate.sdpMLineIndex,
                                "sdpMid": event.candidate.sdpMid,
                            },
                        }
                    )
                    logger.debug(
                        f"Sent ICE candidate to worker {peer_id} via mesh signaling"
                    )

            # Create offer
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)

            # Send offer to worker via admin (mesh signaling)
            await self._send_mesh_message(
                {
                    "type": MSG_MESH_OFFER,
                    "from_peer_id": self.worker.peer_id,
                    "to_peer_id": peer_id,
                    "offer": {
                        "sdp": pc.localDescription.sdp,
                        "type": pc.localDescription.type,
                    },
                }
            )

            # Store connection in registry
            self.worker.worker_connections[peer_id] = pc
            self.connected_peers.add(peer_id)

            return True

        except Exception as e:
            logger.error(f"Failed to connect to worker {peer_id}: {e}")
            return False

    async def _send_mesh_message(self, message: Dict[str, Any]):
        """Send a message via mesh signaling (through admin).

        Args:
            message: Message dictionary to send
        """
        # If we're admin, handle directly
        if self.admin_controller.is_admin:
            await self._relay_mesh_message(message)
        else:
            # Send via admin's data channel
            admin_peer_id = self.worker.admin_peer_id
            if not admin_peer_id:
                logger.error("Cannot send mesh message: no admin peer_id")
                return

            if admin_peer_id not in self.worker.data_channels:
                logger.error(
                    f"Cannot send mesh message: no data channel to admin {admin_peer_id}"
                )
                return

            data_channel = self.worker.data_channels[admin_peer_id]

            if data_channel.readyState != "open":
                logger.error(
                    f"Cannot send mesh message: admin channel state is {data_channel.readyState}"
                )
                return

            try:
                # Send JSON-encoded message
                message_str = json.dumps(message)
                data_channel.send(message_str)
                logger.debug(f"Sent mesh message via admin: {message['type']}")
            except Exception as e:
                logger.error(f"Failed to send mesh message via admin: {e}")

    async def _send_peer_list_to_worker(self, target_peer_id: str):
        """Send list of connected peers to a newly connected worker.

        Called by admin when a new worker's data channel opens.
        Enables the new worker to establish connections to other workers.

        Args:
            target_peer_id: peer_id of the worker to send the list to
        """
        if not self.admin_controller.is_admin:
            logger.debug("Not sending peer list: not admin")
            return

        # Get list of other connected workers (excluding self and target)
        other_peers = [
            pid for pid in self.connected_peers
            if pid != self.worker.peer_id and pid != target_peer_id
        ]

        if not other_peers:
            logger.info(f"No other peers to send to {target_peer_id}")
            return

        # Gather peer metadata from CRDT if available
        peer_metadata = {}
        if self.worker.room_state_crdt:
            all_workers = self.worker.room_state_crdt.get_all_workers()
            for pid in other_peers:
                if pid in all_workers:
                    worker_data = all_workers[pid]
                    peer_metadata[pid] = worker_data.get("metadata", {})

        # Send peer list message
        message = {
            "type": MSG_MESH_PEER_LIST,
            "peer_ids": other_peers,
            "peer_metadata": peer_metadata,
        }

        # Get data channel to target
        if target_peer_id not in self.worker.data_channels:
            logger.error(f"Cannot send peer list: no data channel to {target_peer_id}")
            return

        data_channel = self.worker.data_channels[target_peer_id]

        if data_channel.readyState != "open":
            logger.warning(
                f"Cannot send peer list: channel to {target_peer_id} is {data_channel.readyState}"
            )
            return

        try:
            message_str = json.dumps(message)
            data_channel.send(message_str)
            logger.info(
                f"Sent peer list to {target_peer_id}: {len(other_peers)} peers"
            )
        except Exception as e:
            logger.error(f"Failed to send peer list to {target_peer_id}: {e}")

    async def _notify_existing_workers_of_new_peer(self, new_peer_id: str):
        """Notify all existing workers that a new peer has joined.

        Called by admin when a new worker connects. Enables existing workers
        to proactively establish connections to the new peer.

        Args:
            new_peer_id: peer_id of the newly connected worker
        """
        if not self.admin_controller.is_admin:
            logger.debug("Not notifying workers: not admin")
            return

        # Get new peer's metadata from CRDT if available
        peer_metadata = {}
        if self.worker.room_state_crdt:
            all_workers = self.worker.room_state_crdt.get_all_workers()
            if new_peer_id in all_workers:
                peer_metadata = all_workers[new_peer_id].get("metadata", {})

        # Create notification message
        message = {
            "type": MSG_MESH_PEER_JOINED,
            "peer_id": new_peer_id,
            "metadata": peer_metadata,
        }

        # Send to all existing connected workers (except the new one)
        message_str = json.dumps(message)
        notified_count = 0

        for peer_id in self.connected_peers:
            if peer_id == new_peer_id or peer_id == self.worker.peer_id:
                continue

            if peer_id not in self.worker.data_channels:
                continue

            data_channel = self.worker.data_channels[peer_id]
            if data_channel.readyState != "open":
                continue

            try:
                data_channel.send(message_str)
                notified_count += 1
            except Exception as e:
                logger.error(f"Failed to notify {peer_id} of new peer: {e}")

        if notified_count > 0:
            logger.info(
                f"Notified {notified_count} workers of new peer: {new_peer_id}"
            )

    async def _do_admin_duties_async(self, new_peer_id: str, offer_data: Dict[str, Any]):
        """Perform admin duties when a new worker connects.

        This is called when a new worker's data channel opens with the admin.
        Handles CRDT synchronization and mesh coordination.

        Args:
            new_peer_id: peer_id of the newly connected worker
            offer_data: Original offer data which may contain worker metadata
        """
        try:
            # 1. Extract metadata from offer data (signaling server should include it)
            metadata = offer_data.get("metadata", {})
            if not metadata:
                # Fallback: create minimal metadata
                logger.warning(f"No metadata in offer from {new_peer_id}, using defaults")
                metadata = {
                    "tags": ["sleap-rtc"],
                    "properties": {"status": "available"},
                }

            # 2. Add new worker to admin's CRDT
            if self.worker.room_state_crdt:
                logger.info(f"Adding new worker {new_peer_id} to CRDT")
                self.worker.room_state_crdt.add_worker(
                    new_peer_id, metadata, is_admin=False
                )

                # Also update via AdminController for consistency
                if self.admin_controller:
                    # Note: We don't call handle_worker_joined because that would
                    # trigger re-election, which we don't want for normal joins.
                    # Re-election only happens if new worker has more GPU memory,
                    # but we handle that separately.
                    pass

            # 3. Broadcast updated CRDT state to ALL workers (including the new one)
            if self.admin_controller and self.admin_controller.is_admin:
                logger.info("Broadcasting updated CRDT state to all workers")
                await self.admin_controller.broadcast_state_update()

            # 4. Send peer list to the new worker
            await self._send_peer_list_to_worker(new_peer_id)

            # 5. Notify existing workers about the new peer
            await self._notify_existing_workers_of_new_peer(new_peer_id)

            logger.info(f"Admin duties completed for new worker: {new_peer_id}")

        except Exception as e:
            logger.error(f"Failed to complete admin duties for {new_peer_id}: {e}")

    async def _relay_mesh_message(self, message: Dict[str, Any]):
        """Relay a mesh message to its destination (admin only).

        Admin relays SDP offers/answers and ICE candidates between workers
        who don't have direct connections yet.

        Args:
            message: Message to relay (must have to_peer_id field)
        """
        if not self.admin_controller.is_admin:
            logger.warning("Cannot relay mesh message: not admin")
            return

        to_peer_id = message.get("to_peer_id")
        if not to_peer_id:
            logger.error("Cannot relay message: no to_peer_id")
            return

        # Get data channel to target peer
        if to_peer_id not in self.worker.data_channels:
            logger.error(f"Cannot relay message: no data channel to {to_peer_id}")
            return

        data_channel = self.worker.data_channels[to_peer_id]

        if data_channel.readyState != "open":
            logger.warning(
                f"Cannot relay message to {to_peer_id}: channel state is {data_channel.readyState}"
            )
            return

        try:
            message_str = json.dumps(message)
            data_channel.send(message_str)
            logger.debug(
                f"Relayed {message['type']} from {message.get('from_peer_id')} to {to_peer_id}"
            )
        except Exception as e:
            logger.error(f"Failed to relay message to {to_peer_id}: {e}")

    async def _handle_mesh_message(self, message: str, from_peer_id: str):
        """Handle incoming mesh signaling message.

        Args:
            message: JSON string message
            from_peer_id: peer_id of sender
        """
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            # Check if this message should be relayed (admin only)
            # Messages with to_peer_id != self should be forwarded to target
            to_peer_id = data.get("to_peer_id")
            if (
                to_peer_id
                and to_peer_id != self.worker.peer_id
                and self.admin_controller.is_admin
            ):
                logger.debug(
                    f"Relaying {msg_type} from {from_peer_id} to {to_peer_id}"
                )
                await self._relay_mesh_message(data)
                return

            # Mesh signaling messages (WebRTC connection establishment)
            # For relayed messages, use from_peer_id from message data (original sender)
            # not from the data channel sender (which would be admin for relayed messages)
            if msg_type == MSG_MESH_OFFER:
                original_sender = data.get("from_peer_id", from_peer_id)
                await self._handle_mesh_offer(data, original_sender)
            elif msg_type == MSG_MESH_ANSWER:
                original_sender = data.get("from_peer_id", from_peer_id)
                await self._handle_mesh_answer(data, original_sender)
            elif msg_type == MSG_MESH_ICE_CANDIDATE:
                original_sender = data.get("from_peer_id", from_peer_id)
                await self._handle_mesh_ice_candidate(data, original_sender)
            elif msg_type == MSG_MESH_PEER_JOINED:
                await self._handle_peer_joined(data)
            elif msg_type == MSG_MESH_PEER_LIST:
                await self._handle_peer_list(data)
            # Admin verification messages (election confirmation)
            elif msg_type == MSG_ADMIN_VERIFY:
                await self._handle_admin_verify(data, from_peer_id)
            elif msg_type == MSG_ADMIN_VERIFY_ACK:
                await self._handle_admin_verify_ack(data, from_peer_id)
            # Application-level messages (forward to worker)
            elif msg_type == "heartbeat":
                await self._forward_to_worker_handler("heartbeat", data, from_peer_id)
            elif msg_type == "heartbeat_response":
                await self._forward_to_worker_handler("heartbeat_response", data, from_peer_id)
            elif msg_type == "status_update":
                await self._forward_to_worker_handler("status_update", data, from_peer_id)
            elif msg_type == "state_broadcast":
                await self._forward_to_worker_handler("state_broadcast", data, from_peer_id)
            else:
                logger.warning(f"Unknown mesh message type: {msg_type}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse mesh message: {e}")
        except Exception as e:
            logger.error(f"Error handling mesh message: {e}")

    async def _forward_to_worker_handler(
        self, msg_type: str, data: Dict[str, Any], from_peer_id: str
    ):
        """Forward application-level messages to worker handlers.

        Args:
            msg_type: Message type (heartbeat, status_update, etc.)
            data: Message data dictionary
            from_peer_id: peer_id of sender
        """
        from sleap_rtc.worker.mesh_messages import (
            HeartbeatMessage,
            HeartbeatResponseMessage,
            StatusUpdateMessage,
            StateBroadcastMessage,
        )

        try:
            if msg_type == "heartbeat":
                message = HeartbeatMessage(
                    from_peer_id=data.get("from_peer_id"),
                    timestamp=data.get("timestamp"),
                    sequence=data.get("sequence"),
                )
                await self.worker._handle_heartbeat(message, from_peer_id)
            elif msg_type == "heartbeat_response":
                message = HeartbeatResponseMessage(
                    from_peer_id=data.get("from_peer_id"),
                    to_peer_id=data.get("to_peer_id"),
                    timestamp=data.get("timestamp"),
                    original_timestamp=data.get("original_timestamp"),
                )
                await self.worker._handle_heartbeat_response(message, from_peer_id)
            elif msg_type == "status_update":
                message = StatusUpdateMessage(
                    from_peer_id=data.get("from_peer_id"),
                    status=data.get("status"),
                    current_job=data.get("current_job"),
                    timestamp=data.get("timestamp"),
                )
                await self.worker._handle_status_update(message, from_peer_id)
            elif msg_type == "state_broadcast":
                message = StateBroadcastMessage(
                    from_peer_id=data.get("from_peer_id"),
                    timestamp=data.get("timestamp"),
                    crdt_snapshot=data.get("crdt_snapshot"),
                    version=data.get("version"),
                )
                await self.worker._handle_state_broadcast(message, from_peer_id)
            else:
                logger.warning(f"Unknown message type to forward: {msg_type}")
        except Exception as e:
            logger.error(f"Error forwarding {msg_type} message: {e}")

    async def _handle_mesh_offer(self, data: Dict[str, Any], from_peer_id: str):
        """Handle incoming connection offer from another worker.

        Args:
            data: Offer data dictionary
            from_peer_id: peer_id of offering worker
        """
        logger.info(f"Received mesh offer from {from_peer_id}")

        try:
            # Create peer connection
            pc = RTCPeerConnection()

            # Set up datachannel event handler to receive incoming channel
            @pc.on("datachannel")
            def on_datachannel(channel):
                logger.info(
                    f"Received data channel from {from_peer_id}: {channel.label} "
                    f"(state: {channel.readyState})"
                )

                # Store data channel IMMEDIATELY - don't wait for on_open
                # The sending code will check readyState before sending
                self.worker.data_channels[from_peer_id] = channel
                logger.info(f"Stored data channel for {from_peer_id}")

                # Set up channel event handlers
                @channel.on("open")
                def on_open():
                    logger.info(f"Data channel now open with {from_peer_id}")

                @channel.on("close")
                def on_close():
                    logger.warning(f"Data channel closed with {from_peer_id}")
                    # Remove data channel reference
                    if from_peer_id in self.worker.data_channels:
                        del self.worker.data_channels[from_peer_id]

                @channel.on("error")
                def on_error(error):
                    logger.error(f"Data channel error with {from_peer_id}: {error}")

                @channel.on("message")
                async def on_message(message):
                    await self._handle_mesh_message(message, from_peer_id)

            # Set ICE connection state handler
            @pc.on("iceconnectionstatechange")
            async def on_ice_state_change():
                await self.worker.on_mesh_iceconnectionstatechange(from_peer_id, pc)

            # Set ICE candidate handler - send candidates via mesh signaling (through admin)
            @pc.on("icecandidate")
            async def on_ice_candidate(event):
                if event.candidate:
                    await self._send_mesh_message(
                        {
                            "type": MSG_MESH_ICE_CANDIDATE,
                            "from_peer_id": self.worker.peer_id,
                            "to_peer_id": from_peer_id,
                            "candidate": {
                                "candidate": event.candidate.candidate,
                                "sdpMLineIndex": event.candidate.sdpMLineIndex,
                                "sdpMid": event.candidate.sdpMid,
                            },
                        }
                    )
                    logger.debug(
                        f"Sent ICE candidate to {from_peer_id} via mesh signaling"
                    )

            # Set remote description (offer)
            offer = RTCSessionDescription(
                sdp=data["offer"]["sdp"], type=data["offer"]["type"]
            )
            await pc.setRemoteDescription(offer)

            # Create answer
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            # Send answer back
            await self._send_mesh_message(
                {
                    "type": MSG_MESH_ANSWER,
                    "from_peer_id": self.worker.peer_id,
                    "to_peer_id": from_peer_id,
                    "answer": {
                        "sdp": pc.localDescription.sdp,
                        "type": pc.localDescription.type,
                    },
                }
            )

            # Store connection
            self.worker.worker_connections[from_peer_id] = pc
            self.connected_peers.add(from_peer_id)

            logger.info(f"Sent answer to {from_peer_id}")

        except Exception as e:
            logger.error(f"Failed to handle mesh offer: {e}")

    async def _handle_mesh_answer(self, data: Dict[str, Any], from_peer_id: str):
        """Handle incoming connection answer from another worker.

        Args:
            data: Answer data dictionary
            from_peer_id: peer_id of answering worker
        """
        logger.info(f"Received mesh answer from {from_peer_id}")

        try:
            # Get existing connection
            if from_peer_id not in self.worker.worker_connections:
                logger.error(f"No pending connection for {from_peer_id}")
                return

            pc = self.worker.worker_connections[from_peer_id]

            # Set remote description (answer)
            answer = RTCSessionDescription(
                sdp=data["answer"]["sdp"], type=data["answer"]["type"]
            )
            await pc.setRemoteDescription(answer)

            logger.info(f"Connection established with {from_peer_id}")

        except Exception as e:
            logger.error(f"Failed to handle mesh answer: {e}")

    async def _handle_mesh_ice_candidate(self, data: Dict[str, Any], from_peer_id: str):
        """Handle incoming ICE candidate from another worker.

        Adds the ICE candidate to the peer connection to help establish
        the optimal network path between workers.

        Args:
            data: ICE candidate data with candidate, sdpMLineIndex, sdpMid
            from_peer_id: peer_id of sender
        """
        # Determine the actual peer this ICE candidate is for
        # It could be relayed by admin, so check from_peer_id in the message
        actual_from_peer_id = data.get("from_peer_id", from_peer_id)

        logger.debug(f"Received ICE candidate from {actual_from_peer_id}")

        # Get peer connection for this peer
        if actual_from_peer_id not in self.worker.worker_connections:
            logger.warning(
                f"Received ICE candidate for unknown peer: {actual_from_peer_id}"
            )
            return

        pc = self.worker.worker_connections[actual_from_peer_id]

        try:
            candidate_data = data.get("candidate", {})
            if not candidate_data:
                logger.debug("Received empty ICE candidate (end of candidates)")
                return

            candidate = RTCIceCandidate(
                candidate=candidate_data.get("candidate"),
                sdpMLineIndex=candidate_data.get("sdpMLineIndex"),
                sdpMid=candidate_data.get("sdpMid"),
            )
            await pc.addIceCandidate(candidate)
            logger.debug(f"Added ICE candidate from {actual_from_peer_id}")

        except Exception as e:
            logger.error(f"Failed to add ICE candidate from {actual_from_peer_id}: {e}")

    async def _handle_peer_joined(self, data: Dict[str, Any]):
        """Handle notification that a new peer joined the room.

        Updates local CRDT with new peer's info and establishes mesh connection.

        Args:
            data: Peer joined notification data
                - peer_id: New worker's peer_id
                - metadata: New worker's metadata (optional)
        """
        peer_id = data.get("peer_id")
        if not peer_id:
            logger.warning("peer_joined message missing peer_id")
            return

        logger.info(f"New peer joined room: {peer_id}")

        # 1. Add new peer to local CRDT for consistent election state
        metadata = data.get("metadata", {})
        if not metadata:
            # Use minimal defaults if no metadata provided
            metadata = {
                "tags": ["sleap-rtc"],
                "properties": {"status": "available"},
            }

        if self.worker.room_state_crdt:
            try:
                self.worker.room_state_crdt.add_worker(peer_id, metadata, is_admin=False)
                logger.info(f"Added new peer {peer_id} to local CRDT")
            except Exception as e:
                logger.error(f"Failed to add peer {peer_id} to CRDT: {e}")

        # 2. Connect to new peer if we're not in initial mesh formation
        if not self.mesh_formation_in_progress:
            await self._connect_to_worker(peer_id)

    async def _handle_peer_list(self, data: Dict[str, Any]):
        """Handle list of existing peers in room.

        Updates local CRDT with peer metadata and establishes mesh connections.

        Args:
            data: Peer list data
                - peer_ids: List of peer IDs in the room
                - peer_metadata: Dict mapping peer_id to metadata (optional)
        """
        peer_ids = data.get("peer_ids", [])
        peer_metadata = data.get("peer_metadata", {})
        logger.info(f"Received peer list: {len(peer_ids)} workers")

        # 1. Add all peers to local CRDT for consistent election state
        if self.worker.room_state_crdt:
            for pid in peer_ids:
                if pid != self.worker.peer_id:  # Don't add self
                    metadata = peer_metadata.get(pid, {})
                    if not metadata:
                        metadata = {
                            "tags": ["sleap-rtc"],
                            "properties": {"status": "available"},
                        }
                    try:
                        self.worker.room_state_crdt.add_worker(pid, metadata, is_admin=False)
                        logger.debug(f"Added peer {pid} to local CRDT from peer list")
                    except Exception as e:
                        logger.error(f"Failed to add peer {pid} to CRDT: {e}")

        # 2. Filter out self and admin (already connected) for mesh connections
        workers_to_connect = [
            pid
            for pid in peer_ids
            if pid != self.worker.peer_id and pid != self.worker.admin_peer_id
        ]

        # 3. Connect to all workers in batches
        if workers_to_connect:
            await self.connect_to_workers_batched(workers_to_connect)

    async def _handle_admin_verify(self, data: Dict[str, Any], from_peer_id: str):
        """Handle admin verification request.

        When a worker asks us to confirm we're the admin (after election),
        respond with an acknowledgment.

        Args:
            data: Verification request data
            from_peer_id: peer_id of requesting worker
        """
        request_id = data.get("request_id")
        logger.info(f"Received admin_verify from {from_peer_id} (request_id: {request_id})")

        # Send acknowledgment back
        ack_message = {
            "type": MSG_ADMIN_VERIFY_ACK,
            "from_peer_id": self.worker.peer_id,
            "request_id": request_id,
        }

        # Send directly to the requesting peer
        if from_peer_id in self.worker.data_channels:
            data_channel = self.worker.data_channels[from_peer_id]
            if data_channel.readyState == "open":
                try:
                    data_channel.send(json.dumps(ack_message))
                    logger.info(f"Sent admin_verify_ack to {from_peer_id}")
                except Exception as e:
                    logger.error(f"Failed to send admin_verify_ack: {e}")
            else:
                logger.warning(
                    f"Cannot send admin_verify_ack: channel to {from_peer_id} is {data_channel.readyState}"
                )
        else:
            logger.warning(f"Cannot send admin_verify_ack: no channel to {from_peer_id}")

    async def _handle_admin_verify_ack(self, data: Dict[str, Any], from_peer_id: str):
        """Handle admin verification acknowledgment.

        When the elected admin confirms they're alive, notify the AdminController.

        Args:
            data: Acknowledgment data with request_id
            from_peer_id: peer_id of the admin
        """
        request_id = data.get("request_id")
        logger.info(f"Received admin_verify_ack from {from_peer_id} (request_id: {request_id})")

        # Notify AdminController that verification succeeded
        if self.admin_controller:
            self.admin_controller.on_admin_verify_ack(from_peer_id, request_id)

    async def send_admin_verify(self, target_peer_id: str, request_id: str) -> bool:
        """Send admin verification request to a peer.

        Called by AdminController after election to verify the elected admin is alive.

        Args:
            target_peer_id: peer_id of the elected admin to verify
            request_id: Unique ID for this verification request

        Returns:
            True if message was sent, False otherwise
        """
        if target_peer_id not in self.worker.data_channels:
            logger.warning(f"Cannot send admin_verify: no channel to {target_peer_id}")
            return False

        data_channel = self.worker.data_channels[target_peer_id]
        if data_channel.readyState != "open":
            logger.warning(
                f"Cannot send admin_verify: channel to {target_peer_id} is {data_channel.readyState}"
            )
            return False

        message = {
            "type": MSG_ADMIN_VERIFY,
            "from_peer_id": self.worker.peer_id,
            "request_id": request_id,
        }

        try:
            data_channel.send(json.dumps(message))
            logger.info(f"Sent admin_verify to {target_peer_id} (request_id: {request_id})")
            return True
        except Exception as e:
            logger.error(f"Failed to send admin_verify: {e}")
            return False

    async def on_admin_promotion(self):
        """Handle this worker becoming admin.

        Opens WebSocket connection to signaling server for discovery by
        new workers.
        """
        logger.info("Worker promoted to admin - opening WebSocket for discovery")

        try:
            # Reconnect to signaling server
            import websockets

            if not self.websocket_dns:
                logger.error("Cannot reconnect: no DNS stored")
                return

            self.websocket = await websockets.connect(self.websocket_dns)

            # CRITICAL: Sync websocket references so state_manager uses new connection
            self.worker.websocket = self.websocket
            if self.worker.state_manager:
                self.worker.state_manager.websocket = self.websocket
                logger.info("Updated state_manager websocket reference")

            # Re-register with server as admin (include full metadata for discovery)
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "register",
                        "peer_id": self.worker.peer_id,
                        "room_id": self.worker.room_id,
                        "token": self.worker.room_token,
                        "id_token": self.worker.id_token,
                        "role": "worker",
                        "is_admin": True,  # Signal admin status
                        "metadata": {
                            "tags": [
                                "sleap-rtc",
                                "training-worker",
                                "inference-worker",
                            ],
                            "properties": {
                                "gpu_memory_mb": self.worker.gpu_memory_mb,
                                "gpu_model": self.worker.gpu_model,
                                "status": self.worker.status,
                            },
                        },
                    }
                )
            )

            logger.info("Admin WebSocket reconnected")
            self._start_admin_websocket_handler()

        except Exception as e:
            logger.error(f"Failed to open admin WebSocket: {e}")

    async def on_admin_demotion(self):
        """Handle this worker losing admin status due to conflict resolution.

        When another worker is already admin (split-brain resolution), this worker:
        1. Cancels admin handler task
        2. Closes admin WebSocket
        3. Reopens WebSocket as non-admin
        4. Connects to the actual admin
        5. Starts non-admin signaling handler
        """
        logger.info("Worker demoted from admin - reconnecting as non-admin")

        # 1. Cancel admin handler task
        if self._admin_handler_task and not self._admin_handler_task.done():
            self._admin_handler_task.cancel()
            try:
                await self._admin_handler_task
            except asyncio.CancelledError:
                pass
            self._admin_handler_task = None
            logger.info("Admin handler task cancelled")

        # 2. Close admin WebSocket
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            logger.info("Admin WebSocket closed")

        # 3. Reconnect as non-admin worker
        actual_admin = self.admin_controller.admin_peer_id
        if not actual_admin or actual_admin == self.worker.peer_id:
            logger.error("Cannot reconnect: no valid admin peer_id")
            return

        try:
            import websockets

            if not self.websocket_dns:
                logger.error("Cannot reconnect: no DNS stored")
                return

            logger.info(f"Reopening WebSocket to connect to admin: {actual_admin}")
            self.websocket = await websockets.connect(self.websocket_dns)

            # Sync websocket references
            self.worker.websocket = self.websocket
            if self.worker.state_manager:
                self.worker.state_manager.websocket = self.websocket
                logger.info("Updated state_manager websocket reference")

            # 4. Re-register as non-admin worker
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "register",
                        "peer_id": self.worker.peer_id,
                        "room_id": self.worker.room_id,
                        "token": self.worker.room_token,
                        "id_token": self.worker.id_token,
                        "role": "worker",
                        "is_admin": False,  # Not admin anymore
                        "metadata": {
                            "tags": [
                                "sleap-rtc",
                                "training-worker",
                                "inference-worker",
                            ],
                            "properties": {
                                "gpu_memory_mb": self.worker.gpu_memory_mb,
                                "gpu_model": self.worker.gpu_model,
                                "status": self.worker.status,
                            },
                        },
                    }
                )
            )
            logger.info("Re-registered as non-admin worker")

            # 5. Start non-admin signaling handler (for mesh_answer, ice_candidate)
            self._start_non_admin_websocket_handler()

            # 6. Connect to actual admin
            success = await self.connect_to_admin(actual_admin)
            if success:
                logger.info(f"Successfully connected to actual admin: {actual_admin}")
            else:
                logger.error(f"Failed to connect to actual admin: {actual_admin}")

        except Exception as e:
            logger.error(f"Failed to reconnect as non-admin: {e}")

    def _start_non_admin_websocket_handler(self):
        """Start background task to handle WebSocket signaling messages (non-admin).

        Handles mesh_answer and ice_candidate messages for connection establishment
        after demotion from admin status.
        """
        # Cancel existing handler if any
        if self._non_admin_handler_task and not self._non_admin_handler_task.done():
            self._non_admin_handler_task.cancel()

        # Start new handler task
        self._non_admin_handler_task = asyncio.create_task(
            self._non_admin_websocket_handler_loop()
        )
        logger.info("Non-admin WebSocket handler started")

    async def _non_admin_websocket_handler_loop(self):
        """Background task that handles WebSocket messages for non-admin after demotion.

        Processes signaling messages needed to establish connection to actual admin:
        - mesh_answer: Connection answer from admin
        - ice_candidate: ICE candidates for mesh connections
        - registered_auth: Registration confirmation
        - error: Error messages from signaling server
        """
        logger.info("Starting non-admin WebSocket handler loop")

        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type")

                    if msg_type == "registered_auth":
                        logger.info(
                            f"Non-admin re-registration confirmed: {data.get('peer_id')} "
                            f"in room {data.get('room_id')}"
                        )

                    elif msg_type == "mesh_answer":
                        # Connection answer from admin
                        await self.handle_signaling_answer(data)

                    elif msg_type == "ice_candidate":
                        # ICE candidates for mesh connection
                        await self.handle_signaling_ice_candidate(data)

                    elif msg_type == "worker_joined":
                        # New worker notification (for future mesh expansion)
                        peer_id = data.get("peer_id")
                        logger.info(f"Worker joined notification: {peer_id}")

                    elif msg_type == "error":
                        logger.error(f"Non-admin received error: {data.get('reason')}")

                    else:
                        logger.debug(f"Non-admin ignoring message type: {msg_type}")

                except json.JSONDecodeError:
                    logger.error("Invalid JSON received in non-admin handler")
                except Exception as e:
                    logger.error(f"Error processing non-admin message: {e}")

        except asyncio.CancelledError:
            logger.info("Non-admin WebSocket handler cancelled")
        except Exception as e:
            logger.error(f"Non-admin WebSocket handler error: {e}")

    async def close_websocket_after_mesh_connection(self):
        """Close WebSocket after successfully connecting to mesh (non-admin only).

        This is called after a non-admin worker has connected to the admin
        and established initial mesh connectivity.
        """
        if self.admin_controller.is_admin:
            logger.info("Not closing WebSocket: this worker is admin")
            return

        if not self.websocket:
            logger.debug("WebSocket already closed")
            return

        logger.info("Closing WebSocket after mesh connection (non-admin)")
        await self.websocket.close()
        self.websocket = None

    def get_connected_peers(self) -> List[str]:
        """Get list of connected peer IDs.

        Returns:
            List of peer_ids we're connected to
        """
        return list(self.connected_peers)

    def is_connected_to_peer(self, peer_id: str) -> bool:
        """Check if connected to a specific peer.

        Args:
            peer_id: peer_id to check

        Returns:
            True if connected, False otherwise
        """
        return peer_id in self.connected_peers

    async def handle_signaling_offer(self, data: Dict[str, Any]):
        """Handle mesh offer received from signaling server.

        Called when admin receives connection offer from non-admin worker
        via signaling server (not via mesh).

        Args:
            data: Offer data from signaling server
                - from_peer_id: Offering worker's peer_id
                - offer: SDP offer
        """
        from_peer_id = data.get("from_peer_id")
        if not from_peer_id:
            logger.error("mesh_offer missing from_peer_id")
            return

        logger.info(f"Received mesh offer from signaling server: {from_peer_id}")

        try:
            # Create peer connection
            pc = RTCPeerConnection()

            # Set up datachannel event handler to receive incoming channel
            @pc.on("datachannel")
            def on_datachannel(channel):
                logger.info(
                    f"Received data channel from {from_peer_id}: {channel.label} "
                    f"(state: {channel.readyState})"
                )

                # Store data channel IMMEDIATELY - don't wait for on_open
                # The sending code will check readyState before sending
                self.worker.data_channels[from_peer_id] = channel
                logger.info(f"Stored data channel for {from_peer_id}")

                # Define admin duties to run when channel is ready
                def do_admin_duties():
                    logger.info(f"Data channel open with {from_peer_id}, performing admin duties")
                    # Admin duties:
                    # 1. Add new worker to CRDT (critical for consistent election)
                    # 2. Broadcast updated state to all workers
                    # 3. Send peer list to new worker
                    # 4. Notify existing workers of new peer
                    asyncio.create_task(self._do_admin_duties_async(from_peer_id, data))

                # Set up channel event handlers
                @channel.on("open")
                def on_open():
                    do_admin_duties()

                # If channel is ALREADY open, fire admin duties immediately
                # (on_open won't fire if channel opened before handler was registered)
                if channel.readyState == "open":
                    logger.info(f"Channel already open with {from_peer_id}, triggering admin duties")
                    do_admin_duties()

                @channel.on("close")
                def on_close():
                    logger.warning(f"Data channel closed with {from_peer_id}")
                    if from_peer_id in self.worker.data_channels:
                        del self.worker.data_channels[from_peer_id]

                @channel.on("error")
                def on_error(error):
                    logger.error(f"Data channel error with {from_peer_id}: {error}")

                @channel.on("message")
                async def on_message(message):
                    await self._handle_mesh_message(message, from_peer_id)

            # Set ICE connection state handler
            @pc.on("iceconnectionstatechange")
            async def on_ice_state_change():
                await self.worker.on_mesh_iceconnectionstatechange(from_peer_id, pc)

            # Set ICE candidate handler - send candidates to signaling server
            @pc.on("icecandidate")
            async def on_ice_candidate(event):
                if event.candidate and self.websocket:
                    await self.websocket.send(
                        json.dumps(
                            {
                                "type": "ice_candidate",
                                "from_peer_id": self.worker.peer_id,
                                "target_peer_id": from_peer_id,
                                "candidate": {
                                    "candidate": event.candidate.candidate,
                                    "sdpMLineIndex": event.candidate.sdpMLineIndex,
                                    "sdpMid": event.candidate.sdpMid,
                                },
                            }
                        )
                    )
                    logger.debug(
                        f"Sent ICE candidate to {from_peer_id} via signaling server"
                    )

            # Set remote description (offer)
            offer = RTCSessionDescription(
                sdp=data["offer"]["sdp"], type=data["offer"]["type"]
            )
            await pc.setRemoteDescription(offer)

            # Create answer
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            # Send answer back via signaling server
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "mesh_answer",
                        "from_peer_id": self.worker.peer_id,
                        "target_peer_id": from_peer_id,
                        "answer": {
                            "sdp": pc.localDescription.sdp,
                            "type": pc.localDescription.type,
                        },
                    }
                )
            )

            # Store connection
            self.worker.worker_connections[from_peer_id] = pc
            self.connected_peers.add(from_peer_id)

            logger.info(f"Sent answer to {from_peer_id} via signaling server")

        except Exception as e:
            logger.error(f"Failed to handle signaling offer: {e}")

    async def handle_signaling_answer(self, data: Dict[str, Any]):
        """Handle mesh answer received from signaling server.

        Called when non-admin receives connection answer from admin
        via signaling server (not via mesh).

        Args:
            data: Answer data from signaling server
                - from_peer_id: Admin's peer_id
                - answer: SDP answer
        """
        from_peer_id = data.get("from_peer_id")
        if not from_peer_id:
            logger.error("mesh_answer missing from_peer_id")
            return

        logger.info(f"Received mesh answer from signaling server: {from_peer_id}")

        try:
            # Get existing connection (should exist from connect_to_admin)
            if from_peer_id not in self.worker.worker_connections:
                logger.error(f"No pending connection for {from_peer_id}")
                return

            pc = self.worker.worker_connections[from_peer_id]

            # Set remote description (answer)
            answer = RTCSessionDescription(
                sdp=data["answer"]["sdp"], type=data["answer"]["type"]
            )
            await pc.setRemoteDescription(answer)

            logger.info(f"Connection established with {from_peer_id} via signaling")

        except Exception as e:
            logger.error(f"Failed to handle signaling answer: {e}")

    async def handle_signaling_ice_candidate(self, data: Dict[str, Any]):
        """Handle ICE candidate received from signaling server.

        Args:
            data: ICE candidate data from signaling server
                - from_peer_id: Sender's peer_id
                - candidate: ICE candidate
        """
        from_peer_id = data.get("from_peer_id")
        candidate_data = data.get("candidate")

        if not from_peer_id or not candidate_data:
            logger.error("ice_candidate missing from_peer_id or candidate")
            return

        logger.debug(f"Received ICE candidate from {from_peer_id}")

        try:
            # Get existing connection
            if from_peer_id not in self.worker.worker_connections:
                # Buffer candidate if connection not established yet
                if from_peer_id not in self.pending_ice_candidates:
                    self.pending_ice_candidates[from_peer_id] = []
                self.pending_ice_candidates[from_peer_id].append(candidate_data)
                logger.debug(f"Buffered ICE candidate for {from_peer_id}")
                return

            pc = self.worker.worker_connections[from_peer_id]

            # Add ICE candidate to connection
            candidate = RTCIceCandidate(
                candidate=candidate_data.get("candidate"),
                sdpMLineIndex=candidate_data.get("sdpMLineIndex"),
                sdpMid=candidate_data.get("sdpMid"),
            )
            await pc.addIceCandidate(candidate)

            logger.debug(f"Added ICE candidate from {from_peer_id}")

            # Check if we have buffered candidates for this peer
            if from_peer_id in self.pending_ice_candidates:
                for buffered in self.pending_ice_candidates[from_peer_id]:
                    buffered_candidate = RTCIceCandidate(
                        candidate=buffered.get("candidate"),
                        sdpMLineIndex=buffered.get("sdpMLineIndex"),
                        sdpMid=buffered.get("sdpMid"),
                    )
                    await pc.addIceCandidate(buffered_candidate)
                    logger.debug(f"Added buffered ICE candidate for {from_peer_id}")

                del self.pending_ice_candidates[from_peer_id]

        except Exception as e:
            logger.error(f"Failed to handle ICE candidate: {e}")
