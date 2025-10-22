# Base image with sleap-nn and GPU support (cu128)
FROM ghcr.io/talmolab/sleap-nn-cuda:linux-amd64-5476ba636dd8f4c8a701876e62f1904f0ada5a89-test

# Set working directory
WORKDIR /app

# Set non-interactive mode
ENV DEBIAN_FRONTEND=noninteractive

# Install sleap-rtc specific packages not in base image and clean up in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    zip unzip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Add WebRTC and communication dependencies to existing virtual environment
# sleap-nn is already installed in base image, so we only add worker-specific packages
RUN uv add aiortc websockets pyzmq requests boto3

# Copy the worker script into the container
COPY worker_class.py /app/

# Create shared file directory inside container 
RUN mkdir -p /app/shared_data && chmod 777 /app/shared_data

# Port exposed by the container, NOT computer (localhost), 8080 = websocket server, 3478 = TURN server
EXPOSE 8080 8001 5000 3478/udp 3478/tcp 9001/tcp 9000/tcp



