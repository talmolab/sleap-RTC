## ADDED Requirements

### Requirement: Room Credential CLI Options

The `client-train` and `client-track` commands SHALL accept `--room-id` and `--token` options for room-based connection.

#### Scenario: Client train with room credentials
- **WHEN** user runs `sleap-rtc client-train --room-id ROOM --token TOKEN --pkg-path PATH`
- **THEN** client connects to room using provided credentials
- **AND** client enters worker discovery and selection flow

#### Scenario: Client track with room credentials
- **WHEN** user runs `sleap-rtc client-track --room-id ROOM --token TOKEN --data-path PATH --model-paths M1 M2`
- **THEN** client connects to room using provided credentials
- **AND** client enters worker discovery and selection flow

#### Scenario: Room-id without token
- **WHEN** user provides `--room-id` without `--token`
- **THEN** CLI returns validation error
- **AND** error message indicates both options are required together

#### Scenario: Token without room-id
- **WHEN** user provides `--token` without `--room-id`
- **THEN** CLI returns validation error
- **AND** error message indicates both options are required together

### Requirement: Worker Selection Mode Options

The client commands SHALL accept `--worker-id` and `--auto-select` options to control worker selection.

#### Scenario: Auto-select flag for automatic selection
- **WHEN** user provides `--auto-select` flag with room credentials
- **THEN** client automatically selects best worker by GPU memory
- **AND** client skips interactive selection prompt

#### Scenario: Worker-id for direct worker targeting
- **WHEN** user provides `--worker-id PEER_ID` with room credentials
- **THEN** client connects directly to specified worker
- **AND** client skips worker discovery and interactive selection

#### Scenario: Both auto-select and worker-id provided
- **WHEN** user provides both `--auto-select` and `--worker-id` options
- **THEN** CLI returns validation error
- **AND** error message indicates options are mutually exclusive

### Requirement: Mutual Exclusion Validation

The CLI SHALL enforce mutual exclusion between session string and room credential options.

#### Scenario: Session string with room credentials
- **WHEN** user provides both `--session-string` and `--room-id` options
- **THEN** CLI returns validation error before connection attempt
- **AND** error message explains session string and room credentials cannot be used together

#### Scenario: Session string with worker selection options
- **WHEN** user provides `--session-string` with `--auto-select` or `--worker-id`
- **THEN** CLI returns validation error
- **AND** error message explains session string already encodes target worker

### Requirement: Worker Room Credential Output

Workers SHALL print room credentials in addition to session strings to enable room-based client connections.

#### Scenario: Worker prints room credentials on startup
- **WHEN** worker successfully creates or joins room
- **THEN** worker logs session string for backward compatibility
- **AND** worker logs room-id and token separately
- **AND** output clearly labels session string vs room credentials
- **AND** output explains room credentials are for other workers/clients to join

#### Scenario: Multiple workers join same room
- **WHEN** second worker joins room with room-id and token
- **THEN** second worker prints session string and room credentials
- **AND** both workers show same room-id and token
- **AND** each worker shows unique peer-id in session string

### Requirement: Worker Status Check on Connection

Workers SHALL check their status before accepting WebRTC connections to prevent concurrent job conflicts.

#### Scenario: Worker rejects connection when busy
- **WHEN** worker receives WebRTC offer (type: "offer")
- **AND** worker status is "busy" or "reserved"
- **THEN** worker SHALL NOT accept the connection
- **AND** worker sends error message to client via signaling server
- **AND** error message indicates worker is currently busy
- **AND** error message suggests using room-based discovery to find available workers

#### Scenario: Worker accepts connection when available
- **WHEN** worker receives WebRTC offer
- **AND** worker status is "available"
- **THEN** worker proceeds with normal offer/answer flow
- **AND** worker updates status to "reserved" or "busy"
- **AND** worker establishes WebRTC connection

#### Scenario: Client receives busy rejection
- **WHEN** client sends offer to busy worker via session string
- **AND** worker rejects connection due to busy status
- **THEN** client receives error message from signaling server
- **AND** client logs clear error message
- **AND** client suggests using `--room-id` and `--token` for worker discovery
