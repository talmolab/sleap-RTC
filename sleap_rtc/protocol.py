"""Message protocol definitions for SLEAP-RTC.

This module defines the message types and protocol used for communication between
Client and Worker peers over WebRTC data channels.

Message Protocol Overview
-------------------------

SLEAP-RTC uses two transfer methods:

1. **RTC Transfer** (original): Sends files as chunked binary data over WebRTC
2. **Shared Storage Transfer** (new): Sends only file paths, files accessed via shared filesystem

Transfer Method Selection
-------------------------

The Client automatically selects the transfer method:
- If shared storage is configured on both Client and Worker: Use shared storage transfer
- Otherwise: Use RTC transfer (backward compatible)

Message Types
-------------

### Shared Storage Transfer Messages

These messages are used when both Client and Worker have shared storage configured.

**JOB_ID::{job_id}**
    Sent by: Client
    Purpose: Identifies a unique job for file transfer tracking
    Format: "JOB_ID::job_abc123"
    Example: "JOB_ID::job_f3a8b2c1"

**SHARED_INPUT_PATH::{relative_path}**
    Sent by: Client
    Purpose: Tells Worker where to find input file in shared storage
    Format: "SHARED_INPUT_PATH::jobs/{job_id}/{filename}"
    Example: "SHARED_INPUT_PATH::jobs/job_f3a8b2c1/training.zip"
    Note: Path is relative to shared storage root

**SHARED_OUTPUT_PATH::{relative_path}**
    Sent by: Client
    Purpose: Tells Worker where to write output files in shared storage
    Format: "SHARED_OUTPUT_PATH::jobs/{job_id}/output"
    Example: "SHARED_OUTPUT_PATH::jobs/job_f3a8b2c1/output"
    Note: Path is relative to shared storage root

**SHARED_STORAGE_JOB::start**
    Sent by: Client
    Purpose: Signals Worker to begin processing using shared storage paths
    Format: "SHARED_STORAGE_JOB::start"

**PATH_VALIDATED::{path_type}**
    Sent by: Worker
    Purpose: Confirms path is valid and accessible
    Format: "PATH_VALIDATED::input" or "PATH_VALIDATED::output"
    Example: "PATH_VALIDATED::input"

**PATH_ERROR::{error_message}**
    Sent by: Worker
    Purpose: Reports path validation failure
    Format: "PATH_ERROR::{description}"
    Example: "PATH_ERROR::Input path does not exist: jobs/job_123/data.zip"

**JOB_COMPLETE::{job_id}::{relative_output_path}**
    Sent by: Worker
    Purpose: Notifies Client that job is complete, output is ready
    Format: "JOB_COMPLETE::{job_id}::{relative_path}"
    Example: "JOB_COMPLETE::job_f3a8b2c1::jobs/job_f3a8b2c1/output"

### RTC Transfer Messages (Original Protocol)

These messages are used for backward compatibility when shared storage is unavailable.

**FILE_META::{filename}::{file_size}::{chunk_count}**
    Sent by: Client or Worker
    Purpose: Announces an incoming file transfer
    Format: "FILE_META::{name}::{bytes}::{chunks}"
    Example: "FILE_META::training.zip::5368709120::163840"

**CHUNK::{chunk_index}::{chunk_data_base64}**
    Sent by: Client or Worker
    Purpose: Sends a chunk of file data
    Format: Binary message with chunk index and data

**FILE_COMPLETE::{filename}**
    Sent by: Client or Worker
    Purpose: Signals all chunks received, file is complete
    Format: "FILE_COMPLETE::{name}"
    Example: "FILE_COMPLETE::training.zip"

**TRANSFER_PROGRESS::{percent}**
    Sent by: Client or Worker
    Purpose: Reports file transfer progress
    Format: "TRANSFER_PROGRESS::{0-100}"
    Example: "TRANSFER_PROGRESS::47.3"

### Control Messages (Common to Both Protocols)

**READY**
    Sent by: Worker
    Purpose: Worker is ready to receive tasks
    Format: "READY"

**TRAINING_COMPLETE** / **INFERENCE_COMPLETE**
    Sent by: Worker
    Purpose: Job processing finished successfully
    Format: "TRAINING_COMPLETE" or "INFERENCE_COMPLETE"

**ERROR::{error_message}**
    Sent by: Either peer
    Purpose: Reports an error condition
    Format: "ERROR::{description}"
    Example: "ERROR::Model loading failed: missing checkpoint"

Message Flow Examples
---------------------

### Shared Storage Transfer Flow

1. Client → Worker: JOB_ID::job_abc123
2. Client → Worker: SHARED_INPUT_PATH::jobs/job_abc123/training.zip
3. Worker → Client: PATH_VALIDATED::input
4. Client → Worker: SHARED_OUTPUT_PATH::jobs/job_abc123/output
5. Worker → Client: PATH_VALIDATED::output
6. Client → Worker: SHARED_STORAGE_JOB::start
7. Worker processes job (reads from shared storage, writes to shared storage)
8. Worker → Client: TRAINING_COMPLETE
9. Worker → Client: JOB_COMPLETE::job_abc123::jobs/job_abc123/output

### RTC Transfer Flow (Fallback)

1. Client → Worker: FILE_META::training.zip::5368709120::163840
2. Client → Worker: CHUNK::0::{base64_data}
3. Client → Worker: CHUNK::1::{base64_data}
   ... (repeat for all chunks)
4. Client → Worker: FILE_COMPLETE::training.zip
5. Worker processes job
6. Worker → Client: TRAINING_COMPLETE
7. Worker → Client: FILE_META::results.zip::1024000::32
8. Worker → Client: (chunks...)
9. Worker → Client: FILE_COMPLETE::results.zip

Security Considerations
-----------------------

**Path Validation:**
All shared storage paths received over the network MUST be validated:
1. Convert relative path to absolute using shared_storage_root
2. Resolve symlinks (Path.resolve())
3. Verify resolved path is within shared_storage_root (prevent traversal attacks)
4. Check path exists (for input paths)
5. Check permissions (readable for input, writable for output)

**Example Attack Prevention:**
Bad input: "../../etc/passwd"
Resolved: /etc/passwd (outside shared root)
Result: PATH_ERROR::Path outside shared storage

**Recommended Implementation:**
```python
from sleap_rtc.filesystem import validate_path_in_root
absolute_path = to_absolute_path(relative_path, shared_storage_root)
validated_path = validate_path_in_root(absolute_path, shared_storage_root)
```
"""

