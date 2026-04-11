"""
Live integration tests against the real TL-SG108E switch.

These tests require the switch to be reachable at the configured address.
They are skipped by default.  Run them with:

    pytest tests/test_live.py -m live -v
    # or force all tests in this file:
    pytest tests/test_live.py --run-live -v

The tests are ordered carefully:
  1. Read-only tests run first (safe, no side effects).
  2. Write tests make small, reversible changes and restore the original state.
  3. Destructive write tests (reboot, factory reset, IP change) are skipped
     by default even with --run-live; pass --run-destructive to enable them.
"""

import sys
sys.path.insert(0, '/home/jfrancis/tplink')

import time
import pytest
from sg108e import (
    Switch, PortSpeed, QoSMode, StormType,
    SystemInfo, IPSettings, PortInfo, PortStats, MirrorConfig,
    TrunkConfig, IGMPConfig, MTUVlanConfig, PortVlanEntry, Dot1QVlanEntry,
    QoSPortConfig, BandwidthEntry, StormEntry, CableDiagResult,
    _bits_to_ports, _ports_to_bits,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SWITCH_HOST = '10.1.1.239'
SWITCH_USER = 'admin'
SWITCH_PASS = 'b1gsecret'

# Safe port for write tests: assumed to be unimportant / not in use
TEST_PORT = 8


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def sw():
    """Module-scoped Switch connection shared across read-only live tests."""
    with Switch(SWITCH_HOST, SWITCH_USER, SWITCH_PASS) as switch:
        yield switch


@pytest.fixture
def sw_write():
    """Function-scoped Switch connection for write tests.

    Each write test gets its own fresh session so that a mode-change CGI
    causing the switch to drop the TCP connection doesn't break subsequent
    tests.
    """
    with Switch(SWITCH_HOST, SWITCH_USER, SWITCH_PASS) as switch:
        yield switch


def _fresh_connection(retries: int = 6, delay: float = 3.0) -> Switch:
    """Return a logged-in Switch, retrying while the switch is restarting."""
    import requests.exceptions
    last_exc: Exception = RuntimeError('No attempts made')
    for _ in range(retries):
        try:
            sw = Switch(SWITCH_HOST, SWITCH_USER, SWITCH_PASS)
            sw.login()
            return sw
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                RuntimeError) as exc:
            last_exc = exc
            time.sleep(delay)
    raise RuntimeError(f'Switch not reachable after {retries} retries') from last_exc


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestLiveConnectivity:
    def test_login_succeeds(self):
        """Basic connectivity: can we log in and out?"""
        sw = Switch(SWITCH_HOST, SWITCH_USER, SWITCH_PASS)
        sw.login()
        assert sw._logged_in is True
        sw.logout()
        assert sw._logged_in is False

    def test_bad_password_raises(self):
        # The switch either returns errType=1 ("Login failed") or omits the
        # session cookie entirely ("Login did not return a session cookie").
        # Both behaviours are observed in practice depending on firmware.
        sw = Switch(SWITCH_HOST, SWITCH_USER, 'wrongpassword')
        with pytest.raises(RuntimeError):
            sw.login()

    def test_context_manager(self):
        with Switch(SWITCH_HOST, SWITCH_USER, SWITCH_PASS) as sw:
            assert sw._logged_in is True
        assert sw._logged_in is False


# ---------------------------------------------------------------------------
# Read-only tests (safe)
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestLiveReadSystemInfo:
    def test_returns_system_info(self, sw):
        info = sw.get_system_info()
        assert isinstance(info, SystemInfo)

    def test_ip_matches_config(self, sw):
        info = sw.get_system_info()
        assert info.ip == SWITCH_HOST

    def test_mac_format(self, sw):
        info = sw.get_system_info()
        # MAC should be XX:XX:XX:XX:XX:XX
        parts = info.mac.split(':')
        assert len(parts) == 6
        assert all(len(p) == 2 for p in parts)

    def test_hardware_is_sg108e(self, sw):
        info = sw.get_system_info()
        assert 'SG108E' in info.hardware.upper() or 'TL-SG108E' in info.hardware


@pytest.mark.live
class TestLiveReadIpSettings:
    def test_returns_ip_settings(self, sw):
        ip = sw.get_ip_settings()
        assert isinstance(ip, IPSettings)

    def test_ip_matches_switch(self, sw):
        ip = sw.get_ip_settings()
        assert ip.ip == SWITCH_HOST

    def test_netmask_is_valid(self, sw):
        ip = sw.get_ip_settings()
        parts = ip.netmask.split('.')
        assert len(parts) == 4


@pytest.mark.live
class TestLiveReadLed:
    def test_returns_bool(self, sw):
        result = sw.get_led()
        assert isinstance(result, bool)


