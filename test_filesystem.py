#!/usr/bin/env python3
"""Comprehensive unit tests for filesystem utilities."""

import os
import tempfile
from pathlib import Path
import sys
import shutil

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from sleap_rtc.filesystem import (
    validate_path_in_root,
    to_relative_path,
    to_absolute_path,
    safe_exists,
    safe_copy,
    safe_mkdir,
    safe_remove,
    get_file_info,
    list_directory,
    check_disk_space,
    get_disk_usage,
    PathValidationError,
    SharedStorageError,
)


def test_path_validation():
    """Test path validation and security checks."""
    print("\n🧪 Test 1: Path Validation")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Test 1: Valid path within root
        valid_path = root / "subdir" / "file.txt"
        valid_path.parent.mkdir(parents=True)
        valid_path.touch()

        validated = validate_path_in_root(valid_path, root)
        assert validated.is_absolute()
        print(f"  ✓ Valid path accepted: {valid_path.relative_to(root)}")

        # Test 2: Path traversal attack
        try:
            bad_path = root / ".." / ".." / "etc"
            validate_path_in_root(bad_path, root)
            assert False, "Should have blocked traversal"
        except PathValidationError:
            print(f"  ✓ Blocked path traversal: ../.../etc")

        # Test 3: Symlink resolution
        if os.name != "nt":
            symlink = root / "link"
            target = root / "target.txt"
            target.touch()
            symlink.symlink_to(target)

            validated = validate_path_in_root(symlink, root, resolve_symlinks=True)
            assert validated == target.resolve()
            print(f"  ✓ Symlink resolved correctly")

    print("\n  ✅ Test 1 passed!\n")


def test_path_conversions():
    """Test relative and absolute path conversions."""
    print("\n🧪 Test 2: Path Conversions")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Test 1: Absolute to relative
        abs_path = root / "jobs" / "job_123" / "data.zip"
        rel_path = to_relative_path(abs_path, root)
        assert rel_path == Path("jobs/job_123/data.zip")
        print(f"  ✓ Absolute to relative: {rel_path}")

        # Test 2: Relative to absolute
        abs_back = to_absolute_path(rel_path, root)
        assert abs_back == abs_path
        print(f"  ✓ Relative to absolute: {abs_back.name}")

        # Test 3: Round-trip conversion
        assert to_absolute_path(to_relative_path(abs_path, root), root) == abs_path
        print(f"  ✓ Round-trip conversion successful")

    print("\n  ✅ Test 2 passed!\n")


def test_safe_operations():
    """Test safe file operations with error handling."""
    print("\n🧪 Test 3: Safe File Operations")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Test 1: safe_exists
        existing = root / "exists.txt"
        existing.touch()
        assert safe_exists(existing)
        assert not safe_exists(root / "nonexistent.txt")
        print(f"  ✓ safe_exists works correctly")

        # Test 2: safe_mkdir
        new_dir = root / "parent" / "child" / "grandchild"
        safe_mkdir(new_dir)
        assert new_dir.exists() and new_dir.is_dir()
        print(f"  ✓ safe_mkdir created nested directories")

        # Test 3: safe_copy
        source = root / "source.txt"
        source.write_text("test content")
        dest = root / "dest.txt"

        safe_copy(source, dest)
        assert dest.exists()
        assert dest.read_text() == "test content"
        print(f"  ✓ safe_copy copied file correctly")

        # Test 4: safe_copy with metadata preservation
        import time

        time.sleep(0.1)
        dest2 = root / "dest2.txt"
        safe_copy(source, dest2, preserve_metadata=True)
        assert abs(source.stat().st_mtime - dest2.stat().st_mtime) < 0.1
        print(f"  ✓ safe_copy preserved metadata")

        # Test 5: safe_remove (file)
        safe_remove(dest)
        assert not dest.exists()
        print(f"  ✓ safe_remove removed file")

        # Test 6: safe_remove (directory, recursive)
        dir_to_remove = root / "remove_me"
        (dir_to_remove / "subdir").mkdir(parents=True)
        (dir_to_remove / "file.txt").touch()

        safe_remove(dir_to_remove, recursive=True)
        assert not dir_to_remove.exists()
        print(f"  ✓ safe_remove removed directory recursively")

    print("\n  ✅ Test 3 passed!\n")


def test_file_info():
    """Test file metadata retrieval."""
    print("\n🧪 Test 4: File Information")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Test 1: File info for existing file
        test_file = root / "test.txt"
        test_content = "Hello, World!" * 1000
        test_file.write_text(test_content)

        info = get_file_info(test_file)
        assert info["exists"]
        assert info["is_file"]
        assert not info["is_dir"]
        assert info["size"] == len(test_content.encode())
        print(f"  ✓ File info retrieved: {info['size']} bytes")

        # Test 2: Info for directory
        test_dir = root / "testdir"
        test_dir.mkdir()

        info = get_file_info(test_dir)
        assert info["exists"]
        assert info["is_dir"]
        assert not info["is_file"]
        print(f"  ✓ Directory info retrieved")

        # Test 3: Info for non-existent path
        info = get_file_info(root / "nonexistent")
        assert not info["exists"]
        print(f"  ✓ Non-existent path handled correctly")

    print("\n  ✅ Test 4 passed!\n")


