# Implementation Tasks: Room-Level Registry

## Overview

This document breaks down the implementation into phases with specific, actionable tasks.

**Dependencies**:
- ✅ Worker modularization (`refactor-worker-modular`) must be complete first

**Estimated Timeline**: 3-4 weeks

---

## Phase 1: CRDT Integration & Setup

**Goal**: Set up automerge library and basic CRDT document structure

**Estimated Time**: 3-4 days

### 1.1 Add Dependencies
- [ ] Add `automerge-py` to `requirements.txt`
- [ ] Update `pyproject.toml` with automerge dependency
- [ ] Test installation: `pip install automerge-py`
- [ ] Verify automerge imports work in Python environment

**Acceptance**: `import automerge` succeeds without errors

### 1.2 Create CRDT Document Wrapper
- [ ] Create `sleap_rtc/worker/crdt_state.py`
- [ ] Implement `RoomStateCRDT` class with automerge backend
- [ ] Add methods: `create()`, `update()`, `merge()`, `serialize()`, `deserialize()`
- [ ] Define CRDT schema matching worker metadata structure

**Files Created**:
- `sleap_rtc/worker/crdt_state.py` (~200 lines)

**Acceptance**:
```python
state = RoomStateCRDT.create(room_id="test")
state.add_worker("worker-1", metadata={...})
state.update_worker_status("worker-1", "busy")
serialized = state.serialize()
state2 = RoomStateCRDT.deserialize(serialized)
assert state2.get_worker("worker-1")["status"] == "busy"
```

### 1.3 Test CRDT Merge Operations
- [ ] Write unit tests for CRDT creation
- [ ] Test concurrent updates (two workers update different fields)
- [ ] Test conflicting updates (two workers update same field)
- [ ] Verify automerge resolves conflicts correctly
- [ ] Test serialization round-trip

**Files Created**:
- `tests/worker/test_crdt_state.py` (~150 lines)

**Acceptance**: All CRDT unit tests pass

---

## Phase 2: Admin Election Logic

**Goal**: Implement deterministic admin election and role management

**Estimated Time**: 4-5 days

### 2.1 Create Admin Controller Module
- [ ] Create `sleap_rtc/worker/admin_controller.py`
- [ ] Implement `AdminController` class
- [ ] Add `elect_admin(workers: dict) -> str` function (deterministic priority)
- [ ] Add `is_admin()` property check
- [ ] Add `handle_admin_duties()` method (placeholder)

**Files Created**:
- `sleap_rtc/worker/admin_controller.py` (~300 lines)

**Acceptance**:
```python
controller = AdminController(peer_id="worker-1", crdt_state=state)
admin_id = controller.elect_admin()
assert controller.is_admin == (admin_id == "worker-1")
```

### 2.2 Implement Admin Election Algorithm
- [ ] Implement deterministic sort: `(gpu_memory_mb DESC, peer_id ASC)`
- [ ] Add election trigger on admin departure detection
- [ ] Update CRDT with new `admin_peer_id` on election
- [ ] Log election events (admin elected, admin changed)

**Acceptance**:
```python
# Three workers with different GPU memory
workers = {
    "worker-1": {"metadata": {"properties": {"gpu_memory_mb": 16000}}},
    "worker-2": {"metadata": {"properties": {"gpu_memory_mb": 24000}}},  # Winner
    "worker-3": {"metadata": {"properties": {"gpu_memory_mb": 16000}}},
}
admin_id = AdminController.elect_admin(workers)
assert admin_id == "worker-2"  # Highest GPU memory
```

### 2.3 Admin Departure Detection
- [ ] Detect ICE connection close for admin peer
- [ ] Trigger re-election when admin leaves
- [ ] Update local CRDT with new admin_peer_id
- [ ] Broadcast admin change to all workers

**Acceptance**: When admin worker closes connection, all remaining workers elect same new admin within 5 seconds

### 2.4 Test Admin Election Scenarios
- [ ] Test election with 2 workers (one becomes admin)
- [ ] Test re-election when admin leaves
- [ ] Test election with same GPU memory (peer_id tiebreaker)
- [ ] Test election with 10 workers (performance)

**Files Created**:
- `tests/worker/test_admin_controller.py` (~200 lines)

**Acceptance**: All admin election tests pass

---

## Phase 3: Mesh Connection Management