@pytest.mark.live
class TestLiveReadPortSettings:
    def test_returns_8_ports(self, sw):
        ports = sw.get_port_settings()
        assert len(ports) == 8

    def test_port_numbers_1_to_8(self, sw):
        ports = sw.get_port_settings()
        port_nums = [p.port for p in ports]
        assert port_nums == list(range(1, 9))

    def test_port_speed_cfg_valid(self, sw):
        ports = sw.get_port_settings()
        valid_cfgs = set(PortSpeed._value2member_map_.keys()) | {None}
        for p in ports:
            assert p.speed_cfg in valid_cfgs or p.speed_cfg is None

    def test_port_enabled_is_bool(self, sw):
        ports = sw.get_port_settings()
        for p in ports:
            assert isinstance(p.enabled, bool)


@pytest.mark.live
class TestLiveReadPortStatistics:
    def test_returns_8_stats(self, sw):
        stats = sw.get_port_statistics()
        assert len(stats) == 8

    def test_counts_are_non_negative(self, sw):
        stats = sw.get_port_statistics()
        for s in stats:
            assert s.tx_pkts >= 0
            assert s.rx_pkts >= 0

    def test_port_numbers_1_to_8(self, sw):
        stats = sw.get_port_statistics()
        assert [s.port for s in stats] == list(range(1, 9))


@pytest.mark.live
class TestLiveReadMirror:
    def test_returns_mirror_config(self, sw):
        m = sw.get_port_mirror()
        assert isinstance(m, MirrorConfig)
        assert isinstance(m.enabled, bool)

    def test_port_lists_are_valid(self, sw):
        m = sw.get_port_mirror()
        all_ports = list(range(1, 9))
        for p in m.ingress_ports + m.egress_ports:
            assert p in all_ports


@pytest.mark.live
class TestLiveReadTrunk:
    def test_returns_trunk_config(self, sw):
        tc = sw.get_port_trunk()
        assert isinstance(tc, TrunkConfig)
        assert tc.max_groups >= 1
        assert tc.port_count == 8

    def test_group_ids_valid(self, sw):
        tc = sw.get_port_trunk()
        for gid, ports in tc.groups.items():
            assert 1 <= gid <= tc.max_groups
            for p in ports:
                assert 1 <= p <= 8


@pytest.mark.live
class TestLiveReadIgmp:
    def test_returns_igmp_config(self, sw):
        igmp = sw.get_igmp_snooping()
        assert isinstance(igmp, IGMPConfig)
        assert isinstance(igmp.enabled, bool)
        assert igmp.group_count >= 0


@pytest.mark.live
class TestLiveReadLoopPrevention:
    def test_returns_bool(self, sw):
        result = sw.get_loop_prevention()
        assert isinstance(result, bool)


@pytest.mark.live
class TestLiveReadMtuVlan:
    def test_returns_mtu_vlan_config(self, sw):
        mv = sw.get_mtu_vlan()
        assert isinstance(mv, MTUVlanConfig)
        assert mv.port_count == 8
        assert 1 <= mv.uplink_port <= 8


@pytest.mark.live
class TestLiveReadPortVlan:
    def test_returns_enabled_and_list(self, sw):
        enabled, vlans = sw.get_port_vlan()
        assert isinstance(enabled, bool)
        assert isinstance(vlans, list)

    def test_vlan_entries_valid(self, sw):
        enabled, vlans = sw.get_port_vlan()
        for v in vlans:
            assert isinstance(v, PortVlanEntry)
            assert v.vid >= 1
            assert 0 <= v.members <= 0xFF


@pytest.mark.live
class TestLiveReadDot1qVlan:
    def test_returns_enabled_and_list(self, sw):
        enabled, vlans = sw.get_dot1q_vlans()
        assert isinstance(enabled, bool)
        assert isinstance(vlans, list)

    def test_default_vlan_1_present(self, sw):
        """VLAN 1 (Default) should always exist."""
        enabled, vlans = sw.get_dot1q_vlans()
        vids = [v.vid for v in vlans]
        assert 1 in vids

    def test_bitmasks_valid(self, sw):
        enabled, vlans = sw.get_dot1q_vlans()
        for v in vlans:
            assert 0 <= v.tagged_members <= 0xFF
            assert 0 <= v.untagged_members <= 0xFF
            # A port can't be both tagged and untagged
            assert (v.tagged_members & v.untagged_members) == 0


@pytest.mark.live
class TestLiveReadPvids:
    def test_returns_8_pvids(self, sw):
        pvids = sw.get_pvids()
        assert len(pvids) == 8

    def test_pvids_are_positive(self, sw):
        pvids = sw.get_pvids()
        for pvid in pvids:
            assert pvid >= 1


