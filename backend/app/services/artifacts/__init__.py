"""Artifact service package (BE-S8 split).

Implementation modules behind the ``app.services.artifact_service`` facade.
External callers import from the facade; intra-package references import the
concrete submodule. This package root intentionally re-exports nothing so
there is a single re-export surface (the facade).
"""