**Goal**: Establish full mesh WebRTC connections between workers

**Estimated Time**: 5-6 days

### 3.1 Create Mesh Coordinator Module
- [ ] Create `sleap_rtc/worker/mesh_coordinator.py`
- [ ] Implement `MeshCoordinator` class
- [ ] Add `peer_connections: dict[peer_id, RTCPeerConnection]` tracking
- [ ] Add `data_channels: dict[peer_id, RTCDataChannel]` tracking

**Files Created**:
- `sleap_rtc/worker/mesh_coordinator.py` (~400 lines)

**Acceptance**:
```python
coordinator = MeshCoordinator(self_peer_id="worker-1")
coordinator.add_peer("worker-2")
assert "worker-2" in coordinator.peer_connections
```

### 3.2 Implement Peer Discovery
- [ ] Request peer list from signaling server on room registration
- [ ] Parse `peers` array from registration response
- [ ] Filter out self from peer list
- [ ] Initiate WebRTC connections to each peer

**Acceptance**: Worker receives list of 3 existing peers, initiates 3 WebRTC connections

### 3.3 WebRTC Connection Lifecycle (per peer)
- [ ] Create `RTCPeerConnection` for each peer
- [ ] Create data channel for each peer: `worker-mesh-{peer_id}`
- [ ] Set up ICE candidate handlers per connection
- [ ] Handle ICE connection state changes per peer
- [ ] Detect peer departure (ICE closed/failed)

**Acceptance**: Worker establishes 5 simultaneous WebRTC connections to 5 peers

### 3.4 Message Routing by Peer
- [ ] Route incoming datachannel messages by sender peer_id
- [ ] Implement `send_to_peer(peer_id, message)` method
- [ ] Implement `broadcast_to_all(message)` method (admin only)
- [ ] Handle message parsing errors gracefully

**Files Modified**:
- `sleap_rtc/worker/worker_class.py` - Add mesh coordinator integration

**Acceptance**:
```python
coordinator.send_to_peer("worker-2", {"type": "heartbeat"})
coordinator.broadcast_to_all({"type": "state_update", "crdt": serialized_state})
```

### 3.5 Test Mesh Connections
- [ ] Test 2-worker mesh (1 connection each)
- [ ] Test 5-worker mesh (10 total connections)
- [ ] Test 10-worker mesh (45 total connections)
- [ ] Test peer join mid-session (new connections established)
- [ ] Test peer leave mid-session (connections cleaned up)

**Files Created**:
- `tests/worker/test_mesh_coordinator.py` (~250 lines)

**Acceptance**: 10 workers establish full mesh (45 connections) within 30 seconds

---

## Phase 4: State Synchronization

**Goal**: Implement CRDT state updates, merging, and broadcasting

**Estimated Time**: 4-5 days

### 4.1 Implement Status Update Flow
- [ ] Worker updates local CRDT when status changes
- [ ] Send `status_update` message to admin via datachannel
- [ ] Admin merges update into authoritative CRDT
- [ ] Admin broadcasts merged state to all workers

**Message Protocol**:
```json
// Worker -> Admin
{
  "type": "status_update",
  "from_peer_id": "worker-2",
  "timestamp": 1234567890,
  "status": "busy",
  "current_job": {"job_id": "...", "client_id": "..."}
}

// Admin -> All Workers
{
  "type": "state_broadcast",
  "crdt_snapshot": "<serialized>",
  "version": 43
}
```

**Acceptance**: Worker status change propagates to all workers within 100ms

### 4.2 Implement Heartbeat Mechanism
- [ ] Worker sends heartbeat every 5 seconds to all peers
- [ ] Update `last_heartbeat` timestamp in CRDT
- [ ] Detect stale heartbeat (no update for 15 seconds)
- [ ] Mark worker as departed if heartbeat stale

**Message Protocol**:
```json
{
  "type": "heartbeat",
  "from_peer_id": "worker-3",
  "timestamp": 1234567890
}
```

**Acceptance**: Worker departure detected within 15 seconds via heartbeat timeout

### 4.3 Implement CRDT Merge Logic
- [ ] Admin receives status update from worker
- [ ] Admin merges into local CRDT using automerge
- [ ] Handle merge conflicts (automerge automatic)
- [ ] Serialize merged state for broadcast
- [ ] Send to all workers in mesh

