## 1. Worker Room Credential Sharing

- [ ] 1.1 Modify `sleap_rtc/rtc_worker.py` to print room credentials after room creation
- [ ] 1.2 Update `sleap_rtc/worker/worker_class.py:run_worker()` to log room-id and token separately from session string
- [ ] 1.3 Add clear formatting to distinguish session string from room credentials in worker output

## 2. CLI Interface Updates

- [ ] 2.1 Add `--room-id` and `--token` options to `client-train` command in `sleap_rtc/cli.py`
- [ ] 2.2 Add `--room-id` and `--token` options to `client-track` command
- [ ] 2.3 Add `--worker-id` option to specify particular worker
- [ ] 2.4 Add `--auto-select` flag for automatic worker selection
- [ ] 2.5 Add validation logic to ensure mutually exclusive option groups (session-string vs room credentials)
- [ ] 2.6 Update help text to document new connection modes

## 3. Client Entry Point Modifications

- [ ] 3.1 Update `run_RTCclient()` signature in `sleap_rtc/rtc_client.py` to accept room credentials
- [ ] 3.2 Add worker selection mode parameters (auto_select, worker_id)
- [ ] 3.3 Pass new parameters to `client.run_client()`
- [ ] 3.4 Update `run_RTCclient_track()` with same changes for inference workflow

## 4. Room-Based Connection Logic

- [ ] 4.1 Add `room_credentials` parameter to `RTCClient.run_client()` method
- [ ] 4.2 Implement branching logic to support both session string and room-based connections
- [ ] 4.3 Create `register_with_room()` method for room registration without specific worker
- [ ] 4.4 Implement `discover_workers_in_room()` method to query workers in current room

## 5. Worker Selection Implementation

- [ ] 5.1 Implement `prompt_worker_selection()` for interactive worker selection
- [ ] 5.2 Display worker list with GPU model, memory, status, and hostname
- [ ] 5.3 Add "refresh" command to re-query workers
- [ ] 5.4 Implement `auto_select_worker()` to choose best worker by GPU memory
- [ ] 5.5 Add logging for worker selection decisions

## 6. Worker Discovery Integration

- [ ] 6.1 Verify existing `discover_workers()` method works with room-based filters
- [ ] 6.2 Add room-scoped discovery queries to signaling server
- [ ] 6.3 Handle discovery responses and parse worker metadata
- [ ] 6.4 Filter workers by status (only show "available" workers)

## 7. Worker-Side Status Check Safeguard

- [ ] 7.1 Add status check in `worker_class.py:handle_connection()` before accepting WebRTC offers
- [ ] 7.2 Implement rejection logic when worker status is "busy" or "reserved"
- [ ] 7.3 Send error response to client via signaling server with busy status
- [ ] 7.4 Include helpful error message suggesting room-based discovery
- [ ] 7.5 Update worker status to "reserved" when accepting connection
- [ ] 7.6 Add logging for connection acceptance/rejection decisions

## 8. Testing and Validation

- [ ] 8.1 Test worker startup with room credential printing
- [ ] 8.2 Test multiple workers joining same room
- [ ] 8.3 Test client interactive worker selection
- [ ] 8.4 Test client auto-select mode
- [ ] 8.5 Test backward compatibility with session strings
- [ ] 8.6 Test worker status updates (available → busy → available)
- [ ] 8.7 Test refresh functionality during worker selection
- [ ] 8.8 Test error handling (no workers available, invalid room credentials)
- [ ] 8.9 Test worker rejects session string connection when busy
- [ ] 8.10 Test worker accepts session string connection when available
- [ ] 8.11 Test client receives and displays busy rejection message
- [ ] 8.12 Test multiple clients attempting to connect to same busy worker

## 9. Documentation

- [ ] 9.1 Update README.md with new connection workflow examples
- [ ] 9.2 Document room-based connection options
- [ ] 9.3 Add examples showing multi-worker scenarios
- [ ] 9.4 Update CLI help text and usage examples
- [ ] 9.5 Document worker status check safeguard and busy rejection behavior
