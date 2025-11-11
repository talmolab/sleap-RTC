# Alias Management Usage Examples

This document provides practical examples of using the alias management system in the SLEAP-RTC model registry.

## Table of Contents

1. [Basic Alias Operations](#basic-alias-operations)
2. [Collision Handling](#collision-handling)
3. [Resolution and Retrieval](#resolution-and-retrieval)
4. [Validation and Sanitization](#validation-and-sanitization)
5. [Auto-Suggestions](#auto-suggestions)
6. [Practical Workflows](#practical-workflows)

---

## Basic Alias Operations

### Setting an Alias

```python
from sleap_rtc.client.client_model_registry import ClientModelRegistry

registry = ClientModelRegistry()

# Register a model
registry.register({
    "id": "a3f5e8c9",
    "model_type": "centroid"
})

# Set an alias
registry.set_alias("a3f5e8c9", "production-v1")
# ✓ Model now accessible by both ID and alias
```

### Renaming an Alias

```python
# Change the alias
registry.set_alias("a3f5e8c9", "production-mouse-v2")
# ✓ Old alias removed, new alias assigned
# ✓ Model still has same ID
```

### Removing an Alias

```python
# Remove alias but keep model
registry.remove_alias("production-mouse-v2")
# ✓ Model still exists, accessible by ID
# ✓ Alias "production-mouse-v2" no longer maps to anything
```

### Listing All Aliases

```python
# Get all aliases (sorted alphabetically)
aliases = registry.list_aliases(sort=True)

for alias, model_id in aliases.items():
    model = registry.get(model_id)
    print(f"{alias} → {model_id} ({model['model_type']})")

# Output:
# legacy-2023 → 7f2a1b3c (centroid)
# production-v1 → a3f5e8c9 (centroid)
# rat-baseline → 9c4d2e1f (topdown)
```

---

## Collision Handling

### Detect Collision (Without Force)

```python
# Set alias on first model
registry.set_alias("model1", "my-alias")

# Try to use same alias on second model
success = registry.set_alias("model2", "my-alias")

if not success:
    print("Alias already in use!")
    current_owner = registry.resolve("my-alias")
    print(f"Current owner: {current_owner}")
```

### Force Overwrite

```python
# Forcefully reassign alias
success = registry.set_alias("model2", "my-alias", force=True)
# ✓ Alias now points to model2
# ✓ model1 no longer has this alias
```

### Check Before Setting

```python
# Check if alias exists before setting
existing_aliases = registry.list_aliases()

if "my-alias" in existing_aliases:
    print(f"Alias in use by: {existing_aliases['my-alias']}")
    # Decide: rename, force, or skip
else:
    registry.set_alias("model1", "my-alias")
```

---

## Resolution and Retrieval

### Resolve Any Identifier

```python
# Works with model IDs
model_id = registry.resolve("a3f5e8c9")
# → "a3f5e8c9"

# Works with aliases
model_id = registry.resolve("production-v1")
# → "a3f5e8c9"

# Returns None if not found
model_id = registry.resolve("nonexistent")
# → None
```

### Retrieve by Alias

```python
# Get model directly by alias
model = registry.get_by_alias("production-v1")

if model:
    print(f"ID: {model['id']}")
    print(f"Type: {model['model_type']}")
    print(f"Alias: {model['alias']}")
else:
    print("Alias not found")
```

### Universal Lookup Pattern

```python
def get_model_by_identifier(registry, identifier):
    """Get model by ID or alias."""
    model_id = registry.resolve(identifier)
    if model_id:
        return registry.get(model_id)
    return None

# Use with either ID or alias
model = get_model_by_identifier(registry, "production-v1")
model = get_model_by_identifier(registry, "a3f5e8c9")
# Both work!
```

---

## Validation and Sanitization

### Check Alias Validity

```python
# Validate before setting
is_valid, error_msg = registry._validate_alias("my-model")

if is_valid:
    registry.set_alias("model1", "my-model")
else:
    print(f"Invalid alias: {error_msg}")
```

### Sanitize User Input

```python
# Clean up user input
user_input = "My Model!"
sanitized = registry._sanitize_alias(user_input)
# → "My-Model"

# Validate sanitized version
is_valid, error = registry._validate_alias(sanitized)
if is_valid:
    registry.set_alias("model1", sanitized)
```

### Validation Rules

```python
# ✓ Valid aliases
valid_aliases = [
    "good-model",
    "my_model",
    "Model123",
    "prod-v1",
]

# ✗ Invalid aliases
invalid_aliases = [
    "my model",      # spaces
    "model@prod",    # special chars
    "-leading",      # leading dash
    "trailing_",     # trailing underscore
    "all",           # reserved word
    "a3f5e8c9",      # looks like model ID
]
```

---

## Auto-Suggestions

### Basic Suggestion

```python
# Suggest based on text
suggestion = registry.suggest_alias("my model")
# → "my-model"

# Guaranteed to be valid and unique
registry.set_alias("model1", suggestion)
```

### Suggestion with Model Type

```python
# Include model type in suggestion
suggestion = registry.suggest_alias("mouse", model_type="centroid")
# → "centroid-mouse"
```

### Handle Collisions with Suggestions

```python
# If "my-model" already exists...
suggestion = registry.suggest_alias("my model")
# → "my-model-v1"

# Next suggestion
suggestion = registry.suggest_alias("my model")
# → "my-model-v2"
```

### Interactive Alias Input

```python
def prompt_for_alias(registry, model_id, base_text=None):
    """Interactively prompt user for alias."""

    # Provide suggestion if base text given
    if base_text:
        suggestion = registry.suggest_alias(base_text)
        print(f"Suggestion: {suggestion}")
        alias = input("Enter alias (or press Enter to use suggestion): ")
        if not alias:
            alias = suggestion
    else:
        alias = input("Enter alias: ")

    # Sanitize and validate
    alias = registry._sanitize_alias(alias)
    is_valid, error = registry._validate_alias(alias)

    if not is_valid:
        print(f"Error: {error}")
        return None

    # Check collision
    if alias in registry.list_aliases():
        overwrite = input(f"Alias '{alias}' exists. Overwrite? [y/N]: ")
        force = overwrite.lower() == 'y'
        return registry.set_alias(model_id, alias, force=force)

    return registry.set_alias(model_id, alias)
```

---

## Practical Workflows

### Training Workflow

```python
def after_training(registry, model_id, model_type):
    """Set alias after training completes."""

    # Prompt user
    response = input("Give it a friendly name? [y/N]: ")
    if response.lower() != 'y':
        return

    # Suggest based on type
    suggestion = registry.suggest_alias("model", model_type=model_type)
    print(f"Suggestion: {suggestion}")

    alias = input("Enter alias (or press Enter for suggestion): ")
    if not alias:
        alias = suggestion

    # Set alias
    try:
        registry.set_alias(model_id, alias)
        print(f"✓ Model tagged as '{alias}'")
    except ValueError as e:
        print(f"✗ Invalid alias: {e}")
```

### Model Selection for Inference

```python
def select_model_for_inference(registry):
    """Let user select model by ID or alias."""

    # Show available models
    print("Available models:")
    for alias, model_id in registry.list_aliases(sort=True).items():
        model = registry.get(model_id)
        print(f"  {alias} ({model_id}) - {model['model_type']}")

    # Get user input
    identifier = input("Enter model ID or alias: ")

    # Resolve
    model_id = registry.resolve(identifier)
    if not model_id:
        print(f"Model '{identifier}' not found")
        return None

    model = registry.get(model_id)
    alias_str = f" (alias: {model['alias']})" if model.get('alias') else ""
    print(f"Using model: {model_id}{alias_str}")

    return model['checkpoint_path']
```

### Import with Auto-Alias

```python
def import_model_with_alias(registry, model_path, model_id, model_type):
    """Import model and prompt for alias."""

    # Register model
    registry.register({
        "id": model_id,
        "model_type": model_type,
        "source": "local-import",
        "local_path": str(model_path),
    })

    # Extract directory name for suggestion
    dir_name = Path(model_path).name
    suggestion = registry.suggest_alias(dir_name, model_type=model_type)

    print(f"Suggestion: {suggestion}")
    alias = input("Enter alias (optional, press Enter to skip): ")

    if alias:
        alias = registry._sanitize_alias(alias)
        try:
            registry.set_alias(model_id, alias)
            print(f"✓ Model imported as '{alias}'")
        except ValueError as e:
            print(f"✗ Invalid alias: {e}")
    else:
        print(f"✓ Model imported (no alias)")
```

### Batch Aliasing

```python
def batch_set_aliases(registry, model_ids, prefix):
    """Set aliases for multiple models with version numbers."""

    for i, model_id in enumerate(model_ids, start=1):
        alias = f"{prefix}-v{i}"

        try:
            registry.set_alias(model_id, alias)
            print(f"✓ {model_id} → {alias}")
        except Exception as e:
            print(f"✗ {model_id}: {e}")

# Usage
model_ids = ["a3f5e8c9", "7f2a1b3c", "9c4d2e1f"]
batch_set_aliases(registry, model_ids, "production")
# → production-v1, production-v2, production-v3
```

### Migration from ID-Only

```python
def migrate_to_aliases(registry):
    """Add aliases to models that don't have them."""

    models = registry.list()
    count = 0

    for model in models:
        if not model.get('alias'):
            # Generate alias from model type and ID
            suggestion = f"{model['model_type']}-{model['id'][:6]}"

            try:
                registry.set_alias(model['id'], suggestion)
                print(f"✓ Added alias: {suggestion} → {model['id']}")
                count += 1
            except Exception as e:
                print(f"✗ Failed for {model['id']}: {e}")

    print(f"Added {count} aliases")
```

---

## Best Practices

### 1. Use Descriptive Names

```python
# ✓ Good
"production-mouse-v1"
"experimental-highres"
"baseline-2024"

# ✗ Avoid
"model1"
"test"
"x"
```

### 2. Include Version Numbers

```python
# For iterative development
"my-model-v1"
"my-model-v2"
"my-model-v3"
```

### 3. Use Type Prefixes

```python
# Clear what type of model it is
"centroid-production"
"topdown-baseline"
"bottomup-experimental"
```

### 4. Consistent Naming Scheme

```python
# Pick a pattern and stick to it
"{purpose}-{animal}-v{version}"
# Examples:
"production-mouse-v1"
"experimental-rat-v2"
"baseline-human-v1"
```

### 5. Always Validate User Input

```python
def safe_set_alias(registry, model_id, user_input):
    """Safely set alias with validation."""
    # Sanitize
    alias = registry._sanitize_alias(user_input)

    # Validate
    is_valid, error = registry._validate_alias(alias)
    if not is_valid:
        raise ValueError(f"Invalid alias: {error}")

    # Set with collision handling
    return registry.set_alias(model_id, alias, force=False)
```

---

## Summary

The alias management system provides:

- ✅ Human-readable model names
- ✅ Collision detection and resolution
- ✅ Flexible resolution (ID or alias)
- ✅ Auto-suggestion for convenience
- ✅ Validation for safety
- ✅ Independent client/worker namespaces

**Key principle:** Model IDs are permanent, aliases are flexible labels that can change over time.