**Acceptance**:
```python
# Concurrent updates from two workers
state.update_worker_status("worker-1", "busy")  # Admin
state.update_worker_status("worker-2", "reserved")  # Admin merges
# Both updates preserved in CRDT
assert state.get_worker("worker-1")["status"] == "busy"
assert state.get_worker("worker-2")["status"] == "reserved"
```

### 4.4 Handle State Reception (non-admin workers)
- [ ] Receive `state_broadcast` from admin
- [ ] Deserialize CRDT snapshot
- [ ] Merge with local CRDT (in case of network partition)
- [ ] Update local worker status cache

**Acceptance**: Non-admin worker receives state broadcast, local CRDT updated

### 4.5 Test State Synchronization
- [ ] Test single status update propagation
- [ ] Test concurrent updates from multiple workers
- [ ] Test heartbeat timeout detection
- [ ] Test state convergence (all workers eventually consistent)
- [ ] Test admin broadcasts to 10 workers

**Files Created**:
- `tests/worker/test_state_sync.py` (~200 lines)

**Acceptance**: 10 workers maintain consistent CRDT state under concurrent updates

---

## Phase 5: Client Integration

**Goal**: Enable clients to query admin worker for discovery

**Estimated Time**: 3-4 days

### 5.1 Implement Admin Query Handler
- [ ] Admin handles `query_workers` message from client
- [ ] Filter CRDT state by client-provided criteria
- [ ] Return list of available workers to client
- [ ] Log client query events

**Message Protocol**:
```json
// Client -> Admin (via signaling relay)
{
  "type": "query_workers",
  "from_peer_id": "client-123",
  "filters": {
    "min_gpu_memory_mb": 8000,
    "status": "available"
  }
}

// Admin -> Client (via signaling relay)
{
  "type": "worker_list",
  "workers": [...],  // Filtered from CRDT
  "admin_peer_id": "worker-1",
  "timestamp": 1234567890
}
```

**Acceptance**: Client queries admin, receives list of 3 available workers matching filters

### 5.2 Update Client Worker Discovery
- [ ] Modify `sleap_rtc/client/worker_discovery.py`
- [ ] Send `query_workers` to admin peer_id instead of HTTP request
- [ ] Parse `worker_list` response from admin
- [ ] Fallback to HTTP `/discover-workers` if admin query fails (backwards compat)

**Files Modified**:
- `sleap_rtc/client/worker_discovery.py` - Add admin query logic

**Acceptance**:
```python
# Client queries admin worker
workers = await client.worker_discovery.discover_workers(room_id="test")
assert len(workers) == 3
assert all(w["status"] == "available" for w in workers)
```

### 5.3 Signaling Server: Provide Admin Peer ID
- [ ] Signaling server includes `admin_peer_id` in room registration response
- [ ] Client receives admin_peer_id on room join
- [ ] Client stores admin_peer_id for discovery queries

**Files Modified**:
- `sleap_rtc/signaling_server/room_manager.py` - Add admin_peer_id to response

**Acceptance**: Client registration response includes `admin_peer_id` field

### 5.4 Test Client Discovery via Admin
- [ ] Test client queries admin with no filters
- [ ] Test client queries with GPU memory filter
- [ ] Test client queries with status filter
- [ ] Test fallback to HTTP if admin unavailable
- [ ] Test admin peer_id provided by signaling server

**Files Created**:
- `tests/integration/test_client_admin_discovery.py` (~150 lines)

**Acceptance**: Client discovers workers via admin query in <10ms

---

## Phase 6: Signaling Server Updates

**Goal**: Update signaling server to support mesh topology

**Estimated Time**: 3-4 days

### 6.1 Return Peer List on Room Registration
- [ ] Modify room registration endpoint to include `peers` array
- [ ] Include all workers currently in room (with peer_id, metadata)
- [ ] Exclude self from peer list
- [ ] Include `admin_peer_id` in response

**API Changes**:
```json
// Response to worker registration
{
  "type": "registered_auth",
  "peer_id": "worker-4",
  "room_id": "abc123",
  "peers": [
    {"peer_id": "worker-1", "metadata": {...}, "is_admin": true},
    {"peer_id": "worker-2", "metadata": {...}},
    {"peer_id": "worker-3", "metadata": {...}}
  ],
  "admin_peer_id": "worker-1"
}
```

