# Change Proposal: Add Remote Inference CLI (`client-track`)

## Problem Statement

Currently, sleap-RTC only supports remote training via `sleap-rtc client`. Users who have already trained models locally (or received them from a previous remote training session) cannot run inference remotely on suggested frames.

**Current Limitation:**
```bash
# Can do this:
sleap-rtc client --session_string <session> --pkg_path training_data.slp

# Cannot do this:
# Run inference on suggested frames with pre-trained models
```

**Desired Workflow:**
```bash
# Training (existing, renamed)
sleap-rtc client-train --session_string <session> --pkg_path training_data.slp

# Inference (NEW)
sleap-rtc client-track --session_string <session> \
  --data_path labels.v929.slp \
  --model_paths models/centroid_251024_152308 \
  --model_paths models/centered_instance_251024_152308 \
  --output predictions.slp
```

---

## Proposed Solution

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                       sleap-rtc CLI                             │
├─────────────────────────────────────────────────────────────────┤
│  sleap-rtc worker          # Handles both training & inference  │
│  sleap-rtc client-train    # Remote training (renamed)          │
│  sleap-rtc client-track    # Remote inference (NEW)             │
└─────────────────────────────────────────────────────────────────┘
```

### Training Workflow (Existing, Renamed)
```
Client Package → Worker:
├── labels.slp           # Training data
├── train-script.sh      # Training commands
└── *.yaml               # Training configs

Worker: Trains models → Returns trained models
```

### Inference Workflow (NEW)
```
Client Package → Worker:
├── labels.slp                        # Data with suggested frames
├── models/
│   ├── centroid_251024_152308/       # Pre-trained model 1
│   └── centered_instance_.../        # Pre-trained model 2
└── track-script.sh                   # Inference command (auto-generated)

Worker: Runs sleap-nn track → Returns predictions.slp
```

---

## Implementation Plan

### 1. CLI Changes (`sleap_rtc/cli.py`)

#### Rename Existing Command to `client-train`

Modify the existing `@cli.command()` decorator from `client` to `client-train`:

```python
@cli.group()
def cli():
    """SLEAP-RTC: Remote training and inference for SLEAP."""
    pass

@cli.command(name="worker")
def worker():
    """Start the sleap-RTC worker node."""
    run_RTCworker()

@cli.command(name="client-train")  # Changed from: @cli.command()
@click.option(
    "--session_string",
    "-s",
    type=str,
    required=True,
    help="Session string to connect to the sleap-RTC signaling server.",
)
@click.option(
    "--pkg_path",
    "-p",
    type=str,
    required=True,
    help="Path to the SLEAP training package.",
)
@click.option(
    "--controller_port",
    type=int,
    required=False,
    default=9000,
    help="ZMQ ports for controller communication with SLEAP.",
)
@click.option(
    "--publish_port",
    type=int,
    required=False,
    default=9001,
    help="ZMQ ports for publish communication with SLEAP.",
)
def client_train(**kwargs):  # Renamed from: def client(**kwargs)
    """Run remote training on a worker."""
    logger.info(f"Using controller port: {kwargs['controller_port']}")
    logger.info(f"Using publish port: {kwargs['publish_port']}")
    kwargs["zmq_ports"] = dict()
    kwargs["zmq_ports"]["controller"] = kwargs.pop("controller_port")
    kwargs["zmq_ports"]["publish"] = kwargs.pop("publish_port")

    return run_RTCclient(
        session_string=kwargs.pop("session_string"),
        pkg_path=kwargs.pop("pkg_path"),
        zmq_ports=kwargs.pop("zmq_ports"),
        **kwargs
    )

