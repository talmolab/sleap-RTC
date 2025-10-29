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
black sleap_RTC/

# Lint
ruff check sleap_RTC/

# Type check (if configured)
# mypy sleap_RTC/

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
