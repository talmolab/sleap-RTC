"""Message protocol definitions for SLEAP-RTC.

This module defines the message types and protocol used for communication between
Client and Worker peers over WebRTC data channels.

Message Protocol Overview
-------------------------

SLEAP-RTC uses two transfer methods:

1. **RTC Transfer** (original): Sends files as chunked binary data over WebRTC
2. **Worker I/O Paths** (recommended): Worker has configured input/output paths;
   client sends just the filename, worker reads from its input directory

Transfer Method Selection
-------------------------

The Client automatically selects the transfer method:
- If Worker has I/O paths configured: Use Worker I/O Paths transfer
- Otherwise: Use RTC transfer (backward compatible)

Message Types
-------------

### Worker I/O Paths Messages

These messages are used when Worker has I/O paths configured (input_path, output_path).

**JOB_ID::{job_id}**
    Sent by: Client
    Purpose: Identifies a unique job for file transfer tracking
    Format: "JOB_ID::job_abc123"
    Example: "JOB_ID::job_f3a8b2c1"

**INPUT_FILE::{filename}**
    Sent by: Client
    Purpose: Tells Worker which file to use from its input directory
    Format: "INPUT_FILE::{filename}"
    Example: "INPUT_FILE::my_training.pkg.slp"
    Note: Just the filename, not a path. Worker resolves to {input_path}/{filename}

**FILE_EXISTS::{filename}**
    Sent by: Worker
    Purpose: Confirms the file exists and is readable in worker's input directory
    Format: "FILE_EXISTS::{filename}"
    Example: "FILE_EXISTS::my_training.pkg.slp"

**FILE_NOT_FOUND::{filename}::{reason}**
    Sent by: Worker
    Purpose: Reports that the file was not found or is not accessible
    Format: "FILE_NOT_FOUND::{filename}::{reason}"
    Example: "FILE_NOT_FOUND::missing.slp::File does not exist"

**JOB_OUTPUT_PATH::{path}**
    Sent by: Worker
    Purpose: Tells Client where job outputs will be written
    Format: "JOB_OUTPUT_PATH::{path}"
    Example: "JOB_OUTPUT_PATH::/mnt/shared/outputs/jobs/job_f3a8b2c1"

### Legacy Shared Storage Messages (Deprecated)

These messages are deprecated and will be removed in a future version.

**SHARED_INPUT_PATH::{relative_path}**
    Deprecated: Use INPUT_FILE instead

**SHARED_OUTPUT_PATH::{relative_path}**
    Deprecated: Worker now controls output path

**SHARED_STORAGE_JOB::start**
    Deprecated: Use INPUT_FILE flow instead

**PATH_VALIDATED::{path_type}**
    Deprecated: Use FILE_EXISTS instead

**PATH_ERROR::{error_message}**
    Deprecated: Use FILE_NOT_FOUND instead

**JOB_COMPLETE::{job_id}::{relative_output_path}**
    Deprecated: Use JOB_OUTPUT_PATH instead

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

### Worker I/O Paths Transfer Flow

1. Client → Worker: JOB_ID::job_abc123
2. Client → Worker: INPUT_FILE::my_training.pkg.slp
3. Worker → Client: FILE_EXISTS::my_training.pkg.slp
4. Worker → Client: JOB_OUTPUT_PATH::/mnt/shared/outputs/jobs/job_abc123
5. Worker processes job (reads from input_path, writes to output_path/jobs/{job_id}/)
6. Worker → Client: TRAINING_COMPLETE

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

**Filename Validation (Worker I/O Paths):**
All filenames received from clients MUST be validated:
1. Reject filenames containing path separators (/, \\)
2. Reject parent directory references (..)
3. Resolve the full path: input_path / filename
4. Verify resolved path is within input_path (prevent traversal attacks)
5. Check file exists and is readable

**Example Attack Prevention:**
Bad input: "../../../etc/passwd"
Result: FILE_NOT_FOUND::../../../etc/passwd::Invalid filename (contains path separator)

**Recommended Implementation:**
```python
import os
filename = received_filename

# Reject path traversal attempts
if os.sep in filename or (os.altsep and os.altsep in filename) or ".." in filename:
    send(FILE_NOT_FOUND, filename, "Invalid filename")
    return

# Resolve full path
full_path = input_path / filename
if not full_path.exists():
    send(FILE_NOT_FOUND, filename, "File does not exist")
    return

send(FILE_EXISTS, filename)
```
"""

# Worker I/O Paths Message Types
MSG_JOB_ID = "JOB_ID"
MSG_INPUT_FILE = "INPUT_FILE"
MSG_FILE_EXISTS = "FILE_EXISTS"
MSG_FILE_NOT_FOUND = "FILE_NOT_FOUND"
MSG_JOB_OUTPUT_PATH = "JOB_OUTPUT_PATH"

# Legacy Shared Storage Message Types (Deprecated)
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
