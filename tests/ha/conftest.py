"""Fixtures for HA-side tests (pytest-homeassistant-custom-component)."""

from __future__ import annotations

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations, mock_async_zeroconf):
    """Load custom_components/ and keep zeroconf (a manifest dependency) off the network."""
    yield
