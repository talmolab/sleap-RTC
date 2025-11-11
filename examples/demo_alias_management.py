#!/usr/bin/env python3
"""Demo script showing alias management system functionality.

This script demonstrates:
- Setting and updating aliases
- Alias validation and collision handling
- Resolving identifiers (ID or alias)
- Listing and managing aliases
- Auto-suggestion system
"""

from pathlib import Path
from sleap_rtc.client.client_model_registry import ClientModelRegistry


def main():
    """Run the alias management demo."""
    print("=" * 70)
    print("Alias Management System Demo")
    print("=" * 70)
    print()

    # Create a temporary registry for demo purposes
    demo_path = Path("/tmp/sleap-rtc-alias-demo")
    demo_path.mkdir(exist_ok=True)
    registry_path = demo_path / "manifest.json"

    if registry_path.exists():
        registry_path.unlink()

    print(f"Creating registry at: {registry_path}")
    registry = ClientModelRegistry(registry_path=registry_path)
    print()

    # Register some models without aliases initially
    print("=" * 70)
    print("1. Registering Models (Without Aliases)")
    print("=" * 70)

    models_to_register = [
        {"id": "a3f5e8c9", "model_type": "centroid"},
        {"id": "7f2a1b3c", "model_type": "centroid"},
        {"id": "9c4d2e1f", "model_type": "topdown"},
    ]

    for model_info in models_to_register:
        registry.register(model_info)
        print(f"✓ Registered model: {model_info['id']} ({model_info['model_type']})")

    print()

    # Set aliases
    print("=" * 70)
    print("2. Setting Aliases")
    print("=" * 70)

    print("Setting alias 'production-v1' for model a3f5e8c9...")
    registry.set_alias("a3f5e8c9", "production-v1")
    print("✓ Alias set successfully")
    print()

    print("Setting alias 'legacy-2023' for model 7f2a1b3c...")
    registry.set_alias("7f2a1b3c", "legacy-2023")
    print("✓ Alias set successfully")
    print()

    print("Setting alias 'rat-baseline' for model 9c4d2e1f...")
    registry.set_alias("9c4d2e1f", "rat-baseline")
    print("✓ Alias set successfully")
    print()

    # List all aliases
    print("=" * 70)
    print("3. Listing All Aliases")
    print("=" * 70)

    aliases = registry.list_aliases(sort=True)
    for alias, model_id in aliases.items():
        model = registry.get(model_id)
        print(f"  {alias:20s} → {model_id} ({model['model_type']})")
    print()

    # Demonstrate alias validation
    print("=" * 70)
    print("4. Alias Validation Examples")
    print("=" * 70)

    test_aliases = [
        ("good-model", True),
        ("my model", False),  # has space
        ("model@prod", False),  # special char
        ("-leading", False),  # leading dash
        ("all", False),  # reserved
        ("a3f5e8c9", False),  # looks like model ID
    ]

    for alias, expected_valid in test_aliases:
        is_valid, error_msg = registry._validate_alias(alias)
        status = "✓" if is_valid else "✗"
        result = "Valid" if is_valid else f"Invalid: {error_msg}"
        print(f"  {status} '{alias:20s}' - {result}")
    print()

    # Demonstrate sanitization
    print("=" * 70)
    print("5. Alias Sanitization")
    print("=" * 70)

    sanitize_tests = [
        "my model",
        "Good Model 2024",
        "model@prod!",
        "  -trim-  ",
    ]

    for text in sanitize_tests:
        sanitized = registry._sanitize_alias(text)
        print(f"  '{text}' → '{sanitized}'")
    print()

    # Demonstrate resolution
    print("=" * 70)
    print("6. Identifier Resolution")
    print("=" * 70)

    identifiers = [
        "a3f5e8c9",  # Direct model ID
        "production-v1",  # Alias
        "nonexistent",  # Not found
    ]

    for identifier in identifiers:
        resolved = registry.resolve(identifier)
        if resolved:
            model = registry.get(resolved)
            alias_str = f" (alias: {model.get('alias')})" if model.get('alias') else ""
            print(f"  '{identifier}' → {resolved}{alias_str}")
        else:
            print(f"  '{identifier}' → Not found")
    print()

    # Demonstrate retrieval by alias
    print("=" * 70)
    print("7. Retrieving Models by Alias")
    print("=" * 70)

    model = registry.get_by_alias("production-v1")
    if model:
        print(f"  Model: {model['alias']} ({model['id']})")
        print(f"  Type: {model['model_type']}")
        print("  ✓ Retrieved successfully")
    print()

    # Demonstrate alias collision
    print("=" * 70)
    print("8. Alias Collision Handling")
    print("=" * 70)

    print("Attempting to set 'production-v1' for model 7f2a1b3c (already used by a3f5e8c9)...")
    success = registry.set_alias("7f2a1b3c", "production-v1")
    if not success:
        print("  ✗ Collision detected - alias not changed")
        print(f"  Current owner: {registry.resolve('production-v1')}")
    print()

    print("Using force=True to overwrite...")
    success = registry.set_alias("7f2a1b3c", "production-v1", force=True)
    if success:
        print("  ✓ Alias reassigned")
        print(f"  New owner: {registry.resolve('production-v1')}")
        # Model a3f5e8c9 should no longer have this alias
        old_model = registry.get("a3f5e8c9")
        print(f"  Old owner now has alias: {old_model.get('alias')}")
    print()

    # Restore for next demos
    registry.set_alias("a3f5e8c9", "production-v1", force=True)
    registry.set_alias("7f2a1b3c", "legacy-2023", force=True)

    # Demonstrate renaming
    print("=" * 70)
    print("9. Renaming Aliases")
    print("=" * 70)

    print("Current alias for a3f5e8c9:", registry.get("a3f5e8c9")["alias"])
    print("Changing to 'production-mouse-v2'...")
    registry.set_alias("a3f5e8c9", "production-mouse-v2")

    print("New alias:", registry.get("a3f5e8c9")["alias"])
    print("Old alias 'production-v1' exists?", "production-v1" in registry.list_aliases())
    print("New alias 'production-mouse-v2' exists?", "production-mouse-v2" in registry.list_aliases())
    print()

    # Demonstrate removal
    print("=" * 70)
    print("10. Removing Aliases")
    print("=" * 70)

    print("Aliases before removal:")
    for alias in registry.list_aliases():
        print(f"  - {alias}")
    print()

    print("Removing alias 'legacy-2023'...")
    success = registry.remove_alias("legacy-2023")
    if success:
        print("  ✓ Alias removed")
        model = registry.get("7f2a1b3c")
        print(f"  Model still exists: {registry.exists('7f2a1b3c')}")
        print(f"  Model alias field: {model.get('alias')}")
    print()

    print("Aliases after removal:")
    for alias in registry.list_aliases():
        print(f"  - {alias}")
    print()

    # Demonstrate auto-suggestion
    print("=" * 70)
    print("11. Auto-Suggestion System")
    print("=" * 70)

    suggestion_tests = [
        ("my model", None),
        ("mouse", "centroid"),
        ("production-mouse-v2", None),  # Already exists
        ("@#$%", None),  # Invalid characters
    ]

    for text, model_type in suggestion_tests:
        suggestion = registry.suggest_alias(text, model_type=model_type)
        type_str = f" (type: {model_type})" if model_type else ""
        print(f"  '{text}'{type_str} → '{suggestion}'")
    print()

    # Final state
    print("=" * 70)
    print("12. Final Registry State")
    print("=" * 70)

    all_models = registry.list()
    print(f"Total models: {len(all_models)}")
    print()

    print("Models with aliases:")
    for model in all_models:
        alias_str = model.get('alias') or '(no alias)'
        location = "ID only" if not model.get('alias') else f"ID + alias"
        print(f"  {model['id']:12s} - {alias_str:25s} [{location}]")
    print()

    print("All aliases (sorted):")
    for alias, model_id in registry.list_aliases(sort=True).items():
        print(f"  {alias:25s} → {model_id}")
    print()

    # Demonstrate practical usage
    print("=" * 70)
    print("13. Practical Usage Pattern")
    print("=" * 70)

    print("Typical workflow:")
    print()

    # Simulate user wanting to use a model
    user_input = "production-mouse-v2"
    print(f"User wants to use model: '{user_input}'")
    print()

    # Resolve identifier
    model_id = registry.resolve(user_input)
    if model_id:
        model = registry.get(model_id)
        alias_display = f" (alias: {model['alias']})" if model.get('alias') else ""
        print(f"  ✓ Resolved to: {model_id}{alias_display}")
        print(f"  Type: {model['model_type']}")
        print(f"  Checkpoint: {model.get('checkpoint_path', 'N/A')}")
        print()
        print("  → Can now use this model for inference!")
    else:
        print(f"  ✗ Model '{user_input}' not found")
        print()
        print("  Available models:")
        for alias in registry.list_aliases():
            print(f"    - {alias}")
    print()

    print("=" * 70)
    print("Demo Complete!")
    print("=" * 70)
    print()
    print(f"Registry saved to: {registry_path}")
    print("You can inspect the JSON file to see the alias structure.")
    print()


if __name__ == "__main__":
    main()
