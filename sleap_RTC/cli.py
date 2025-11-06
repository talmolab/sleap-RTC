"""Unified CLI for sleap-RTC using Click."""

import click
from loguru import logger
from pathlib import Path
from sleap_rtc.rtc_worker import run_RTCworker
from sleap_rtc.rtc_client import run_RTCclient
from sleap_rtc.rtc_client_track import run_RTCclient_track
from sleap_rtc.worker.model_registry import ModelRegistry
import sys
import json
from datetime import datetime

@click.group()
def cli():
    pass

def show_worker_help():
    """Display """
    help_text = """
    sleap-rtc worker - Set this machine as a sleap-RTC worker node.

    Usage:
      sleap-rtc worker

    Tips:
      - This machine should have a GPU available for optimal model inference.
      - Ensure that the sleap-RTC Client is running and accessible.
      - Make sure to copy the session-string after connecting to the signaling
        server.
    """
    click.echo(help_text)

@cli.command()
@click.option(
    "--room-id",
    "-r",
    type=str,
    required=False,
    help="Room ID to join (if not provided, a new room will be created).",
)
@click.option(
    "--token",
    "-t",
    type=str,
    required=False,
    help="Room token for authentication (required if --room-id is provided).",
)
def worker(room_id, token):
    """Start the sleap-RTC worker node."""
    # Validate that both room_id and token are provided together
    if (room_id and not token) or (token and not room_id):
        logger.error("Both --room-id and --token must be provided together")
        sys.exit(1)

    run_RTCworker(room_id=room_id, token=token)

