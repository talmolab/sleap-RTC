# sleap-RTC

Remote training and inference system for SLEAP using WebRTC peer-to-peer connections.

## Overview

sleap-RTC enables remote, GPU-accelerated training of SLEAP models by establishing secure WebRTC connections between a client machine (where you initiate training) and a worker machine (with GPU, where training executes). Training progress is monitored in real-time through either CLI or GUI interfaces.

## Relationship to the SLEAP Ecosystem

sleap-RTC is part of the broader SLEAP ecosystem and integrates with several SLEAP packages:

### SLEAP Components

The SLEAP ecosystem consists of:

- **[sleap](https://github.com/talmolab/sleap)**: The main SLEAP application with GUI for labeling animal poses in videos
- **[sleap-io](https://github.com/talmolab/sleap-io)**: Standalone I/O utilities for SLEAP datasets and predictions
- **[sleap-nn](https://github.com/talmolab/sleap-nn)**: Neural network training and inference engine for SLEAP models
- **sleap-RTC** (this package): Remote training and inference orchestration

### How sleap-RTC Integrates

#### With sleap-nn (Primary Dependency)

sleap-RTC **orchestrates remote execution of sleap-nn training jobs**:

- **Invokes sleap-nn CLI**: Executes `sleap-nn train` commands remotely on worker machines
- **Configuration management**: Handles Hydra-based YAML config files (e.g., `centroid.yaml`, `top_down.yaml`)
- **Progress monitoring**: Receives real-time training metrics via ZMQ sockets from sleap-nn trainers
- **Model management**: Transfers trained PyTorch checkpoints back to the client

**Example training command executed by sleap-RTC worker:**
```bash
sleap-nn train --config-name centroid.yaml --config-dir . \
  trainer_config.ckpt_dir=models \
  trainer_config.zmq.control_port=9000 \
  trainer_config.zmq.publish_port=9001
```

#### With sleap (GUI Integration)

sleap-RTC includes components from the main SLEAP GUI for monitoring:

- **LossViewer widget**: Real-time visualization of training loss curves
- **Training configuration**: Uses SLEAP's `TrainingJobConfig` for config parsing
- **Project files**: Works with `.slp` project files created by SLEAP labeling GUI

#### With sleap-io (Implicit)

sleap-io handles data loading implicitly through sleap-nn:

- sleap-nn uses sleap-io to load labeled data from `.slp` files
- Training packages contain SLEAP project files (`.slp`) which are HDF5-based datasets
- No direct sleap-io imports in sleap-RTC; data format compatibility is handled by sleap-nn

### Architecture Overview

```
┌─────────────────┐                                           ┌──────────────────┐
│  SLEAP GUI      │                                           │  Worker Machine  │
│  (labeling)     │                                           │  (GPU training)  │
│                 │                                           │                  │
│  Creates:       │                                           │  Runs:           │
│  - labels.slp ──┼──┐                                    ┌──▶│  - sleap-nn      │
│  - configs.yaml │  │                                    │   │  - PyTorch       │
└─────────────────┘  │                                    │   │  - ZMQ progress  │
                     │                                    │   └──────────────────┘
                     │   ┌─────────────────────┐         │
                     └──▶│  sleap-RTC Client   │─────────┘
                         │  (local machine)    │  WebRTC
                         │                     │  Connection
                         │  - Uploads training │
                         │    package (zip)    │
                         │  - Monitors via ZMQ │◀─── Progress
                         │  - Downloads models │      Metrics
                         └─────────────────────┘
```

### Typical Workflow

1. **Label data** with SLEAP GUI → produces `.slp` project file
2. **Configure training** with SLEAP GUI → produces YAML config files
3. **Export training package** → creates `.slp.training_job.zip`
4. **Run sleap-RTC client** → uploads package to remote worker
5. **Worker executes sleap-nn** → trains model on GPU
6. **Monitor progress** → real-time loss curves via ZMQ
7. **Download trained models** → PyTorch `.ckpt` files returned to client

### Data Flow

```
SLEAP → sleap-RTC → sleap-nn → sleap-io
 │         │           │           │
 │         │           │           └─ Loads .slp datasets
 │         │           └─ Trains PyTorch models
 │         └─ Orchestrates remote training
 └─ Creates labeled datasets
```

### Key Dependencies

From `pyproject.toml`:
```toml
dependencies = [
    "sleap-nn[torch]",  # Neural network training engine
    "aiortc",           # WebRTC peer connections
    "websockets",       # Signaling server communication
    "pyzmq",            # Real-time progress monitoring
    # ... other dependencies
]
```

## Use Cases

- **Remote GPU training**: Train SLEAP models on a powerful GPU server from your laptop
- **Cloud training**: Deploy workers in AWS/GCP/Azure for scalable training
- **Lab infrastructure**: Share GPU resources across multiple researchers
- **Containerized training**: Run training in isolated Docker containers

## Installation

```bash
pip install sleap-rtc
```

This installs sleap-RTC along with sleap-nn and all required dependencies.

## Quick Start

### Start a worker (on GPU machine):
```bash
sleap-rtc worker
```

This outputs a session string like:
```
sleap-session:eyJyIjogInJvb21faWQiLCAidCI6ICJ0b2tlbiIsICJwIjogInBlZXJfaWQifQ==
```

### Connect client (on local machine):
```bash
sleap-rtc client -s "sleap-session:..." -p labels.slp.training_job.zip
```

The client will:
1. Connect to the worker via WebRTC
2. Upload the training package
3. Stream training logs in real-time
4. Download trained models when complete
