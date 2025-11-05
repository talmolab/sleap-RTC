## ADDED Requirements

### Requirement: Room-Based Client Connection

Clients SHALL be able to connect to rooms using room credentials (room-id and token) without specifying a target worker peer-id.

#### Scenario: Client joins room with room credentials
- **WHEN** client provides `--room-id` and `--token` options
- **THEN** client authenticates with signaling server and joins the room
- **AND** client does not establish WebRTC connection yet
- **AND** client is ready to discover workers in the room

#### Scenario: Client connection without room credentials or session string
- **WHEN** client provides neither session string nor room credentials
- **THEN** client returns error message
- **AND** error message explains required options

### Requirement: Session String Backward Compatibility

Clients SHALL continue to support direct worker connection via session strings for backward compatibility.

#### Scenario: Client connects with session string
- **WHEN** client provides `--session-string` option
- **THEN** client parses room-id, token, and worker peer-id from encoded string
- **AND** client connects directly to specified worker
- **AND** client skips worker discovery phase

#### Scenario: Client provides both session string and room credentials
- **WHEN** client provides both `--session-string` and `--room-id` options
- **THEN** client returns validation error
- **AND** error message explains options are mutually exclusive

### Requirement: Two-Phase Connection Model

Clients using room-based connection SHALL follow a two-phase model: (1) join room and discover workers, (2) select worker and establish WebRTC connection.

#### Scenario: Successful two-phase connection
- **WHEN** client joins room with room credentials
- **THEN** client discovers available workers in room
- **AND** client selects target worker based on selection mode
- **AND** client establishes WebRTC connection to selected worker
- **AND** client proceeds with job submission

#### Scenario: No workers available in room
- **WHEN** client joins room but no workers are available
- **THEN** client returns error message
- **AND** error message indicates no available workers found
- **AND** error message suggests checking worker status or waiting
