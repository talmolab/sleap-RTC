# Room-Level Registry Design Document

## Context

Current worker discovery architecture:
- Client sends HTTP request to signaling server `/discover-workers`
- Signaling server queries DynamoDB for workers in room
- Response includes list of available workers with metadata
- Client selects worker, establishes WebRTC connection for job execution

**Problems with current approach**:
1. **Signaling server bottleneck**: Every client discovery = HTTP request + DB query
2. **Scalability**: Signaling server load scales linearly with clients
3. **Latency**: ~100ms HTTP roundtrip per discovery
4. **Cost**: DynamoDB query charges for every discovery
5. **Single point of failure**: Signaling server down = no discovery

**Observation**: Workers in same room could coordinate directly via WebRTC after initial handshake, eliminating signaling server dependency.

## Goals / Non-Goals

### Goals
- **Decentralize worker discovery**: Workers coordinate via peer-to-peer mesh
- **Reduce signaling server load**: 80% reduction in HTTP traffic
- **Lower latency**: <10ms discovery (vs ~100ms HTTP)
- **Eliminate DynamoDB costs**: No queries for worker discovery
- **Fault tolerance**: Admin failure handled automatically via re-election
- **Maintain compatibility**: Gradual migration, no breaking changes

### Non-Goals
- Replacing signaling server entirely (still needed for initial WebRTC handshake)
- Supporting >10 workers per room (full mesh becomes impractical)
- Implementing consensus algorithms (Raft, Paxos) - too complex for this use case
- Real-time state synchronization (<100ms) - eventual consistency is acceptable

## Decisions

### Decision 1: Full Mesh Topology ✅

**Choice**: All workers establish WebRTC connections to all other workers in room

**Alternatives Considered**:
- **Star topology** (all workers connect only to admin)
  - ❌ Admin becomes bottleneck
  - ❌ Admin failure breaks entire mesh
  - ✅ Fewer connections (N-1 instead of N*(N-1)/2)

- **Hierarchical** (admin + worker subgroups)
  - ❌ Complex to implement
  - ❌ Overkill for 8-10 workers
  - ✅ Scales to 50+ workers

- **Gossip protocol** (workers relay state to random subsets)
  - ❌ Eventual consistency too slow
  - ❌ State divergence risk
  - ✅ Scales to 100+ workers

**Rationale for full mesh**:
- Simple to implement and reason about
- Sufficient for target use case (8-10 workers)
- Direct communication = lowest latency
- No single point of failure (after initial setup)

**Trade-off**: N*(N-1)/2 connections scales poorly
- 5 workers = 10 connections ✅
- 10 workers = 45 connections ⚠️
- 20 workers = 190 connections ❌

**Mitigation**: Hard limit of 10 workers per room (enforced by signaling server)

---

### Decision 2: CRDT State Synchronization ✅

**Choice**: Use automerge library for conflict-free replicated data type (CRDT)

**Alternatives Considered**:
- **Manual last-write-wins** (Lamport timestamps)
  - ❌ Requires custom conflict resolution logic
  - ❌ Risk of data loss on concurrent updates
  - ✅ Simpler, no external dependency

- **Operational transformation** (OT)
  - ❌ Complex to implement correctly
  - ❌ Not suitable for structured data (better for text)
  - ✅ Real-time convergence

- **Centralized state** (admin is source of truth, no replication)
  - ❌ Admin failure = state loss
  - ❌ Requires all updates go through admin
  - ✅ No conflict resolution needed

**Rationale for CRDT (automerge)**:
- Battle-tested library (used in production by many companies)
- Automatic conflict resolution (no manual logic needed)
- Guarantees eventual consistency
- Supports structured data (maps, lists, nested objects)
- Mature Python implementation (`automerge-py`)

**Trade-off**: External dependency, learning curve

**Mitigation**:
- Automerge is stable and well-maintained
- Simple API for our use case (mostly `update()` and `merge()`)
- Comprehensive tests ensure correct usage

