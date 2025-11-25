# Add Decentralized Room-Level Worker Registry

## Why

**Problem**: Current architecture relies on signaling server HTTP endpoints for worker discovery, creating:
- High signaling server load (every client queries `/discover-workers` via HTTP)
- Dependency on external services (DynamoDB for room state storage)
- Centralized bottleneck for scaling (all discovery goes through signaling server)
- Latency overhead (~100ms HTTP roundtrip for every worker query)

**Impact**:
- Signaling server CPU and bandwidth usage scales linearly with client requests
- DynamoDB query costs for every worker discovery operation
- Single point of failure (signaling server down = no worker discovery)

**Solution**: Implement decentralized room-level registry where workers coordinate via peer-to-peer WebRTC connections, using CRDTs (automerge) for conflict-free state replication.

## What Changes

### Architecture Changes
- **NEW**: Full mesh WebRTC topology between workers in same room
- **NEW**: Admin worker election (first worker becomes coordinator)
- **NEW**: CRDT-based state synchronization using automerge library
- **NEW**: Deterministic leader re-election on admin failure
- **NEW**: Client queries admin worker instead of HTTP endpoint
- **BREAKING**: Signaling server API changes (returns peer list on registration)
- **MODIFIED**: Worker supports multiple simultaneous WebRTC connections (one per peer)

### Component Changes
- `sleap_rtc/worker/mesh_coordinator.py` - NEW: Manages worker mesh connections and CRDT state
- `sleap_rtc/worker/admin_controller.py` - NEW: Admin-specific logic (client queries, state broadcasting)
- `sleap_rtc/worker/worker_class.py` - MODIFIED: Support multiple RTC peer connections
- `sleap_rtc/signaling_server/` - MODIFIED: Return peer list on room registration
- `sleap_rtc/client/worker_discovery.py` - MODIFIED: Query admin worker instead of HTTP

### Dependency Changes
- **ADD**: `automerge-py` - CRDT library for conflict-free state replication

## Impact

### Affected Specs
- **DEPENDS ON**: `refactor-worker-modular` - Requires modular worker architecture
- **NEW**: `worker-mesh-coordinator` - Full mesh connection management
- **NEW**: `worker-admin-controller` - Admin election and client query handling
- **NEW**: `worker-crdt-sync` - CRDT state synchronization protocol
- **MODIFIED**: `worker-connection` - Support multiple WebRTC peer connections
- **MODIFIED**: `signaling-server-registration` - Return peer list on room join

### Affected Code
- `sleap_rtc/worker/mesh_coordinator.py` - NEW (~400 lines)
- `sleap_rtc/worker/admin_controller.py` - NEW (~300 lines)
- `sleap_rtc/worker/worker_class.py` - MODIFIED (add mesh support)
- `sleap_rtc/client/worker_discovery.py` - MODIFIED (query admin)
- `sleap_rtc/signaling_server/room_manager.py` - MODIFIED (peer list API)
- `requirements.txt` - ADD automerge-py dependency

### Migration Path
- **Phase 1**: Complete worker modularization (prerequisite)
- **Phase 2**: Implement CRDT + admin election (this change)
- **Phase 3**: Full mesh connections between workers
- **Phase 4**: Client integration (query admin instead of HTTP)
- **Phase 5**: Deprecate HTTP `/discover-workers` endpoint

### Backwards Compatibility
- Rooms with 1 worker: Works as before (no mesh needed)
- Rooms with 2+ workers: Automatically form mesh
- Old clients: Continue using HTTP endpoint (gradual migration)
- Admin worker: Also available for job execution (not dedicated)

## Success Criteria

### Performance Metrics
- **80% reduction** in signaling server HTTP traffic
- **<10ms** worker discovery latency (vs ~100ms HTTP roundtrip)
- **Zero** DynamoDB query costs for worker discovery
- **<5 seconds** admin re-election time on failure

### Functional Requirements
- Workers coordinate via peer-to-peer WebRTC (no signaling server after handshake)
- Admin failure handled automatically via deterministic election
- Clients receive real-time worker availability from admin
- Support 8-10 workers per room reliably

