# Model Registry Architecture Discussion

**Date:** 2025-11-11
**Status:** Planning Phase - Before Step 6 Implementation
**Context:** Discussing overarching architecture considerations before proceeding with model registry implementation

---

## Overview

This document captures the architectural discussion about how the model registry will fit into the broader SLEAP-RTC project, including considerations for room-level model sharing, W&B integration, resource management, UI design, and orchestration systems.

---

## Key Architectural Considerations

### 1. Common Room Manifest with References to All Models

**Current Design (Client-side registries):**
- Each Client maintains their own `~/.sleap-rtc/models/manifest.json`
- Workers maintain their own local registries
- No shared knowledge of what models exist in a room

**Proposed: Room-level manifest**
- A shared manifest stored per room (possibly in S3, database, or signaling server)
- All clients/workers in a room can see what models are available
- Models are "published" to a room rather than just imported locally

**Key Questions:**
- Where would this room manifest be stored? (S3 bucket, database, Redis, signaling server memory?)
- Who can write to it? (Any client? Only workers? Only after training completes?)
- How does it relate to local registries? (Does local registry become a cache of the room manifest?)
- What happens when a user works offline or outside a room?

**Potential Architecture:**
```
┌─────────────────────────────────────────────────────────┐
│                    Room: "lab-mice-2025"                 │
│                                                           │
│  Room Manifest (S3/DB):                                  │
│  {                                                        │
│    "models": {                                           │
│      "a3f5e8c9": {                                       │
│        "id": "a3f5e8c9",                                 │
│        "alias": "production-v2",                         │
│        "trained_by": "user@example.com",                 │
│        "trained_on_worker": "worker-gpu-01",             │
│        "s3_path": "s3://room-models/a3f5e8c9/",         │
│        "available_on_workers": ["worker-gpu-01"],        │
│        "wandb_run": "user/project/run123"                │
│      }                                                    │
│    }                                                      │
│  }                                                        │
└─────────────────────────────────────────────────────────┘
         ↓ queries                    ↓ queries
    ┌─────────┐                  ┌──────────┐
    │ Client  │                  │  Worker  │
    │ (local  │                  │ (local   │
    │  cache) │                  │  models) │
    └─────────┘                  └──────────┘
```

**User Priority:** VERY IMPORTANT - Required for teams sharing rooms and accessing each other's models for remote inference

---

### 2. Saving Checkpoints & Configs to W&B

**Purpose:**
- **Artifact storage** for model checkpoints
- **Experiment tracking** for training runs
- **Model versioning** and lineage tracking

**Proposed Registry Schema with W&B:**
```json
{
  "id": "a3f5e8c9",
  "model_type": "centroid",
  "alias": "mouse-v3",
  "trained_at": "2025-11-11T12:00:00Z",

  "wandb": {
    "project": "sleap-rtc-models",
    "run_id": "2z8x9abc",
    "run_url": "https://wandb.ai/team/project/runs/2z8x9abc",
    "artifact_name": "model-centroid-a3f5e8c9:v1",
    "artifact_url": "https://wandb.ai/team/project/artifacts/model/model-centroid-a3f5e8c9/v1"
  },

  "storage": {
    "worker_local": "/data/models/centroid_a3f5e8c9/",
    "s3": "s3://sleap-rtc-models/room-123/a3f5e8c9/",
    "wandb_artifact": "team/project/model-centroid-a3f5e8c9:v1"
  },

  "training": {
    "dataset_hash": "d7f3a1b2",
    "epochs": 100,
    "final_loss": 0.023,
    "training_duration_sec": 3600
  }
}
```

**Benefits:**
- Easy model sharing across team via W&B artifacts
- Training curves and metrics automatically tracked
- Model versioning with W&B's artifact system
- Can download models from W&B instead of S3

**Implementation Considerations:**
- Add W&B SDK to worker dependencies
- Log checkpoints as W&B artifacts after training
- Add `wandb-artifact://` as a model source type (alongside `local-import`, `s3`, etc.)
- CLI: `sleap-rtc import-model wandb://team/project/model-name:v1 --alias my-model`

