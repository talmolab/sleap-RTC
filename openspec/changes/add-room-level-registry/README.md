# Room-Level Decentralized Registry

## Quick Summary

**What**: Decentralized worker coordination using peer-to-peer WebRTC mesh + CRDT state synchronization

**Why**: Reduce signaling server load by 80%, lower discovery latency from ~100ms to <10ms, eliminate DynamoDB costs

**How**: Workers form full mesh in room, elect admin for coordination, use automerge for conflict-free state replication

**Status**: ✅ Design finalized, ready for implementation after worker modularization

---

## Key Documents

📋 **[proposal.md](./proposal.md)** - High-level overview, motivation, impact, success criteria

📐 **[design.md](./design.md)** - Detailed architecture, decisions, trade-offs, diagrams

✅ **[tasks.md](./tasks.md)** - Step-by-step implementation plan (120+ tasks, 9 phases)

---

## Architecture at a Glance

```
Room "abc123" with 4 workers:

┌─────────────────────────────────────────────────────────┐
│                    Full Mesh Topology                   │
│                                                         │
│    W1 (Admin) ←→ W2 ←→ W3 ←→ W4                       │
│       ↓ ↖         ↓ ↖    ↓ ↖                           │
│       ↓   ↖       ↓   ↖  ↓   ↖                         │
│       ↓     ↖─────┴─────┴─────↘                        │
│       ↓                                                 │
│    CRDT Document (automerge)                           │
│    {                                                    │
│      "workers": {                                       │
│        "W1": {status: "available", gpu: 24GB, ...},    │
│        "W2": {status: "busy", gpu: 16GB, ...},         │
│        "W3": {status: "available", gpu: 24GB, ...},    │
│        "W4": {status: "reserved", gpu: 16GB, ...}      │
│      },                                                 │
│      "admin_peer_id": "W1"                             │
│    }                                                    │
└─────────────────────────────────────────────────────────┘

Client queries admin:
  Client → Signaling Server (relay) → W1 (Admin)
  W1 filters CRDT → returns available workers [W1, W3]
```

---

## Core Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Topology** | Full mesh (all-to-all) | Simple, low latency, no single point of failure |
| **State Sync** | CRDT (automerge) | Conflict-free, automatic merging, battle-tested |
| **Admin Election** | Deterministic priority | No network communication, all workers agree |
| **Room Size** | Max 10 workers | Mesh feasible (45 connections), realistic use case |
| **Partition Handling** | Stale CRDT (read-only) | Simple, jobs continue, syncs on heal |
| **Admin Role** | Also available worker | Maximizes resource utilization |

---

## Implementation Timeline

**Prerequisites**: Worker modularization (separate PR)

**Phase 1**: CRDT Integration (3-4 days)
- Add automerge-py dependency
- Create CRDT wrapper with worker state schema
- Test merge operations

**Phase 2**: Admin Election (4-5 days)
- Implement deterministic election algorithm
- Handle admin departure and re-election
- Test election scenarios

**Phase 3**: Mesh Connections (5-6 days)
- Multiple WebRTC peer connections per worker
- Connection lifecycle management
- Message routing by peer_id

**Phase 4**: State Synchronization (4-5 days)
- Event-driven status updates
- Periodic heartbeat (every 5 seconds)
- CRDT merge and broadcast

**Phase 5**: Client Integration (3-4 days)
- Client queries admin for discovery
- Admin filters CRDT by criteria
- Backwards compatibility (HTTP fallback)

**Phase 6**: Signaling Server Updates (3-4 days)
- Return peer list on room registration
- Enforce room size limits (max 10)
- Track admin_peer_id in room metadata

**Phase 7**: Integration Testing (4-5 days)
- Full mesh formation (10 workers)
- Admin re-election scenarios
- State convergence under load
- Network partition handling

**Phase 8**: Deployment (2-3 days)
- Feature flag implementation
- Gradual rollout (10% → 50% → 100%)
- Monitoring and metrics

**Phase 9**: Final Validation (2-3 days)
- Load testing (100 concurrent rooms)
- Failure scenario testing
- Security review

**Total**: 3-4 weeks

---

## Success Metrics

### Performance
- ✅ **80% reduction** in signaling server HTTP traffic
- ✅ **<10ms** worker discovery latency (vs ~100ms HTTP)
- ✅ **Zero** DynamoDB query costs for discovery
- ✅ **<5 seconds** admin re-election time

### Reliability
- ✅ Admin failure handled automatically
- ✅ Network partitions tolerated (stale read-only mode)
- ✅ No data loss on worker crashes
- ✅ 8-10 workers per room supported

### Compatibility
- ✅ Gradual migration (no breaking changes)
- ✅ HTTP endpoint maintained for old clients
- ✅ Admin worker can execute jobs
- ✅ Backwards compatible with current workers

---

## Dependencies

**New Libraries**:
- `automerge-py` - CRDT implementation

**Prerequisites**:
- Worker modularization complete (`refactor-worker-modular`)
- Signaling server supports peer list API

**Updated Components**:
- `sleap_rtc/worker/worker_class.py` - Mesh integration
- `sleap_rtc/client/worker_discovery.py` - Admin query
- `sleap_rtc/signaling_server/room_manager.py` - Peer list API

---

## Risks & Mitigations

**Risk**: CRDT complexity
- **Mitigation**: Use automerge (battle-tested), comprehensive tests

**Risk**: Full mesh scalability
- **Mitigation**: Hard limit 10 workers/room, can switch to hierarchical later

**Risk**: Admin election split-brain
- **Mitigation**: Deterministic algorithm ensures all workers agree

**Risk**: Backwards compatibility breakage
- **Mitigation**: Feature flag, gradual rollout, HTTP fallback

---

## Testing Strategy

**Unit Tests** (~800 lines):
- CRDT operations (create, merge, serialize)
- Admin election (various scenarios)
- Mesh coordinator (connection lifecycle)

**Integration Tests** (~600 lines):
- Full mesh formation (2, 5, 10 workers)
- Admin re-election on departure
- State convergence under concurrent updates
- Client discovery via admin

**Performance Tests** (~400 lines):
- Discovery latency (P50, P95, P99)
- State propagation time
- Admin re-election time
- Signaling server load reduction

**Failure Tests** (~400 lines):
- Admin crashes mid-broadcast
- Network partition scenarios
- Signaling server restart
- Worker churn (rapid join/leave)

---

## Next Steps

1. ✅ Review and approve this OpenSpec proposal
2. ⏳ Complete worker modularization (prerequisite)
3. 🔨 Begin Phase 1: CRDT Integration
4. 🔨 Iterate through Phases 2-9
5. 🚀 Deploy to production with gradual rollout

---

## Questions?

See:
- **[proposal.md](./proposal.md)** for high-level overview
- **[design.md](./design.md)** for detailed decisions and trade-offs
- **[tasks.md](./tasks.md)** for step-by-step implementation plan

All critical design decisions have been finalized. Ready to implement! 🎯
