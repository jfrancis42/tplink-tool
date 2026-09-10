"""
Shared pytest configuration for the tplink test suite.

Live-switch options are registered here so they are available for
both test_live.py and any future tests that use the live marker.
"""


def pytest_addoption(parser):
    parser.addoption(
        '--run-live', action='store_true', default=False,
        help='Run tests that require a live switch at 10.1.1.239',
    )
    parser.addoption(
        '--run-destructive', action='store_true', default=False,
        help='Run destructive tests (reboot, factory reset) — implies --run-live',
    )


def pytest_configure(config):
    config.addinivalue_line(
        'markers', 'live: requires live switch (pass --run-live to enable)',
    )
    config.addinivalue_line(
        'markers',
        'destructive: destructive / hard-to-reverse write (pass --run-destructive to enable)',
    )


def pytest_collection_modifyitems(config, items):
    import pytest
    run_live = config.getoption('--run-live') or config.getoption('--run-destructive')
    run_destructive = config.getoption('--run-destructive')

    skip_live = pytest.mark.skip(reason='requires live switch — pass --run-live')
    skip_destructive = pytest.mark.skip(reason='destructive test — pass --run-destructive')

    for item in items:
        if 'live' in item.keywords and not run_live:
            item.add_marker(skip_live)
        if 'destructive' in item.keywords and not run_destructive:
            item.add_marker(skip_destructive)
