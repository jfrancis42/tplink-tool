"""
Backward-compatibility shim.  The SDK now lives in the tplink_tool package.

    pip install tplink-tool

Then use:

    from tplink_tool import Switch, PortSpeed

This module re-exports everything for code written against the old name.
"""

from tplink_tool import *  # noqa: F401, F403
from tplink_tool import (  # noqa: F401
    PortSpeed, QoSMode, StormType, STORM_RATE_KBPS,
    SystemInfo, IPSettings, PortInfo, PortStats,
    MirrorConfig, TrunkConfig, IGMPConfig, LoopPreventionConfig,
    MTUVlanConfig, PortVlanEntry, Dot1QVlanEntry, QoSPortConfig,
    BandwidthEntry, StormEntry, CableDiagResult, Switch,
    _bits_to_ports, _ports_to_bits,
)
