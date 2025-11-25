# Worker-Signaling Server Handoff

This document summarizes the **completed worker-side implementation** and the **required signaling server changes** for the mesh networking architecture.

---

## ✅ Worker-Side Implementation (COMPLETE)

All mesh networking features are now implemented on the worker side:

### Phase 2-5: Core Mesh Architecture
- ✅ CRDT state management (pycrdt)
- ✅ Admin election (deterministic)
- ✅ Connection registries (client/worker separation)
- ✅ Mesh coordinator (batched connections)
- ✅ Heartbeat mechanism (5s interval, 15s timeout)
- ✅ State broadcasting (admin → workers)
- ✅ Network partition detection and recovery
- ✅ Exponential backoff retry (1s → 32s)

### Phase 6: Data Channel & ICE
- ✅ Data channel tracking per peer
- ✅ Message sending via data channels
- ✅ Data channel lifecycle handlers (open/close/error)
- ✅ ICE candidate gathering (3 paths)
- ✅ Admin conflict handling

---

## 📋 Signaling Server Requirements (TO IMPLEMENT)

The signaling server needs these changes to work with the updated worker code:

### Required Changes (Phase 1 - Core)

#### 1. Add State Variables
```python
# Track admin per room
room_admins = {}  # room_id -> admin_peer_id

# Track all peers in room
room_peers = {}  # room_id -> Set[peer_id]

# Store peer metadata
peer_metadata_store = {}  # (room_id, peer_id) -> metadata
```

#### 2. Accept `is_admin` Field in Registration
**Message from worker:**
```json
{
  "type": "register",
  "peer_id": "worker-1",
  "room_id": "room-123",
  "token": "token-abc",
  "role": "worker",
  "is_admin": true,  // NEW
  "metadata": { ... }
}
```

**Server handling:**
```python
async def handle_register(websocket, data):
    peer_id = data["peer_id"]
    room_id = data["room_id"]
    is_admin = data.get("is_admin", False)
    metadata = data.get("metadata", {})

    # Track peer
    if room_id not in room_peers:
        room_peers[room_id] = set()
    room_peers[room_id].add(peer_id)

    # Store metadata
    peer_metadata_store[(room_id, peer_id)] = metadata

    # Handle admin registration
    if is_admin:
        current_admin = room_admins.get(room_id)

        if current_admin and current_admin != peer_id:
            # CONFLICT: Another admin exists
            await websocket.send(json.dumps({
                "type": "admin_conflict",
                "room_id": room_id,
                "current_admin": current_admin,
            }))
            # Don't update room_admins
        else:
            # First admin or re-registration
            room_admins[room_id] = peer_id

    # ... continue with normal registration ...
```

#### 3. Return Discovery Info in Registration Response
**Response to worker:**
```json
{
  "type": "registered_auth",
  "room_id": "room-123",
  "token": "token-abc",
  "peer_id": "worker-1",
  "admin_peer_id": "worker-2",  // NEW: Who is admin
  "peer_list": ["worker-2", "worker-3"],  // NEW: Other workers
  "peer_metadata": {  // NEW: Optional
    "worker-2": { "gpu_memory_mb": 24000, "status": "available" },
    "worker-3": { "gpu_memory_mb": 16000, "status": "busy" }
  }
}
```

**Server implementation:**
```python
async def send_registration_response(websocket, peer_id, room_id, token):
    admin_peer_id = room_admins.get(room_id)
    peer_list = list(room_peers.get(room_id, set()))

    # Optional: Include metadata
    peer_metadata = {}
    for pid in peer_list:
        peer_metadata[pid] = peer_metadata_store.get((room_id, pid), {})

    await websocket.send(json.dumps({
        "type": "registered_auth",
        "room_id": room_id,
        "token": token,
        "peer_id": peer_id,
        "admin_peer_id": admin_peer_id,  # NEW
        "peer_list": peer_list,  # NEW
        "peer_metadata": peer_metadata,  # NEW (optional)
    }))
```