---

### Decision 3: Deterministic Admin Election ✅

**Choice**: Admin elected by `(highest_gpu_memory, lowest_peer_id)` priority

**Alternatives Considered**:
- **Raft-based election** (voting rounds, term numbers)
  - ❌ Over-engineered for full mesh (everyone sees same state)
  - ❌ Requires multiple network roundtrips
  - ✅ Proven correctness guarantees

- **First-to-claim** (first worker to detect admin failure claims role)
  - ❌ Race condition = split-brain
  - ❌ Requires tie-breaking via CRDT timestamp (complex)
  - ✅ Fastest election (no coordination needed)

- **Random selection** (pick random worker from list)
  - ❌ Non-deterministic (workers may disagree)
  - ❌ Doesn't utilize worker capabilities
  - ✅ Simple to implement

- **Oldest worker** (longest uptime)
  - ❌ Requires tracking join time
  - ❌ Doesn't utilize worker capabilities
  - ✅ Stable (less frequent re-elections)

**Rationale for deterministic priority**:
- All workers compute same result independently (no network communication)
- GPU memory is meaningful: strongest worker becomes coordinator
- Tiebreaker (peer_id) ensures uniqueness
- Simple to implement and test

**Implementation**:
```python
def elect_admin(workers: dict) -> str:
    candidates = [
        (w["metadata"]["properties"]["gpu_memory_mb"], w["peer_id"])
        for w in workers.values()
    ]
    candidates.sort(key=lambda x: (-x[0], x[1]))  # GPU DESC, ID ASC
    return candidates[0][1]
```

**Trade-off**: Workers with higher GPU memory elected more often

