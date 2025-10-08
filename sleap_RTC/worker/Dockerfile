# Base image with SLEAP and GPU support
# https://hub.docker.com/repository/docker/eberrigan/sleap-cuda/general
# FROM eberrigan/sleap-cuda:latest

# Use a lightweight multi-architecture base image & install uv
# FROM ghcr.io/talmolab/sleap-cuda:linux-amd64-2a8cefd7d0291d2ce07998fa3c54c9d0d618d31b
FROM python:3.12-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Install dependencies
# opencv requires opengl https://github.com/conda-forge/opencv-feedstock/issues/401
# Default python3 is 3.8 in ubuntu 20.04 https://wiki.ubuntu.com/FocalFossa/ReleaseNotes#Python3_by_default
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     apt-get clean && \
#     rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* 

# Goal: Connect to Remote Signaling Server w/ aiortc and websockets
  
# Install websocket dependencies onto image @ build time (asyncio is included in python3.8)
# put into requirement.txt later

# Install system dependencies for OpenCV and OpenGL
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    ffmpeg libgl1 libsm6 libxext6 libxrender1 libglib2.0-0 \
    zip unzip \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies (CHANGE TO TORCH-CUDA LATER)
# TORCH-CPU FOR LOCAL MAC TESTING
COPY pyproject.toml /app/
RUN uv sync

# Copy the sleap_webRTC directory into the image's root dir
# COPY worker.py /app/
# COPY zmq_check.py /app/
COPY worker_class.py /app/
# COPY run_training.py /app/

# Create shared file directory inside container 
RUN mkdir -p /app/shared_data && chmod 777 /app/shared_data

# Port exposed by the container, NOT computer (localhost), 8080 = websocket server, 3478 = TURN server
EXPOSE 8080 8001 5000 3478/udp 3478/tcp 9001/tcp 9000/tcp

# Signaling server -> worker -> client in sequenece
# Worker makes req to signaling server, signaling server sends offer to client and accepts offer
# Connection is established between client and worker
# ENTRYPOINT ["sh", "-c", "python3 /app/worker.py"]