#### 4. Handle `mesh_connect` Message (Relay Offer)
**Message from worker:**
```json
{
  "type": "mesh_connect",
  "from_peer_id": "worker-3",
  "target_peer_id": "worker-2",
  "offer": {
    "sdp": "...",
    "type": "offer"
  }
}
```

**Server relay:**
```python
async def handle_mesh_connect(websocket, data):
    """Relay mesh connection offer to target peer."""
    target_peer_id = data["target_peer_id"]
    target_websocket = get_peer_websocket(target_peer_id)

    if not target_websocket:
        await websocket.send(json.dumps({
            "type": "error",
            "reason": "peer_not_found",
            "target_peer_id": target_peer_id,
        }))
        return

    # Relay as mesh_offer
    await target_websocket.send(json.dumps({
        "type": "mesh_offer",
        "from_peer_id": data["from_peer_id"],
        "offer": data["offer"],
    }))
```

#### 5. Handle `mesh_answer` Message (Relay Answer)
**Message from admin worker:**
```json
{
  "type": "mesh_answer",
  "from_peer_id": "worker-2",
  "target_peer_id": "worker-3",
  "answer": {
    "sdp": "...",
    "type": "answer"
  }
}
```

**Server relay:**
```python
async def handle_mesh_answer(websocket, data):
    """Relay mesh connection answer to original peer."""
    target_peer_id = data["target_peer_id"]
    target_websocket = get_peer_websocket(target_peer_id)

    if target_websocket:
        await target_websocket.send(json.dumps({
            "type": "mesh_answer",
            "from_peer_id": data["from_peer_id"],
            "answer": data["answer"],
        }))
```

#### 6. Handle `ice_candidate` Message (Relay ICE)
**Message from worker:**
```json
{
  "type": "ice_candidate",
  "from_peer_id": "worker-3",
  "target_peer_id": "worker-2",
  "candidate": {
    "candidate": "candidate:1 1 UDP...",
    "sdpMLineIndex": 0,
    "sdpMid": "0"
  }
}
```

**Server relay:**
```python
async def handle_ice_candidate(websocket, data):
    """Relay ICE candidate to target peer."""
    target_peer_id = data["target_peer_id"]
    target_websocket = get_peer_websocket(target_peer_id)

    if target_websocket:
        await target_websocket.send(json.dumps({
            "type": "ice_candidate",
            "from_peer_id": data["from_peer_id"],
            "candidate": data["candidate"],
        }))
```

#### 7. Update Disconnect Handling
```python
async def handle_peer_disconnect(peer_id, room_id):
    # Remove from peer list
    if room_id in room_peers:
        room_peers[room_id].discard(peer_id)

    # Clean up metadata
    if (room_id, peer_id) in peer_metadata_store:
        del peer_metadata_store[(room_id, peer_id)]

    # If this was admin, clear admin mapping
    if room_admins.get(room_id) == peer_id:
        del room_admins[room_id]
        logger.info(f"Room {room_id}: admin {peer_id} disconnected")

    # Clean up empty rooms
    if not room_peers[room_id]:
        del room_peers[room_id]
        if room_id in room_admins:
            del room_admins[room_id]
```

---

## 🔄 Message Flow Examples

### Example 1: Worker-2 Joins Room (Worker-1 is Admin)

**1. Worker-2 → Signaling Server**
```json
{
  "type": "register",
  "peer_id": "worker-2",
  "room_id": "room-123",
  "role": "worker"
}
```

**2. Signaling Server → Worker-2**
```json
{
  "type": "registered_auth",
  "peer_id": "worker-2",
  "admin_peer_id": "worker-1",  // Discover admin
  "peer_list": ["worker-1"]
}
```

**3. Worker-2 → Signaling Server**
```json
{
  "type": "mesh_connect",
  "from_peer_id": "worker-2",
  "target_peer_id": "worker-1",
  "offer": { "sdp": "...", "type": "offer" }
}
```

**4. Signaling Server → Worker-1 (Admin)**
```json
{
  "type": "mesh_offer",
  "from_peer_id": "worker-2",
  "offer": { "sdp": "...", "type": "offer" }
}
```