def test_directory_listing():
    """Test directory listing functionality."""
    print("\n🧪 Test 5: Directory Listing")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create test structure
        (root / "file1.txt").touch()
        (root / "file2.txt").touch()
        (root / "data.zip").touch()
        (root / "subdir").mkdir()

        # Test 1: List all contents
        contents = list_directory(root)
        assert len(contents) == 4
        print(f"  ✓ Listed all contents: {len(contents)} items")

        # Test 2: List with pattern filter
        txt_files = list_directory(root, pattern="*.txt")
        assert len(txt_files) == 2
        print(f"  ✓ Filtered by pattern: {len(txt_files)} .txt files")

        # Test 3: List with zip pattern
        zip_files = list_directory(root, pattern="*.zip")
        assert len(zip_files) == 1
        print(f"  ✓ Filtered by pattern: {len(zip_files)} .zip files")

    print("\n  ✅ Test 5 passed!\n")


def test_disk_space():
    """Test disk space checking."""
    print("\n🧪 Test 6: Disk Space Checking")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Test 1: Get disk usage
        usage = get_disk_usage(root)
        assert "total" in usage
        assert "used" in usage
        assert "free" in usage
        assert "percent_used" in usage
        assert 0 <= usage["percent_used"] <= 100
        print(f"  ✓ Disk usage retrieved:")
        print(f"    Total: {usage['total'] / 1e9:.1f} GB")
        print(f"    Free:  {usage['free'] / 1e9:.1f} GB")
        print(f"    Used:  {usage['percent_used']:.1f}%")

        # Test 2: Check space for small file
        assert check_disk_space(root, 1024)  # 1 KB
        print(f"  ✓ Sufficient space for small file")

        # Test 3: Check space for impossibly large file
        assert not check_disk_space(root, 1024**5)  # 1 PB
        print(f"  ✓ Insufficient space for huge file")

        # Test 4: Check space for reasonable file (1 MB)
        has_space = check_disk_space(root, 1024 * 1024)
        print(f"  ✓ Space check for 1 MB file: {has_space}")

    print("\n  ✅ Test 6 passed!\n")


def test_error_conditions():
    """Test error handling in filesystem operations."""
    print("\n🧪 Test 7: Error Conditions")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Test 1: Copy from non-existent source
        try:
            safe_copy(root / "nonexistent.txt", root / "dest.txt")
            assert False, "Should have raised error"
        except SharedStorageError as e:
            assert "Failed to copy" in str(e)
            print(f"  ✓ Caught non-existent source error")

        # Test 2: Permission denied (Unix-like systems only)
        if os.name != "nt":
            readonly_dir = root / "readonly"
            readonly_dir.mkdir()
            readonly_dir.chmod(0o555)

            try:
                safe_mkdir(readonly_dir / "subdir")
                assert False, "Should have raised error"
            except SharedStorageError as e:
                assert "Permission denied" in str(e)
                print(f"  ✓ Caught permission denied error")
            finally:
                readonly_dir.chmod(0o755)
        else:
            print(f"  ⚠ Skipped permission test (Windows)")

        # Test 3: Path validation error
        try:
            validate_path_in_root(Path("/etc/passwd"), root)
            assert False, "Should have raised error"
        except PathValidationError:
            print(f"  ✓ Caught path validation error")

    print("\n  ✅ Test 7 passed!\n")


def test_cross_platform_paths():
    """Test cross-platform path handling."""
    print("\n🧪 Test 8: Cross-Platform Path Handling")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Test 1: Forward slashes work on all platforms
        rel_path = Path("jobs/job_123/data.zip")
        abs_path = to_absolute_path(rel_path, root)
        assert abs_path.is_absolute()
        print(f"  ✓ Forward slashes work: {rel_path}")

        # Test 2: Path with spaces
        path_with_spaces = root / "path with spaces" / "file.txt"
        path_with_spaces.parent.mkdir(parents=True)
        path_with_spaces.touch()

        rel = to_relative_path(path_with_spaces, root)
        assert to_absolute_path(rel, root) == path_with_spaces
        print(f"  ✓ Paths with spaces work: {rel}")

        # Test 3: Unicode characters in paths
        unicode_path = root / "测试" / "файл.txt"
        unicode_path.parent.mkdir(parents=True, exist_ok=True)
        unicode_path.touch()

        rel_unicode = to_relative_path(unicode_path, root)
        assert to_absolute_path(rel_unicode, root) == unicode_path
        print(f"  ✓ Unicode paths work: {rel_unicode}")

    print("\n  ✅ Test 8 passed!\n")


def main():
    """Run all filesystem tests."""
    print("\n" + "=" * 60)
    print("FILESYSTEM UNIT TESTS")
    print("=" * 60)

    try:
        test_path_validation()
        test_path_conversions()
        test_safe_operations()
        test_file_info()
        test_directory_listing()
        test_disk_space()
        test_error_conditions()
        test_cross_platform_paths()

        print("\n" + "=" * 60)
        print("✅ ALL FILESYSTEM TESTS PASSED!")
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
