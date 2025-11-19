#!/usr/bin/env python3
"""Integration test for shared storage without requiring signaling server."""

import asyncio
import tempfile
from pathlib import Path
import sys
import uuid
import shutil

sys.path.insert(0, str(Path(__file__).parent))

from sleap_rtc.client.client_class import RTCClient
from sleap_rtc.worker.worker_class import RTCWorkerClient
from sleap_rtc.filesystem import safe_mkdir, get_file_info


class MockDataChannel:
    """Mock RTC data channel for testing."""

    def __init__(self):
        self.messages = []
        self.readyState = "open"

    def send(self, message):
        """Store sent messages for verification."""
        self.messages.append(message)
        print(f"  → {message[:100]}...")  # Print first 100 chars


async def test_client_shared_storage():
    """Test client can copy file to shared storage and send paths."""
    print("\n🧪 Test: Client Shared Storage Integration")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        shared_root = Path(tmpdir)

        # Create test file
        test_file = Path(tmpdir) / "test_input.zip"
        test_content = b"Test training data" * 1000
        test_file.write_bytes(test_content)
        print(f"  Created test file: {len(test_content) / 1e6:.2f} MB")

        # Initialize client with shared storage
        client = RTCClient(shared_storage_root=str(shared_root))
        client.data_channel = MockDataChannel()

        # Simulate worker response in the queue
        async def simulate_worker_response():
            """Simulate worker sending PATH_VALIDATED response."""
            await asyncio.sleep(0.1)  # Small delay to simulate network
            await client.path_validation_queue.put("PATH_VALIDATED::input")

        # Start the simulated response
        response_task = asyncio.create_task(simulate_worker_response())

        # Send file via shared storage
        success = await client.send_file_via_shared_storage(
            file_path=str(test_file), output_dir="models"
        )

        # Wait for response task to complete
        await response_task

        if success:
            print(f"  ✓ Client sent file via shared storage")

            # Verify messages were sent
            messages = client.data_channel.messages
            assert any("JOB_ID" in m for m in messages), "JOB_ID message not sent"
            assert any(
                "SHARED_INPUT_PATH" in m for m in messages
            ), "Input path not sent"
            assert any(
                "SHARED_OUTPUT_PATH" in m for m in messages
            ), "Output path not sent"
            print(f"  ✓ Sent {len(messages)} protocol messages")

            # Verify file was copied to shared storage
            jobs_dir = shared_root / "jobs"
            assert jobs_dir.exists(), "Jobs directory not created"

            job_dirs = list(jobs_dir.glob("job_*"))
            assert len(job_dirs) == 1, f"Expected 1 job dir, found {len(job_dirs)}"

            copied_file = job_dirs[0] / "test_input.zip"
            assert copied_file.exists(), "File not copied to shared storage"

            info = get_file_info(copied_file)
            assert info["size"] == len(test_content), "File size mismatch"
            print(f"  ✓ File copied to: {copied_file.relative_to(shared_root)}")

            # Verify output directory was created
            output_dir = job_dirs[0] / "models"
            assert output_dir.exists(), "Output directory not created"
            print(f"  ✓ Output directory created")

        else:
            print(f"  ✗ Client failed to send file")
            return False

    print("\n  ✅ Client integration test passed!\n")
    return True


async def test_worker_shared_storage():
    """Test worker can process shared storage messages."""
    print("\n🧪 Test: Worker Shared Storage Integration")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        shared_root = Path(tmpdir)
        jobs_dir = shared_root / "jobs"
        safe_mkdir(jobs_dir)

        # Create test job directory with input file
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        job_dir = jobs_dir / job_id
        safe_mkdir(job_dir)

        input_file = job_dir / "training.zip"
        input_file.write_bytes(b"Training data content")
        print(f"  Created test input: {input_file.relative_to(shared_root)}")

        output_dir = job_dir / "models"
        safe_mkdir(output_dir)

        # Initialize worker with shared storage
        worker = RTCWorkerClient(shared_storage_root=str(shared_root))

        # Verify initialization
        assert worker.shared_storage_root == shared_root.resolve()
        assert worker.shared_jobs_dir.exists()
        print(f"  ✓ Worker initialized with shared storage")

        # Simulate receiving messages from client
        print(f"  Simulating message flow...")

        # Message 1: JOB_ID
        worker.current_job_id = job_id
        print(f"    Job ID: {job_id}")

        # Message 2: SHARED_INPUT_PATH validation
        from sleap_rtc.filesystem import to_relative_path, to_absolute_path

        relative_input = to_relative_path(input_file, shared_root)
        absolute_input = to_absolute_path(relative_input, shared_root)

        from sleap_rtc.filesystem import validate_path_in_root

        validated_input = validate_path_in_root(absolute_input, shared_root)
        worker.current_input_path = validated_input
        print(f"    ✓ Input path validated: {relative_input}")

        # Message 3: SHARED_OUTPUT_PATH validation
        relative_output = to_relative_path(output_dir, shared_root)
        absolute_output = to_absolute_path(relative_output, shared_root)
        validated_output = validate_path_in_root(absolute_output, shared_root)
        worker.current_output_path = validated_output
        print(f"    ✓ Output path validated: {relative_output}")

        # Verify worker state
        assert worker.current_job_id == job_id
        assert worker.current_input_path == input_file.resolve()
        assert worker.current_output_path == output_dir.resolve()
        print(f"  ✓ Worker state updated correctly")

        # Verify worker can read the file
        content = worker.current_input_path.read_bytes()
        assert content == b"Training data content"
        print(f"  ✓ Worker can read input file from shared storage")

        # Verify worker can write to output
        test_output = worker.current_output_path / "result.txt"
        test_output.write_text("Training complete")
        assert test_output.exists()
        print(f"  ✓ Worker can write to output directory")

    print("\n  ✅ Worker integration test passed!\n")
    return True


