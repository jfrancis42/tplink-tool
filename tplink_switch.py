"""
Backward-compatibility shim.  The SDK now lives in the tplink_tool package.

    pip install tplink-tool

Then use:

    from tplink_tool import Switch, PortSpeed

This module re-exports everything for code written against the old name.
"""

from tplink_tool import *  # noqa: F401, F403
from tplink_tool import (  # noqa: F401
    # The private parsing helpers too: tests/test_parser.py imports them
    # through this shim, and after the package rename they were the only
    # names it did NOT re-export -- so the whole suite failed at collection
    # with ImportError and had stopped running entirely.
    _extract_top_script, _extract_var, _js_to_py, _extract_tmp_info,
    PortSpeed, QoSMode, StormType, STORM_RATE_KBPS,
    SystemInfo, IPSettings, PortInfo, PortStats,
    MirrorConfig, TrunkConfig, IGMPConfig, LoopPreventionConfig,
    MTUVlanConfig, PortVlanEntry, Dot1QVlanEntry, QoSPortConfig,
    BandwidthEntry, StormEntry, CableDiagResult, Switch,
    _bits_to_ports, _ports_to_bits,
)