**User Priority:** Good for backups, but NOT a priority right now

---

### 3. Compute Resources (Fractional Worker Usage)

**Current Assumption:** 1 job = 1 entire worker (exclusive access)

**Future Need:** Multiple jobs per worker (e.g., 0.7 worker for training, 0.3 for inference)

**This Requires:**

#### Resource Management System
```python
# Worker capabilities
{
  "worker_id": "gpu-01",
  "resources": {
    "gpu_memory_total_mb": 16384,
    "gpu_memory_available_mb": 14000,
    "gpu_compute_units": 10,  # Abstract units
    "cpu_cores": 16,
    "ram_mb": 64000
  },
  "current_jobs": [
    {
      "job_id": "job-123",
      "allocated_resources": {
        "gpu_memory_mb": 8000,
        "gpu_compute_units": 7,
        "cpu_cores": 4
      }
    }
  ]
}
```

#### Job Resource Specification
```python
# When client requests training:
{
  "job_id": "job-456",
  "job_type": "training",
  "resource_request": {
    "gpu_memory_mb": 4000,
    "gpu_compute_units": 3,  # 30% of GPU
    "cpu_cores": 2,
    "estimated_duration_minutes": 60
  }
}
```

#### Worker Scheduling Logic
- Worker checks available resources before accepting job
- Can run multiple jobs concurrently if resources allow
- Jobs might queue if resources insufficient
- Priority system for job scheduling

**Note:** This is essentially building a mini-Kubernetes scheduler!

**User Priority:** Not required right now, but relevant later (if possible for current use cases)

---

### 4. Ideal UI Considerations

#### Dashboard Views Needed

**1. Room Overview**
```
┌─────────────────────────────────────────────────────────┐
│ Room: lab-mice-2025                    [Settings] [Invite]│
├─────────────────────────────────────────────────────────┤
│                                                           │
│ 📊 Active Jobs (3)        🤖 Workers (5)      📦 Models  │
│                                                           │
│ ┌─────────────────┐    ┌──────────────┐    (12)         │
│ │ Training        │    │ gpu-01 ████  │                  │
│ │ mouse-v4        │    │ 80% busy     │    [View All]   │
│ │ ⏱️  45m remaining│    │              │                  │
│ └─────────────────┘    │ gpu-02 ██░░  │                  │
│                         │ 40% busy     │                  │
│ ┌─────────────────┐    └──────────────┘                  │
│ │ Inference       │                                       │
│ │ production-v2   │    [Add Worker]                      │
│ │ ✓ Complete      │                                       │
│ └─────────────────┘                                       │
└─────────────────────────────────────────────────────────┘
```

**2. Model Registry View**
```
┌─────────────────────────────────────────────────────────┐
│ Models in Room: lab-mice-2025                            │
├─────────────────────────────────────────────────────────┤
│ Search: [_____________]  Filter: [All Types ▼] [Tags ▼] │
├─────────────────────────────────────────────────────────┤
│                                                           │
│ ⭐ production-v2 (a3f5e8c9)                               │
│   centroid • trained 2 days ago • 🏷️ validated, prod    │
│   📊 W&B Run  💾 8.2 MB  🖥️  Available on: gpu-01, gpu-02│
│   [Use for Inference] [View Details] [Download]         │
│                                                           │
│ mouse-v3 (7f2a1b3c)                                      │
│   centroid • trained 1 week ago • 🏷️ experimental       │
│   📊 W&B Run  💾 8.1 MB  🖥️  Available on: gpu-01        │
│   [Use for Inference] [View Details] [Download]         │
│                                                           │
│ [Import Model] [Train New Model]                         │
└─────────────────────────────────────────────────────────┘
```

