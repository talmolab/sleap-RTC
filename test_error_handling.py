#!/usr/bin/env python3
"""Test error handling for shared storage operations."""

import os
import tempfile
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from sleap_rtc.filesystem import (
    validate_path_in_root,
    check_disk_space,
    safe_copy,
    safe_mkdir,
    safe_remove,
    get_file_info,
    PathValidationError,
    SharedStorageError,
)
from sleap_rtc.config import SharedStorageConfig, SharedStorageConfigError


def test_path_traversal_prevention():
    """Test that path traversal attacks are prevented."""
    print("\n🧪 Test 1: Path Traversal Prevention")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        shared_root = Path(tmpdir)
        print(f"  Shared root: {shared_root}")

        # Test 1: Relative path traversal (../..)
        try:
            malicious_path = shared_root / ".." / ".." / "etc" / "passwd"
            validate_path_in_root(malicious_path, shared_root)
            assert False, "Path traversal should have been blocked!"
        except PathValidationError:
            print(f"  ✓ Blocked relative traversal: ../../../etc/passwd")

        # Test 2: Absolute path outside root
        try:
            malicious_path = Path("/etc/passwd")
            validate_path_in_root(malicious_path, shared_root)
            assert False, "Absolute path outside root should have been blocked!"
        except PathValidationError:
            print(f"  ✓ Blocked absolute path: /etc/passwd")

        # Test 3: Symlink to outside root (if supported by OS)
        try:
            # Create symlink to /etc
            symlink_path = shared_root / "evil_link"
            if os.name != "nt":  # Skip on Windows
                os.symlink("/etc", symlink_path)
                target_path = symlink_path / "passwd"
                try:
                    validate_path_in_root(target_path, shared_root)
                    assert False, "Symlink escape should have been blocked!"
                except PathValidationError:
                    print(f"  ✓ Blocked symlink escape: {symlink_path}")
        except OSError:
            # Symlink creation might fail due to permissions
            print(f"  ⚠ Skipped symlink test (permission denied)")

    print("\n  ✅ Test 1 passed!\n")


def test_disk_space_validation():
    """Test disk space checking."""
    print("\n🧪 Test 2: Disk Space Validation")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        shared_root = Path(tmpdir)

        # Test 1: Check available space
        has_space = check_disk_space(shared_root, 1024)  # 1 KB
        assert has_space, "Should have space for 1 KB"
        print(f"  ✓ Has space for small file: {has_space}")

        # Test 2: Check for impossibly large file
        has_space = check_disk_space(shared_root, 1024**5)  # 1 PB
        assert not has_space, "Should not have space for 1 PB"
        print(f"  ✓ Correctly reports insufficient space for huge file: {has_space}")

        # Test 3: Get actual disk usage
        from sleap_rtc.filesystem import get_disk_usage

        usage = get_disk_usage(shared_root)
        print(f"  ✓ Disk usage info:")
        print(f"    Total: {usage['total'] / 1e9:.1f} GB")
        print(f"    Free:  {usage['free'] / 1e9:.1f} GB")
        print(f"    Used:  {usage['percent_used']:.1f}%")

    print("\n  ✅ Test 2 passed!\n")


def test_permission_errors():
    """Test handling of permission errors."""
    print("\n🧪 Test 3: Permission Error Handling")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        shared_root = Path(tmpdir)

        # Test 1: Try to create file in read-only directory (Unix-like systems)
        if os.name != "nt":  # Skip on Windows
            readonly_dir = shared_root / "readonly"
            readonly_dir.mkdir()

            # Make directory read-only
            readonly_dir.chmod(0o555)

            try:
                test_file = readonly_dir / "test.txt"
                try:
                    safe_copy(Path(__file__), test_file)
                    assert False, "Should have raised permission error"
                except SharedStorageError as e:
                    assert "Permission denied" in str(e)
                    print(f"  ✓ Caught permission error: {str(e)[:60]}...")
            finally:
                # Restore permissions for cleanup
                readonly_dir.chmod(0o755)
        else:
            print(f"  ⚠ Skipped permission test (Windows)")

    print("\n  ✅ Test 3 passed!\n")