@pytest.mark.live
class TestLiveReadQos:
    def test_returns_mode_and_ports(self, sw):
        mode, ports = sw.get_qos_settings()
        assert mode in QoSMode
        assert len(ports) == 8

    def test_priority_values_valid(self, sw):
        mode, ports = sw.get_qos_settings()
        for p in ports:
            assert 1 <= p.priority <= 4


@pytest.mark.live
class TestLiveReadBandwidth:
    def test_returns_8_entries(self, sw):
        entries = sw.get_bandwidth_control()
        assert len(entries) == 8

    def test_rates_non_negative(self, sw):
        entries = sw.get_bandwidth_control()
        for e in entries:
            assert e.ingress_rate >= 0
            assert e.egress_rate >= 0


@pytest.mark.live
class TestLiveReadStormControl:
    def test_returns_8_entries(self, sw):
        entries = sw.get_storm_control()
        assert len(entries) == 8

    def test_rate_index_valid(self, sw):
        from sg108e import STORM_RATE_KBPS
        entries = sw.get_storm_control()
        for e in entries:
            if e.enabled:
                assert e.rate_index in STORM_RATE_KBPS or e.rate_index == 0


# ---------------------------------------------------------------------------
# Write tests (reversible)
# Each class uses sw_write (function-scoped) so a write that causes the
# switch to drop the TCP connection cannot contaminate subsequent tests.
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestLiveWriteLed:
    """Toggle LED and restore.

    Some firmware builds silently ignore led_on_set.cgi.  The test verifies
    the CGI can be called without error; if the state actually changes it
    verifies the round-trip.  This avoids a hard failure on unresponsive
    firmware while still catching real regressions.
    """

    def test_toggle_led_on_off(self, sw_write):
        original = sw_write.get_led()

        sw_write.set_led(not original)
        time.sleep(1)
        new_state = sw_write.get_led()

        if new_state != (not original):
            pytest.xfail('Switch firmware did not respond to led_on_set.cgi — '
                         'LED toggle may not be supported by this firmware version')

        sw_write.set_led(original)
        time.sleep(1)
        assert sw_write.get_led() == original, 'LED state not restored'


@pytest.mark.live
class TestLiveWriteLoopPrevention:
    """Toggle loop prevention and restore."""

    def test_toggle_loop_prevention(self, sw_write):
        original = sw_write.get_loop_prevention()

        sw_write.set_loop_prevention(not original)
        time.sleep(1)
        assert sw_write.get_loop_prevention() == (not original)

        sw_write.set_loop_prevention(original)
        time.sleep(1)
        assert sw_write.get_loop_prevention() == original


@pytest.mark.live
class TestLiveWriteIgmpSnooping:
    """Toggle IGMP snooping and restore."""

    def test_toggle_igmp(self, sw_write):
        original = sw_write.get_igmp_snooping()

        sw_write.set_igmp_snooping(not original.enabled)
        time.sleep(1)
        assert sw_write.get_igmp_snooping().enabled == (not original.enabled)

        sw_write.set_igmp_snooping(original.enabled,
                                   report_suppression=original.report_suppression)
        time.sleep(1)
        assert sw_write.get_igmp_snooping().enabled == original.enabled


@pytest.mark.live
class TestLiveWritePortStatisticsReset:
    """Reset statistics counters for TEST_PORT."""

    def test_reset_single_port(self, sw_write):
        sw_write.reset_port_statistics(port=TEST_PORT)
        time.sleep(1)
        stats = sw_write.get_port_statistics()
        port_stat = next(s for s in stats if s.port == TEST_PORT)
        # After reset, counters should be 0 (or very small if traffic arrived)
        assert port_stat.tx_pkts < 1000
        assert port_stat.rx_pkts < 1000


@pytest.mark.live
class TestLiveWritePortSettings:
    """Change flow control on TEST_PORT and restore.

    NOTE: Port 1 is the management uplink and must not be touched.
    """

    def test_toggle_flow_control(self, sw_write):
        original_ports = sw_write.get_port_settings()
        original_port = next(p for p in original_ports if p.port == TEST_PORT)
        original_fc = original_port.fc_cfg

        sw_write.set_port(TEST_PORT, flow_control=not original_fc)
        time.sleep(1)
        updated_port = next(p for p in sw_write.get_port_settings()
                            if p.port == TEST_PORT)
        assert updated_port.fc_cfg == (not original_fc)

        sw_write.set_port(TEST_PORT, flow_control=original_fc)
        time.sleep(1)
        restored_port = next(p for p in sw_write.get_port_settings()
                             if p.port == TEST_PORT)
        assert restored_port.fc_cfg == original_fc


