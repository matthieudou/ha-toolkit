# HA Toolkit

This public repository contains the HA Toolkit custom integration for Home Assistant. It must remain free of instance-specific configuration, credentials, device names, and inventory data.

## Product language

Read [`CONTEXT.md`](CONTEXT.md) when naming a feature, configuration family, device, or user-facing concept. Record a newly settled domain term there when it changes the glossary.

## Integration structure

- Keep one Home Assistant integration with the domain `ha_toolkit` under `custom_components/ha_toolkit`.
- Treat Energy, Lights, and Device Management as independent internal modules with a shared installation and release.
- Keep Home Assistant platform adapters at the integration root and place family-specific implementation behind small interfaces.
- Read [`docs/architecture.md`](docs/architecture.md) before changing Recorder calculations, statistics imports, Light Group+, or config-entry ownership.
- Use public Home Assistant interfaces. Document and test any intentional dependency on an internal Home Assistant class.

## Language

- Write code, technical identifiers, docstrings, reference strings, and public documentation in English.
- Keep `translations/fr.json` complete for every user-facing string.
- Use Home Assistant's required English keys and enum values unchanged.

## Validation

After changing Python code, run all three commands from the repository root:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The change is complete when all three commands pass and every modified user-facing string has an English source and a French translation.

## Distribution

- Keep the repository compatible with HACS category `Integration`.
- Publish only `custom_components/ha_toolkit` as the integration directory.
- Keep the manifest version and project version aligned when preparing a release.
- Verify the repository with `git rev-parse --show-toplevel` before committing, tagging, or pushing.
