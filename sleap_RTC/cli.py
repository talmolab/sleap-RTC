"""Unified CLI for sleap-RTC using Click."""

import click
from loguru import logger
from pathlib import Path
from sleap_RTC.RTCworker import run_RTCworker
from sleap_RTC.RTCclient import run_RTCclient
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
def worker():
    """Start the sleap-RTC worker node."""
    run_RTCworker()

@cli.command()
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
    help="Path to the SLEAP training/inference package.",
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
def client(**kwargs):
    """Run the sleap-RTC GUI client."""
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

if __name__ == "__main__":
    cli()