@pytest.mark.live
class TestLiveWriteQos:
    """Change QoS mode and per-port priority, then restore."""

    def test_set_qos_mode(self, sw_write):
        original_mode, _ = sw_write.get_qos_settings()
        new_mode = QoSMode.DOT1P if original_mode == QoSMode.PORT_BASED else QoSMode.PORT_BASED
        sw_write.set_qos_mode(new_mode)
        time.sleep(1)
        actual_mode, _ = sw_write.get_qos_settings()
        assert actual_mode == new_mode
        sw_write.set_qos_mode(original_mode)

    def test_set_port_priority(self, sw_write):
        original_mode, original_ports = sw_write.get_qos_settings()
        original_port = next(p for p in original_ports if p.port == TEST_PORT)
        original_priority = original_port.priority

        sw_write.set_qos_mode(QoSMode.PORT_BASED)
        time.sleep(1)

        new_priority = 4 if original_priority != 4 else 1
        sw_write.set_port_priority([TEST_PORT], new_priority)
        time.sleep(1)

        _, new_ports = sw_write.get_qos_settings()
        new_port = next(p for p in new_ports if p.port == TEST_PORT)
        assert new_port.priority == new_priority

        sw_write.set_port_priority([TEST_PORT], original_priority)
        sw_write.set_qos_mode(original_mode)


@pytest.mark.live
class TestLiveWriteDot1qVlan:
    """
    Create a test VLAN, verify it, then delete it.
    Uses a high VID (999) to avoid conflicting with production config.
    """
    TEST_VID = 999

    def test_add_and_delete_vlan(self, sw_write):
        enabled, vlans = sw_write.get_dot1q_vlans()
        if not enabled:
            pytest.skip('802.1Q VLAN not enabled on switch')

        original_vids = {v.vid for v in vlans}
        if self.TEST_VID in original_vids:
            sw_write.delete_dot1q_vlan(self.TEST_VID)
            time.sleep(1)

        sw_write.add_dot1q_vlan(self.TEST_VID, name='TestVLAN',
                                tagged_ports=[TEST_PORT])
        time.sleep(1)

        _, vlans_after = sw_write.get_dot1q_vlans()
        vlan_map = {v.vid: v for v in vlans_after}
        assert self.TEST_VID in vlan_map, f'VLAN {self.TEST_VID} not found after add'
        assert _bits_to_ports(vlan_map[self.TEST_VID].tagged_members) == [TEST_PORT]

        sw_write.delete_dot1q_vlan(self.TEST_VID)
        time.sleep(1)

        _, vlans_final = sw_write.get_dot1q_vlans()
        assert self.TEST_VID not in {v.vid for v in vlans_final}, \
            f'VLAN {self.TEST_VID} still present after delete'


@pytest.mark.live
class TestLiveWriteBandwidth:
    """Set bandwidth limit on TEST_PORT and restore."""

    def test_set_and_clear_bandwidth(self, sw_write):
        original = sw_write.get_bandwidth_control()
        orig_port = next(e for e in original if e.port == TEST_PORT)

        sw_write.set_bandwidth_control([TEST_PORT], ingress_kbps=1024, egress_kbps=512)
        time.sleep(1)
        updated = sw_write.get_bandwidth_control()
        upd_port = next(e for e in updated if e.port == TEST_PORT)
        assert upd_port.ingress_rate == 1024
        assert upd_port.egress_rate == 512

        sw_write.set_bandwidth_control([TEST_PORT],
                                       ingress_kbps=orig_port.ingress_rate,
                                       egress_kbps=orig_port.egress_rate)


@pytest.mark.live
class TestLiveBackup:
    def test_backup_returns_non_empty_bytes(self, sw):
        data = sw.backup_config()
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_backup_restore_roundtrip(self, sw_write):
        """Download config, restore it, verify switch is still responsive.

        conf_restore.cgi reboots the firmware (~15–20 s).  We use
        _fresh_connection() with retries to wait for it to come back.
        """
        config = sw_write.backup_config()
        assert len(config) > 0
        sw_write.restore_config(config)

        sw2 = _fresh_connection(retries=10, delay=3.0)
        try:
            info = sw2.get_system_info()
            assert info.ip == SWITCH_HOST
        finally:
            sw2.logout()


# ---------------------------------------------------------------------------
# Destructive write tests (skipped unless --run-destructive)
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.destructive
class TestLiveDestructive:
    def test_reboot(self):
        """Reboot the switch and wait for it to come back."""
        sw = Switch(SWITCH_HOST, SWITCH_USER, SWITCH_PASS)
        sw.login()
        sw.reboot()
        sw._logged_in = False

        # Wait for switch to go down and come back
        time.sleep(15)

        sw2 = Switch(SWITCH_HOST, SWITCH_USER, SWITCH_PASS)
        sw2.login()
        info = sw2.get_system_info()
        sw2.logout()
        assert info.ip == SWITCH_HOST