### Operational Requirements
- Room size limit enforced (max 10 workers, warn at 8)
- Graceful network partition handling (stale CRDT read-only mode)
- Full backwards compatibility with current workers/clients
- No regression in worker job execution performance

## Implementation Plan

See `tasks.md` for detailed task breakdown.

**High-level phases**:
1. ✅ **Prerequisite**: Worker modularization (separate PR)
2. 🔨 **Core Registry**: CRDT + admin election + state sync
3. 🔨 **Mesh Topology**: Full mesh WebRTC connections
4. 🔨 **Client Integration**: Query admin for discovery
5. 🔨 **Testing & Validation**: Integration tests, load testing
6. 🔨 **Deployment**: Gradual rollout, monitoring

**Estimated timeline**: 3-4 weeks (after worker modularization complete)

## Risks & Mitigations

### Risk: CRDT Complexity
**Impact**: Automerge integration bugs, merge conflicts
**Mitigation**:
- Comprehensive unit tests for CRDT operations
- Start with simple state structure, iterate
- Use automerge's battle-tested conflict resolution

### Risk: Full Mesh Scalability
**Impact**: 10 workers = 45 connections per worker, may overwhelm some systems
**Mitigation**:
- Hard limit of 10 workers per room (enforced by signaling server)
- Monitor connection health and CPU usage
- Can switch to hierarchical topology if needed (future)

### Risk: Admin Election Split-Brain
**Impact**: Multiple workers claim admin during network partition
**Mitigation**:
- Deterministic election ensures all workers compute same winner
- No network communication needed for election
- Use `(gpu_memory_mb DESC, peer_id ASC)` as tiebreaker

### Risk: Backwards Compatibility Breakage
**Impact**: Old clients or workers can't discover/join rooms
**Mitigation**:
- Signaling server maintains HTTP endpoint as fallback
- Feature flag for gradual rollout
- Rooms with 1 worker use old behavior (no mesh needed)

### Risk: Network Partition Handling
**Impact**: Worker can't reach admin but can reach other workers
**Mitigation**:
- Workers use stale CRDT (read-only mode) until partition heals
- Can still execute jobs with cached state
- Assumes workers typically on same network (low risk)

## Rollback Plan

If critical issues found after deployment:

**Step 1**: Disable room-level registry via feature flag
```python
ENABLE_ROOM_REGISTRY = False  # Revert to HTTP discovery
```

**Step 2**: Signaling server continues serving HTTP `/discover-workers`
- No client-side changes needed
- Workers fall back to old behavior

**Step 3**: Fix issues in separate branch
- Address bugs or performance problems
- Re-test thoroughly

**Step 4**: Re-enable with fixes
- Gradual rollout (10% → 50% → 100% of rooms)
- Monitor metrics and error rates

## Open Questions

### Q: Should we support rooms larger than 10 workers?
**A**: No, not initially. Full mesh becomes unwieldy beyond 10 workers (45 connections). If needed in future, consider hierarchical topology (admin + worker subgroups).

### Q: What if admin worker crashes mid-job?
**A**:
- Admin re-election happens automatically (<5s)
- New admin takes over coordination role
- Crashed admin's job continues on that worker (job execution independent of admin role)
- Client may need to re-query for updated admin peer_id

### Q: How to handle worker joining room during network partition?
**A**:
- New worker connects to reachable subset of workers
- Receives CRDT snapshot from local admin (if partitioned)
- When partition heals, CRDTs merge automatically
- No special handling needed (automerge handles this)

### Q: Should heartbeat interval be configurable?
**A**: Yes, make it configurable via environment variable:
```python
WORKER_HEARTBEAT_INTERVAL = os.getenv("WORKER_HEARTBEAT_INTERVAL", "5")  # seconds
```

### Q: What about NAT traversal for worker-to-worker connections?
**A**: Use same WebRTC ICE infrastructure as client-worker connections. STUN/TURN servers handle NAT traversal. No changes needed to NAT handling.