@cli.command(name="client-train")
@click.option(
    "--session-string",
    "--session_string",
    "-s",
    type=str,
    required=False,
    help="Session string for direct connection to a specific worker.",
)
@click.option(
    "--room-id",
    type=str,
    required=False,
    help="Room ID for room-based worker discovery.",
)
@click.option(
    "--token",
    type=str,
    required=False,
    help="Room token for authentication (required with --room-id).",
)
@click.option(
    "--worker-id",
    type=str,
    required=False,
    help="Specific worker peer-id to connect to (skips discovery).",
)
@click.option(
    "--auto-select",
    is_flag=True,
    default=False,
    help="Automatically select best worker by GPU memory (use with --room-id).",
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
@click.option(
    "--min-gpu-memory",
    type=int,
    required=False,
    default=None,
    help="Minimum GPU memory in MB required for training.",
)
def client_train(**kwargs):
    """Run remote training on a worker.

    Connection modes (mutually exclusive):

    1. Session string (direct): --session-string SESSION
       Connect directly to a specific worker using its session string.

    2. Room-based discovery: --room-id ROOM --token TOKEN
       Join a room and discover available workers. Supports:
       - Interactive selection (default)
       - Auto-select: --auto-select
       - Direct worker: --worker-id PEER_ID
       - GPU filter: --min-gpu-memory MB
    """
    # Extract connection options
    session_string = kwargs.pop("session_string", None)
    room_id = kwargs.pop("room_id", None)
    token = kwargs.pop("token", None)
    worker_id = kwargs.pop("worker_id", None)
    auto_select = kwargs.pop("auto_select", False)
    min_gpu_memory = kwargs.pop("min_gpu_memory", None)

    # Validation: Must provide either session string OR room credentials
    has_session = session_string is not None
    has_room = room_id is not None

    if has_session and has_room:
        logger.error("Connection modes are mutually exclusive. Use only one of:")
        logger.error("  --session-string (direct connection)")
        logger.error("  --room-id and --token (room-based discovery)")
        sys.exit(1)

    if not has_session and not has_room:
        logger.error("Must provide a connection method:")
        logger.error("  --session-string SESSION (direct connection)")
        logger.error("  --room-id ROOM --token TOKEN (room-based discovery)")
        sys.exit(1)

    # Validation: room-id and token must be together
    if (room_id and not token) or (token and not room_id):
        logger.error("Both --room-id and --token must be provided together")
        sys.exit(1)

    # Validation: worker selection options require room-id
    if (worker_id or auto_select) and not room_id:
        logger.error("--worker-id and --auto-select require --room-id and --token")
        sys.exit(1)

    # Validation: worker-id and auto-select are mutually exclusive
    if worker_id and auto_select:
        logger.error("Cannot use both --worker-id and --auto-select")
        sys.exit(1)

    # Setup ZMQ ports
    logger.info(f"Using controller port: {kwargs['controller_port']}")
    logger.info(f"Using publish port: {kwargs['publish_port']}")
    kwargs["zmq_ports"] = dict()
    kwargs["zmq_ports"]["controller"] = kwargs.pop("controller_port")
    kwargs["zmq_ports"]["publish"] = kwargs.pop("publish_port")

    # Handle room-based connection
    if room_id:
        logger.info(f"Room-based connection: room_id={room_id}")
        kwargs["room_id"] = room_id
        kwargs["token"] = token

        if worker_id:
            logger.info(f"Direct worker connection: worker_id={worker_id}")
            kwargs["worker_id"] = worker_id
        elif auto_select:
            logger.info("Auto-select mode enabled")
            kwargs["auto_select"] = True
        else:
            logger.info("Interactive worker selection mode")

        if min_gpu_memory:
            logger.info(f"Minimum GPU memory filter: {min_gpu_memory}MB")
            kwargs["min_gpu_memory"] = min_gpu_memory

    return run_RTCclient(
        session_string=session_string,
        pkg_path=kwargs.pop("pkg_path"),
        zmq_ports=kwargs.pop("zmq_ports"),
        **kwargs
    )

@cli.command(name="client-track")
@click.option(
    "--session-string",
    "--session_string",
    "-s",
    type=str,
    required=False,
    help="Session string for direct connection to a specific worker.",
)
@click.option(
    "--room-id",
    type=str,
    required=False,
    help="Room ID for room-based worker discovery.",
)
@click.option(
    "--token",
    type=str,
    required=False,
    help="Room token for authentication (required with --room-id).",
)
@click.option(
    "--worker-id",
    type=str,
    required=False,
    help="Specific worker peer-id to connect to (skips discovery).",
)
@click.option(
    "--auto-select",
    is_flag=True,
    default=False,
    help="Automatically select best worker by GPU memory (use with --room-id).",
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
@click.option(
    "--min-gpu-memory",
    type=int,
    required=False,
    default=None,
    help="Minimum GPU memory in MB required for inference.",
)
def client_track(**kwargs):
    """Run remote inference on a worker with pre-trained models.

    Connection modes (mutually exclusive):

    1. Session string (direct): --session-string SESSION
       Connect directly to a specific worker using its session string.

    2. Room-based discovery: --room-id ROOM --token TOKEN
       Join a room and discover available workers. Supports:
       - Interactive selection (default)
       - Auto-select: --auto-select
       - Direct worker: --worker-id PEER_ID
       - GPU filter: --min-gpu-memory MB
    """
    # Extract connection options
    session_string = kwargs.pop("session_string", None)
    room_id = kwargs.pop("room_id", None)
    token = kwargs.pop("token", None)
    worker_id = kwargs.pop("worker_id", None)
    auto_select = kwargs.pop("auto_select", False)
    min_gpu_memory = kwargs.pop("min_gpu_memory", None)

    # Validation: Must provide either session string OR room credentials
    has_session = session_string is not None
    has_room = room_id is not None

    if has_session and has_room:
        logger.error("Connection modes are mutually exclusive. Use only one of:")
        logger.error("  --session-string (direct connection)")
        logger.error("  --room-id and --token (room-based discovery)")
        sys.exit(1)

    if not has_session and not has_room:
        logger.error("Must provide a connection method:")
        logger.error("  --session-string SESSION (direct connection)")
        logger.error("  --room-id ROOM --token TOKEN (room-based discovery)")
        sys.exit(1)

    # Validation: room-id and token must be together
    if (room_id and not token) or (token and not room_id):
        logger.error("Both --room-id and --token must be provided together")
        sys.exit(1)

    # Validation: worker selection options require room-id
    if (worker_id or auto_select) and not room_id:
        logger.error("--worker-id and --auto-select require --room-id and --token")
        sys.exit(1)

    # Validation: worker-id and auto-select are mutually exclusive
    if worker_id and auto_select:
        logger.error("Cannot use both --worker-id and --auto-select")
        sys.exit(1)

    logger.info(f"Running inference with models: {kwargs['model_paths']}")

    # Handle room-based connection
    if room_id:
        logger.info(f"Room-based connection: room_id={room_id}")
        kwargs["room_id"] = room_id
        kwargs["token"] = token

        if worker_id:
            logger.info(f"Direct worker connection: worker_id={worker_id}")
            kwargs["worker_id"] = worker_id
        elif auto_select:
            logger.info("Auto-select mode enabled")
            kwargs["auto_select"] = True
        else:
            logger.info("Interactive worker selection mode")

        if min_gpu_memory:
            logger.info(f"Minimum GPU memory filter: {min_gpu_memory}MB")
            kwargs["min_gpu_memory"] = min_gpu_memory

    return run_RTCclient_track(
        session_string=session_string,
        data_path=kwargs.pop("data_path"),
        model_paths=list(kwargs.pop("model_paths")),
        output=kwargs.pop("output"),
        only_suggested_frames=kwargs.pop("only_suggested_frames"),
        **kwargs
    )

# Deprecated alias for backward compatibility
@cli.command(name="client", hidden=True)
@click.pass_context
def client_deprecated(ctx, **kwargs):
    """[DEPRECATED] Use 'client-train' instead."""
    logger.warning("Warning: 'sleap-rtc client' is deprecated. Use 'sleap-rtc client-train' instead.")
    ctx.invoke(client_train, **kwargs)


@cli.command(name="list-models")
@click.option(
    "--registry-dir",
    type=click.Path(exists=True, path_type=Path),
    default=Path("models/.registry"),
    help="Path to the model registry directory.",
)
@click.option(
    "--status",
    type=click.Choice(["training", "completed", "interrupted", "failed"], case_sensitive=False),
    required=False,
    help="Filter models by status.",
)
@click.option(
    "--model-type",
    type=str,
    required=False,
    help="Filter models by type (e.g., centroid, centered_instance).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format (table or json).",
)
def list_models(registry_dir, status, model_type, output_format):
    """List all trained models in the registry.

    Examples:
      sleap-rtc list-models
      sleap-rtc list-models --status completed
      sleap-rtc list-models --model-type centroid
      sleap-rtc list-models --format json
    """
    try:
        registry = ModelRegistry(registry_dir=registry_dir)
    except Exception as e:
        logger.error(f"Failed to load registry: {e}")
        sys.exit(1)

    # Apply filters
    filters = {}
    if status:
        filters['status'] = status
    if model_type:
        filters['model_type'] = model_type

    models = registry.list(filters=filters)

    if not models:
        logger.info("No models found matching criteria")
        return

    if output_format == "json":
        # JSON output
        print(json.dumps(models, indent=2))
    else:
        # Table output
        logger.info(f"\nFound {len(models)} model(s):\n")

        # Print header
        header = f"{'Model ID':<12} {'Type':<20} {'Status':<12} {'Created':<20} {'Val Loss':<10}"
        print(header)
        print("=" * len(header))

        # Print rows
        for model in models:
            model_id = model.get('id', 'N/A')[:12]
            model_type = model.get('model_type', 'N/A')[:20]
            status = model.get('status', 'N/A')[:12]
            created = model.get('created_at', 'N/A')[:19]  # Trim milliseconds
            val_loss = model.get('metrics', {}).get('final_val_loss', 'N/A')

            # Format val_loss
            if isinstance(val_loss, (int, float)):
                val_loss_str = f"{val_loss:.4f}"
            else:
                val_loss_str = str(val_loss)

            print(f"{model_id:<12} {model_type:<20} {status:<12} {created:<20} {val_loss_str:<10}")


@cli.command(name="model-info")
@click.argument("model_id", type=str)
@click.option(
    "--registry-dir",
    type=click.Path(exists=True, path_type=Path),
    default=Path("models/.registry"),
    help="Path to the model registry directory.",
)
def model_info(model_id, registry_dir):
    """Display detailed information about a specific model.

    Examples:
      sleap-rtc model-info a3f5e8c9
    """
    try:
        registry = ModelRegistry(registry_dir=registry_dir)
    except Exception as e:
        logger.error(f"Failed to load registry: {e}")
        sys.exit(1)

    model = registry.get(model_id)

    if not model:
        logger.error(f"Model '{model_id}' not found in registry")
        sys.exit(1)

    # Display model information
    logger.info(f"\nModel Information: {model_id}\n")
    print("=" * 60)

    # Basic info
    print(f"ID:               {model.get('id', 'N/A')}")
    print(f"Type:             {model.get('model_type', 'N/A')}")
    print(f"Status:           {model.get('status', 'N/A')}")
    print(f"Run Name:         {model.get('run_name', 'N/A')}")
    print(f"Training Job:     {model.get('training_job_hash', 'N/A')}")

    # Timestamps
    print(f"\nTimestamps:")
    print(f"  Created:        {model.get('created_at', 'N/A')}")
    completed_at = model.get('completed_at')
    if completed_at:
        print(f"  Completed:      {completed_at}")
    interrupted_at = model.get('interrupted_at')
    if interrupted_at:
        print(f"  Interrupted:    {interrupted_at}")

    # Paths
    print(f"\nPaths:")
    print(f"  Checkpoint:     {model.get('checkpoint_path', 'N/A')}")
    print(f"  Config:         {model.get('config_path', 'N/A')}")

    # Check if checkpoint exists
    checkpoint_path = Path(model.get('checkpoint_path', ''))
    if checkpoint_path.exists():
        size_mb = checkpoint_path.stat().st_size / (1024 * 1024)
        print(f"  Checkpoint Size: {size_mb:.2f} MB")
    else:
        print(f"  Checkpoint Size: [FILE NOT FOUND]")

    # Metrics
    metrics = model.get('metrics', {})
    if metrics:
        print(f"\nMetrics:")
        for key, value in metrics.items():
            if isinstance(value, float):
                print(f"  {key:<20}: {value:.4f}")
            else:
                print(f"  {key:<20}: {value}")

    # Metadata
    metadata = model.get('metadata', {})
    if metadata:
        print(f"\nMetadata:")
        for key, value in metadata.items():
            print(f"  {key:<20}: {value}")

    # Resume info for interrupted jobs
    if model.get('status') == 'interrupted':
        last_epoch = model.get('last_epoch', 0)
        print(f"\nResume Information:")
        print(f"  Last Epoch:     {last_epoch}")
        print(f"  To resume, run training with the same configuration")

    print("=" * 60)


if __name__ == "__main__":
    cli()