**Mitigation**: Admin role is lightweight (doesn't impact job execution), so GPU advantage is beneficial for coordination tasks

---

### Decision 4: Event-Driven + Heartbeat Updates ✅

**Choice**: Workers send status updates on change + heartbeat every 5 seconds

**Alternatives Considered**:
- **Polling only** (workers query admin every 2 seconds)
  - ❌ High overhead (constant queries)
  - ❌ Stale state between polls
  - ✅ Simple to implement

- **Event-driven only** (no heartbeat)
  - ❌ Can't detect silent failures (worker crashes)
  - ❌ No connection health monitoring
  - ✅ Minimal network traffic

- **Heartbeat only** (broadcast every 1 second)
  - ❌ Slow to propagate changes (up to 1 second delay)
  - ❌ High overhead
  - ✅ Reliable failure detection

**Rationale for hybrid approach**:
- Event-driven: Immediate responsiveness (<100ms for status changes)
- Heartbeat: Failure detection (detect crash within 15 seconds = 3 missed heartbeats)
- Best of both worlds: low latency + reliability

**Trade-off**: More complex message handling (two update paths)

**Mitigation**: Clear separation of concerns (event handler vs heartbeat handler)

---

### Decision 5: Stale CRDT on Network Partition ✅

**Choice**: Workers without admin connection use read-only cached state

**Alternatives Considered**:
- **Local admin election** (partition elects temporary admin)
  - ❌ Split-brain: two admins in different partitions
  - ❌ Conflicting state when partitions merge
  - ✅ Write operations continue in partition

- **Block operations** (workers can't update state without admin)
  - ❌ Workers can't execute jobs (too restrictive)
  - ❌ Poor user experience
  - ✅ Prevents state divergence

- **Optimistic updates** (workers update locally, merge on reconnect)
  - ❌ Risk of conflicts on merge
  - ❌ Requires complex conflict resolution
  - ✅ No blocking on partition

**Rationale for read-only mode**:
- Workers can still execute jobs with cached worker list
- No risk of state divergence (no writes)
- Simple to implement (just check admin connection status)
- Assumption: Workers typically on same network (partitions rare)

**Trade-off**: Stale state during partition (new workers not visible)

**Mitigation**:
- Partitions expected to be rare and short-lived
- When partition heals, workers re-sync immediately
- Jobs can still execute with cached state

---

### Decision 6: Admin is Also Available Worker ✅

**Choice**: Admin worker can execute jobs while handling coordination

**Alternatives Considered**:
- **Dedicated admin** (admin only coordinates, doesn't execute jobs)
  - ❌ Wastes GPU resources (admin worker idle)
  - ❌ Reduces effective room capacity by 1 worker
  - ✅ Simpler coordination logic (no job conflicts)

- **Admin rotation** (workers take turns being admin)
  - ❌ Frequent re-elections (disruptive)
  - ❌ State migration overhead
  - ✅ Load balancing

**Rationale for dual-role admin**:
- Maximizes resource utilization (all workers can execute jobs)
- Admin overhead is lightweight (query handling, broadcasting)
- No performance impact on job execution
- Maintains backwards compatibility (admin appears as available worker)

**Trade-off**: Admin must handle both coordination and job execution

**Mitigation**:
- Coordination tasks are async (don't block job execution)
- Admin broadcasts state in background thread
- Job execution has priority over coordination

---

## Architecture Components

### 1. MeshCoordinator (`mesh_coordinator.py`)

**Responsibilities**:
- Establish and maintain WebRTC connections to all peers in room
- Route messages to specific peers by peer_id
- Broadcast messages to all peers (admin only)
- Detect peer departure via ICE connection state
- Handle peer join (establish new connections)

**Key Methods**:
```python
class MeshCoordinator:
    def __init__(self, self_peer_id: str):
        self.peer_connections: dict[str, RTCPeerConnection] = {}
        self.data_channels: dict[str, RTCDataChannel] = {}

    async def connect_to_peer(self, peer_id: str, offer_sdp: str):
        """Establish WebRTC connection to peer."""

    def send_to_peer(self, peer_id: str, message: dict):
        """Send message to specific peer via datachannel."""

    def broadcast_to_all(self, message: dict):
        """Broadcast message to all connected peers."""

    def on_peer_departed(self, peer_id: str):
        """Handle peer connection close."""
```

**Dependencies**: `aiortc` (WebRTC), `websockets` (signaling)

---

### 2. AdminController (`admin_controller.py`)

**Responsibilities**:
- Determine if local worker is admin (via election)
- Handle client discovery queries (filter CRDT by criteria)
- Broadcast CRDT state to all workers
- Trigger re-election on admin departure
- Merge status updates from workers into CRDT

**Key Methods**:
```python
class AdminController:
    def __init__(self, peer_id: str, crdt_state: RoomStateCRDT):
        self.peer_id = peer_id
        self.crdt_state = crdt_state

    @staticmethod
    def elect_admin(workers: dict) -> str:
        """Deterministic admin election."""

    @property
    def is_admin(self) -> bool:
        """Check if this worker is current admin."""

    async def handle_client_query(self, filters: dict) -> list:
        """Filter CRDT workers by client criteria."""

    async def broadcast_state(self, mesh: MeshCoordinator):
        """Broadcast CRDT to all workers."""

    async def handle_status_update(self, peer_id: str, status: str):
        """Merge worker status update into CRDT."""
```

**Dependencies**: `MeshCoordinator`, `RoomStateCRDT`

---

### 3. RoomStateCRDT (`crdt_state.py`)

**Responsibilities**:
- Wrap automerge CRDT document
- Provide type-safe API for worker state updates
- Serialize/deserialize CRDT for network transmission
- Track admin peer_id and room metadata

**Key Methods**:
```python
class RoomStateCRDT:
    def __init__(self, room_id: str):
        self.doc = automerge.init()
        self.room_id = room_id

    @classmethod
    def create(cls, room_id: str) -> RoomStateCRDT:
        """Create new CRDT document."""

    def add_worker(self, peer_id: str, metadata: dict):
        """Add worker to CRDT."""

    def update_worker_status(self, peer_id: str, status: str):
        """Update worker status in CRDT."""

    def remove_worker(self, peer_id: str):
        """Remove worker from CRDT."""

    def get_worker(self, peer_id: str) -> dict:
        """Get worker metadata."""

    def get_all_workers(self) -> dict:
        """Get all workers in room."""

    def merge(self, other_crdt: bytes):
        """Merge with another CRDT snapshot."""

    def serialize(self) -> bytes:
        """Serialize CRDT for network transmission."""

    @classmethod
    def deserialize(cls, data: bytes) -> RoomStateCRDT:
        """Deserialize CRDT from network."""
```

**Dependencies**: `automerge-py`

---

### 4. Worker Integration (`worker_class.py` modifications)

**Changes to RTCWorkerClient**:
```python
class RTCWorkerClient:
    def __init__(self, ...):
        # Existing
        self.capabilities = WorkerCapabilities(...)
        self.state_manager = StateManager(...)

        # NEW: Mesh coordination
        self.mesh_coordinator = MeshCoordinator(self.peer_id)
        self.crdt_state = RoomStateCRDT(room_id)
        self.admin_controller = AdminController(self.peer_id, self.crdt_state)

    async def run_worker(self, ...):
        # 1. Register with signaling server (get peer list)
        registration_response = await self.register_with_room(...)
        peer_list = registration_response["peers"]
        admin_peer_id = registration_response["admin_peer_id"]

        # 2. Connect to all existing peers (full mesh)
        for peer in peer_list:
            await self.mesh_coordinator.connect_to_peer(peer["peer_id"])

        # 3. Determine if admin (via election)
        if self.admin_controller.is_admin:
            logging.info("This worker is the admin")

        # 4. Start heartbeat task
        asyncio.create_task(self._heartbeat_loop())

        # 5. Handle incoming messages (existing + mesh messages)
        await self.handle_connection(...)  # Existing logic

    async def _heartbeat_loop(self):
        """Send heartbeat every 5 seconds."""
        while True:
            self.mesh_coordinator.broadcast_to_all({"type": "heartbeat"})
            await asyncio.sleep(5)
```

---

## Message Protocol

### Worker-to-Worker Messages

**Heartbeat**:
```json
{
  "type": "heartbeat",
  "from_peer_id": "worker-2",
  "timestamp": 1234567890
}
```

**Status Update** (to admin):
```json
{
  "type": "status_update",
  "from_peer_id": "worker-3",
  "timestamp": 1234567890,
  "status": "busy",
  "current_job": {
    "job_id": "abc123",
    "client_id": "client-456"
  }
}
```

**State Broadcast** (from admin):
```json
{
  "type": "state_broadcast",
  "from_peer_id": "worker-1",
  "crdt_snapshot": "<base64-encoded-crdt>",
  "version": 43,
  "timestamp": 1234567890
}
```

**Admin Election** (broadcast):
```json
{
  "type": "admin_elected",
  "new_admin_peer_id": "worker-2",
  "timestamp": 1234567890
}
```

### Client-to-Admin Messages (via signaling relay)

**Query Workers**:
```json
{
  "type": "query_workers",
  "from_peer_id": "client-789",
  "filters": {
    "min_gpu_memory_mb": 8000,
    "status": "available",
    "tags": ["training-worker"]
  }
}
```

**Admin Response**:
```json
{
  "type": "worker_list",
  "from_peer_id": "worker-1",
  "workers": [
    {
      "peer_id": "worker-2",
      "metadata": {
        "properties": {
          "gpu_memory_mb": 24000,
          "gpu_model": "NVIDIA A100",
          "status": "available",
          ...
        }
      }
    },
    ...
  ],
  "admin_peer_id": "worker-1",
  "timestamp": 1234567890
}
```

---

## Data Flow Diagrams

### Scenario 1: Worker Joins Room

```
Worker-4                Signaling Server           Worker-1 (Admin)       Worker-2, 3
   |                           |                           |                    |
   |-- register(room) -------->|                           |                    |
   |                           |                           |                    |
   |<-- peers=[1,2,3] ---------|                           |                    |
   |    admin_peer_id=1        |                           |                    |
   |                           |                           |                    |
   |-- WebRTC handshake ---------------------------------->|                    |
   |                           |                           |                    |
   |-- WebRTC handshake --------------------------------------------------->|
   |                           |                           |                    |
   |<-- CRDT snapshot ------------------------------------ |                    |
   |                           |                           |                    |
   |-- heartbeat ----------------------------------------->|                    |
   |-- heartbeat ---------------------------------------------------------->|
```

### Scenario 2: Worker Status Update

```
Worker-2                     Worker-1 (Admin)              Worker-3, 4
   |                                |                            |
   |-- status_update("busy") ------>|                            |
   |                                |                            |
   |                                |-- merge CRDT               |
   |                                |                            |
   |                                |-- broadcast(new state) --->|
   |<-- broadcast(new state) -------|                            |
   |                                |                            |
```

### Scenario 3: Client Queries Admin

```
Client                  Signaling Server           Worker-1 (Admin)
  |                           |                           |
  |-- register(room) -------->|                           |
  |                           |                           |
  |<-- admin_peer_id=1 -------|                           |
  |                           |                           |
  |-- relay(query_workers) -->|-- forward ------------->  |
  |                           |                           |
  |                           |                           |-- filter CRDT
  |                           |                           |
  |                           |<-- worker_list ---------- |
  |<-- relay(worker_list) ----|                           |
```

### Scenario 4: Admin Re-Election

```
Worker-1 (Admin)        Worker-2                Worker-3
   |                       |                       |
   |                       |                       |
   X (crashes)             |                       |
                           |                       |
                           |-- detect ICE close ---|
                           |                       |
                           |-- elect_admin() ------|
                           |   (both compute       |
                           |    worker-2 wins)     |
                           |                       |
                           |<-- admin_elected -----|
                           |                       |
                           |-- broadcast state --->|
```

---

## Performance Characteristics

### Latency

**Worker Discovery** (client queries admin):
- Current (HTTP): ~100ms (network roundtrip + DB query)
- New (admin query): <10ms (local CRDT lookup)
- **Improvement**: 10x faster

**State Propagation** (status update):
- Event-driven: <100ms (worker → admin → all workers)
- Heartbeat: 5 seconds (periodic sync)

**Admin Re-Election**:
- Detection: 15 seconds (3 missed heartbeats)
- Election: <1 second (deterministic, no network communication)
- **Total**: <5 seconds from admin crash to new admin operational

### Throughput

**Signaling Server HTTP Traffic**:
- Current: N clients × M discoveries/min × HTTP request
- New: N clients × 1 registration (then peer-to-peer)
- **Reduction**: ~80% (only initial registration, no discovery queries)

**CRDT Update Rate**:
- Event-driven: ~10 updates/min per worker (status changes)
- Heartbeat: 12 updates/min per worker (every 5 seconds)
- **Total**: ~22 updates/min × 10 workers = 220 updates/min
- Admin broadcasts: 220/min × 9 workers = ~2000 messages/min
- **Bandwidth**: ~2KB/message × 2000/min = ~4MB/min per room

### Scalability

**Room Size Limits**:
- 5 workers: 10 connections total ✅ Excellent
- 10 workers: 45 connections total ⚠️ Target limit
- 20 workers: 190 connections total ❌ Too many

**Mesh Connection Overhead**:
- Each worker maintains N-1 WebRTC connections
- Each connection: ~1-5 MB/s bandwidth (depending on traffic)
- 10 workers: ~45 MB/s total room bandwidth

---

## Failure Modes & Recovery

### Admin Worker Crashes

**Detection**: Workers detect ICE connection close within 15 seconds (3 missed heartbeats)

**Recovery**:
1. All workers run deterministic election: `elect_admin(remaining_workers)`
2. All workers compute same winner (e.g., worker-2)
3. Worker-2 becomes new admin, starts broadcasting state
4. Other workers acknowledge new admin
5. **Total recovery time**: <5 seconds

**State**: No data loss (all workers have CRDT copy)

---

### Worker-to-Worker Connection Failure

**Detection**: ICE connection state = "failed" or "closed"

**Recovery**:
1. Worker removes peer from local CRDT
2. If departed peer was admin → trigger re-election
3. Otherwise → continue with remaining mesh

**State**: CRDT automatically converges (automerge handles removals)

---

### Network Partition (Admin Unreachable)

**Detection**: Admin connection closes, but other workers still connected

**Recovery**:
1. Workers enter **read-only mode** (use stale CRDT)
2. Workers can still execute jobs with cached worker list
3. When partition heals:
   - Workers reconnect to admin
   - Workers receive updated CRDT snapshot
   - Workers merge local changes (if any)

**State**: Stale during partition, converges on heal

---

### Signaling Server Restart

**Detection**: WebSocket connection to signaling server closes

**Recovery**:
1. Workers maintain mesh connections (peer-to-peer, no signaling dependency)
2. Workers reconnect to signaling server for future registrations
3. Room state persists in worker CRDTs (no data loss)

**State**: No impact on mesh operation

---

## Testing Strategy

### Unit Tests
- CRDT operations (create, update, merge, serialize)
- Admin election (various scenarios, tiebreakers)
- Mesh coordinator (connection lifecycle, message routing)
- Message protocol (serialize/deserialize)

### Integration Tests
- 2-worker mesh formation
- 10-worker mesh formation (full scale)
- Admin re-election scenarios
- CRDT state convergence under concurrent updates
- Client discovery via admin query

### Performance Tests
- Discovery latency (measure P50, P95, P99)
- State propagation time (10 workers, concurrent updates)
- Admin re-election time (from crash to new admin)
- CPU and memory usage (baseline vs mesh overhead)

### Failure Tests
- Admin crashes mid-broadcast
- Worker crashes during CRDT merge
- Network partition (50% of workers)
- Signaling server restart
- Room join/leave churn (rapid membership changes)

### Load Tests
- 100 concurrent rooms (10 workers each)
- 1000 workers total across all rooms
- Signaling server load (measure HTTP traffic reduction)

---

## Migration Path

### Phase 0: Current State (Before)
- Client queries HTTP `/discover-workers`
- Signaling server queries DynamoDB
- No worker-to-worker connections

### Phase 1: Worker Modularization (Prerequisite)
- Extract mesh coordinator module
- Extract admin controller module
- No behavioral changes yet

### Phase 2: Mesh Formation (Feature Flag Off)
- Workers connect to each other (mesh)
- CRDT state synchronization
- Feature flag: `ENABLE_ROOM_REGISTRY=false` (disabled)

### Phase 3: Gradual Rollout (10% → 100%)
- Enable for 10% of rooms
- Monitor metrics (latency, errors, re-elections)
- Expand to 50%, then 100%

### Phase 4: Client Migration
- Update clients to query admin instead of HTTP
- Maintain HTTP endpoint for backwards compatibility
- Track usage metrics (old vs new)

### Phase 5: Deprecation (6 months later)
- Add deprecation warnings to HTTP endpoint
- Notify users of upcoming removal
- Remove HTTP endpoint after migration complete

---

## Risks & Mitigations

See proposal.md for comprehensive risk analysis.

**Top 3 Risks**:
1. **CRDT merge conflicts** → Automerge handles automatically
2. **Full mesh scalability** → Hard limit of 10 workers per room
3. **Admin election split-brain** → Deterministic election prevents this

---

## Open Questions

See proposal.md for full list of open questions.

**Critical Questions**:
- ✅ Leader election algorithm → Deterministic priority
- ✅ Room size limits → Max 10 workers
- ✅ Network partition handling → Stale CRDT (read-only)
- ✅ Admin role → Also available for job execution
- ✅ CRDT structure → Mirrors current worker metadata

All critical decisions finalized. Ready for implementation.