**3. Worker Resource View**
```
┌─────────────────────────────────────────────────────────┐
│ Worker: gpu-01 (RTX 4090)                                │
├─────────────────────────────────────────────────────────┤
│                                                           │
│ GPU Memory:  ████████████░░░░░░  12.8 / 16.0 GB (80%)   │
│ GPU Compute: ███████░░░░░░░░░░░   7 / 10 units  (70%)   │
│ CPU:         ████░░░░░░░░░░░░░░   4 / 16 cores  (25%)   │
│ RAM:         ████████░░░░░░░░░░  32 / 64 GB     (50%)   │
│                                                           │
│ Running Jobs:                                             │
│ • Training: mouse-v4     [7 GPU units] ⏱️  45m remaining │
│                                                           │
│ Queued Jobs: (1)                                          │
│ • Inference: rat-v1      [3 GPU units] ⏱️  waiting...    │
│                                                           │
│ Local Models: (8)        [View Registry]                 │
└─────────────────────────────────────────────────────────┘
```

**Key UI Features:**
- Real-time updates (WebSocket-based)
- Resource utilization graphs
- Model comparison tools
- Drag-and-drop model import
- One-click "use this model" workflow
- Job queue management
- W&B integration (click to see training runs)

---

### 5. Argo Workflows

Argo Workflows is a **Kubernetes-native** workflow engine for orchestrating complex jobs.

**What Argo Provides:**
- Workflow orchestration (DAG-based job dependencies)
- Resource management (via Kubernetes)
- Job scheduling and queuing
- Retry logic and error handling
- UI for monitoring workflows
- Artifact management

**Example Argo Workflow for SLEAP Training:**
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  name: train-mouse-model-v4
spec:
  entrypoint: training-pipeline
  templates:
  - name: training-pipeline
    steps:
    - - name: prepare-data
        template: prepare-dataset
    - - name: train-centroid
        template: train-model
        arguments:
          parameters:
          - name: model-type
            value: centroid
          - name: epochs
            value: "100"
    - - name: train-centered-instance
        template: train-model
        arguments:
          parameters:
          - name: model-type
            value: centered_instance
          - name: epochs
            value: "100"
    - - name: evaluate-models
        template: evaluate

  - name: train-model
    inputs:
      parameters:
      - name: model-type
      - name: epochs
    container:
      image: sleap-rtc-worker:latest
      command: [python]
      args: ["-m", "sleap_rtc.training.train", "{{inputs.parameters.model-type}}"]
      resources:
        limits:
          nvidia.com/gpu: 1
          memory: 16Gi
```

**Integration Options:**

**Option A: Full Argo Integration**
- Deploy SLEAP-RTC workers as Kubernetes pods
- Each training job becomes an Argo workflow
- Argo handles resource allocation, scheduling, queuing
- Your workers become stateless containers

**Option B: Hybrid Approach**
- Keep current WebRTC-based architecture
- Use Argo only for complex multi-step workflows
- Workers can optionally run in K8s or standalone

**User Context:** Workers will run on RunAI (Kubernetes-based GPU orchestration platform)

---

## User Answers to Key Questions

1. **Continue with Step 6?**
   - **Answer:** Depends on whether completing Steps 1-11 would make it significantly harder to implement room manifest later
   - If not too difficult to refactor later, can proceed with current plan
   - **Do not proceed yet until decision is made**

2. **Room Manifest Priority:**
   - **Answer:** VERY IMPORTANT
   - **Use Case:** Teams sharing a room need access to each other's models for remote inference
   - **Problem:** If a worker leaves, other workers need to inspect models trained on that worker - impossible without common room manifest

3. **W&B Integration:**
   - **Answer:** Good option for backups, but NOT a priority right now
   - Can be added later

4. **Fractional Worker Usage:**
   - **Answer:** Not absolutely required right now
   - Will be relevant later (if even possible for current use cases)

5. **Kubernetes/Argo:**
   - **Answer:** YES - Workers will run on RunAI (Kubernetes-based platform)
   - This means Kubernetes deployment is definitely relevant

---

## Recommended Phasing

### Phase 1: Basic Model Registry with Room Manifest
**Goal:** Core functionality with room-level model sharing

**Includes:**
- Room-level manifest (S3 or database-backed) - **CRITICAL**
- Client/Worker local registries (cache of room manifest)
- Import, tagging, metadata
- Resolution by path/ID/alias
- Registry queries via signaling server (Step 6 modified)
- Model transfer to/from room storage (Steps 7-9 modified)
- Training integration with room manifest updates (Steps 10-11 modified)

**Defers:**
- W&B integration
- Fractional resource management
- Advanced UI
- Full Argo integration

### Phase 2: Enterprise Features
**Goal:** Production-ready system

**Includes:**
- W&B integration for model artifacts
- Resource management for concurrent jobs
- Enhanced web UI (React/Vue app)
- RunAI/K8s integration
- Argo workflows (optional)

---

## Critical Architectural Decision: Room Manifest Storage

**Question:** Where should the room manifest be stored?

**Options:**

### Option A: S3-backed Manifest
```
Room Manifest Location: s3://sleap-rtc-rooms/{room_id}/manifest.json

