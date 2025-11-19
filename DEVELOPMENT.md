# Development Practices

This document outlines the development workflow and best practices for the sleap-RTC project.

## Git Workflow

### Branching Strategy

1. **NEVER commit directly to `main`** - Always work on a feature branch
   - Create descriptive branch names: `feature/add-xyz`, `fix/bug-name`, `refactor/component-name`
   - Example: `git checkout -b feature/add-signaling-server-config`

2. **NEVER merge directly to `main` without a PR** - All changes must go through pull request review
   - This ensures code review, CI checks, and proper documentation
   - PRs provide a record of changes and decision-making

3. **ALWAYS squash merge PRs into `main`** - Keep the main branch history clean
   - Use GitHub's "Squash and merge" option
   - Write a clear, descriptive commit message that summarizes the entire PR
   - This creates a linear history that's easier to understand and bisect

### Commit Practices

**NEVER commit all files indiscriminately** - Always inspect changes carefully before committing:

```bash
# Review what's changed
git status
git diff

# Stage files selectively
git add path/to/specific/file.py

# Or stage specific chunks interactively
git add -p path/to/file.py

# Review staged changes before committing
git diff --staged
```

**Before committing, consider:**
- Should any files be added to `.gitignore`?
- Are there temporary files, test configs, or debug artifacts?
- Are there sensitive credentials or tokens?
- Do all changes belong in this logical commit?

**Write descriptive commit messages:**
- Follow conventional commits format: `feat:`, `fix:`, `refactor:`, `docs:`, etc.
- Include context: why the change was made, not just what changed
- Reference issues or PRs when relevant

Example good commits:
```
feat: add environment-based configuration system

Replaces hardcoded AWS signaling server URLs with flexible
configuration supporting dev/staging/prod environments.
Uses TOML config files with SLEAP_RTC_ENV selector.

Implements proposal in openspec/changes/add-signaling-server-config/
```

## Pull Requests

### Using the `gh` CLI

**ALWAYS use the `gh` CLI for GitHub operations:**

```bash
# Create a PR
gh pr create --title "Add signaling server configuration" --body "..."

# Check PR status
gh pr view

# Check CI status
gh pr checks

# Monitor CI logs
gh run view
gh run view --log

# List recent workflow runs
gh run list

# Watch a specific run
gh run watch
```

### PR Workflow

1. **Create PR after pushing your branch:**
   ```bash
   git push -u origin feature/your-branch
   gh pr create --title "Description" --body "$(cat <<'EOF'
   ## Summary
   - Change 1
   - Change 2

   ## Test plan
   - [ ] Tested scenario A
   - [ ] Tested scenario B
   EOF
   )"
   ```

2. **Monitor CI checks periodically:**
   ```bash
   # Check status
   gh pr checks

   # View detailed logs if failures occur
   gh run list
   gh run view <run-id> --log
   ```

3. **Address review comments:**
   - Make changes in new commits on your branch
   - Push to update the PR automatically
   - Respond to review comments on GitHub

4. **Merge when approved and CI passes:**
   - Use GitHub UI: "Squash and merge"
   - Delete the branch after merging

## CI/CD Best Practices

### Monitoring Workflow Runs

When you have an active PR with changes:

1. **Check CI status after pushing:**
   ```bash
   gh pr checks
   ```

2. **If checks fail, investigate immediately:**
   ```bash
   gh run list --limit 5
   gh run view <run-id> --log
   ```

3. **Common CI issues to watch for:**
   - Linting failures (Black, Ruff)
   - Type errors
   - Test failures
   - Docker build failures
   - Platform-specific issues

### Workflow Files

