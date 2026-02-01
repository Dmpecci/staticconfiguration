# Changelog

All notable changes to this project will be documented in this file.

## [1.0.1] - 2026-02-01

### Changed
- Migration logic now uses decoders strictly for semantic validation:
  - If a decoder validates the stored value (returning an instance of `data_type`),
    the raw serialized value is preserved.
  - If validation fails, the field is reset to its default value.
- `StaticConfigBase.set()` now correctly accepts `None` as a valid value for any
  `Data` field, representing explicit absence of value.
- Clarified and enforced the semantics of `None` across persistence and migration.

### Fixed
- Fixed incorrect decoder handling during migrations that caused valid complex
  values to reset to defaults.
- Fixed type validation in `set()` that incorrectly rejected `None`.