Pros:
- Persistent storage
- Easy to backup
- Scalable
- Can store large model files alongside manifest

Cons:
- Requires S3 credentials
- Slightly higher latency for queries
- Need locking mechanism for concurrent writes
```

### Option B: Database-backed Manifest
```
Database: PostgreSQL/DynamoDB/Firebase

Pros:
- Built-in transactions and locking
- Fast queries
- Can support complex queries (filter by tags, search, etc.)
- Real-time updates

Cons:
- Requires database setup
- Additional infrastructure
- More complex than flat file
```

### Option C: Signaling Server Memory (Redis-backed)
```
Storage: Redis on signaling server

Pros:
- Very fast queries
- Already have signaling server infrastructure
- Real-time updates via pub/sub
- Simple implementation

Cons:
- Not as persistent (need backup to S3)
- Requires Redis setup
- Signaling server becomes more critical
```

### Recommendation: Hybrid Approach
```
Primary: Redis on signaling server (fast queries, real-time)
Backup: S3 (persistent storage, loaded on startup)
Model Files: S3 (large binary files)

Flow:
1. Room manifest lives in Redis for fast queries
2. Periodically synced to S3 (every 5 minutes or on changes)
3. On signaling server restart, load from S3
4. Model files always stored in S3
5. Workers cache models locally after download
```

---

## Next Steps

Before proceeding with implementation:

1. **Decide on room manifest storage approach** (S3, database, Redis, or hybrid)
2. **Decide whether to refactor current work** (Steps 1-5) to support room manifest or build on top of it
3. **Create detailed architecture diagram** using Mermaid
4. **Update tasks.md** to reflect room-level manifest architecture

---

## Questions for Architecture Diagram

To create a comprehensive Mermaid diagram, need clarification on:

1. **Signaling server details:**
   - What is the signaling server implementation? (Custom WebSocket server, AWS AppSync, etc.)
   - Where is it hosted? (AWS Lambda, EC2, etc.)
   - Does it already have database/Redis backing?

2. **Storage infrastructure:**
   - Do you have S3 buckets set up?
   - What AWS services are available?
   - Any existing databases?

3. **RunAI integration:**
   - How do workers connect when running on RunAI?
   - Do they have persistent storage?
   - How do they access S3?

4. **Authentication:**
   - Cognito-based (saw references in code)
   - How do users authenticate?
   - How do workers authenticate?

5. **Data flow priorities:**
   - Most important flows to diagram first?
   - Training workflow vs inference workflow priority?

---

## Open Questions

1. How do we handle model conflicts in room manifest? (e.g., two users train models with same alias)
2. Should room manifest have access control? (who can publish models?)
3. How long do models stay in room manifest? (retention policy)
4. What happens if a model is deleted from S3 but still in manifest?
5. How do we handle model versioning in room manifest?
6. Should we support model forking/branching?
7. How do offline users work with room manifests?

---

## References

- Current task list: `openspec/changes/add-client-model-registry/tasks.md`
- Completed steps: 1-5
- Current branch: `amick/create-model-registry`
- Test results: `/tmp/sleap-rtc-alias-demo/TEST_RESULTS.md`
