# Base image with sleap-nn and GPU support (cu128)
FROM ghcr.io/talmolab/sleap-nn-cuda:latest

# Set working directory
WORKDIR /app

# Set non-interactive mode
ENV DEBIAN_FRONTEND=noninteractive

# Add WebRTC and communication dependencies to existing virtual environment
# sleap-nn is already installed in base image, so we only add worker-specific packages
# Note: boto3 removed (unused), zip/unzip not needed (Python's shutil handles archiving)
RUN uv add aiortc websockets pyzmq requests

# Copy the worker script into the container
COPY worker_class.py /app/

# Create shared file directory inside container
RUN mkdir -p /app/shared_data && chmod 777 /app/shared_data

# Expose ports for WebSocket server, API, and TURN server
EXPOSE 8080 8001 5000 3478/udp 3478/tcp 9001/tcp 9000/tcp

# Keep container running for interactive testing/development
# For production, override this with: docker run ... python worker_class.py
CMD ["tail", "-f", "/dev/null"]