The project uses GitHub Actions workflows:
- `worker_test.yml` - Runs on non-main branches when worker/** or workflow files change
- `worker_production.yml` - Production builds with multi-platform support

Be mindful that changes to these files trigger builds, so test locally when possible.

## OpenSpec Workflow

When implementing new features or making significant changes, follow the OpenSpec process:

1. **Create a proposal** - Use `/openspec:proposal` or create manually
2. **Get approval** - Don't implement until proposal is reviewed
3. **Implement** - Follow the tasks.md checklist
4. **Archive after deployment** - Move to archive and update specs

See `openspec/AGENTS.md` for detailed instructions.

## Code Quality

### Before Committing

Run these checks locally:

```bash
# Format code
black sleap_rtc/

# Lint
ruff check sleap_rtc/

# Type check (if configured)
# mypy sleap_rtc/

# Run tests
pytest
```

### Code Style

- **Formatter**: Black (line length 88)
- **Linter**: Ruff with pydocstyle rules
- **Docstrings**: Google-style
- **Type hints**: Use Python type hints (typing module)
- **Naming**:
  - `snake_case` for functions and variables
  - `PascalCase` for classes
  - `UPPER_CASE` for constants

## Summary Checklist

Before pushing:
- [ ] Working on a feature branch (not `main`)
- [ ] Reviewed all changes with `git status` and `git diff`
- [ ] Staged only relevant files (no temp files, secrets, or unrelated changes)
- [ ] Written descriptive commit message(s)
- [ ] Ran code formatters and linters
- [ ] Tested changes locally

Before merging:
- [ ] Created PR using `gh pr create`
- [ ] Monitored CI checks with `gh pr checks`
- [ ] Addressed all review comments
- [ ] All CI checks passing
- [ ] Using "Squash and merge" in GitHub UI

## Shared Storage Configuration

SLEAP-RTC supports high-performance file transfer via shared filesystem (NFS, local mounts) in addition to the original RTC chunked transfer. This is particularly beneficial for large training packages (5-9 GB).

### Overview

**Transfer Methods:**
1. **Shared Storage** (recommended for large files): Client writes files to shared storage, sends only paths over RTC
2. **RTC Transfer** (fallback): Sends files as chunked binary data over WebRTC data channel

The system automatically selects the optimal method:
- If shared storage is configured on both Client and Worker → Use shared storage
- Otherwise → Use RTC transfer (backward compatible)

### Performance Comparison

| Transfer Method | 5 GB File | Advantages |
|----------------|-----------|------------|
| **Shared Storage** | ~10-30 seconds | ✅ Fast, ✅ Scalable, ✅ No network overhead |
| **RTC Transfer** | ~15-30 minutes | ✅ No setup required, ✅ Works anywhere |

### Configuration

#### Environment Variable (Recommended)

Set the `SHARED_STORAGE_ROOT` environment variable to the shared filesystem path:

```bash
# On Client (macOS/local)
export SHARED_STORAGE_ROOT="/Volumes/talmo/amick"

# On Worker (Vast.ai/RunAI)
export SHARED_STORAGE_ROOT="/home/jovyan/vast/amick"
```

#### CLI Argument

Pass the path directly when starting Client or Worker:

```bash
# Client
python -m sleap_rtc.client --shared-storage-root /Volumes/talmo/amick

# Worker
python -m sleap_rtc.worker --shared-storage-root /home/jovyan/vast/amick
```

#### Verification

Check if shared storage is detected:

```python
from sleap_rtc.config import SharedStorageConfig

root = SharedStorageConfig.get_shared_storage_root()
if root:
    print(f"✓ Shared storage configured: {root}")
else:
    print("✗ Shared storage not configured (will use RTC transfer)")
```

### Platform-Specific Setup

#### Vast.ai Configuration

Vast.ai workers typically mount shared storage at `/home/jovyan/vast/amick`:

```bash
# SSH into Vast.ai instance
ssh -p <port> root@<vast-ip>

# Verify mount point exists
ls -la /home/jovyan/vast/amick

# Set environment variable in worker startup script
export SHARED_STORAGE_ROOT="/home/jovyan/vast/amick"
python -m sleap_rtc.worker
```

**Common Issues:**
- Mount point doesn't exist → Check Vast.ai storage configuration
- Permission denied → Verify directory is writable: `touch /home/jovyan/vast/amick/test.txt`

#### RunAI Configuration

RunAI workspaces may use different mount points:

```bash
# Check available mounts
df -h

# Common RunAI paths
export SHARED_STORAGE_ROOT="/workspace/shared"
# or
export SHARED_STORAGE_ROOT="/data/shared"
```

#### Local Development (Docker)

For local testing with Docker:

```bash
# Create shared volume in docker-compose.yml
volumes:
  shared_storage:
    driver: local

services:
  client:
    volumes:
      - shared_storage:/shared
    environment:
      - SHARED_STORAGE_ROOT=/shared

  worker:
    volumes:
      - shared_storage:/shared
    environment:
      - SHARED_STORAGE_ROOT=/shared
```

### Troubleshooting

#### Shared Storage Not Detected

**Symptom:** Logs show "Shared storage not configured, will use RTC transfer"

**Solutions:**
1. Verify environment variable is set: `echo $SHARED_STORAGE_ROOT`
2. Check path exists: `ls -la $SHARED_STORAGE_ROOT`
3. Verify path is a directory: `test -d $SHARED_STORAGE_ROOT && echo "OK" || echo "Not a directory"`
4. Check read/write permissions: `touch $SHARED_STORAGE_ROOT/test.txt && rm $SHARED_STORAGE_ROOT/test.txt`

#### Path Validation Errors

**Symptom:** Worker logs show "PATH_ERROR::Path outside shared storage"

**Solutions:**
1. Ensure Client and Worker use the same logical shared storage (even if mount points differ)
2. Verify both peers have read/write access
3. Check for symlink issues: Paths are resolved before validation

#### Disk Space Errors

**Symptom:** Client logs show "Insufficient disk space on shared storage"

**Solutions:**
1. Check available space: `df -h $SHARED_STORAGE_ROOT`
2. Clean up old job directories: `rm -rf $SHARED_STORAGE_ROOT/jobs/job_*`
3. The system requires 120% of file size (20% buffer for extracted files)

#### File Copy Timeout

**Symptom:** Client logs show "File copy timed out after X seconds"

**Solutions:**
1. Check network speed to shared storage: `dd if=/dev/zero of=$SHARED_STORAGE_ROOT/test.dat bs=1M count=1024`
2. Verify shared storage is mounted correctly (not hanging)
3. Consider increasing timeout for very slow storage (modify code if needed)

#### Permission Denied Errors

**Symptom:** Worker cannot read files or Client cannot write files

**Solutions:**
1. Check directory permissions: `ls -la $SHARED_STORAGE_ROOT`
2. Ensure user has write access: `id` and verify user/group permissions
3. For Docker: Verify volume mount permissions in docker-compose.yml
4. Try creating a test file: `echo "test" > $SHARED_STORAGE_ROOT/test.txt`

### Testing Shared Storage

Run the test suite to verify shared storage functionality:

```bash
# Test filesystem utilities
python test_filesystem.py

# Test error handling
python test_error_handling.py

# Test worker shared storage integration
python test_worker_shared_storage.py
```

**Manual Testing:**

```python
from pathlib import Path
from sleap_rtc.config import SharedStorageConfig
from sleap_rtc.filesystem import safe_copy, safe_mkdir, check_disk_space

# 1. Verify configuration
root = SharedStorageConfig.get_shared_storage_root()
print(f"Root: {root}")

# 2. Check disk space (5 GB)
has_space = check_disk_space(root, 5 * 1024**3)
print(f"Has 5 GB available: {has_space}")

# 3. Test write access
test_dir = root / "test_jobs" / "test_123"
safe_mkdir(test_dir)
test_file = test_dir / "test.txt"
test_file.write_text("Hello, shared storage!")
print(f"✓ Write test successful: {test_file}")

# 4. Test read access
content = test_file.read_text()
print(f"✓ Read test successful: {content}")

# 5. Cleanup
import shutil
shutil.rmtree(test_dir.parent)
print("✓ Cleanup successful")
```

### Security Considerations

The shared storage implementation includes multiple security layers:

1. **Path Validation:** All paths are validated to prevent traversal attacks
   ```python
   # Example: This is blocked
   bad_path = "../../etc/passwd"
   # Raises: PathValidationError
   ```

2. **Symlink Resolution:** Symlinks are resolved before validation
   ```python
   # Symlinks pointing outside shared root are rejected
   ```

3. **Permission Checks:** Read/write permissions verified before operations

4. **Relative Paths:** Only relative paths sent over network (security by design)

**Best Practices:**
- Never disable path validation
- Regularly audit shared storage access logs
- Use dedicated shared storage mount (don't share with sensitive data)
- Set appropriate filesystem permissions (755 for directories, 644 for files)

### Architecture Notes

**Message Flow (Shared Storage):**
```
Client                          Worker
------                          ------
1. Copy file to shared storage
2. JOB_ID::job_abc123       →
3. SHARED_INPUT_PATH::...   →   Validate path exists & within root
4.                          ←   PATH_VALIDATED::input
5. SHARED_OUTPUT_PATH::...  →   Create output directory
6.                          ←   PATH_VALIDATED::output
7. SHARED_STORAGE_JOB::start →  Process job (read from shared storage)
8.                          ←   TRAINING_COMPLETE
9.                          ←   JOB_COMPLETE::job_abc123::output_path
10. Read results from shared storage
```

**File Organization:**
```
$SHARED_STORAGE_ROOT/
├── jobs/
│   ├── job_abc123/
│   │   ├── training.zip     (input, copied by Client)
│   │   └── models/          (output, written by Worker)
│   │       ├── model1.h5
│   │       └── model2.h5
│   └── job_def456/
│       └── ...
```

For implementation details, see:
- `sleap_rtc/filesystem.py` - Core filesystem utilities
- `sleap_rtc/config.py` - Configuration management
- `sleap_rtc/protocol.py` - Message protocol definitions
- OpenSpec change proposal: `openspec/changes/add-shared-filesystem-transfer/`
