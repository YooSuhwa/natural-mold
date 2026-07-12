"""Marketplace install package — BE-S3 split of ``install_service``.

Type-specific install/update logic lives in ``skill`` / ``mcp`` /
``agent_blueprint``; shared pieces in ``common`` / ``snapshot`` /
``bindings``. ``app.marketplace.install_service`` stays the public
facade + dispatcher — import from there, not from this package.
"""
