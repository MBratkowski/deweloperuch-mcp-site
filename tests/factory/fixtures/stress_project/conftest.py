import pytest


@pytest.fixture
def missing_fixture_that_raises() -> None:
    raise RuntimeError("setup crashed")
