## Context

sleap-RTC currently uses session strings to encode room credentials and a specific worker's peer_id, creating tight coupling between client and worker. This design works for single-worker scenarios but doesn't scale to environments with multiple workers in a room where clients need to:
- See available workers and their capabilities
- Choose the best worker for their workload
- Retry with another worker if the first choice is busy

The signaling server already supports worker discovery via v2.0 protocol (peer messages, metadata, status updates). We need to expose this capability at the CLI/client layer while maintaining backward compatibility with session strings.

## Goals / Non-Goals

### Goals
- Enable clients to join rooms and discover multiple workers
- Provide interactive worker selection with capability visibility
- Support automatic worker selection based on GPU memory
- Maintain backward compatibility with session string workflow
- Real-time worker status updates during selection

### Non-Goals
- Multi-job scheduling or queue management (future work)
- Worker health monitoring or heartbeats (signaling server handles this)
- Room management CLI (create/delete rooms remains worker-initiated)
- Load balancing across multiple concurrent jobs (out of scope)

## Decisions

### Decision 1: Two-Phase Connection Model

**Choice**: Split client connection into two phases: (1) join room + discover workers, (2) select worker + establish WebRTC connection.

**Rationale**:
- Allows clients to see worker capabilities before committing
- Supports interactive selection and auto-selection modes
- Enables real-time status updates during decision-making
- Natural extension of existing worker discovery protocol

**Alternatives considered**:
- Single-phase with immediate connection: Doesn't allow worker comparison
- Separate `discover` CLI command: Extra step, more complex UX
- Session strings only: Doesn't scale to multi-worker scenarios

### Decision 2: CLI Option Design

**Choice**: Add `--room-id` and `--token` as mutually exclusive alternative to `--session-string`.

**Rationale**:
- Clear separation between room-based and direct connection modes
- Session strings remain for backward compatibility and scripting
- Room credentials are easier to share than session strings (no base64 encoding)
- Validation ensures users don't mix incompatible options

**Alternatives considered**:
- New `connect` subcommand: Too many commands, harder to discover
- Always require session strings: Doesn't solve the multi-worker problem
- Implicit mode detection: Confusing error messages

### Decision 3: Worker Selection Modes

**Choice**: Support three selection modes: interactive (default), auto-select (flag), direct (worker-id).

**Rationale**:
- Interactive mode provides best UX for exploratory use
- Auto-select enables scripting and automation
- Direct worker-id allows targeting specific machines
- All modes use same underlying discovery mechanism

**Implementation**:
```python
if worker_selection['mode'] == 'auto':
    worker = auto_select_worker(workers)
elif worker_selection.get('worker_id'):
    worker = worker_id
else:
    worker = prompt_worker_selection(workers)
```

### Decision 4: Room Credential Printing

**Choice**: Workers print both session strings (for backward compat) and separate room credentials.

**Rationale**:
- Session strings encode worker-specific peer_id (direct connection)
- Room credentials allow joining without targeting specific worker
- Both outputs support different use cases
- Clear labeling prevents confusion

**Format**:
```
[INFO] Worker authenticated. Session string for direct connection:
sleap-session:eyJyIjogInJvb20t...

[INFO] Room credentials for other workers/clients to join:
[INFO]   Room ID: room-abc123
[INFO]   Token: tok-xyz789
```

### Decision 5: Backward Compatibility Strategy

**Choice**: Keep all existing session string logic intact, add room-based path as new branch.

**Rationale**:
- Zero risk to existing workflows
- Allows gradual migration
- Users can choose based on use case
- Easy to test both paths independently

**Implementation**:
```python
if session_string:
    # Existing path: parse session string, connect directly
    session_data = parse_session_string(session_string)
    target_worker = session_data['peer_id']
elif room_credentials:
    # New path: discover workers, select, then connect
    workers = discover_workers_in_room(room_id)
    target_worker = select_worker(workers, mode)
```

## Risks / Trade-offs

### Risk: Race Conditions During Worker Selection

**Description**: Worker might become busy between discovery and connection.

**Mitigation**:
- Use "reserved" status during job negotiation (already implemented in v2.0 protocol)
- Client receives rejection if worker no longer available
- Interactive mode allows user to select different worker
- Auto-select can retry with next best worker

### Risk: Longer Time-to-First-Job

**Trade-off**: Two-phase connection adds latency vs. direct session string.

**Justification**:
- Interactive selection is inherently user-paced
- Auto-select adds ~1-2 seconds for discovery (acceptable)
- Benefit of seeing worker status outweighs latency cost
- Users wanting fastest path can still use session strings

### Risk: WebSocket Connection Timeout

**Description**: Client keeps connection open during interactive selection.

**Mitigation**:
- Signaling server already handles long-lived connections
- Keep-alive messages prevent timeout (already implemented)
- Set reasonable user prompt timeout (e.g., 5 minutes)
- Clear error message if connection drops during selection

### Risk: Confusion Between Connection Modes

**Description**: Users might mix `--session-string` with `--room-id`.

**Mitigation**:
- CLI validation enforces mutually exclusive groups
- Clear error messages explain options
- Updated documentation with decision tree
- Help text shows examples for each mode

## Migration Plan

### Phase 1: Implementation (This Change)
1. Add room-based connection options to CLI
2. Implement worker discovery and selection logic
3. Update worker output to print room credentials
4. Add comprehensive testing for both paths

### Phase 2: Documentation
1. Update README with room-based workflow examples
2. Add troubleshooting guide for worker selection
3. Document when to use each connection mode
4. Create video/tutorial for multi-worker setup

### Phase 3: Adoption (Post-deployment)
1. Session strings remain default in docs initially
2. Promote room-based workflow for multi-worker setups
3. Collect user feedback on selection UX
4. Consider making room-based default in future release

### Rollback Strategy
- Feature flag (environment variable) to disable room-based path if issues found
- Session string path remains unchanged, always available as fallback
- No database/state changes, so rollback is just code revert

## Open Questions

- [ ] Should we limit how long clients can stay in selection phase before timeout?
  - **Tentative**: 5-minute timeout with clear warning message

- [ ] Should `--auto-select` be default for non-interactive terminals (scripting)?
  - **Tentative**: No, require explicit flag to avoid surprises

- [ ] Should we add `--min-gpu-memory` filter to room-based discovery?
  - **Tentative**: Yes, consistent with existing `--discover-workers` flag

- [ ] How should we handle rooms with zero available workers?
  - **Tentative**: Clear error message, suggest checking worker status or waiting

- [ ] Should we show "busy" workers in the list with grayed-out status?
  - **Tentative**: No, only show available workers to reduce confusion
