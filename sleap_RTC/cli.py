"""Unified CLI for sleap-RTC using Click."""

import click
from loguru import logger
from pathlib import Path
from sleap_rtc.rtc_worker import run_RTCworker
from sleap_rtc.rtc_client import run_RTCclient
from sleap_rtc.rtc_client_track import run_RTCclient_track
from sleap_rtc.worker.model_registry import ModelRegistry
from sleap_rtc.client.registry_query import query_registry_list, query_registry_info
import sys
import json
import asyncio
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
    required=False,
    help="Paths to trained model directories (can specify multiple times).",
)
@click.option(
    "--model",
    multiple=True,
    required=False,
    help="Model IDs from worker registry (alternative to --model_paths).",
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

    Model specification (mutually exclusive):

    1. Model paths: --model_paths PATH1 --model_paths PATH2
       Use local model directories.

    2. Model IDs: --model MODEL_ID1 --model MODEL_ID2
       Use models from worker's registry (requires querying worker).

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

    # Extract model options
    model_paths = kwargs.pop("model_paths", ())
    model_ids = kwargs.pop("model", ())

    # Validation: Must provide either model_paths OR model IDs
    has_model_paths = len(model_paths) > 0
    has_model_ids = len(model_ids) > 0

    if has_model_paths and has_model_ids:
        logger.error("Model specification modes are mutually exclusive. Use only one of:")
        logger.error("  --model_paths (local model directories)")
        logger.error("  --model (model IDs from worker registry)")
        sys.exit(1)

    if not has_model_paths and not has_model_ids:
        logger.error("Must provide models:")
        logger.error("  --model_paths PATH1 --model_paths PATH2 (local directories)")
        logger.error("  --model MODEL_ID1 --model MODEL_ID2 (worker registry IDs)")
        sys.exit(1)

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

    # If using model IDs, resolve them to paths via registry query
    if has_model_ids:
        logger.info(f"Resolving model IDs: {list(model_ids)}")

        # Build connection params for registry query
        connection_params = {}
        if session_string:
            connection_params['session_string'] = session_string
        else:
            connection_params['room_id'] = room_id
            connection_params['token'] = token
            if worker_id:
                connection_params['worker_id'] = worker_id

        # Query each model ID to get checkpoint path
        resolved_paths = []
        for model_id in model_ids:
            try:
                logger.info(f"Querying worker for model {model_id}...")
                model_info = asyncio.run(query_registry_info(model_id, **connection_params))

                if not model_info:
                    logger.error(f"Model '{model_id}' not found in worker registry")
                    sys.exit(1)

                # Extract checkpoint path
                checkpoint_path = model_info.get('checkpoint_path')
                if not checkpoint_path:
                    logger.error(f"Model '{model_id}' has no checkpoint path")
                    sys.exit(1)

                # Get parent directory (model directory)
                model_dir = str(Path(checkpoint_path).parent)
                resolved_paths.append(model_dir)
                logger.info(f"  Resolved to: {model_dir}")

            except Exception as e:
                logger.error(f"Failed to resolve model {model_id}: {e}")
                sys.exit(1)

        # Use resolved paths
        model_paths = tuple(resolved_paths)
        logger.info(f"Using models: {list(model_paths)}")
    else:
        logger.info(f"Running inference with models: {list(model_paths)}")

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
        model_paths=list(model_paths),
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
    "--session-string",
    "--session_string",
    "-s",
    type=str,
    required=False,
    help="Session string for remote worker connection.",
)
@click.option(
    "--room-id",
    type=str,
    required=False,
    help="Room ID for remote worker connection.",
)
@click.option(
    "--token",
    type=str,
    required=False,
    help="Room token (required with --room-id).",
)
@click.option(
    "--worker-id",
    type=str,
    required=False,
    help="Specific worker to query (optional with --room-id).",
)
@click.option(
    "--registry-dir",
    type=click.Path(exists=True, path_type=Path),
    default=Path("models/.registry"),
    help="Path to LOCAL registry directory (used only without connection params).",
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
def list_models(session_string, room_id, token, worker_id, registry_dir, status, model_type, output_format):
    """List trained models in registry (local or remote).

    LOCAL (worker machine):
      sleap-rtc list-models
      sleap-rtc list-models --status completed

    REMOTE (query worker from client):
      sleap-rtc list-models --room-id ROOM --token TOKEN
      sleap-rtc list-models --session-string SESSION
      sleap-rtc list-models --room-id ROOM --token TOKEN --worker-id WORKER
    """
    # Determine if this is a remote or local query
    is_remote = session_string or room_id

    if is_remote:
        # Validate connection parameters
        if session_string and room_id:
            logger.error("Cannot use both --session-string and --room-id")
            sys.exit(1)

        if room_id and not token:
            logger.error("--token required with --room-id")
            sys.exit(1)

        # Build connection params
        connection_params = {}
        if session_string:
            connection_params['session_string'] = session_string
        else:
            connection_params['room_id'] = room_id
            connection_params['token'] = token
            if worker_id:
                connection_params['worker_id'] = worker_id

        # Build filters
        filters = {}
        if status:
            filters['status'] = status
        if model_type:
            filters['model_type'] = model_type

        # Query remote worker
        try:
            logger.info("Querying remote worker registry...")
            models = asyncio.run(query_registry_list(filters=filters, **connection_params))
        except Exception as e:
            logger.error(f"Failed to query remote registry: {e}")
            sys.exit(1)

    else:
        # Local registry query
        try:
            registry = ModelRegistry(registry_dir=registry_dir)
        except Exception as e:
            logger.error(f"Failed to load local registry: {e}")
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
    "--session-string",
    "--session_string",
    "-s",
    type=str,
    required=False,
    help="Session string for remote worker connection.",
)
@click.option(
    "--room-id",
    type=str,
    required=False,
    help="Room ID for remote worker connection.",
)
@click.option(
    "--token",
    type=str,
    required=False,
    help="Room token (required with --room-id).",
)
@click.option(
    "--worker-id",
    type=str,
    required=False,
    help="Specific worker to query (optional with --room-id).",
)
@click.option(
    "--registry-dir",
    type=click.Path(exists=True, path_type=Path),
    default=Path("models/.registry"),
    help="Path to LOCAL registry directory (used only without connection params).",
)
def model_info(model_id, session_string, room_id, token, worker_id, registry_dir):
    """Display detailed model information (local or remote).

    LOCAL (worker machine):
      sleap-rtc model-info a3f5e8c9

    REMOTE (query worker from client):
      sleap-rtc model-info a3f5e8c9 --room-id ROOM --token TOKEN
      sleap-rtc model-info a3f5e8c9 --session-string SESSION
    """
    # Determine if this is a remote or local query
    is_remote = session_string or room_id

    if is_remote:
        # Validate connection parameters
        if session_string and room_id:
            logger.error("Cannot use both --session-string and --room-id")
            sys.exit(1)

        if room_id and not token:
            logger.error("--token required with --room-id")
            sys.exit(1)

        # Build connection params
        connection_params = {}
        if session_string:
            connection_params['session_string'] = session_string
        else:
            connection_params['room_id'] = room_id
            connection_params['token'] = token
            if worker_id:
                connection_params['worker_id'] = worker_id

        # Query remote worker
        try:
            logger.info(f"Querying remote worker for model {model_id}...")
            model = asyncio.run(query_registry_info(model_id, **connection_params))
        except Exception as e:
            logger.error(f"Failed to query remote registry: {e}")
            sys.exit(1)

    else:
        # Local registry query
        try:
            registry = ModelRegistry(registry_dir=registry_dir)
        except Exception as e:
            logger.error(f"Failed to load local registry: {e}")
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