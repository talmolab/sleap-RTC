#!/usr/bin/env python3
"""Simple test to register multiple workers and query room status."""

import asyncio
import json
import requests
import websockets
import sys


async def test_multi_worker():
    """Test registering two workers in the same room."""

    # Configuration
    ws_url = "ws://localhost:8080"
    http_base = "http://localhost:8001"

    print("\n🧪 Multi-Worker Room Test\n")
    print("=" * 60)

    # Step 1: Create room and get credentials
    print("\n📦 Step 1: Creating shared room...")

    response = requests.post(f"{http_base}/anonymous-signin")
    creator_creds = response.json()
    print(f"  ✓ Signed in as: {creator_creds['username'][:8]}...")

    headers = {"Authorization": f"Bearer {creator_creds['id_token']}"}
    response = requests.post(f"{http_base}/create-room", headers=headers)
    room = response.json()
    print(f"  ✓ Room created: {room['room_id']} (token: {room['token']})")

    # Step 2: Register two workers
    print(f"\n👷 Step 2: Registering 2 workers in room {room['room_id']}...")

    workers = []
    for i in range(1, 3):
        # Get credentials
        response = requests.post(f"{http_base}/anonymous-signin")
        creds = response.json()

        # Connect and register
        ws = await websockets.connect(ws_url)

        registration = {
            "type": "register",
            "peer_id": creds["username"],
            "room_id": room["room_id"],
            "token": room["token"],
            "id_token": creds["id_token"],
            "role": "worker",
            "metadata": {
                "tags": [f"test-worker-{i}"],
                "properties": {
                    "worker_number": i,
                    "gpu_memory_mb": 8192 * i,
                    "status": "available"
                }
            }
        }

        await ws.send(json.dumps(registration))
        response_msg = await ws.recv()
        response_data = json.loads(response_msg)

        if response_data.get("type") == "registered_auth":
            print(f"  ✓ Worker {i} registered: {creds['username'][:8]}...")
            workers.append({"peer_id": creds["username"], "ws": ws, "creds": creds})
        else:
            print(f"  ✗ Worker {i} failed: {response_data}")
            await ws.close()

    # Give time for registration to complete
    await asyncio.sleep(1)

    # Step 3: Query room status via discover_peers
    print(f"\n🔍 Step 3: Discovering workers in room {room['room_id']}...")

    # Register a client to do the discovery
    response = requests.post(f"{http_base}/anonymous-signin")
    client_creds = response.json()

    client_ws = await websockets.connect(ws_url)
    await client_ws.send(json.dumps({
        "type": "register",
        "peer_id": client_creds["username"],
        "room_id": room["room_id"],
        "token": room["token"],
        "id_token": client_creds["id_token"],
        "role": "client"
    }))

    # Wait for registration
    reg_response = await client_ws.recv()
    print(f"  ✓ Client registered: {client_creds['username'][:8]}...")

    # Discover workers
    await client_ws.send(json.dumps({
        "type": "discover_peers",
        "from_peer_id": client_creds["username"],
        "filters": {"role": "worker"}
    }))

    # Get peer list
    peer_list_msg = await client_ws.recv()
    peer_list = json.loads(peer_list_msg)

    # Display results
    print("\n" + "=" * 60)
    print(f"ROOM STATUS: {room['room_id']}")
    print("=" * 60)

    if peer_list.get("type") == "peer_list":
        count = peer_list.get("count", 0)
        print(f"\n✅ Found {count} worker(s) in room:\n")

        for i, peer in enumerate(peer_list.get("peers", []), 1):
            print(f"Worker {i}:")
            print(f"  Peer ID: {peer['peer_id']}")
            print(f"  Role: {peer['role']}")
            print(f"  Tags: {peer.get('metadata', {}).get('tags', [])}")
            props = peer.get('metadata', {}).get('properties', {})
            print(f"  Properties:")
            for key, value in props.items():
                print(f"    - {key}: {value}")
            print()
    else:
        print(f"❌ Unexpected response: {peer_list}")

    # Step 4: Query global metrics
    print("=" * 60)
    print("GLOBAL METRICS")
    print("=" * 60)

    response = requests.get(f"{http_base}/metrics")
    metrics = response.json()
    print(f"\nActive rooms: {metrics.get('active_rooms', 0)}")
    print(f"Active connections: {metrics.get('active_connections', 0)}")
    print(f"Peers by role: {json.dumps(metrics.get('peers_by_role', {}), indent=2)}")

    print("\n" + "=" * 60)

    # Cleanup
    print("\n🧹 Cleaning up...")
    await client_ws.close()
    for w in workers:
        await w["ws"].close()

    print("✅ Test complete!\n")


if __name__ == "__main__":
    try:
        asyncio.run(test_multi_worker())
    except KeyboardInterrupt:
        print("\n\n🛑 Test interrupted")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