# Shared Storage Transfer Message Types
MSG_JOB_ID = "JOB_ID"
MSG_SHARED_INPUT_PATH = "SHARED_INPUT_PATH"
MSG_SHARED_OUTPUT_PATH = "SHARED_OUTPUT_PATH"
MSG_SHARED_STORAGE_JOB = "SHARED_STORAGE_JOB"
MSG_PATH_VALIDATED = "PATH_VALIDATED"
MSG_PATH_ERROR = "PATH_ERROR"
MSG_JOB_COMPLETE = "JOB_COMPLETE"

# RTC Transfer Message Types (Original Protocol)
MSG_FILE_META = "FILE_META"
MSG_CHUNK = "CHUNK"
MSG_FILE_COMPLETE = "FILE_COMPLETE"
MSG_TRANSFER_PROGRESS = "TRANSFER_PROGRESS"

# Control Message Types
MSG_READY = "READY"
MSG_TRAINING_COMPLETE = "TRAINING_COMPLETE"
MSG_INFERENCE_COMPLETE = "INFERENCE_COMPLETE"
MSG_ERROR = "ERROR"

# Message separators
MSG_SEPARATOR = "::"


def format_message(msg_type: str, *args) -> str:
    """Format a protocol message with type and arguments.

    Args:
        msg_type: The message type constant (e.g., MSG_JOB_ID).
        *args: Message arguments to append.

    Returns:
        Formatted message string.

    Examples:
        >>> format_message(MSG_JOB_ID, "job_abc123")
        'JOB_ID::job_abc123'

        >>> format_message(MSG_SHARED_INPUT_PATH, "jobs/job_123/data.zip")
        'SHARED_INPUT_PATH::jobs/job_123/data.zip'

        >>> format_message(MSG_PATH_VALIDATED, "input")
        'PATH_VALIDATED::input'
    """
    if args:
        return (
            f"{msg_type}{MSG_SEPARATOR}{MSG_SEPARATOR.join(str(arg) for arg in args)}"
        )
    return msg_type


def parse_message(message: str) -> tuple[str, list[str]]:
    """Parse a protocol message into type and arguments.

    Args:
        message: The message string to parse.

    Returns:
        Tuple of (message_type, arguments_list).

    Examples:
        >>> parse_message("JOB_ID::job_abc123")
        ('JOB_ID', ['job_abc123'])

        >>> parse_message("PATH_VALIDATED::input")
        ('PATH_VALIDATED', ['input'])

        >>> parse_message("READY")
        ('READY', [])
    """
    parts = message.split(MSG_SEPARATOR, 1)
    msg_type = parts[0]
    args = parts[1].split(MSG_SEPARATOR) if len(parts) > 1 else []
    return msg_type, args