**Files Modified**:
- `sleap_rtc/signaling_server/room_manager.py` - Add peer list to registration

**Acceptance**: Worker registration returns list of 3 existing peers

### 6.2 Enforce Room Size Limits
- [ ] Add `MAX_WORKERS_PER_ROOM = 10` constant
- [ ] Check room size on worker registration
- [ ] Reject registration if room has 10+ workers
- [ ] Log warning when room reaches 8 workers

**Acceptance**: 11th worker attempting to join room receives rejection error

### 6.3 Track Admin Peer ID in Room Metadata
- [ ] Add `admin_peer_id` field to room state (DynamoDB or in-memory)
- [ ] Update admin_peer_id when first worker joins
- [ ] Update admin_peer_id on re-election (via worker notification)
- [ ] Return admin_peer_id to clients on room join

**Files Modified**:
- `sleap_rtc/signaling_server/room_manager.py` - Admin tracking

**Acceptance**: Room metadata includes current admin_peer_id, updated on re-election

### 6.4 Backwards Compatibility: Maintain HTTP Endpoint
- [ ] Keep `/discover-workers` HTTP endpoint active
- [ ] Add deprecation warning in response headers
- [ ] Log usage metrics (track old vs new client discovery)
- [ ] Plan deprecation timeline (6 months)

**Acceptance**: Old clients continue to use HTTP endpoint successfully

### 6.5 Test Signaling Server Changes
- [ ] Test peer list returned on registration
- [ ] Test room size limit enforcement
- [ ] Test admin_peer_id tracking and updates
- [ ] Test backwards compatibility (HTTP endpoint still works)

**Files Created**:
- `tests/signaling_server/test_room_registry.py` (~200 lines)

**Acceptance**: All signaling server tests pass

---

## Phase 7: Integration Testing

**Goal**: End-to-end testing of full room registry system

**Estimated Time**: 4-5 days

### 7.1 Test Basic Mesh Formation
- [ ] Test 2 workers join room → form mesh
- [ ] Test 5 workers join room → full mesh (10 connections)
- [ ] Test 10 workers join room → full mesh (45 connections)
- [ ] Verify CRDT state consistent across all workers

**Acceptance**: 10 workers establish full mesh, all have identical CRDT state

### 7.2 Test Admin Election Scenarios
- [ ] First worker becomes admin
- [ ] Admin leaves, second worker elected
- [ ] Admin with highest GPU memory elected
- [ ] Tiebreaker (peer_id) works correctly

**Acceptance**: Admin election deterministic, all workers agree on same admin

### 7.3 Test State Synchronization Under Load
- [ ] 10 workers send concurrent status updates
- [ ] Verify all updates propagated within 1 second
- [ ] No state conflicts or data loss
- [ ] Admin broadcasts to all workers successfully

**Acceptance**: 100 concurrent updates from 10 workers, all propagated correctly

### 7.4 Test Network Partition Handling
- [ ] Simulate admin unreachable by 3 workers
- [ ] Workers use stale CRDT (read-only)
- [ ] Partition heals, workers re-sync with admin
- [ ] No data loss after partition recovery

**Acceptance**: Workers handle partition gracefully, re-sync on recovery

### 7.5 Test Client Discovery via Admin
- [ ] Client joins room, queries admin for workers
- [ ] Client receives list of available workers
- [ ] Client selects worker, establishes job connection
- [ ] Job executes successfully

**Acceptance**: End-to-end client workflow works with admin discovery

### 7.6 Performance Testing
- [ ] Measure discovery latency (admin query vs HTTP)
- [ ] Measure state propagation time (10 workers)
- [ ] Measure admin re-election time
- [ ] Monitor CPU and memory usage

**Targets**:
- Discovery latency: <10ms (vs ~100ms HTTP)
- State propagation: <500ms for 10 workers
- Re-election: <5 seconds
- CPU: <5% overhead for mesh coordination

**Files Created**:
- `tests/integration/test_room_registry_e2e.py` (~400 lines)
- `tests/performance/test_mesh_performance.py` (~200 lines)

**Acceptance**: All performance targets met

---

## Phase 8: Deployment & Monitoring

**Goal**: Roll out room registry to production with monitoring

**Estimated Time**: 2-3 days