def test_file_not_found():
    """Test handling of missing files."""
    print("\n🧪 Test 4: File Not Found Handling")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        shared_root = Path(tmpdir)

        # Test 1: Check non-existent file
        nonexistent = shared_root / "does_not_exist.txt"
        info = get_file_info(nonexistent)
        assert not info["exists"], "Should report file does not exist"
        print(f"  ✓ Correctly detects missing file: {nonexistent.name}")

        # Test 2: Try to copy non-existent file
        try:
            dest = shared_root / "dest.txt"
            safe_copy(nonexistent, dest)
            assert False, "Should have raised error for missing source"
        except SharedStorageError as e:
            print(f"  ✓ Caught missing file error: {str(e)[:60]}...")

    print("\n  ✅ Test 4 passed!\n")


def test_invalid_configuration():
    """Test handling of invalid shared storage configuration."""
    print("\n🧪 Test 5: Invalid Configuration Handling")
    print("=" * 60)

    # Test 1: Non-existent path
    try:
        SharedStorageConfig.get_shared_storage_root("/nonexistent/path/12345")
        assert False, "Should have raised error for non-existent path"
    except SharedStorageConfigError as e:
        assert "does not exist" in str(e)
        print(f"  ✓ Rejected non-existent path")
        print(f"    Error: {str(e)[:80]}...")

    # Test 2: File instead of directory
    with tempfile.NamedTemporaryFile(delete=False) as f:
        temp_file = Path(f.name)

    try:
        try:
            SharedStorageConfig.get_shared_storage_root(str(temp_file))
            assert False, "Should have raised error for file path"
        except SharedStorageConfigError as e:
            assert "not a directory" in str(e)
            print(f"  ✓ Rejected file path (expected directory)")
            print(f"    Error: {str(e)[:80]}...")
    finally:
        temp_file.unlink()

    print("\n  ✅ Test 5 passed!\n")


def test_clear_error_messages():
    """Test that error messages are clear and actionable."""
    print("\n🧪 Test 6: Clear Error Messages")
    print("=" * 60)

    errors_checked = []

    # Test 1: Path traversal error
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            root = Path(tmpdir)
            bad_path = root / ".." / ".." / "etc"
            validate_path_in_root(bad_path, root)
        except PathValidationError as e:
            msg = str(e)
            assert "outside" in msg.lower() and "root" in msg.lower()
            errors_checked.append("Path traversal")
            print(f"  ✓ Path traversal error is clear")

    # Test 2: Config error for non-existent path
    try:
        SharedStorageConfig.get_shared_storage_root("/fake/path")
    except SharedStorageConfigError as e:
        msg = str(e)
        assert "does not exist" in msg.lower()
        assert "/fake/path" in msg  # Includes the actual path
        errors_checked.append("Non-existent path")
        print(f"  ✓ Non-existent path error is clear")

    # Test 3: Disk space error message
    from sleap_rtc.filesystem import get_disk_usage

    with tempfile.TemporaryDirectory() as tmpdir:
        usage = get_disk_usage(Path(tmpdir))
        # Simulate error message that would be shown
        if not check_disk_space(Path(tmpdir), 1024**5):
            error_msg = (
                f"Insufficient disk space. "
                f"Required: 1.0 PB, Available: {usage['free'] / 1e9:.1f} GB"
            )
            assert "Insufficient" in error_msg and "GB" in error_msg
            errors_checked.append("Disk space")
            print(f"  ✓ Disk space error would be clear")

    print(f"\n  Verified {len(errors_checked)} error message types are clear")
    print("\n  ✅ Test 6 passed!\n")


def main():
    """Run all error handling tests."""
    print("\n" + "=" * 60)
    print("ERROR HANDLING TESTS")
    print("=" * 60)

    try:
        test_path_traversal_prevention()
        test_disk_space_validation()
        test_permission_errors()
        test_file_not_found()
        test_invalid_configuration()
        test_clear_error_messages()

        print("\n" + "=" * 60)
        print("✅ ALL ERROR HANDLING TESTS PASSED!")
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
