# Testing Guide: Room-Based Client-Worker Connection

This guide provides step-by-step instructions for validating the room-based connection feature. Each test scenario includes setup steps, execution commands, and expected outcomes.

## Prerequisites

- Signaling server running (with room support)
- Valid SLEAP training package (.zip file)
- Terminal windows for each worker/client process

## Test 1: Worker Startup with Room Credential Printing

**Objective:** Verify that workers display both session strings and room credentials on startup.

**Steps:**
1. Start a worker:
   ```bash
   sleap-rtc worker
   ```

**Expected Output:**
```
================================================================================
Worker authenticated with server
================================================================================

Session string for DIRECT connection to this worker:
  eyJyIjogInJvb21faWQiLCAidCI6ICJ0b2tlbiIsICJwIjogInBlZXJfaWQifQ==

Room credentials for OTHER workers/clients to join this room:
  Room ID: room_abc123
  Token:   token_xyz789

Use session string with --session-string for direct connection
Use room credentials with --room-id and --token for worker discovery
================================================================================
```

**Validation:**
- [ ] Session string is displayed in base64 format
- [ ] Room ID and Token are shown separately
- [ ] Clear usage instructions are provided
- [ ] Format is easy to copy/paste

---

## Test 2: Multiple Workers Joining Same Room

**Objective:** Verify that multiple workers can join the same room using shared credentials.

**Steps:**
1. Start Worker 1:
   ```bash
   sleap-rtc worker
   ```
   Save the room credentials (room_id and token)

2. Start Worker 2 with same room credentials:
   ```bash
   sleap-rtc worker --room-id <room_id> --token <token>
   ```

3. Start Worker 3 with same credentials:
   ```bash
   sleap-rtc worker --room-id <room_id> --token <token>
   ```

**Expected Output:**
- Each worker authenticates successfully
- Each worker shows the same room_id but different peer_ids
- Each worker displays unique session strings

**Validation:**
- [ ] All workers join the same room
- [ ] Workers have different peer_ids
- [ ] Workers have different session strings
- [ ] All workers show status "available"

---

## Test 3: Client Interactive Worker Selection

**Objective:** Verify that clients can discover workers in a room and interactively select one.

**Setup:**
1. Start 3 workers in the same room (from Test 2)
2. Ensure each worker has different GPU configurations if possible

**Steps:**
1. Start client with room credentials:
   ```bash
   sleap-rtc client-train \
     --room-id <room_id> \
     --token <token> \
     --pkg-path /path/to/package.zip
   ```

**Expected Output:**
```
Discovering workers in room...
Found 3 available workers:

1. Worker peer_abc123
   GPU: NVIDIA RTX 4090 (24576 MB)
   Status: available
   Hostname: gpu-server-1

2. Worker peer_def456
   GPU: NVIDIA RTX 3090 (24576 MB)
   Status: available
   Hostname: gpu-server-2

3. Worker peer_ghi789
   GPU: NVIDIA GTX 1080 Ti (11264 MB)
   Status: available
   Hostname: gpu-workstation

Select worker (1-3) or 'r' to refresh:
```

**Validation:**
- [ ] All 3 workers are discovered
- [ ] Worker metadata displays correctly (GPU model, memory, status, hostname)
- [ ] Workers are numbered for selection
- [ ] Refresh option ('r') is available
- [ ] Entering a number (1-3) selects that worker
- [ ] Connection proceeds after selection

---

## Test 4: Client Auto-Select Mode

**Objective:** Verify that clients can automatically select the best worker by GPU memory.

**Setup:**
1. Start 3 workers with different GPU configurations

**Steps:**
1. Start client with auto-select flag:
   ```bash
   sleap-rtc client-train \
     --room-id <room_id> \
     --token <token> \
     --pkg-path /path/to/package.zip \
     --auto-select
   ```

**Expected Output:**
```
Discovering workers in room...
Found 3 available workers.
Auto-selecting worker with most GPU memory...
Selected worker: peer_abc123 (NVIDIA RTX 4090, 24576 MB)
Connecting to worker...
```

**Validation:**
- [ ] Worker with highest GPU memory is automatically selected
- [ ] No user interaction required
- [ ] Selection decision is logged
- [ ] Connection proceeds immediately