### 8.1 Feature Flag Implementation
- [ ] Add `ENABLE_ROOM_REGISTRY` environment variable
- [ ] Default to `False` initially (opt-in)
- [ ] Workers check flag before enabling mesh
- [ ] Clients check flag before admin query

**Acceptance**: Feature can be enabled/disabled without code changes

### 8.2 Gradual Rollout Plan
- [ ] Phase 1: Enable for 10% of rooms (testing)
- [ ] Phase 2: Enable for 50% of rooms (validation)
- [ ] Phase 3: Enable for 100% of rooms (full deployment)
- [ ] Monitor error rates and performance at each phase

**Acceptance**: Rollout plan documented, monitoring in place

### 8.3 Add Monitoring Metrics
- [ ] Track room registry usage (% of rooms using mesh)
- [ ] Track admin elections (count, frequency)
- [ ] Track CRDT merge conflicts (count, resolution time)
- [ ] Track mesh connection failures
- [ ] Track discovery latency (P50, P95, P99)

**Logging**:
```python
logger.info(
    "Admin elected",
    extra={
        "room_id": room_id,
        "admin_peer_id": admin_id,
        "election_time_ms": elapsed,
        "num_workers": len(workers)
    }
)
```

**Acceptance**: All key metrics tracked and visible in monitoring dashboard

### 8.4 Documentation Updates
- [ ] Update worker deployment docs (automerge dependency)
- [ ] Update architecture docs (room registry design)
- [ ] Update troubleshooting guide (mesh debugging)
- [ ] Update API docs (signaling server changes)

**Files Updated**:
- `docs/worker_deployment.md`
- `docs/architecture.md`
- `docs/troubleshooting.md`
- `docs/api_reference.md`

**Acceptance**: Documentation complete and accurate

### 8.5 Deprecation Plan for HTTP Discovery
- [ ] Add deprecation warnings to `/discover-workers` endpoint
- [ ] Set deprecation timeline (6 months)
- [ ] Notify users of upcoming change
- [ ] Monitor HTTP endpoint usage decline

**Acceptance**: Deprecation plan communicated, timeline set

---

## Phase 9: Final Validation

**Goal**: Comprehensive testing before full deployment

**Estimated Time**: 2-3 days

### 9.1 Load Testing
- [ ] Test 100 concurrent rooms (10 workers each)
- [ ] Test rapid worker join/leave (churn)
- [ ] Test admin election under high load
- [ ] Monitor signaling server load (should be 80% lower)

**Acceptance**: System handles 100 concurrent rooms without degradation

### 9.2 Failure Scenario Testing
- [ ] Admin crashes mid-broadcast
- [ ] Worker crashes during CRDT merge
- [ ] Network partition for 50% of workers
- [ ] Signaling server restart (workers maintain mesh)
- [ ] DDoS simulation (room join flood)

**Acceptance**: All failure scenarios handled gracefully, no data loss

### 9.3 Backwards Compatibility Testing
- [ ] Old client + new workers
- [ ] New client + old workers (HTTP fallback)
- [ ] Mixed room (old + new workers)

**Acceptance**: All combinations work without errors

### 9.4 Security Review
- [ ] Verify peer_id validation (no spoofing)
- [ ] Verify CRDT access control (only workers can update)
- [ ] Verify admin privileges (only admin broadcasts)
- [ ] Check for DoS vulnerabilities (message flooding)

**Acceptance**: No security vulnerabilities found

### 9.5 Final Sign-off Checklist
- [ ] All unit tests passing (>95% coverage)
- [ ] All integration tests passing
- [ ] Performance targets met
- [ ] Documentation complete
- [ ] Monitoring in place
- [ ] Rollback plan tested
- [ ] Security review approved

**Acceptance**: All checklist items completed, ready for production deployment

---

## Summary

**Total Tasks**: ~120 tasks across 9 phases
**Estimated Timeline**: 3-4 weeks (after worker modularization)
**Success Criteria**: 80% reduction in signaling server load, <10ms discovery latency, <5s re-election

**Critical Path**:
1. Worker modularization (prerequisite)
2. CRDT integration (foundation)
3. Mesh connections (core feature)
4. State synchronization (correctness)
5. Client integration (user-facing)
6. Testing & validation (quality)

**Next Step**: Begin Phase 1 (CRDT Integration) after worker modularization PR merged.
