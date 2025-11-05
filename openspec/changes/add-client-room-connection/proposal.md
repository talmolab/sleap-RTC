## Why

Currently, clients must use session strings that encode a specific worker's peer_id, creating a tight coupling between client and worker. This has several limitations:
- Clients cannot see or choose from multiple available workers in a room
- No visibility into worker status (busy/available) before connection
- Difficult to support multiple workers in the same room competing for jobs
- Poor experience when the target worker is busy or offline
- No support for load balancing across workers

The primary use case requires many workers and at least one client connected to the same room, where clients can query which workers are available and select the best one for their job.

## What Changes

- **Two-phase client connection**: Clients join rooms first, then discover and select workers before job submission
- **Room-based worker discovery**: Clients can query all available workers in a room with their capabilities (GPU model, memory, status)
- **Worker selection modes**:
  - Interactive: Display list of workers and prompt user to select
  - Auto-select: Automatically choose best worker based on GPU memory
  - Direct: Specify worker-id if known
  - Session string (backward compatible): Existing session string workflow continues to work
- **CLI enhancements**:
  - `--room-id` and `--token` options for room-based connection
  - `--worker-id` to specify a particular worker
  - `--auto-select` flag for automatic worker selection
  - Existing `--session-string` remains for backward compatibility
- **Real-time status updates**: Workers update their status (available/busy/reserved) via signaling server
- **Room credential sharing**: Workers print room credentials (room-id and token) for other workers and clients to join

## Impact

- Affected specs: client-connection, worker-discovery, cli (new capabilities)
- Affected code:
  - `sleap_rtc/cli.py:55-211` - Add room-based connection options
  - `sleap_rtc/rtc_client.py:8-44` - Add room credentials and worker selection parameters
  - `sleap_rtc/rtc_worker.py:11-42` - Print room credentials for sharing
  - `sleap_rtc/client/client_class.py:608-704,917-1070` - Implement worker discovery and selection
  - `sleap_rtc/worker/worker_class.py:1437-1536` - Print room credentials on startup
- **Backward compatible**: Existing session string workflow continues to work
- **Breaking changes**: None - all changes are additive