**5. Worker-1 → Signaling Server**
```json
{
  "type": "mesh_answer",
  "from_peer_id": "worker-1",
  "target_peer_id": "worker-2",
  "answer": { "sdp": "...", "type": "answer" }
}
```

**6. Signaling Server → Worker-2**
```json
{
  "type": "mesh_answer",
  "from_peer_id": "worker-1",
  "answer": { "sdp": "...", "type": "answer" }
}
```

**7-N. ICE Candidate Exchange**
```json
// Worker-2 → Server → Worker-1
{ "type": "ice_candidate", "from_peer_id": "worker-2", "target_peer_id": "worker-1", "candidate": {...} }

// Worker-1 → Server → Worker-2
{ "type": "ice_candidate", "from_peer_id": "worker-1", "target_peer_id": "worker-2", "candidate": {...} }
```

**Result:** WebRTC connection established, Worker-2 closes WebSocket

---

### Example 2: Admin Conflict (Race Condition)

**Scenario:** Worker-1 and Worker-2 both try to become admin simultaneously

**1. Worker-1 registers first**
```json
{ "type": "register", "peer_id": "worker-1", "is_admin": true }
```
→ Server sets `room_admins["room-123"] = "worker-1"`

**2. Worker-2 registers second**
```json
{ "type": "register", "peer_id": "worker-2", "is_admin": true }
```
→ Server detects conflict, sends:
```json
{
  "type": "admin_conflict",
  "room_id": "room-123",
  "current_admin": "worker-1"
}
```

**3. Worker-2 handles conflict**
- Demotes itself to non-admin
- Updates `admin_peer_id = "worker-1"`
- Closes WebSocket (if opened)

---

## 📊 Implementation Checklist

Use this checklist to track your signaling server implementation:

### Core Functionality (REQUIRED)
- [ ] Add `room_admins`, `room_peers`, `peer_metadata_store` state variables
- [ ] Accept `is_admin` field in registration
- [ ] Detect admin conflicts and send `admin_conflict` message
- [ ] Return `admin_peer_id` and `peer_list` in `registered_auth` response
- [ ] Handle `mesh_connect` and relay as `mesh_offer`
- [ ] Handle `mesh_answer` and relay to target
- [ ] Handle `ice_candidate` and relay to target
- [ ] Update disconnect handling to clean up admin/peers/metadata

### Optional Enhancements (Phase 2+)
- [ ] Return `peer_metadata` in registration response
- [ ] Implement WebSocket keep-alive for admin connections
- [ ] Add `admin_changed` notification broadcast

### Testing
- [ ] Test worker registration with admin discovery
- [ ] Test mesh connection negotiation (offer/answer/ICE)
- [ ] Test admin conflict handling
- [ ] Test admin disconnect and re-election
- [ ] Test multiple rooms with different admins

---

## 🚀 After Implementation

Once you've implemented these changes in your signaling server:

1. **Test with Single Worker:**
   - Worker becomes admin
   - Keeps WebSocket open
   - No mesh connections yet

2. **Test with Two Workers:**
   - Worker-1 becomes admin
   - Worker-2 discovers Worker-1
   - Workers establish mesh connection
   - Worker-2 closes WebSocket

3. **Test with Multiple Workers:**
   - Admin coordinates mesh formation
   - Batched connections (3 at a time)
   - All workers maintain CRDT state
   - Heartbeats and state broadcasts work

4. **Test Admin Departure:**
   - Admin disconnects
   - Remaining workers detect loss
   - New admin elected
   - New admin reconnects to signaling server

---

## 📚 Reference Documents

- `SIGNALING_SERVER_REQUIREMENTS.md` - Detailed server requirements
- `CONNECTION_REGISTRY_DESIGN.md` - Worker architecture overview
- `sleap_rtc/worker/mesh_coordinator.py` - Mesh protocol implementation
- `sleap_rtc/worker/admin_controller.py` - Admin election logic
- `sleap_rtc/worker/mesh_messages.py` - Message format definitions

---

**Status:** Worker implementation complete ✅ | Signaling server ready for implementation 🚀
