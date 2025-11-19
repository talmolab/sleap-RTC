# sleap-rtc
- Remote training and inference w/ SLEAP
- Remote Authenticated CLI Training w/ SLEAP

## Configuration

SLEAP-RTC supports flexible configuration for different deployment environments (development, staging, production).

### Configuration Priority

Configuration is loaded in the following priority order (highest to lowest):

1. **CLI arguments** - Explicit command-line flags like `--server`
2. **Environment variables** - `SLEAP_RTC_SIGNALING_WS`, `SLEAP_RTC_SIGNALING_HTTP`
3. **Configuration file** - TOML file with environment-specific settings
4. **Defaults** - Production signaling server

### Environment Selection

Set the environment using the `SLEAP_RTC_ENV` environment variable:

```bash
export SLEAP_RTC_ENV=development  # Use development environment
export SLEAP_RTC_ENV=staging      # Use staging environment
export SLEAP_RTC_ENV=production   # Use production environment (default)
```

Valid environments: `development`, `staging`, `production`

### Configuration File

Create a configuration file at one of these locations:
- `sleap-rtc.toml` in your project directory
- `~/.sleap-rtc/config.toml` in your home directory

See `config.example.toml` for a complete example with all environments.

Example configuration:

```toml
[default]
# Shared settings across all environments
connection_timeout = 30
chunk_size = 65536

[environments.development]
signaling_websocket = "ws://localhost:8080"
signaling_http = "http://localhost:8001"

[environments.staging]
signaling_websocket = "ws://staging-server.example.com:8080"
signaling_http = "http://staging-server.example.com:8001"

[environments.production]
signaling_websocket = "ws://ec2-54-176-92-10.us-west-1.compute.amazonaws.com:8080"
signaling_http = "http://ec2-54-176-92-10.us-west-1.compute.amazonaws.com:8001"
```

### Environment Variable Overrides

Override specific settings using environment variables:

```bash
# Override WebSocket URL
export SLEAP_RTC_SIGNALING_WS="ws://custom-server.com:8080"

# Override HTTP API URL
export SLEAP_RTC_SIGNALING_HTTP="http://custom-server.com:8001"
```

### Usage Examples

```bash
# Use default production environment
sleap-rtc train data.slp

# Use development environment
SLEAP_RTC_ENV=development sleap-rtc train data.slp

# Use staging environment
SLEAP_RTC_ENV=staging sleap-rtc train data.slp

# Override with environment variable
SLEAP_RTC_SIGNALING_WS=ws://custom.com:8080 sleap-rtc train data.slp

# Override with CLI argument
sleap-rtc train data.slp --server ws://custom.com:8080
```

### Backward Compatibility

If no configuration is provided, SLEAP-RTC defaults to the production signaling server, maintaining backward compatibility with existing deployments.

## Performance & File Transfer

SLEAP-RTC supports two file transfer methods with automatic selection based on your environment:

### Transfer Methods

| Method | Speed (5 GB file) | Use Case | Setup Required |
|--------|-------------------|----------|----------------|
| **Shared Storage** | ~10-30 seconds | Large training packages (5-9 GB) | ✅ Yes - configure shared filesystem |
| **RTC Transfer** | ~15-30 minutes | Any environment | ❌ No - works out of the box |

**Automatic Selection:** SLEAP-RTC automatically uses the fastest available method:
- If shared storage is configured → **Shared Storage** (60-90x faster for large files)
- Otherwise → **RTC Transfer** (original method, fully backward compatible)

### Quick Setup: Shared Storage

For optimal performance with large files, configure shared storage:

```bash
# Set environment variable pointing to your shared filesystem
export SHARED_STORAGE_ROOT="/Volumes/talmo/amick"  # On Client (macOS/local)
export SHARED_STORAGE_ROOT="/home/jovyan/vast/amick"  # On Worker (Vast.ai)

# Or use CLI argument
sleap-rtc client --shared-storage-root /Volumes/talmo/amick
sleap-rtc worker --shared-storage-root /home/jovyan/vast/amick
```

**How It Works:**
- **Shared Storage:** Client writes files to shared filesystem, sends only paths over network
- **RTC Transfer:** Client sends files as chunked data over WebRTC data channel

**Requirements:**
- Both Client and Worker must have access to the same shared filesystem (NFS, local mount, etc.)
- Paths can differ (e.g., `/Volumes/talmo/amick` on macOS, `/home/jovyan/vast/amick` on Linux)

