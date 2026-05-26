"""On-disk storage path helpers (ADR-018).

All storage_path columns store paths *relative to* ``settings.data_root``.
Read sites must go through :func:`app.storage.paths.resolve_data_path`.
"""
