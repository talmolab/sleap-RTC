"""Unified CLI for sleap-RTC using Click."""

import click
from loguru import logger
from pathlib import Path
from typing import Optional
from sleap_rtc.rtc_worker import run_RTCworker
from sleap_rtc.rtc_client import run_RTCclient
from sleap_rtc.rtc_client_track import run_RTCclient_track
from sleap_rtc.client.client_model_registry import ClientModelRegistry
from sleap_rtc.client.model_utils import (
    find_checkpoint_files,
    detect_model_type,
    calculate_model_size,
    validate_checkpoint_files,
    generate_model_id_from_config,
    format_size,
)
import sys
import shutil

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

@cli.command(name="import-model")
@click.argument("model_path", type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path))
@click.option(
    "--alias",
    "-a",
    type=str,
    required=False,
    help="Friendly name for the model (optional).",
)
@click.option(
    "--model-type",
    "-t",
    type=click.Choice(["centroid", "topdown", "bottomup", "single_instance"], case_sensitive=False),
    required=False,
    help="Model type (if not auto-detected).",
)
@click.option(
    "--copy",
    is_flag=True,
    default=False,
    help="Copy files instead of creating symlink (default: symlink).",
)
@click.option(
    "--registry-path",
    type=click.Path(path_type=Path),
    required=False,
    help="Path to registry file (default: ~/.sleap-rtc/models/manifest.json).",
)
def import_model(model_path: Path, alias: Optional[str], model_type: Optional[str], copy: bool, registry_path: Optional[Path]):
    """Import a pre-trained model into the client registry.

    MODEL_PATH: Directory containing the trained model files (.ckpt, .h5, etc.)

    This command will:
    1. Detect model type from training_config.yaml (or prompt if not found)
    2. Validate checkpoint files exist and are readable
    3. Create symlink or copy files to ~/.sleap-rtc/models/{type}_{id}/
    4. Register model in client registry
    5. Optionally set a friendly alias for easy reference

    Examples:
        # Auto-detect model type and create symlink
        sleap-rtc import-model /path/to/model --alias production-v1

        # Specify model type and copy files
        sleap-rtc import-model /path/to/model --model-type centroid --copy

        # Import without alias
        sleap-rtc import-model /path/to/model
    """
    logger.info("=" * 70)
    logger.info("Model Import")
    logger.info("=" * 70)
    logger.info("")

    # Initialize registry
    registry = ClientModelRegistry(registry_path=registry_path)
    logger.info(f"Using registry: {registry.registry_path}")
    logger.info("")

    # Step 1: Validate source directory
    logger.info(f"Source: {model_path}")

    # Find checkpoint files
    try:
        checkpoint_files = find_checkpoint_files(model_path)
    except ValueError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)

    if not checkpoint_files:
        logger.error(f"No checkpoint files found in {model_path}")
        logger.error("Expected files with extensions: .ckpt, .h5, .pth, .pt")
        sys.exit(1)

    logger.info(f"Found {len(checkpoint_files)} checkpoint file(s):")
    for ckpt in checkpoint_files:
        logger.info(f"  - {ckpt.name}")
    logger.info("")

    # Step 2: Validate checkpoint files
    logger.info("Validating checkpoint files...")
    if not validate_checkpoint_files(model_path):
        logger.error("Checkpoint validation failed")
        sys.exit(1)
    logger.info("✓ All checkpoint files are valid")
    logger.info("")

    # Step 3: Detect or prompt for model type
    if not model_type:
        detected_type = detect_model_type(model_path)
        if detected_type:
            model_type = detected_type
            logger.info(f"Auto-detected model type: {model_type}")
        else:
            logger.warning("Could not auto-detect model type from config")
            model_type = click.prompt(
                "Enter model type",
                type=click.Choice(["centroid", "topdown", "bottomup", "single_instance"], case_sensitive=False)
            )
    else:
        logger.info(f"Model type: {model_type}")
    logger.info("")

    # Step 4: Generate model ID
    model_id = generate_model_id_from_config(model_path)
    logger.info(f"Generated model ID: {model_id}")
    logger.info("")

    # Step 5: Check if model already exists
    if registry.exists(model_id):
        logger.warning(f"Model {model_id} already exists in registry")
        overwrite = click.confirm("Overwrite existing model entry?", default=False)
        if not overwrite:
            logger.info("Import cancelled")
            sys.exit(0)
        # Delete existing entry
        registry.delete(model_id)
        logger.info("Removed existing model entry")

    # Step 6: Create destination directory
    dest_dir = Path.home() / ".sleap-rtc" / "models" / f"{model_type}_{model_id}"
    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    # Remove destination if it already exists
    if dest_dir.exists():
        logger.info(f"Removing existing directory: {dest_dir}")
        shutil.rmtree(dest_dir)

    # Step 7: Copy or symlink files
    if copy:
        logger.info(f"Copying files to {dest_dir}...")
        shutil.copytree(model_path, dest_dir, symlinks=True)
        logger.info("✓ Files copied")
    else:
        logger.info(f"Creating symlink to {dest_dir}...")
        dest_dir.symlink_to(model_path.resolve(), target_is_directory=True)
        logger.info("✓ Symlink created")
    logger.info("")

    # Step 8: Calculate model size
    model_size = calculate_model_size(model_path)
    logger.info(f"Model size: {format_size(model_size)}")
    logger.info("")

    # Step 9: Find checkpoint path (use first checkpoint file found)
    checkpoint_path = dest_dir / checkpoint_files[0].relative_to(model_path)

    # Step 10: Register model
    model_info = {
        "id": model_id,
        "model_type": model_type,
        "source": "local-import",
        "local_path": str(dest_dir),
        "checkpoint_path": str(checkpoint_path),
        "on_worker": False,
        "size_bytes": model_size,
        "import_mode": "copy" if copy else "symlink",
        "original_path": str(model_path.resolve()),
    }

    registry.register(model_info)
    logger.info("✓ Model registered in client registry")
    logger.info("")

    # Step 11: Set alias if provided
    if alias:
        # Sanitize and validate alias
        sanitized_alias = registry._sanitize_alias(alias)
        is_valid, error_msg = registry._validate_alias(sanitized_alias)

        if not is_valid:
            logger.error(f"Invalid alias: {error_msg}")
            logger.warning("Model imported without alias")
        else:
            if sanitized_alias != alias:
                logger.info(f"Sanitized alias: '{alias}' → '{sanitized_alias}'")

            # Check for collision
            success = registry.set_alias(model_id, sanitized_alias, force=False)
            if not success:
                logger.warning(f"Alias '{sanitized_alias}' already in use")
                force_overwrite = click.confirm("Reassign alias to this model?", default=False)
                if force_overwrite:
                    registry.set_alias(model_id, sanitized_alias, force=True)
                    logger.info(f"✓ Alias set: {sanitized_alias}")
                else:
                    logger.warning("Model imported without alias")
            else:
                logger.info(f"✓ Alias set: {sanitized_alias}")
        logger.info("")
    elif click.confirm("Set a friendly alias for this model?", default=False):
        # Interactive alias prompt
        suggestion = registry.suggest_alias(model_path.name, model_type=model_type)
        logger.info(f"Suggestion: {suggestion}")
        alias_input = click.prompt("Enter alias (or press Enter for suggestion)", default=suggestion, show_default=False)

        try:
            success = registry.set_alias(model_id, alias_input, force=False)
            if success:
                logger.info(f"✓ Alias set: {alias_input}")
            else:
                logger.warning(f"Alias '{alias_input}' already in use")
        except ValueError as e:
            logger.error(f"Invalid alias: {e}")
        logger.info("")

    # Step 12: Display summary
    logger.info("=" * 70)
    logger.info("Import Summary")
    logger.info("=" * 70)
    model = registry.get(model_id)
    logger.info(f"Model ID:      {model_id}")
    if model.get("alias"):
        logger.info(f"Alias:         {model['alias']}")
    logger.info(f"Type:          {model_type}")
    logger.info(f"Size:          {format_size(model_size)}")
    logger.info(f"Local path:    {dest_dir}")
    logger.info(f"Import mode:   {'Copy' if copy else 'Symlink'}")
    logger.info(f"Checkpoint:    {checkpoint_path}")
    logger.info("")
    logger.info("✓ Model successfully imported!")
    logger.info("")

if __name__ == "__main__":
    cli()