For detailed setup instructions, see [DEVELOPMENT.md - Shared Storage Configuration](DEVELOPMENT.md#shared-storage-configuration).

### Performance Benchmarks

Typical transfer times for training packages:

| File Size | Shared Storage | RTC Transfer | Speedup |
|-----------|----------------|--------------|---------|
| 500 MB    | ~5 seconds     | ~2 minutes   | 24x     |
| 2 GB      | ~10 seconds    | ~8 minutes   | 48x     |
| 5 GB      | ~20 seconds    | ~20 minutes  | 60x     |
| 9 GB      | ~30 seconds    | ~30 minutes  | 60x     |

*Benchmarks assume: Shared storage on local NFS/SSD (100+ MB/s), RTC transfer over internet (5-10 MB/s)*

### When to Use Each Method

**Use Shared Storage when:**
- ✅ Training packages are large (> 1 GB)
- ✅ Client and Worker have access to shared filesystem (Vast.ai, RunAI, local NFS)
- ✅ You need fast iteration cycles during model development
- ✅ You're doing batch training on multiple datasets

**Use RTC Transfer when:**
- ✅ No shared storage available (different networks, cloud instances)
- ✅ Files are small (< 100 MB)
- ✅ One-time training runs
- ✅ Quick testing without infrastructure setup

**Both methods coexist** - SLEAP-RTC automatically selects the best option and falls back gracefully.

## CLI Usage

SLEAP-RTC provides commands for running workers and clients for remote training and inference.

### Worker Commands

Start a worker to process training or inference jobs:

```bash
# Start a worker (creates a new room)
sleap-rtc worker

# Join an existing room (for multi-worker scenarios)
sleap-rtc worker --room-id <room_id> --token <token>
```

When a worker starts, it displays connection credentials:

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

### Client Commands

#### Training Client

Connect to a worker to run a training job:

```bash
# Option 1: Direct connection using session string
sleap-rtc client-train \
  --session-string <session_string> \
  --pkg-path /path/to/training_package.zip

# Option 2: Room-based discovery with interactive worker selection
sleap-rtc client-train \
  --room-id <room_id> \
  --token <token> \
  --pkg-path /path/to/training_package.zip

# Option 3: Auto-select best worker by GPU memory
sleap-rtc client-train \
  --room-id <room_id> \
  --token <token> \
  --pkg-path /path/to/training_package.zip \
  --auto-select

# Option 4: Connect to specific worker in room (skip discovery)
sleap-rtc client-train \
  --room-id <room_id> \
  --token <token> \
  --worker-id <peer_id> \
  --pkg-path /path/to/training_package.zip
```

Additional options:
- `--controller-port <port>`: ZMQ controller port (default: 9000)
- `--publish-port <port>`: ZMQ publish port (default: 9001)
- `--min-gpu-memory <MB>`: Filter workers by minimum GPU memory

#### Inference Client

Connect to a worker to run an inference job:

```bash
# Option 1: Direct connection using session string
sleap-rtc client-track \
  --session-string <session_string> \
  --pkg-path /path/to/inference_package.zip

# Option 2: Room-based discovery with interactive worker selection
sleap-rtc client-track \
  --room-id <room_id> \
  --token <token> \
  --pkg-path /path/to/inference_package.zip

# Option 3: Auto-select best worker by GPU memory
sleap-rtc client-track \
  --room-id <room_id> \
  --token <token> \
  --pkg-path /path/to/inference_package.zip \
  --auto-select
```

## Connection Workflows

### Two-Phase Connection Model

SLEAP-RTC supports a flexible two-phase connection workflow:

1. **Phase 1: Join Room** - Client authenticates with signaling server and joins a room
2. **Phase 2: Worker Discovery & Selection** - Client discovers available workers and selects one

This model provides several advantages:
- **Visibility**: See all available workers before connecting
- **Flexibility**: Choose workers based on capabilities (GPU memory, status, hostname)
- **Resilience**: If a worker is busy, easily discover and select alternatives
- **Multi-worker**: Support multiple workers in a single room for load balancing

### Connection Mode 1: Session String (Direct Connection)

Use when you have a session string from a specific worker:

```bash
# Worker displays session string on startup
sleap-rtc worker
# Copy the session string from output

# Client connects directly to that worker
sleap-rtc client-train --session-string <session_string> --pkg-path package.zip
```

**When to use:**
- Single worker scenarios
- Direct connection to a specific known worker
- Minimal configuration required

**Limitations:**
- If the worker is busy, connection will be rejected
- No worker discovery or selection capability
- Must obtain new session string if worker restarts

### Connection Mode 2: Room-Based Discovery (Interactive Selection)

Use when you want to see available workers and choose interactively:

```bash
# Start multiple workers in the same room
sleap-rtc worker  # Worker 1 creates room, displays credentials
sleap-rtc worker --room-id <room_id> --token <token>  # Worker 2 joins
sleap-rtc worker --room-id <room_id> --token <token>  # Worker 3 joins

# Client discovers and selects worker interactively
sleap-rtc client-train --room-id <room_id> --token <token> --pkg-path package.zip
```

**Interactive selection displays:**
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

**When to use:**
- Multiple workers available
- Want to see worker specifications before connecting
- Need to verify worker status before job submission
- Want to manually choose based on current availability

**Features:**
- Real-time worker information (GPU model, memory, status, hostname)
- Refresh capability to update worker list
- Only shows workers with status "available"

### Connection Mode 3: Auto-Select (Automatic Best Worker)

Use when you want the system to automatically choose the best worker:

```bash
sleap-rtc client-train \
  --room-id <room_id> \
  --token <token> \
  --pkg-path package.zip \
  --auto-select
```

**Behavior:**
- Discovers all available workers in the room
- Automatically selects worker with highest GPU memory
- No user interaction required
- Ideal for scripts and automated workflows

**When to use:**
- Automated training pipelines
- Scripts that need deterministic worker selection
- Prefer best hardware without manual selection

### Connection Mode 4: Direct Worker in Room

Use when you know the specific worker peer-id you want:

```bash
sleap-rtc client-train \
  --room-id <room_id> \
  --token <token> \
  --worker-id <peer_id> \
  --pkg-path package.zip
```

**Behavior:**
- Skips worker discovery
- Connects directly to specified worker by peer-id
- Still uses room credentials for authentication

**When to use:**
- You know the exact worker peer-id you need
- Want to target a specific worker without discovery overhead
- Scripted workflows with predetermined worker assignment

## Multi-Worker Scenarios

### Scenario 1: Load Balancing Across Multiple Workers

Set up multiple workers in a room for parallel job processing:

```bash
# Terminal 1: Start Worker 1 (creates room)
sleap-rtc worker
# Save room_id and token from output

# Terminal 2: Start Worker 2 (joins same room)
sleap-rtc worker --room-id <room_id> --token <token>

# Terminal 3: Start Worker 3 (joins same room)
sleap-rtc worker --room-id <room_id> --token <token>

# Terminal 4: Client 1 discovers and selects a worker
sleap-rtc client-train --room-id <room_id> --token <token> --pkg-path job1.zip

# Terminal 5: Client 2 discovers and selects different worker
sleap-rtc client-train --room-id <room_id> --token <token> --pkg-path job2.zip
```

**Result:** Each client can independently select from available workers, enabling parallel job execution.

### Scenario 2: Heterogeneous Worker Pool

Workers with different GPU configurations can coexist in a room:

```bash
# High-end worker (RTX 4090)
sleap-rtc worker --room-id shared_room --token shared_token

# Mid-tier worker (RTX 3090)
sleap-rtc worker --room-id shared_room --token shared_token

# Budget worker (GTX 1080 Ti)
sleap-rtc worker --room-id shared_room --token shared_token

# Client auto-selects best worker (RTX 4090)
sleap-rtc client-train \
  --room-id shared_room \
  --token shared_token \
  --pkg-path large_job.zip \
  --auto-select
```

**Features:**
- Clients can filter by `--min-gpu-memory` to ensure sufficient resources
- Auto-select automatically chooses worker with most GPU memory
- Interactive mode shows GPU specs for informed selection

### Scenario 3: High-Availability Setup

If a worker becomes unavailable, clients can easily discover alternatives:

```bash
# Client attempts connection to Worker 1 via session string
sleap-rtc client-train --session-string <worker1_session> --pkg-path job.zip
# ERROR: Worker is currently busy

# Client falls back to room-based discovery
sleap-rtc client-train --room-id <room_id> --token <token> --pkg-path job.zip
# SUCCESS: Discovers Worker 2 and Worker 3 are available, selects Worker 2
```

## Worker Status and Safeguards

### Worker Status Lifecycle

Workers maintain status to coordinate connections and prevent conflicts:

| Status      | Description                                      | Accepts New Connections? |
|-------------|--------------------------------------------------|--------------------------|
| `available` | Worker is idle and ready to accept jobs         | ✅ Yes                   |
| `reserved`  | Worker accepted connection, negotiating job     | ❌ No                    |
| `busy`      | Worker is actively processing a job             | ❌ No                    |

**Status transitions:**
```
available → reserved → busy → available
    ↑                            ↓
    └────────────────────────────┘
```

### Busy Rejection Behavior

When a client attempts to connect to a busy or reserved worker (e.g., via session string), the worker will reject the connection:

**Client output:**
```
Connecting to worker...
ERROR: Worker is currently busy. Please use --room-id and --token to discover available workers.
Connection rejected by worker.
```

**Worker output:**
```
Received offer SDP
Rejecting connection from peer_xyz789 - worker is busy
Sent busy rejection to client peer_xyz789
```

**Why this matters:**
- **Prevents job conflicts**: Multiple clients cannot interfere with each other's jobs
- **Protects data integrity**: Ensures one job completes before starting another
- **Clear error messages**: Clients receive actionable feedback
- **Room-based alternative**: Rejection message suggests using room discovery to find available workers

### Best Practices

1. **Use room-based discovery for production**: More resilient to worker availability changes
2. **Session strings for development**: Convenient for testing with a single known worker
3. **Auto-select for automation**: Deterministic worker selection in scripts
4. **Check worker status**: Room-based discovery only shows "available" workers
5. **Multi-worker for availability**: Deploy multiple workers to handle concurrent jobs
6. **GPU filtering**: Use `--min-gpu-memory` to ensure workers have sufficient resources
