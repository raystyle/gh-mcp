import pytest


@pytest.fixture
def anyio_backend():
    """Run the async tests on asyncio only (anyio's default also tries trio)."""
    return "asyncio"