---

## Test 5: Backward Compatibility with Session Strings

**Objective:** Verify that existing session string workflow still works.

**Steps:**
1. Start a worker and copy its session string
2. Start client with session string:
   ```bash
   sleap-rtc client-train \
     --session-string <session_string> \
     --pkg-path /path/to/package.zip
   ```

**Expected Output:**
- Client parses session string successfully
- Client joins the worker's room automatically
- Client connects directly to specified worker (no discovery)
- Training proceeds normally

**Validation:**
- [ ] Session string is parsed correctly
- [ ] No worker discovery occurs (direct connection)
- [ ] Connection succeeds when worker is available
- [ ] Training job starts successfully

---

## Test 6: Worker Status Updates (available → busy → available)

**Objective:** Verify that worker status updates correctly during job lifecycle.

**Steps:**
1. Start a worker and observe initial status
2. Connect a client and start a job
3. Check worker status during job execution
4. Wait for job completion
5. Check worker status after job completes

**Expected Status Progression:**
```
Worker startup:        status = "available"
Client connects:       status = "reserved"
Job starts:            status = "busy"
Job completes:         status = "available"
```

**Validation:**
- [ ] Initial status is "available"
- [ ] Status changes to "reserved" when accepting connection
- [ ] Status changes to "busy" when job starts
- [ ] Status returns to "available" after job completes
- [ ] Status updates are visible to discovery queries

---

## Test 7: Refresh Functionality During Worker Selection

**Objective:** Verify that clients can refresh worker list during interactive selection.

**Setup:**
1. Start 1 worker in a room
2. Start client with room credentials (interactive mode)

**Steps:**
1. Client displays worker list with 1 worker
2. Without selecting, start a 2nd worker in same room
3. In client terminal, type 'r' to refresh
4. Verify 2nd worker now appears in list
5. Select a worker and proceed

**Expected Output:**
```
Found 1 available worker:
1. Worker peer_abc123 [...]

Select worker (1) or 'r' to refresh: r

Refreshing worker list...
Found 2 available workers:
1. Worker peer_abc123 [...]
2. Worker peer_def456 [...]

Select worker (1-2) or 'r' to refresh: 1
```

**Validation:**
- [ ] Refresh command ('r') triggers new discovery
- [ ] New workers appear in updated list
- [ ] Worker numbering updates correctly
- [ ] Selection works with updated list

---

## Test 8: Error Handling

### Test 8a: No Workers Available

**Steps:**
1. Start client with valid room credentials but NO workers running:
   ```bash
   sleap-rtc client-train \
     --room-id <room_id> \
     --token <token> \
     --pkg-path /path/to/package.zip
   ```

**Expected Output:**
```
Discovering workers in room...
No available workers found in room.
Please ensure workers are running and have status "available".
```

**Validation:**
- [ ] Error message is clear and actionable
- [ ] Client exits gracefully (no crash)
- [ ] Helpful suggestion provided

### Test 8b: Invalid Room Credentials

**Steps:**
1. Start client with invalid token:
   ```bash
   sleap-rtc client-train \
     --room-id <room_id> \
     --token invalid_token \
     --pkg-path /path/to/package.zip
   ```

**Expected Output:**
```
Failed to register with room: authentication failed
Error: Invalid room credentials
```

**Validation:**
- [ ] Authentication failure detected
- [ ] Error message indicates credential issue
- [ ] Client exits gracefully

---

## Test 9: Worker Rejects Session String Connection When Busy

**Objective:** Verify that busy workers reject direct connection attempts.

**Setup:**
1. Start Worker 1, save its session string
2. Start Client 1 with session string to occupy worker
3. Ensure job is running (worker status = "busy")

**Steps:**
1. Attempt to connect Client 2 with same session string:
   ```bash
   sleap-rtc client-train \
     --session-string <worker1_session_string> \
     --pkg-path /path/to/package.zip
   ```

**Expected Output (Client 2):**
```
Connecting to worker...
ERROR: Worker is currently busy. Please use --room-id and --token to discover available workers.
Connection rejected by worker.
```

**Expected Output (Worker 1 logs):**
```
Received offer SDP
Rejecting connection from peer_xyz789 - worker is busy
Sent busy rejection to client peer_xyz789
```