@cli.command(name="client-track")  # NEW
@click.option(
    "--session_string",
    "-s",
    type=str,
    required=True,
    help="Session string to connect to the sleap-RTC signaling server.",
)
@click.option(
    "--data_path",
    "-d",
    type=str,
    required=True,
    help="Path to .slp file with data for inference.",
)
@click.option(
    "--model_paths",
    "-m",
    multiple=True,
    required=True,
    help="Paths to trained model directories (can specify multiple times).",
)
@click.option(
    "--output",
    "-o",
    type=str,
    default="predictions.slp",
    help="Output predictions filename.",
)
@click.option(
    "--only_suggested_frames",
    is_flag=True,
    default=True,
    help="Track only suggested frames.",
)
def client_track(**kwargs):
    """Run remote inference on a worker with pre-trained models."""
    logger.info(f"Running inference with models: {kwargs['model_paths']}")

    return run_RTCclient_track(
        session_string=kwargs.pop("session_string"),
        data_path=kwargs.pop("data_path"),
        model_paths=list(kwargs.pop("model_paths")),
        output=kwargs.pop("output"),
        only_suggested_frames=kwargs.pop("only_suggested_frames"),
    )
```

**Backward Compatibility (Optional):**
```python
# Add deprecated alias for 'client' command
@cli.command(name="client", hidden=True)
@click.pass_context
def client_deprecated(ctx, **kwargs):
    """[DEPRECATED] Use 'client-train' instead."""
    logger.warning("Warning: 'sleap-rtc client' is deprecated. Use 'sleap-rtc client-train' instead.")
    ctx.invoke(client_train, **kwargs)
```

---

### 2. New Client Track Class (`sleap_rtc/client/client_track_class.py`)

Create a new class similar to `RTCClient`, in the same `client/` folder for organization:

```python
"""Remote inference client for sleap-RTC.

Similar structure to RTCClient in client_class.py but specialized for inference.
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import List, Optional

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
from websockets.client import ClientConnection
import websockets
import requests

from sleap_rtc.config import get_config

logging.basicConfig(level=logging.INFO)

# Global constants (same as RTCClient)
CHUNK_SIZE = 64 * 1024

class RTCTrackClient:
    """Client for running remote inference via WebRTC.

    Mirrors structure of RTCClient but specialized for inference workflow.
    """

    def __init__(
        self,
        DNS: Optional[str] = None,
        port_number: str = "8080",
    ):
        # Initialize RTC peer connection and websocket (same as RTCClient)
        self.pc = RTCPeerConnection()
        self.websocket: ClientConnection = None
        self.data_channel: RTCDataChannel = None
        self.pc.on("iceconnectionstatechange", self.on_iceconnectionstatechange)

        # Initialize given parameters
        config = get_config()
        self.DNS = DNS if DNS is not None else config.signaling_websocket
        self.port_number = port_number

        # Inference-specific variables
        self.chunk_size = CHUNK_SIZE
        self.received_files = {}
        self.predictions_data = bytearray()
        self.cognito_username = None
        self.target_worker = None

    def create_track_package(
        self,
        data_path: str,
        model_paths: List[str],
        output: str,
        only_suggested_frames: bool
    ) -> str:
        """Creates a track package with data + models + track-script.sh

        Args:
            data_path: Path to .slp file with data
            model_paths: List of paths to trained model directories
            output: Output filename for predictions
            only_suggested_frames: Whether to track only suggested frames

        Returns:
            Path to created .zip package
        """
        temp_dir = tempfile.mkdtemp(prefix="sleap_track_")
        pkg_dir = Path(temp_dir) / "track_package"
        pkg_dir.mkdir()

        # 1. Copy data file
        data_file = Path(data_path)
        if not data_file.exists():
            raise FileNotFoundError(f"Data file not found: {data_path}")

        shutil.copy(data_file, pkg_dir / data_file.name)
        logging.info(f"Copied data file: {data_file.name}")

        # 2. Copy model directories
        models_dir = pkg_dir / "models"
        models_dir.mkdir()

        for model_path in model_paths:
            model_dir = Path(model_path)
            if not model_dir.exists():
                raise FileNotFoundError(f"Model directory not found: {model_path}")

            # Copy entire model directory
            dest_model_dir = models_dir / model_dir.name
            shutil.copytree(model_dir, dest_model_dir)
            logging.info(f"Copied model: {model_dir.name}")

        # 3. Generate track-script.sh
        track_script = self._generate_track_script(
            data_filename=data_file.name,
            model_names=[Path(p).name for p in model_paths],
            output=output,
            only_suggested_frames=only_suggested_frames
        )

        track_script_path = pkg_dir / "track-script.sh"
        track_script_path.write_text(track_script)
        track_script_path.chmod(0o755)  # Make executable
        logging.info("Generated track-script.sh")

        # 4. Zip the package
        zip_path = Path(temp_dir) / "track_package.zip"
        shutil.make_archive(
            str(zip_path.with_suffix('')),
            'zip',
            pkg_dir
        )
        logging.info(f"Created track package: {zip_path}")

        return str(zip_path)

    def _generate_track_script(
        self,
        data_filename: str,
        model_names: List[str],
        output: str,
        only_suggested_frames: bool
    ) -> str:
        """Generates track-script.sh for the worker to execute.

        Args:
            data_filename: Name of the data .slp file
            model_names: List of model directory names
            output: Output predictions filename
            only_suggested_frames: Whether to track only suggested frames

        Returns:
            Shell script content
        """
        model_paths_args = " \\\n  ".join([
            f"--model_paths models/{name}"
            for name in model_names
        ])

        suggested_flag = "--only_suggested_frames \\\n  " if only_suggested_frames else ""

        script = f"""#!/bin/bash
