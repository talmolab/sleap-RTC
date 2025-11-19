#!/usr/bin/env python3
"""Test worker shared storage functionality."""

import os
import tempfile
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from sleap_rtc.worker.worker_class import RTCWorkerClient
from sleap_rtc.config import SharedStorageConfig, SharedStorageConfigError
from sleap_rtc.filesystem import (
    validate_path_in_root,
    to_relative_path,
    to_absolute_path,
    PathValidationError,
)


def test_shared_storage_initialization():
    """Test that worker initializes with shared storage configuration."""
    print("\n🧪 Test 1: Shared Storage Initialization")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        shared_root = Path(tmpdir)
        print(f"  Using temporary shared root: {shared_root}")

        # Test with explicit path
        worker = RTCWorkerClient(shared_storage_root=str(shared_root))

        # Compare resolved paths (handles symlinks like /var -> /private/var on macOS)
        assert (
            worker.shared_storage_root.resolve() == shared_root.resolve()
        ), "Shared root not set correctly"
        assert (
            worker.shared_jobs_dir.resolve() == (shared_root / "jobs").resolve()
        ), "Jobs dir not set correctly"
        assert worker.shared_jobs_dir.exists(), "Jobs directory not created"

        print(f"  ✓ Worker initialized with shared storage")
        print(f"  ✓ Shared root: {worker.shared_storage_root}")
        print(f"  ✓ Jobs directory: {worker.shared_jobs_dir}")
        print(f"  ✓ Jobs directory exists: {worker.shared_jobs_dir.exists()}")

    print("\n  ✅ Test 1 passed!\n")


def test_path_validation():
    """Test path validation and security checks."""
    print("\n🧪 Test 2: Path Validation and Security")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        shared_root = Path(tmpdir)
        print(f"  Using shared root: {shared_root}")

        # Test 1: Valid path within root
        test_dir = shared_root / "jobs" / "job_123"
        test_dir.mkdir(parents=True)
        test_file = test_dir / "data.zip"
        test_file.touch()

        validated = validate_path_in_root(test_file, shared_root)
        assert validated == test_file.resolve(), "Path validation failed for valid path"
        print(f"  ✓ Valid path accepted: {test_file}")

        # Test 2: Relative path conversion
        relative = to_relative_path(test_file, shared_root)
        assert relative == Path(
            "jobs/job_123/data.zip"
        ), "Relative path conversion failed"
        print(f"  ✓ Relative path: {relative}")

        # Test 3: Absolute path conversion
        absolute = to_absolute_path(relative, shared_root)
        assert absolute == shared_root / relative, "Absolute path conversion failed"
        print(f"  ✓ Absolute path: {absolute}")

        # Test 4: Path traversal attack prevention
        try:
            malicious_path = shared_root / ".." / ".." / "etc" / "passwd"
            validate_path_in_root(malicious_path, shared_root)
            assert False, "Path traversal attack was not detected!"
        except PathValidationError as e:
            print(f"  ✓ Path traversal blocked: {malicious_path}")
            print(f"    Error: {str(e)[:80]}...")

    print("\n  ✅ Test 2 passed!\n")


def test_shared_storage_config():
    """Test SharedStorageConfig with environment variable."""
    print("\n🧪 Test 3: Shared Storage Configuration")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        shared_root = Path(tmpdir)

        # Test 1: CLI override takes precedence
        root = SharedStorageConfig.get_shared_storage_root(
            cli_override=str(shared_root)
        )
        assert root.resolve() == shared_root.resolve(), "CLI override not applied"
        print(f"  ✓ CLI override works: {root}")

        # Test 2: Environment variable
        os.environ["SHARED_STORAGE_ROOT"] = str(shared_root)
        root = SharedStorageConfig.get_shared_storage_root()
        assert (
            root.resolve() == shared_root.resolve()
        ), "Environment variable not applied"
        print(f"  ✓ Environment variable works: {root}")

        # Test 3: Invalid path raises error
        os.environ["SHARED_STORAGE_ROOT"] = "/nonexistent/path/12345"
        try:
            SharedStorageConfig.get_shared_storage_root()
            assert False, "Invalid path should raise error"
        except SharedStorageConfigError as e:
            print(f"  ✓ Invalid path rejected")
            print(f"    Error: {str(e)[:80]}...")

        # Cleanup
        del os.environ["SHARED_STORAGE_ROOT"]

    print("\n  ✅ Test 3 passed!\n")


def test_worker_message_state():
    """Test that worker properly tracks message state."""
    print("\n🧪 Test 4: Worker Message State Tracking")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        shared_root = Path(tmpdir)
        worker = RTCWorkerClient(shared_storage_root=str(shared_root))

        # Verify initial state
        assert worker.current_job_id is None, "Job ID should be None initially"
        assert worker.current_input_path is None, "Input path should be None initially"
        assert (
            worker.current_output_path is None
        ), "Output path should be None initially"

        print(f"  ✓ Worker state initialized correctly")
        print(f"    Job ID: {worker.current_job_id}")
        print(f"    Input path: {worker.current_input_path}")
        print(f"    Output path: {worker.current_output_path}")

    print("\n  ✅ Test 4 passed!\n")


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("WORKER SHARED STORAGE TESTS")
    print("=" * 60)

    try:
        test_shared_storage_initialization()
        test_path_validation()
        test_shared_storage_config()
        test_worker_message_state()

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
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
    sys.exit(main())
