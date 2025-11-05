## ADDED Requirements

### Requirement: Room-Scoped Worker Discovery

Clients SHALL be able to discover all available workers within their connected room with worker capabilities and status information.

#### Scenario: Discover multiple available workers
- **WHEN** client requests worker discovery in room
- **THEN** client receives list of workers with role "worker"
- **AND** each worker includes metadata with GPU model, memory, CUDA version, and status
- **AND** only workers with status "available" are included
- **AND** workers with status "busy" or "reserved" are filtered out

#### Scenario: Worker status updates during discovery
- **WHEN** worker status changes from "busy" to "available"
- **AND** client refreshes worker list
- **THEN** newly available worker appears in discovery results

### Requirement: Interactive Worker Selection

Clients SHALL provide interactive prompt to display workers and allow user selection.

#### Scenario: Display worker capabilities for selection
- **WHEN** client displays worker list in interactive mode
- **THEN** each worker shows peer-id, GPU model, GPU memory, CUDA version, and hostname
- **AND** workers are numbered for easy selection
- **AND** user can select worker by number
- **AND** user can type "refresh" to re-query workers

#### Scenario: User selects worker by number
- **WHEN** user enters valid worker number
- **THEN** client sets selected worker as target
- **AND** client proceeds with WebRTC connection

#### Scenario: User refreshes worker list
- **WHEN** user types "refresh" at selection prompt
- **THEN** client re-queries signaling server for worker list
- **AND** client displays updated worker list
- **AND** user can select from refreshed list

### Requirement: Automatic Worker Selection

Clients SHALL support automatic worker selection based on GPU memory when `--auto-select` flag is provided.

#### Scenario: Auto-select best worker by GPU memory
- **WHEN** client uses `--auto-select` flag
- **AND** multiple workers are available
- **THEN** client selects worker with highest GPU memory
- **AND** client logs selected worker details
- **AND** client proceeds with WebRTC connection without user prompt

#### Scenario: Auto-select with no available workers
- **WHEN** client uses `--auto-select` flag
- **AND** no workers are available in room
- **THEN** client raises `NoWorkersAvailableError`
- **AND** error message indicates no workers found

### Requirement: Direct Worker Selection

Clients SHALL support direct worker selection via `--worker-id` option for targeting specific machines.

#### Scenario: Select worker by peer-id
- **WHEN** client provides `--worker-id` option with valid peer-id
- **THEN** client sets specified worker as target
- **AND** client skips worker discovery and interactive selection
- **AND** client proceeds with WebRTC connection

#### Scenario: Invalid worker-id provided
- **WHEN** client provides `--worker-id` with peer-id not in room
- **THEN** client returns error during connection attempt
- **AND** error message indicates worker not found