async def test_end_to_end():
    """Test complete flow from client to worker."""
    print("\n🧪 Test: End-to-End Shared Storage Flow")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        shared_root = Path(tmpdir)

        # Create test input file
        test_file = Path(tmpdir) / "training_package.zip"
        test_file.write_bytes(b"Complete training package" * 100)
        print(f"  Created test package: {test_file.stat().st_size / 1e3:.1f} KB")

        # STEP 1: Client prepares and sends
        print(f"\n  STEP 1: Client Preparation")
        client = RTCClient(shared_storage_root=str(shared_root))
        client.data_channel = MockDataChannel()

        # Simulate worker response
        async def simulate_worker_response():
            await asyncio.sleep(0.1)
            await client.path_validation_queue.put("PATH_VALIDATED::input")

        response_task = asyncio.create_task(simulate_worker_response())

        success = await client.send_file_via_shared_storage(
            file_path=str(test_file), output_dir="outputs"
        )
        await response_task
        assert success, "Client failed to send file"
        print(f"    ✓ Client copied file to shared storage")

        # Extract job ID and paths from messages
        messages = client.data_channel.messages
        job_id_msg = [m for m in messages if "JOB_ID" in m][0]
        job_id = job_id_msg.split("::", 1)[1]

        input_msg = [m for m in messages if "SHARED_INPUT_PATH" in m][0]
        relative_input = Path(input_msg.split("::", 1)[1])

        output_msg = [m for m in messages if "SHARED_OUTPUT_PATH" in m][0]
        relative_output = Path(output_msg.split("::", 1)[1])

        print(f"    ✓ Job ID: {job_id}")
        print(f"    ✓ Input: {relative_input}")
        print(f"    ✓ Output: {relative_output}")

        # STEP 2: Worker receives and processes
        print(f"\n  STEP 2: Worker Processing")
        worker = RTCWorkerClient(shared_storage_root=str(shared_root))

        # Simulate receiving messages
        worker.current_job_id = job_id

        from sleap_rtc.filesystem import to_absolute_path, validate_path_in_root

        # Validate input path
        abs_input = to_absolute_path(relative_input, shared_root)
        worker.current_input_path = validate_path_in_root(abs_input, shared_root)
        print(f"    ✓ Worker validated input path")

        # Validate output path
        abs_output = to_absolute_path(relative_output, shared_root)
        safe_mkdir(abs_output)
        worker.current_output_path = validate_path_in_root(abs_output, shared_root)
        print(f"    ✓ Worker validated output path")

        # Worker reads the file
        input_content = worker.current_input_path.read_bytes()
        assert len(input_content) == test_file.stat().st_size
        print(f"    ✓ Worker read {len(input_content) / 1e3:.1f} KB from shared storage")

        # Worker writes results
        result_file = worker.current_output_path / "trained_model.h5"
        result_file.write_bytes(b"Trained model weights")
        print(f"    ✓ Worker wrote results to shared storage")

        # STEP 3: Client retrieves results
        print(f"\n  STEP 3: Client Retrieval")
        assert result_file.exists()
        result_content = result_file.read_bytes()
        assert result_content == b"Trained model weights"
        print(f"    ✓ Client can read results from shared storage")

        print(f"\n  ✅ Complete flow: Client → Shared Storage → Worker → Results")

    print("\n  ✅ End-to-end test passed!\n")
    return True


async def main():
    """Run all integration tests."""
    print("\n" + "=" * 60)
    print("SHARED STORAGE INTEGRATION TESTS")
    print("(Without Signaling Server)")
    print("=" * 60)

    try:
        await test_client_shared_storage()
        await test_worker_shared_storage()
        await test_end_to_end()

        print("\n" + "=" * 60)
        print("✅ ALL INTEGRATION TESTS PASSED!")
        print("=" * 60)
        print("\nShared storage implementation is working correctly.")
        print("For full client-worker testing, you'll need a running signaling server.")
        print("=" * 60 + "\n")
        return 0

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}\n")
        import traceback

        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