# Auto-generated inference script for sleap-nn track

sleap-nn track \\
  --data_path {data_filename} \\
  {suggested_flag}{model_paths_args} \\
  -o {output}
"""
        return script

    async def send_track_package(self, channel: RTCDataChannel, package_path: str):
        """Sends the track package to the worker.

        Args:
            channel: WebRTC data channel
            package_path: Path to the .zip package
        """
        if channel.readyState != "open":
            logging.error(f"Data channel not open: {channel.readyState}")
            return

        # Send package type indicator
        channel.send("PACKAGE_TYPE::track")
        await asyncio.sleep(0.1)

        # Send output directory (where predictions will be saved)
        channel.send("OUTPUT_DIR::.")  # Save in root of extracted directory
        await asyncio.sleep(0.1)

        # Send file metadata
        file_name = os.path.basename(package_path)
        file_size = os.path.getsize(package_path)

        channel.send(f"FILE_META::{file_name}:{file_size}:false")  # gui=false for inference
        logging.info(f"Sending track package: {file_name} ({file_size} bytes)")

        # Send file in chunks
        with open(package_path, "rb") as file:
            bytes_sent = 0
            while chunk := file.read(self.chunk_size):
                # Flow control: wait if buffer is too full
                while channel.bufferedAmount is not None and channel.bufferedAmount > 16 * 1024 * 1024:
                    await asyncio.sleep(0.1)

                channel.send(chunk)
                bytes_sent += len(chunk)

                if bytes_sent % (1024 * 1024) == 0:  # Log every 1MB
                    progress = (bytes_sent / file_size) * 100
                    logging.info(f"Upload progress: {progress:.1f}%")

        # Signal end of file
        channel.send("END_OF_FILE")
        logging.info("Track package sent successfully")

    async def receive_predictions(self, channel: RTCDataChannel, output_dir: str):
        """Receives predictions.slp from the worker and saves locally.

        Args:
            channel: WebRTC data channel
            output_dir: Directory to save predictions
        """
        logging.info("Waiting for predictions from worker...")
        # Predictions receiving logic handled in on_message callback

    def on_datachannel_message(self, message):
        """Handles incoming messages from worker during inference.

        Args:
            message: Message from worker (string or bytes)
        """
        if isinstance(message, str):
            if message == "END_OF_FILE":
                # Save received predictions file
                if self.predictions_data:
                    output_path = Path("predictions.slp")
                    output_path.write_bytes(self.predictions_data)
                    logging.info(f"Predictions saved to: {output_path}")
                    self.predictions_data = bytearray()

            elif "FILE_META::" in message:
                # Predictions file metadata
                _, meta = message.split("FILE_META::", 1)
                file_name, file_size, _ = meta.split(":")
                logging.info(f"Receiving predictions: {file_name} ({file_size} bytes)")
                self.predictions_data = bytearray()

            elif "TRACK_LOG:" in message:
                # Inference log from sleap-nn track
                _, log = message.split("TRACK_LOG:", 1)
                print(log)  # Print to console

            elif "INFERENCE_START" in message:
                logging.info("Worker started inference...")

            elif "INFERENCE_JOBS_DONE" in message:
                logging.info("Inference completed on worker")

            elif message.startswith("INFERENCE_JOB_"):
                logging.info(message)

        elif isinstance(message, bytes):
            # Accumulate predictions file data
            if message != b"KEEP_ALIVE":
                self.predictions_data.extend(message)


### 3. New Entry Point Module (`sleap_rtc/rtc_client_track.py`)

Create the entry point that mirrors `rtc_client.py`:

```python
"""Entry point for remote inference client."""

import asyncio
from pathlib import Path
from loguru import logger

from sleap_rtc.client.client_track_class import RTCTrackClient


def run_RTCclient_track(
    session_string: str,
    data_path: str,
    model_paths: list,
    output: str,
    only_suggested_frames: bool,
) -> None:
    """Main entry point for remote inference client.

    Args:
        session_string: Session string from worker
        data_path: Path to .slp file with data
        model_paths: List of paths to trained model directories
        output: Output predictions filename
        only_suggested_frames: Whether to track only suggested frames

    Returns:
        None
    """
    # Validate inputs
    if not Path(data_path).exists():
        logger.error(f"Data file not found: {data_path}")
        return

    for model_path in model_paths:
        if not Path(model_path).exists():
            logger.error(f"Model directory not found: {model_path}")
            return

    # Create client instance
    client = RTCTrackClient()

    # Create track package
    logger.info("Creating track package...")
    try:
        package_path = client.create_track_package(
            data_path=data_path,
            model_paths=model_paths,
            output=output,
            only_suggested_frames=only_suggested_frames
        )
    except Exception as e:
        logger.error(f"Failed to create track package: {e}")
        return

    # Run the client
    logger.info(f"Starting inference client...")
    asyncio.run(
        client.run_client(
            file_path=package_path,
            output_dir=".",  # Save predictions to current directory
            session_string=session_string
        )
    )

    logger.info("Inference session complete")
```

```

---

### 4. Worker Changes (`sleap_rtc/worker/worker_class.py`)

Add inference handling to existing worker:

```python
# In on_message() method, add detection for track packages

async def on_message(self, message):
    """Handles incoming messages from the client."""

    if isinstance(message, str):
        # NEW: Detect track package vs training package
        if "PACKAGE_TYPE::track" in message:
            self.package_type = "track"
            logging.info("Received track package (inference mode)")

        elif message == "END_OF_FILE":
            # ... existing file save logic ...

            if self.package_type == "track":
                # Run inference workflow
                await self.run_track_workflow(channel)
            else:
                # Run training workflow (existing)
                await self.run_training_workflow(channel)

async def run_track_workflow(self, channel: RTCDataChannel):
    """Executes inference workflow on received track package.

    Args:
        channel: RTC data channel for sending progress
    """
    track_script_path = os.path.join(self.unzipped_dir, "track-script.sh")

    if not Path(track_script_path).exists():
        logging.error("No track-script.sh found in package")
        channel.send("INFERENCE_ERROR::No track script found")
        return

    logging.info("Starting inference workflow...")
    channel.send("INFERENCE_START")

    # Make script executable
    os.chmod(track_script_path, os.stat(track_script_path).st_mode | stat.S_IEXEC)

    # Parse and run track commands
    track_commands = self.parse_track_script(track_script_path)

    for cmd_args in track_commands:
        job_name = "inference"
        channel.send(f"INFERENCE_JOB_START::{job_name}")

        # Run sleap-nn track
        process = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.unzipped_dir,
            env={**os.environ, "PYTHONUNBUFFERED": "1"}
        )

        # Stream logs
        async for line in process.stdout:
            decoded_line = line.decode().rstrip()
            if channel.readyState == "open":
                try:
                    channel.send(f"TRACK_LOG:{decoded_line}")
                except Exception as e:
                    logging.error(f"Failed to send track log: {e}")

        await process.wait()

        if process.returncode == 0:
            logging.info("Inference completed successfully")
            channel.send(f"INFERENCE_JOB_END::{job_name}")
        else:
            logging.error(f"Inference failed with code {process.returncode}")
            channel.send(f"INFERENCE_JOB_ERROR::{job_name}::{process.returncode}")

    # Find and send predictions file
    predictions_files = list(Path(self.unzipped_dir).glob("*.predictions.slp"))

    if predictions_files:
        predictions_path = predictions_files[0]
        logging.info(f"Sending predictions: {predictions_path}")

        # Send predictions back to client
        await self.send_file(channel, str(predictions_path))
    else:
        logging.warning("No predictions file found")

    channel.send("INFERENCE_JOBS_DONE")

def parse_track_script(self, track_script_path: str) -> List[List[str]]:
    """Parses track-script.sh and extracts sleap-nn track commands.

    Args:
        track_script_path: Path to track-script.sh

    Returns:
        List of command argument lists
    """
    commands = []
    pattern = re.compile(r"^\s*sleap-nn\s+track\s+(.+)")

    with open(track_script_path, "r") as f:
        script_content = f.read()

    # Handle multi-line commands with backslashes
    script_content = script_content.replace("\\\n", " ")

    for line in script_content.split("\n"):
        match = pattern.match(line)
        if match:
            args = ["sleap-nn", "track"] + match.group(1).split()
            commands.append(args)

    return commands
```

---

## Usage Examples

### Training Workflow (Renamed Command)
```bash
# Worker starts and displays session string
sleap-rtc worker
# Output: sleap-session:eyJyIjogImFiYzEyMyIsICJ0IjogImRlZjQ1NiIsICJwIjogIndvcmtlci0xMjM0In0=

# Client runs training (renamed from 'client')
sleap-rtc client-train \
  --session_string "sleap-session:eyJyIjogImFiYzEyMyIs..." \
  --pkg_path labels.v929.slp

# Returns: trained models in models/
```

### Inference Workflow (NEW)
```bash
# Worker starts (same worker, handles both training and inference)
sleap-rtc worker
# Output: sleap-session:eyJyIjogImFiYzEyMyIsICJ0IjogImRlZjQ1NiIsICJwIjogIndvcmtlci0xMjM0In0=

# Client runs inference with pre-trained models
sleap-rtc client-track \
  --session_string "sleap-session:eyJyIjogImFiYzEyMyIs..." \
  --data_path labels.v929.slp \
  --model_paths models/centroid_251024_152308 \
  --model_paths models/centered_instance_251024_152308 \
  --output predictions.slp

# Returns: predictions.slp
```

### Combined Workflow
```bash
# 1. Train remotely
sleap-rtc client-train \
  --session_string "sleap-session:..." \
  --pkg_path training_data.slp

# Models downloaded to: models/centroid_*/
#                      models/centered_instance_*/

# 2. Run inference on new data with those models
sleap-rtc client-track \
  --session_string "sleap-session:..." \
  --data_path new_video_labels.slp \
  --model_paths models/centroid_251024_152308 \
  --model_paths models/centered_instance_251024_152308
```

---

## Package Structure Comparison

### Training Package (client-train)
```
training_package.zip
├── labels.slp           # Training data
├── train-script.sh      # sleap-nn train commands
├── centroid.yaml        # Training config 1
└── centered_instance.yaml  # Training config 2
```

### Inference Package (client-track)
```
track_package.zip
├── labels.slp                        # Data with suggested frames
├── models/
│   ├── centroid_251024_152308/       # Pre-trained model 1
│   │   ├── config.json
│   │   └── best.ckpt
│   └── centered_instance_251024_152308/  # Pre-trained model 2
│       ├── config.json
│       └── best.ckpt
└── track-script.sh                   # sleap-nn track command
```

---

## Benefits of This Approach

1. **Separation of Concerns**: Training and inference are distinct workflows with their own CLI commands
2. **Reusable Models**: Train once, run inference multiple times on different data
3. **No Path Translation Needed**: User provides models explicitly, so no guessing required
4. **Flexible**: Can use locally trained models or models from previous remote training
5. **Consistent UX**: Both commands follow similar patterns (session string, package path)
6. **Backward Compatible**: Deprecated `client` command redirects to `client-train`

---

## Migration Guide

### For Existing Users

**Before:**
```bash
sleap-rtc client --session_string "..." --pkg_path data.slp
```

**After:**
```bash
sleap-rtc client-train --session_string "..." --pkg_path data.slp
# Or continue using 'client' (deprecated but works)
```

### New Inference Capability

**Now possible:**
```bash
sleap-rtc client-track \
  --session_string "..." \
  --data_path new_data.slp \
  --model_paths models/centroid_* \
  --model_paths models/centered_*
```

---

## Testing Plan

1. **Unit Tests**:
   - Track package creation
   - Track script generation
   - Track script parsing

2. **Integration Tests**:
   - Full training workflow with renamed command
   - Full inference workflow with pre-trained models
   - Error handling (missing models, invalid scripts)

3. **End-to-End Tests**:
   - Train on worker → Download models → Run inference with those models
   - Inference on suggested frames only
   - Inference on all frames

---

## Open Questions

1. **GPU requirements**: Should inference require GPU or support CPU fallback?
   - **Recommendation**: Support both, default to GPU if available

2. **Model validation**: Should we verify models are compatible with data?
   - **Recommendation**: Let sleap-nn handle validation (fail fast with error message)

3. **Multiple predictions**: What if user wants to run inference on multiple .slp files?
   - **Recommendation**: Run client-track multiple times, or add `--batch` flag in future

4. **Progress bar**: Should inference show progress like training does?
   - **Recommendation**: Yes, stream logs from sleap-nn track

---

## File Structure

```
sleap-RTC/
├── sleap_rtc/
│   ├── cli.py                           # Modified: rename client → client-train, add client-track
│   ├── rtc_client.py                    # Existing: entry point for training
│   ├── rtc_client_track.py              # NEW: entry point for inference
│   ├── rtc_worker.py                    # Existing: unchanged
│   │
│   ├── client/
│   │   ├── client_class.py              # Existing: RTCClient for training
│   │   ├── client_track_class.py        # NEW: RTCTrackClient for inference
│   │   └── ...
│   │
│   └── worker/
│       ├── worker_class.py              # Modified: add track workflow handling
│       └── ...
```

## Next Steps

1. Modify `cli.py`: Rename `client` → `client-train`, add `client-track` command
2. Create `client/client_track_class.py`: Implement `RTCTrackClient` class (mirror `RTCClient` structure)
3. Create `rtc_client_track.py`: Entry point for inference (mirror `rtc_client.py`)
4. Modify `worker/worker_class.py`: Add track workflow detection and execution
5. Test end-to-end workflows:
   - Training only: `client-train`
   - Inference only: `client-track`
   - Combined: Train → Download models → Inference with those models
6. Update documentation with new CLI commands

---

## Future Enhancements

1. **Streaming Inference**: When sleap-nn supports it, add frame-by-frame inference over WebRTC
2. **Batch Inference**: Run inference on multiple .slp files in one session
3. **Model Selection UI**: GUI for selecting which models to use from a directory
4. **Inference Profiles**: Save common inference configurations as reusable profiles
