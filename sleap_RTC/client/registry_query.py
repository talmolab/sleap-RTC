"""Client module for querying remote worker model registries."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
import websockets

from sleap_rtc.config import get_config


class RegistryQueryClient:
    """Client for querying model registry on remote workers.

    This lightweight client connects to a worker via WebRTC and queries
    the worker's local model registry without transferring files or running
    training/inference jobs.
    """

    def __init__(self):
        """Initialize registry query client."""
        self.pc = None
        self.channel = None
        self.response_received = asyncio.Event()
        self.response_data = None
        self.response_error = None

    async def connect_and_query(
        self,
        query_type: str,
        query_payload: str = "",
        session_string: Optional[str] = None,
        room_id: Optional[str] = None,
        token: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> Dict:
        """Connect to worker and execute registry query.

        Args:
            query_type: Type of query ("list" or "info")
            query_payload: Payload for the query (filters JSON or model ID)
            session_string: Session string for direct worker connection
            room_id: Room ID for room-based connection
            token: Room token for authentication
            worker_id: Specific worker to connect to (optional)

        Returns:
            Dictionary containing query response

        Raises:
            ValueError: If connection parameters are invalid
            RuntimeError: If query fails or times out
        """
        # Decode session string if provided
        if session_string:
            room_id, token, worker_peer_id = self._parse_session_string(session_string)
            worker_id = worker_peer_id

        if not room_id or not token:
            raise ValueError("Must provide either session_string or (room_id + token)")

        config = get_config()

        # Create peer connection
        self.pc = RTCPeerConnection()

        # Create data channel for registry queries
        self.channel = self.pc.createDataChannel("registry")
        self._setup_channel_handlers(query_type, query_payload)

        # Register anonymous signin
        sign_in_response = await self._anonymous_signin()
        client_peer_id = sign_in_response['username']
        id_token = sign_in_response['id_token']

        # Connect to signaling server
        async with websockets.connect(config.signaling_websocket) as websocket:
            # Register with signaling server
            await websocket.send(json.dumps({
                'type': 'register',
                'peer_id': client_peer_id,
                'room_id': room_id,
                'token': token,
                'id_token': id_token,
                'role': 'client'
            }))

            # Wait for registration confirmation
            response = await websocket.recv()
            data = json.loads(response)

            if data.get('type') != 'registered_auth':
                raise RuntimeError(f"Registration failed: {data}")

            logging.info(f"Registered as {client_peer_id} in room {room_id}")

            # If worker_id specified, connect directly
            # Otherwise, discover workers and pick first one
            if not worker_id:
                # Request worker list using list_peers message
                await websocket.send(json.dumps({
                    'type': 'list_peers',
                    'peer_id': client_peer_id
                }))

                # Get worker list
                response = await websocket.recv()
                data = json.loads(response)

                if data.get('type') == 'peer_list':
                    # Filter for workers only
                    all_peers = data.get('peers', [])
                    workers = [p for p in all_peers if p.get('role') == 'worker']

                    if not workers:
                        raise RuntimeError("No workers available in room")

                    # Pick first available worker
                    worker_id = workers[0]['peer_id']
                    logging.info(f"Selected worker: {worker_id}")
                elif data.get('type') == 'error':
                    error_msg = data.get('message', 'Unknown error')
                    raise RuntimeError(f"Worker discovery failed: {error_msg}. Try specifying --worker-id directly.")
                else:
                    raise RuntimeError(f"Unexpected response from signaling server: {data.get('type')}")

            # Create offer and connect to worker
            await self.pc.setLocalDescription(await self.pc.createOffer())

            # Send offer to worker via signaling server
            await websocket.send(json.dumps({
                'type': 'offer',
                'sender': client_peer_id,
                'target': worker_id,
                'sdp': self.pc.localDescription.sdp
            }))

            logging.info(f"Sent offer to worker {worker_id}")

            # Wait for answer from worker
            while True:
                message = await websocket.recv()
                data = json.loads(message)

                if data.get('type') == 'answer' and data.get('sender') == worker_id:
                    # Set remote description
                    await self.pc.setRemoteDescription(
                        RTCSessionDescription(sdp=data['sdp'], type='answer')
                    )
                    logging.info("Connection established with worker")
                    break
                elif data.get('type') == 'error':
                    raise RuntimeError(f"Worker error: {data.get('reason')}")

            # Wait for response from worker (with timeout)
            try:
                await asyncio.wait_for(self.response_received.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                raise RuntimeError("Registry query timed out after 10 seconds")

            # Close connection
            await self.pc.close()

            # Return response or raise error
            if self.response_error:
                raise RuntimeError(f"Registry query failed: {self.response_error}")

            return self.response_data

    def _setup_channel_handlers(self, query_type: str, query_payload: str):
        """Setup data channel event handlers for registry query."""

        @self.channel.on("open")
        def on_open():
            """Send query when channel opens."""
            logging.info(f"Data channel open, sending {query_type} query")

            if query_type == "list":
                self.channel.send(f"REGISTRY_QUERY_LIST::{query_payload}")
            elif query_type == "info":
                self.channel.send(f"REGISTRY_QUERY_INFO::{query_payload}")
            else:
                raise ValueError(f"Invalid query type: {query_type}")

        @self.channel.on("message")
        def on_message(message):
            """Handle response from worker."""
            logging.info(f"Received response: {message[:100]}...")

            if message.startswith("REGISTRY_RESPONSE_LIST::"):
                _, response_json = message.split("REGISTRY_RESPONSE_LIST::", 1)
                self.response_data = json.loads(response_json)
                self.response_received.set()

            elif message.startswith("REGISTRY_RESPONSE_INFO::"):
                _, response_json = message.split("REGISTRY_RESPONSE_INFO::", 1)
                self.response_data = json.loads(response_json)
                self.response_received.set()

            elif message.startswith("REGISTRY_RESPONSE_ERROR::"):
                _, error_json = message.split("REGISTRY_RESPONSE_ERROR::", 1)
                try:
                    error_data = json.loads(error_json)
                    self.response_error = error_data.get('error', 'Unknown error')
                except json.JSONDecodeError:
                    self.response_error = error_json
                self.response_received.set()

    def _parse_session_string(self, session_string: str) -> tuple:
        """Parse session string into room_id, token, and peer_id.

        Args:
            session_string: Encoded session string (sleap-session:BASE64)

        Returns:
            Tuple of (room_id, token, peer_id)
        """
        import base64

        if not session_string.startswith("sleap-session:"):
            raise ValueError("Invalid session string format")

        encoded = session_string.split("sleap-session:", 1)[1]
        decoded = base64.urlsafe_b64decode(encoded).decode()
        session_data = json.loads(decoded)

        return (
            session_data['r'],  # room_id
            session_data['t'],  # token
            session_data['p']   # peer_id
        )

    async def _anonymous_signin(self) -> Dict:
        """Sign in anonymously to get credentials.

        Returns:
            Dictionary with 'username' and 'id_token'
        """
        import asyncio
        import requests

        config = get_config()
        url = config.get_http_endpoint("/anonymous-signin")

        # Run synchronous requests.post in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: requests.post(url))

        if response.status_code == 200:
            return response.json()
        else:
            raise RuntimeError(f"Anonymous signin failed: {response.text}")


async def query_registry_list(
    filters: Optional[Dict] = None,
    **connection_params
) -> List[Dict]:
    """Query worker for list of models in registry.

    Args:
        filters: Optional filters (status, model_type)
        **connection_params: Connection params (session_string or room_id/token)

    Returns:
        List of model dictionaries
    """
    client = RegistryQueryClient()
    filters_json = json.dumps(filters) if filters else ""

    response = await client.connect_and_query(
        query_type="list",
        query_payload=filters_json,
        **connection_params
    )

    return response.get('models', [])


async def query_registry_info(
    model_id: str,
    **connection_params
) -> Dict:
    """Query worker for detailed model information.

    Args:
        model_id: Model ID to query
        **connection_params: Connection params (session_string or room_id/token)

    Returns:
        Model metadata dictionary
    """
    client = RegistryQueryClient()

    response = await client.connect_and_query(
        query_type="info",
        query_payload=model_id,
        **connection_params
    )

    return response.get('model', {})
