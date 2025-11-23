"""Worker state management and signaling server interactions.

This module handles worker registration, status updates, room management,
and all HTTP API interactions with the signaling server for SLEAP-RTC workers.
"""

import base64
import json
import logging
import socket
from typing import TYPE_CHECKING, Optional

import requests

from sleap_rtc.config import get_config

if TYPE_CHECKING:
    from websockets.client import ClientConnection
    from sleap_rtc.worker.capabilities import WorkerCapabilities


class StateManager:
    """Manages worker registration, status, and signaling server interactions.

    This class handles worker lifecycle management including authentication,
    room creation, registration with the signaling server, and status updates.

    Attributes:
        worker_id: Unique worker identifier (Cognito username).
        websocket: WebSocket connection to signaling server.
        capabilities: WorkerCapabilities instance for metadata.
        status: Current worker status ("available", "busy", "reserved", "maintenance").
        room_id: Room ID for this worker.
        room_token: Room authentication token.
        id_token: Cognito ID token for API authentication.
        max_concurrent_jobs: Maximum concurrent jobs this worker can handle.
    """

    def __init__(
        self,
        worker_id: str,
        websocket: "ClientConnection",
        capabilities: "WorkerCapabilities",
        max_concurrent_jobs: int = 1,
    ):
        """Initialize state manager.

        Args:
            worker_id: Unique worker identifier (Cognito username).
            websocket: WebSocket connection to signaling server.
            capabilities: WorkerCapabilities instance for metadata.
            max_concurrent_jobs: Maximum concurrent jobs (default 1).
        """
        self.worker_id = worker_id
        self.websocket = websocket
        self.capabilities = capabilities
        self.max_concurrent_jobs = max_concurrent_jobs

        # Worker state
        self.status = "available"

        # Room credentials (set during registration)
        self.room_id: Optional[str] = None
        self.room_token: Optional[str] = None
        self.id_token: Optional[str] = None

    async def update_status(self, status: str, **extra_properties):
        """Update worker status in signaling server.

        Args:
            status: "available", "busy", "reserved", or "maintenance".
            **extra_properties: Additional properties to update (e.g., current_job_id).
        """
        self.status = status
        self.capabilities.status = status  # Keep capabilities in sync

        metadata = {"properties": {"status": status, **extra_properties}}

        try:
            # Send metadata update to signaling server
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "update_metadata",
                        "peer_id": self.worker_id,
                        "metadata": metadata,
                    }
                )
            )

            # Response handled by websocket in handle_connection()

        except Exception as e:
            logging.error(f"Failed to update status: {e}")

    async def reregister_worker(self):
        """Re-register worker with signaling server to become discoverable again.

        Called after resetting from a client disconnect to ensure the worker
        appears in discovery queries.
        """
        # Get SLEAP version
        try:
            import sleap

            sleap_version = sleap.__version__
        except (ImportError, AttributeError):
            sleap_version = "unknown"

        try:
            # Send full registration message (not just metadata update)
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "register",
                        "peer_id": self.worker_id,
                        "room_id": self.room_id,
                        "token": self.room_token,
                        "id_token": self.id_token,  # Required for authentication
                        "role": "worker",
                        "metadata": {
                            "tags": [
                                "sleap-rtc",
                                "training-worker",
                                "inference-worker",
                            ],
                            "properties": {
                                "gpu_memory_mb": self.capabilities.gpu_memory_mb,
                                "gpu_model": self.capabilities.gpu_model,
                                "sleap_version": sleap_version,
                                "cuda_version": self.capabilities.cuda_version,
                                "hostname": socket.gethostname(),
                                "status": self.status,
                                "max_concurrent_jobs": self.max_concurrent_jobs,
                                "supported_models": self.capabilities.supported_models,
                                "supported_job_types": self.capabilities.supported_job_types,
                            },
                        },
                    }
                )
            )
            logging.info("Worker re-registered with signaling server")
        except Exception as e:
            logging.error(f"Failed to re-register worker: {e}")

    def generate_session_string(self, room_id: str, token: str, peer_id: str) -> str:
        """Generate an encoded session string for the room.

        Args:
            room_id: Room identifier.
            token: Room authentication token.
            peer_id: Peer identifier.

        Returns:
            Encoded session string in format "sleap-session:<base64>".
        """
        session_data = {"r": room_id, "t": token, "p": peer_id}
        encoded = base64.urlsafe_b64encode(json.dumps(session_data).encode()).decode()

        return f"sleap-session:{encoded}"

    def request_peer_room_deletion(self, peer_id: str):
        """Request signaling server to delete the room and associated peer.

        Args:
            peer_id: Peer ID to delete (Cognito username).

        Returns:
            None on success, otherwise None with error logged.
        """
        config = get_config()
        url = config.get_http_endpoint("/delete-peer")
        payload = {
            "peer_id": peer_id,
        }

        # Pass the Cognito username (peer_id) to identify which room/peers to delete.
        response = requests.post(url, json=payload)

        if response.status_code == 200:
            return  # Success
        else:
            logging.error(f"Failed to delete room and peer: {response.text}")
            return None

    @staticmethod
    def request_create_room(id_token: str) -> dict:
        """Request signaling server to create a room.

        Args:
            id_token: Cognito ID token for authentication.

        Returns:
            Dictionary with room_id and token if successful.

        Raises:
            Exception: If room creation fails.
        """
        config = get_config()
        url = config.get_http_endpoint("/create-room")
        headers = {
            "Authorization": f"Bearer {id_token}"
        }  # Use the ID token string for authentication

        response = requests.post(url, headers=headers)

        if response.status_code == 200:
            return response.json()
        else:
            logging.error(
                f"Failed to create room: {response.status_code} - {response.text}"
            )
            raise Exception("Failed to create room")

    @staticmethod
    def request_anonymous_signin() -> Optional[dict]:
        """Request anonymous sign-in from signaling server.

        Returns:
            Dictionary with id_token and username if successful, None otherwise.
        """
        config = get_config()
        url = config.get_http_endpoint("/anonymous-signin")
        response = requests.post(url)

        if response.status_code == 200:
            return response.json()
        else:
            logging.error(f"Failed to get anonymous token: {response.text}")
            return None

    def set_room_credentials(self, room_id: str, token: str, id_token: str):
        """Set room credentials for re-registration.

        Args:
            room_id: Room identifier.
            token: Room authentication token.
            id_token: Cognito ID token.
        """
        self.room_id = room_id
        self.room_token = token
        self.id_token = id_token

    def get_status(self) -> str:
        """Get current worker status.

        Returns:
            Current status string.
        """
        return self.status
