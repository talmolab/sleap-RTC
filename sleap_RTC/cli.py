"""Unified CLI for sleap-RTC using Click."""

import click
from loguru import logger
from pathlib import Path
from sleap_rtc.rtc_worker import run_RTCworker
from sleap_rtc.rtc_client import run_RTCclient
from sleap_rtc.rtc_client_track import run_RTCclient_track
import sys

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
    "--session_string",
    "-s",
    type=str,
    required=False,
    help="Session string to connect to the sleap-RTC signaling server (optional with worker discovery).",
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
    help="Minimum GPU memory in MB required for training (enables worker discovery).",
)
@click.option(
    "--discover-workers",
    is_flag=True,
    default=False,
    help="Enable automatic worker discovery (requires signaling server v2.0+).",
)
def client_train(**kwargs):
    """Run remote training on a worker.

    With --discover-workers flag, automatically finds and selects the best
    available worker. Otherwise, uses the session string for manual connection.
    """
    logger.info(f"Using controller port: {kwargs['controller_port']}")
    logger.info(f"Using publish port: {kwargs['publish_port']}")
    kwargs["zmq_ports"] = dict()
    kwargs["zmq_ports"]["controller"] = kwargs.pop("controller_port")
    kwargs["zmq_ports"]["publish"] = kwargs.pop("publish_port")

    # Store discovery options
    discover_workers = kwargs.pop("discover_workers", False)
    min_gpu_memory = kwargs.pop("min_gpu_memory", None)

    if discover_workers:
        logger.info("Worker discovery enabled")
        if min_gpu_memory:
            logger.info(f"Minimum GPU memory requirement: {min_gpu_memory}MB")
        kwargs["job_requirements"] = {
            "min_gpu_memory_mb": min_gpu_memory
        } if min_gpu_memory else {}
    else:
        if not kwargs.get("session_string"):
            logger.error("Either --session_string or --discover-workers must be provided")
            sys.exit(1)

    return run_RTCclient(
        session_string=kwargs.pop("session_string", None),
        pkg_path=kwargs.pop("pkg_path"),
        zmq_ports=kwargs.pop("zmq_ports"),
        **kwargs
    )

@cli.command(name="client-track")
@click.option(
    "--session_string",
    "-s",
    type=str,
    required=False,
    help="Session string to connect to the sleap-RTC signaling server (optional with worker discovery).",
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
    help="Minimum GPU memory in MB required for inference (enables worker discovery).",
)
@click.option(
    "--discover-workers",
    is_flag=True,
    default=False,
    help="Enable automatic worker discovery (requires signaling server v2.0+).",
)
def client_track(**kwargs):
    """Run remote inference on a worker with pre-trained models.

    With --discover-workers flag, automatically finds and selects the best
    available worker. Otherwise, uses the session string for manual connection.
    """
    logger.info(f"Running inference with models: {kwargs['model_paths']}")

    # Store discovery options
    discover_workers = kwargs.pop("discover_workers", False)
    min_gpu_memory = kwargs.pop("min_gpu_memory", None)

    if discover_workers:
        logger.info("Worker discovery enabled for inference")
        if min_gpu_memory:
            logger.info(f"Minimum GPU memory requirement: {min_gpu_memory}MB")
        kwargs["job_requirements"] = {
            "min_gpu_memory_mb": min_gpu_memory
        } if min_gpu_memory else {}
    else:
        if not kwargs.get("session_string"):
            logger.error("Either --session_string or --discover-workers must be provided")
            sys.exit(1)

    return run_RTCclient_track(
        session_string=kwargs.pop("session_string", None),
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

if __name__ == "__main__":
    cli()