**Validation:**
- [ ] Worker detects its busy status
- [ ] Worker sends rejection message to client
- [ ] Client receives and displays error message
- [ ] Client exits gracefully
- [ ] Worker continues running first job uninterrupted

---

## Test 10: Worker Accepts Session String Connection When Available

**Objective:** Verify that available workers accept direct connections.

**Steps:**
1. Start Worker 1, save its session string
2. Verify worker status is "available"
3. Connect client with session string:
   ```bash
   sleap-rtc client-train \
     --session-string <worker1_session_string> \
     --pkg-path /path/to/package.zip
   ```

**Expected Output (Worker logs):**
```
Received offer SDP
Accepting connection from peer_xyz789 (status: available)
Worker status updated to 'reserved'
```

**Expected Output (Client):**
```
Connecting to worker...
Connection established.
Starting training job...
```

**Validation:**
- [ ] Worker accepts connection
- [ ] Worker status updates to "reserved" then "busy"
- [ ] Client connection succeeds
- [ ] Job starts successfully

---

## Test 11: Client Receives and Displays Busy Rejection Message

**Objective:** Verify that clients properly handle and display worker rejection messages.

**Setup:**
1. Set up scenario from Test 9 (busy worker)

**Steps:**
1. Attempt connection to busy worker via session string
2. Observe client error handling

**Expected Client Behavior:**
- [ ] Client detects rejection (error type: 'worker_busy')
- [ ] Client displays worker's error message
- [ ] Client shows current worker status
- [ ] Client suggests using room-based discovery
- [ ] Client exits cleanly without crash

**Implementation Check:**
- Verify `client_class.py` has error handler for 'error' message type
- Verify error messages are user-friendly
- Verify client cleans up WebRTC resources on rejection

---

## Test 12: Multiple Clients Attempting to Connect to Same Busy Worker

**Objective:** Verify that status check prevents race conditions with concurrent connections.

**Setup:**
1. Start Worker 1, save its session string

**Steps:**
1. Start Client 1 with session string (connection succeeds)
2. Immediately start Client 2 with same session string (should be rejected)
3. Immediately start Client 3 with same session string (should be rejected)
4. Observe worker behavior

**Expected Outcomes:**
- **Client 1**: Connection accepted, worker status → "reserved" → "busy"
- **Client 2**: Connection rejected ("worker is reserved/busy")
- **Client 3**: Connection rejected ("worker is busy")

**Validation:**
- [ ] Only first client connects successfully
- [ ] Subsequent clients receive rejection messages
- [ ] Worker status updates prevent race conditions
- [ ] No job conflicts or data corruption
- [ ] Worker continues Client 1's job without interruption

---

## Test Completion Checklist

After completing all tests:

- [ ] All 12 test scenarios pass
- [ ] No crashes or unexpected errors
- [ ] Error messages are clear and actionable
- [ ] Worker status updates correctly throughout lifecycle
- [ ] Session string backward compatibility maintained
- [ ] Room-based discovery works reliably
- [ ] Status check safeguard prevents concurrent connections
- [ ] Multiple workers can coexist in same room
- [ ] Client selection modes all work (interactive, auto-select, direct)

---

## Notes for Developers

### Logging Tips
Enable debug logging to see detailed connection flow:
```bash
export SLEAP_RTC_LOG_LEVEL=DEBUG
```

### Common Issues

1. **Workers not appearing in discovery:**
   - Verify room_id and token match exactly
   - Check worker status (must be "available")
   - Ensure signaling server is running

2. **Session string connection fails:**
   - Verify session string hasn't expired
   - Check worker is still running and available
   - Ensure room_id in session string matches worker's current room

3. **Status updates not working:**
   - Verify signaling server propagates status changes
   - Check worker's `update_status()` method is being called
   - Ensure discovery filters include `properties: {status: "available"}`

### Manual Testing Environment

For comprehensive testing, set up:
- 1 signaling server
- 3-4 workers with varying GPU configurations
- 2-3 terminal windows for clients
- Test package ready for quick job submission

### Automated Testing

Consider creating automated integration tests for:
- Session string parsing
- Room credential validation
- Worker discovery filtering
- Status update propagation
- Error message formatting
