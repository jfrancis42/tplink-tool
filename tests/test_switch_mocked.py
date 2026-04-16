"""
Tests for the Switch class using mocked HTTP responses.

No network access or live switch is required.
"""

import sys
sys.path.insert(0, '/home/jfrancis/tplink')

import time
import pytest
from unittest.mock import MagicMock, patch, call

from tplink_switch import (
    Switch, SystemInfo, IPSettings, PortInfo, PortStats, MirrorConfig,
    TrunkConfig, IGMPConfig, MTUVlanConfig, PortVlanEntry, Dot1QVlanEntry,
    QoSMode, QoSPortConfig, BandwidthEntry, StormEntry, StormType,
    PortSpeed, CableDiagResult,
)


# ---------------------------------------------------------------------------
# HTML fixture builders
# ---------------------------------------------------------------------------

def script(content):
    return f'<html><head></head><body><script>\n{content}\n</script></body></html>'


LOGIN_OK_HTML = '<script>errType=0</script>'
LOGIN_FAIL_HTML = '<script>errType=1</script>'
LOGIN_LIMIT_HTML = '<script>errType=3</script>'
# A response that looks like the login page (triggers re-auth)
EXPIRED_SESSION_HTML = 'Please login <form action="logon.cgi">errType</form>'

SYSTEM_INFO_HTML = script(
    "var info_ds = {\n"
    "  descriStr:['TL-SG108E'],\n"
    "  macStr:['AA:BB:CC:DD:EE:FF'],\n"
    "  ipStr:['10.1.1.239'],\n"
    "  netmaskStr:['255.255.255.0'],\n"
    "  gatewayStr:['10.1.1.1'],\n"
    "  firmwareStr:['1.0.0 Build 20230218'],\n"
    "  hardwareStr:['TL-SG108E 6.0']\n"
    "};"
)

IP_SETTINGS_HTML = script(
    "var ip_ds = {state:0, ipStr:['10.1.1.239'], "
    "netmaskStr:['255.255.255.0'], gatewayStr:['10.1.1.1']};"
)

IP_SETTINGS_DHCP_HTML = script(
    "var ip_ds = {state:1, ipStr:['192.168.1.100'], "
    "netmaskStr:['255.255.255.0'], gatewayStr:['192.168.1.1']};"
)

LED_ON_HTML = script('var led = 1;')
LED_OFF_HTML = script('var led = 0;')

PORT_SETTINGS_HTML = script(
    "var max_port_num = 8;\n"
    "var all_info = {\n"
    "  state:[1,1,1,0,1,1,1,1,0,0],\n"
    "  spd_cfg:[1,1,1,1,1,1,1,1,0,0],\n"
    "  spd_act:[4,5,0,0,0,0,0,0,0,0],\n"
    "  fc_cfg:[0,1,0,0,0,0,0,0,0,0],\n"
    "  fc_act:[0,0,0,0,0,0,0,0,0,0],\n"
    "  trunk_info:[0,0,0,0,1,1,0,0,0,0]\n"
    "};"
)

PORT_STATS_HTML = script(
    "var max_port_num = 8;\n"
    "var all_info = {pkts:[100,200,300,400,500,600,700,800,"
    "900,1000,1100,1200,1300,1400,1500,1600]};"
)

PORT_MIRROR_HTML = script(
    "var max_port_num = 8;\n"
    "var MirrEn = 1;\n"
    "var MirrPort = 8;\n"
    "var MirrMode = 0;\n"
    "var mirr_info = {ingress:[1,1,0,0,0,0,0,0], egress:[0,0,1,0,0,0,0,0]};"
)

PORT_MIRROR_DISABLED_HTML = script(
    "var max_port_num = 8;\n"
    "var MirrEn = 0;\n"
    "var MirrPort = 0;\n"
    "var MirrMode = 0;\n"
    "var mirr_info = {ingress:[0,0,0,0,0,0,0,0], egress:[0,0,0,0,0,0,0,0]};"
)

PORT_TRUNK_HTML = script(
    "var trunk_conf = {maxTrunkNum:2, portNum:8, "
    "portStr_g1:[1,1,0,0,0,0,0,0], portStr_g2:[0,0,0,0,0,0,0,0]};"
)

PORT_TRUNK_EMPTY_HTML = script(
    "var trunk_conf = {maxTrunkNum:2, portNum:8, "
    "portStr_g1:[0,0,0,0,0,0,0,0], portStr_g2:[0,0,0,0,0,0,0,0]};"
)

IGMP_HTML = script(
    "var igmp_ds = {state:1, suppressionState:0, count:3};"
)

IGMP_DISABLED_HTML = script(
    "var igmp_ds = {state:0, suppressionState:0, count:0};"
)

LOOP_PREVENTION_ON_HTML = script('var lpEn = 1;')
LOOP_PREVENTION_OFF_HTML = script('var lpEn = 0;')

MTU_VLAN_HTML = script(
    "var mtu_ds = {state:1, portNum:8, uplinkPort:1};"
)

MTU_VLAN_DISABLED_HTML = script(
    "var mtu_ds = {state:0, portNum:8, uplinkPort:1};"
)

PORT_VLAN_HTML = script(
    "var pvlan_ds = {state:1, count:2, vids:[1,2], mbrs:[0xFF,0x03]};"
)

PORT_VLAN_DISABLED_HTML = script(
    "var pvlan_ds = {state:0, count:0, vids:[], mbrs:[]};"
)

DOT1Q_VLAN_HTML = script(
    "var qvlan_ds = {state:1, count:2, vids:[1,100], "
    "names:['Default','Management'], tagMbrs:[0,128], untagMbrs:[255,3]};"
)

DOT1Q_VLAN_DISABLED_HTML = script(
    "var qvlan_ds = {state:0, count:1, vids:[1], "
    "names:['Default'], tagMbrs:[0], untagMbrs:[255]};"
)

PVID_HTML = script(
    "var pvid_ds = {pvids:[1,1,1,100,1,1,1,1]};"
)

QOS_HTML = script(
    "var qosMode = 0;\n"      # 0=PORT_BASED (was incorrectly 1 in original SDK)
    "var portNumber = 8;\n"
    "var pPri = [1,2,3,4,1,2,3,4];"
)

BANDWIDTH_HTML = script(
    "var portNumber = 8;\n"
    "var bcInfo = [1024,512,0, 0,0,0, 0,0,0, 0,0,0, 0,0,0, 0,0,0, 0,0,0, 0,0,0];"
)

STORM_HTML = script(
    "var portNumber = 8;\n"
    "var scInfo = [5,7,1, 0,0,0, 0,0,0, 0,0,0, 0,0,0, 0,0,0, 0,0,0, 0,0,0];"
)

CABLE_DIAG_HTML = script(
    "var maxPort = 8;\n"
    "var cablestate = [0,1,2,3,0,0,0,0];\n"
    "var cablelength = [15,-1,-1,-1,-1,-1,-1,-1];"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_response(text='', status_code=200, content=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.content = content if content is not None else text.encode()
    resp.raise_for_status = MagicMock()
    return resp


def make_cookie(name='H_P_SSID'):
    c = MagicMock()
    c.name = name
    return c


@pytest.fixture
def sw():
    """Switch instance with a pre-authenticated mocked session."""
    switch = Switch('10.1.1.1', password='test')
    switch._logged_in = True
    switch._login_time = time.time()
    switch._session = MagicMock()
    switch._session.cookies = [make_cookie()]
    return switch


@pytest.fixture
def sw_unauthed():
    """Switch instance that requires login on first use."""
    switch = Switch('10.1.1.1', password='test')
    switch._logged_in = False
    mock_session = MagicMock()
    cookie = make_cookie()
    mock_session.cookies = [cookie]
    # Default POST (login) returns success
    mock_session.post.return_value = make_response(LOGIN_OK_HTML)
    switch._session = mock_session
    return switch


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_success(self, sw_unauthed):
        sw_unauthed._session.post.return_value = make_response(LOGIN_OK_HTML)
        sw_unauthed.login()
        assert sw_unauthed._logged_in is True

    def test_login_sends_credentials(self, sw_unauthed):
        sw_unauthed._session.post.return_value = make_response(LOGIN_OK_HTML)
        sw_unauthed.login()
        post_call = sw_unauthed._session.post.call_args
        data = post_call.kwargs.get('data') or post_call.args[1] if len(post_call.args) > 1 else {}
        # Also check via keyword
        if not data:
            data = post_call[1].get('data', {})
        assert 'username' in str(post_call)
        assert 'password' in str(post_call)

    def test_login_posts_to_logon_cgi(self, sw_unauthed):
        sw_unauthed._session.post.return_value = make_response(LOGIN_OK_HTML)
        sw_unauthed.login()
        url_called = sw_unauthed._session.post.call_args.args[0]
        assert 'logon.cgi' in url_called

    def test_login_bad_credentials_raises(self, sw_unauthed):
        sw_unauthed._session.post.return_value = make_response(LOGIN_FAIL_HTML)
        with pytest.raises(RuntimeError, match='Login failed'):
            sw_unauthed.login()

    def test_login_limit_full_raises(self, sw_unauthed):
        sw_unauthed._session.post.return_value = make_response(LOGIN_LIMIT_HTML)
        with pytest.raises(RuntimeError, match='Login failed'):
            sw_unauthed.login()

    def test_login_no_cookie_raises(self, sw_unauthed):
        sw_unauthed._session.post.return_value = make_response(LOGIN_OK_HTML)
        sw_unauthed._session.cookies = []  # No cookie returned
        with pytest.raises(RuntimeError, match='session cookie'):
            sw_unauthed.login()

    def test_logout_calls_logout_url(self, sw):
        sw._session.get.return_value = make_response('')
        sw.logout()
        url_called = sw._session.get.call_args.args[0]
        assert 'Logout.htm' in url_called

    def test_logout_clears_logged_in(self, sw):
        sw._session.get.return_value = make_response('')
        sw.logout()
        assert sw._logged_in is False

    def test_context_manager_logs_in_and_out(self):
        switch = Switch('10.1.1.1', password='test')
        mock_session = MagicMock()
        mock_session.cookies = [make_cookie()]
        mock_session.post.return_value = make_response(LOGIN_OK_HTML)
        mock_session.get.return_value = make_response('')
        switch._session = mock_session

        with switch:
            assert switch._logged_in is True
        assert switch._logged_in is False

    def test_is_login_page_true(self):
        assert Switch._is_login_page(EXPIRED_SESSION_HTML) is True

    def test_is_login_page_false(self):
        assert Switch._is_login_page('<html>normal page</html>') is False

    def test_session_reauth_on_expired_cookie(self, sw):
        """If a page fetch returns the login page, the SDK re-authenticates."""
        sw._logged_in = True
        # First GET returns expired session HTML; login() then fetches
        # PortSettingRpm to cache port count; third GET is the actual retry.
        sw._session.get.side_effect = [
            make_response(EXPIRED_SESSION_HTML),  # triggers re-auth
            make_response(PORT_SETTINGS_HTML),    # port count fetch inside login()
            make_response(SYSTEM_INFO_HTML),       # after re-auth, success
        ]
        sw._session.post.return_value = make_response(LOGIN_OK_HTML)

        info = sw.get_system_info()
        assert info.ip == '10.1.1.239'
        # post (login) should have been called once during re-auth
        assert sw._session.post.called


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

class TestGetSystemInfo:
    def test_returns_correct_fields(self, sw):
        sw._session.get.return_value = make_response(SYSTEM_INFO_HTML)
        info = sw.get_system_info()
        assert isinstance(info, SystemInfo)
        assert info.ip == '10.1.1.239'
        assert info.netmask == '255.255.255.0'
        assert info.gateway == '10.1.1.1'
        assert info.mac == 'AA:BB:CC:DD:EE:FF'
        assert info.firmware == '1.0.0 Build 20230218'
        assert info.hardware == 'TL-SG108E 6.0'

    def test_fetches_correct_page(self, sw):
        sw._session.get.return_value = make_response(SYSTEM_INFO_HTML)
        sw.get_system_info()
        url = sw._session.get.call_args.args[0]
        assert 'SystemInfoRpm.htm' in url

    def test_raises_on_unparseable_html(self, sw):
        sw._session.get.return_value = make_response('<html>garbage</html>')
        with pytest.raises(RuntimeError):
            sw.get_system_info()


class TestSetDeviceDescription:
    def test_sends_correct_param(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_device_description('MySwitch')
        call_kwargs = sw._session.get.call_args.kwargs
        assert call_kwargs['params']['sysName'] == 'MySwitch'

    def test_correct_cgi(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_device_description('X')
        url = sw._session.get.call_args.args[0]
        assert 'system_name_set.cgi' in url


class TestGetIpSettings:
    def test_returns_static_config(self, sw):
        sw._session.get.return_value = make_response(IP_SETTINGS_HTML)
        ip = sw.get_ip_settings()
        assert isinstance(ip, IPSettings)
        assert ip.dhcp is False
        assert ip.ip == '10.1.1.239'
        assert ip.netmask == '255.255.255.0'
        assert ip.gateway == '10.1.1.1'

    def test_returns_dhcp_config(self, sw):
        sw._session.get.return_value = make_response(IP_SETTINGS_DHCP_HTML)
        ip = sw.get_ip_settings()
        assert ip.dhcp is True
        assert ip.ip == '192.168.1.100'

    def test_raises_on_bad_html(self, sw):
        sw._session.get.return_value = make_response('<html></html>')
        with pytest.raises(RuntimeError):
            sw.get_ip_settings()


class TestSetIpSettings:
    def test_sends_correct_cgi(self, sw):
        # First call is the read-back for current settings, second is the write
        sw._session.get.side_effect = [
            make_response(IP_SETTINGS_HTML),
            make_response(''),
        ]
        sw.set_ip_settings(ip='10.1.1.100')
        write_call = sw._session.get.call_args_list[1]
        url = write_call.args[0]
        assert 'ip_setting.cgi' in url

    def test_uses_current_values_for_unspecified_params(self, sw):
        sw._session.get.side_effect = [
            make_response(IP_SETTINGS_HTML),
            make_response(''),
        ]
        sw.set_ip_settings(ip='10.1.1.100')
        write_params = sw._session.get.call_args_list[1].kwargs['params']
        assert write_params['ip_address'] == '10.1.1.100'
        assert write_params['ip_netmask'] == '255.255.255.0'  # from current
        assert write_params['ip_gateway'] == '10.1.1.1'       # from current

    def test_dhcp_enabled(self, sw):
        sw._session.get.side_effect = [
            make_response(IP_SETTINGS_HTML),
            make_response(''),
        ]
        sw.set_ip_settings(dhcp=True)
        write_params = sw._session.get.call_args_list[1].kwargs['params']
        assert write_params['dhcpSetting'] == 'enable'

    def test_dhcp_disabled(self, sw):
        sw._session.get.side_effect = [
            make_response(IP_SETTINGS_DHCP_HTML),
            make_response(''),
        ]
        sw.set_ip_settings(dhcp=False)
        write_params = sw._session.get.call_args_list[1].kwargs['params']
        assert write_params['dhcpSetting'] == 'disable'


class TestLed:
    def test_get_led_on(self, sw):
        sw._session.get.return_value = make_response(LED_ON_HTML)
        assert sw.get_led() is True

    def test_get_led_off(self, sw):
        sw._session.get.return_value = make_response(LED_OFF_HTML)
        assert sw.get_led() is False

    def test_set_led_on(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_led(True)
        params = sw._session.get.call_args.kwargs['params']
        assert params['rd_led'] == '1'
        assert params['led_cfg'] == 'Apply'

    def test_set_led_off(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_led(False)
        params = sw._session.get.call_args.kwargs['params']
        assert params['rd_led'] == '0'


# ---------------------------------------------------------------------------
# Port settings
# ---------------------------------------------------------------------------

class TestGetPortSettings:
    def test_returns_8_ports(self, sw):
        sw._session.get.return_value = make_response(PORT_SETTINGS_HTML)
        ports = sw.get_port_settings()
        assert len(ports) == 8

    def test_port_enabled_disabled(self, sw):
        sw._session.get.return_value = make_response(PORT_SETTINGS_HTML)
        ports = sw.get_port_settings()
        assert ports[0].enabled is True   # state[0]=1
        assert ports[3].enabled is False  # state[3]=0

    def test_port_speed_actual(self, sw):
        sw._session.get.return_value = make_response(PORT_SETTINGS_HTML)
        ports = sw.get_port_settings()
        assert ports[0].speed_act == PortSpeed.M100H   # spd_act[0]=4
        assert ports[1].speed_act == PortSpeed.M100F   # spd_act[1]=5
        assert ports[2].speed_act is None              # spd_act[2]=0

    def test_port_flow_control(self, sw):
        sw._session.get.return_value = make_response(PORT_SETTINGS_HTML)
        ports = sw.get_port_settings()
        assert ports[0].fc_cfg is False
        assert ports[1].fc_cfg is True

    def test_port_trunk_membership(self, sw):
        sw._session.get.return_value = make_response(PORT_SETTINGS_HTML)
        ports = sw.get_port_settings()
        assert ports[4].trunk_id == 1  # trunk_info[4]=1
        assert ports[5].trunk_id == 1  # trunk_info[5]=1
        assert ports[0].trunk_id == 0  # no trunk

    def test_port_numbers_are_1_based(self, sw):
        sw._session.get.return_value = make_response(PORT_SETTINGS_HTML)
        ports = sw.get_port_settings()
        assert ports[0].port == 1
        assert ports[7].port == 8


class TestSetPort:
    def test_set_single_port_speed(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_port(1, speed=PortSpeed.AUTO)
        url = sw._session.get.call_args.args[0]
        assert 'port_setting.cgi' in url
        params = sw._session.get.call_args.kwargs['params']
        assert ('portid', '1') in params
        assert ('speed', '1') in params

    def test_set_port_no_change_sentinel(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_port(1, speed=PortSpeed.AUTO)
        params = sw._session.get.call_args.kwargs['params']
        # Unspecified fields should use '7' (no-change sentinel)
        assert ('state', '7') in params
        assert ('flowcontrol', '7') in params

    def test_set_port_enabled(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_port(3, enabled=True)
        params = sw._session.get.call_args.kwargs['params']
        assert ('state', '1') in params
        assert ('portid', '3') in params

    def test_set_port_disabled(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_port(3, enabled=False)
        params = sw._session.get.call_args.kwargs['params']
        assert ('state', '0') in params

    def test_set_flow_control(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_port(2, flow_control=True)
        params = sw._session.get.call_args.kwargs['params']
        assert ('flowcontrol', '1') in params

    def test_set_multiple_ports(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_ports([1, 2, 3], speed=PortSpeed.M1000F)
        params = sw._session.get.call_args.kwargs['params']
        port_ids = [v for k, v in params if k == 'portid']
        assert port_ids == ['1', '2', '3']


# ---------------------------------------------------------------------------
# Port statistics
# ---------------------------------------------------------------------------

class TestGetPortStatistics:
    def test_returns_8_entries(self, sw):
        sw._session.get.return_value = make_response(PORT_STATS_HTML)
        stats = sw.get_port_statistics()
        assert len(stats) == 8

    def test_tx_rx_values(self, sw):
        sw._session.get.return_value = make_response(PORT_STATS_HTML)
        stats = sw.get_port_statistics()
        # pkts = [100,200,300,400,...] → port1: tx=100, rx=200
        assert stats[0].tx_pkts == 100
        assert stats[0].rx_pkts == 200
        assert stats[1].tx_pkts == 300
        assert stats[1].rx_pkts == 400

    def test_port_numbers_1_based(self, sw):
        sw._session.get.return_value = make_response(PORT_STATS_HTML)
        stats = sw.get_port_statistics()
        assert stats[0].port == 1
        assert stats[7].port == 8


class TestResetPortStatistics:
    def test_reset_all(self, sw):
        sw._session.get.return_value = make_response('')
        sw.reset_port_statistics()
        url = sw._session.get.call_args.args[0]
        assert 'port_statistics_set.cgi' in url
        params = sw._session.get.call_args.kwargs['params']
        assert params['op'] == '1'
        assert 'portid' not in params

    def test_reset_single_port(self, sw):
        sw._session.get.return_value = make_response('')
        sw.reset_port_statistics(port=3)
        params = sw._session.get.call_args.kwargs['params']
        assert params['op'] == '1'
        assert params['portid'] == '3'


# ---------------------------------------------------------------------------
# Port mirroring
# ---------------------------------------------------------------------------

class TestGetPortMirror:
    def test_enabled_mirror(self, sw):
        sw._session.get.return_value = make_response(PORT_MIRROR_HTML)
        m = sw.get_port_mirror()
        assert isinstance(m, MirrorConfig)
        assert m.enabled is True
        assert m.dest_port == 8
        assert m.ingress_ports == [1, 2]
        assert m.egress_ports == [3]

    def test_disabled_mirror(self, sw):
        sw._session.get.return_value = make_response(PORT_MIRROR_DISABLED_HTML)
        m = sw.get_port_mirror()
        assert m.enabled is False
        assert m.ingress_ports == []
        assert m.egress_ports == []


class TestSetPortMirror:
    def test_enable_mirror(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_port_mirror(True, dest_port=8, ingress_ports=[1, 2])
        calls = sw._session.get.call_args_list
        # First call: mirror_enabled_set.cgi
        url0 = calls[0].args[0]
        assert 'mirror_enabled_set.cgi' in url0
        params0 = calls[0].kwargs['params']
        assert params0['state'] == '1'
        assert params0['mirroringport'] == '8'

    def test_enable_mirror_sets_mirrored_ports(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_port_mirror(True, dest_port=8, ingress_ports=[1], egress_ports=[2])
        calls = sw._session.get.call_args_list
        # Should have 3 calls: enable + 2 mirrored ports
        assert len(calls) == 3
        port_urls = [c.args[0] for c in calls[1:]]
        assert all('mirrored_port_set.cgi' in u for u in port_urls)

    def test_disable_mirror(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_port_mirror(False)
        calls = sw._session.get.call_args_list
        assert len(calls) == 1
        url = calls[0].args[0]
        assert 'mirror_enabled_set.cgi' in url
        params = calls[0].kwargs['params']
        assert params['state'] == '0'


# ---------------------------------------------------------------------------
# Port trunking (LAG)
# ---------------------------------------------------------------------------

class TestGetPortTrunk:
    def test_returns_trunk_config(self, sw):
        sw._session.get.return_value = make_response(PORT_TRUNK_HTML)
        tc = sw.get_port_trunk()
        assert isinstance(tc, TrunkConfig)
        assert tc.max_groups == 2
        assert tc.port_count == 8
        assert tc.groups == {1: [1, 2]}

    def test_no_trunk_groups(self, sw):
        sw._session.get.return_value = make_response(PORT_TRUNK_EMPTY_HTML)
        tc = sw.get_port_trunk()
        assert tc.groups == {}


class TestSetPortTrunk:
    def test_create_trunk_group(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_port_trunk(1, [1, 2])
        url = sw._session.get.call_args.args[0]
        assert 'port_trunk_set.cgi' in url
        params = sw._session.get.call_args.kwargs['params']
        assert ('groupId', '1') in params
        assert ('portid', '1') in params
        assert ('portid', '2') in params

    def test_delete_trunk_group(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_port_trunk(1, [])
        url = sw._session.get.call_args.args[0]
        assert 'port_trunk_display.cgi' in url
        params = sw._session.get.call_args.kwargs['params']
        assert params['chk_trunk'] == '1'
        assert params['setDelete'] == 'Delete'


# ---------------------------------------------------------------------------
# IGMP snooping
# ---------------------------------------------------------------------------

class TestGetIgmpSnooping:
    def test_enabled(self, sw):
        sw._session.get.return_value = make_response(IGMP_HTML)
        igmp = sw.get_igmp_snooping()
        assert isinstance(igmp, IGMPConfig)
        assert igmp.enabled is True
        assert igmp.report_suppression is False
        assert igmp.group_count == 3

    def test_disabled(self, sw):
        sw._session.get.return_value = make_response(IGMP_DISABLED_HTML)
        igmp = sw.get_igmp_snooping()
        assert igmp.enabled is False


class TestSetIgmpSnooping:
    def test_enable(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_igmp_snooping(True)
        params = sw._session.get.call_args.kwargs['params']
        assert params['igmp_mode'] == '1'
        assert params['reportSu_mode'] == '0'

    def test_enable_with_report_suppression(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_igmp_snooping(True, report_suppression=True)
        params = sw._session.get.call_args.kwargs['params']
        assert params['reportSu_mode'] == '1'

    def test_disable(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_igmp_snooping(False)
        params = sw._session.get.call_args.kwargs['params']
        assert params['igmp_mode'] == '0'


# ---------------------------------------------------------------------------
# Loop prevention
# ---------------------------------------------------------------------------

class TestLoopPrevention:
    def test_get_enabled(self, sw):
        sw._session.get.return_value = make_response(LOOP_PREVENTION_ON_HTML)
        assert sw.get_loop_prevention() is True

    def test_get_disabled(self, sw):
        sw._session.get.return_value = make_response(LOOP_PREVENTION_OFF_HTML)
        assert sw.get_loop_prevention() is False

    def test_set_enabled(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_loop_prevention(True)
        url = sw._session.get.call_args.args[0]
        assert 'loop_prevention_set.cgi' in url
        params = sw._session.get.call_args.kwargs['params']
        assert params['lpEn'] == '1'

    def test_set_disabled(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_loop_prevention(False)
        params = sw._session.get.call_args.kwargs['params']
        assert params['lpEn'] == '0'


# ---------------------------------------------------------------------------
# VLAN: MTU
# ---------------------------------------------------------------------------

class TestMtuVlan:
    def test_get_enabled(self, sw):
        sw._session.get.return_value = make_response(MTU_VLAN_HTML)
        mv = sw.get_mtu_vlan()
        assert isinstance(mv, MTUVlanConfig)
        assert mv.enabled is True
        assert mv.uplink_port == 1
        assert mv.port_count == 8

    def test_get_disabled(self, sw):
        sw._session.get.return_value = make_response(MTU_VLAN_DISABLED_HTML)
        mv = sw.get_mtu_vlan()
        assert mv.enabled is False

    def test_set_enabled(self, sw):
        sw._session.get.side_effect = [
            make_response(MTU_VLAN_HTML),  # read for current uplink
            make_response(''),
        ]
        sw.set_mtu_vlan(True)
        write_params = sw._session.get.call_args_list[1].kwargs['params']
        assert write_params['mtu_en'] == '1'
        assert write_params['uplinkPort'] == '1'  # preserved from current

    def test_set_with_custom_uplink(self, sw):
        sw._session.get.side_effect = [
            make_response(MTU_VLAN_HTML),
            make_response(''),
        ]
        sw.set_mtu_vlan(True, uplink_port=4)
        write_params = sw._session.get.call_args_list[1].kwargs['params']
        assert write_params['uplinkPort'] == '4'


# ---------------------------------------------------------------------------
# VLAN: Port-based
# ---------------------------------------------------------------------------

class TestPortVlan:
    def test_get_enabled_with_vlans(self, sw):
        sw._session.get.return_value = make_response(PORT_VLAN_HTML)
        enabled, vlans = sw.get_port_vlan()
        assert enabled is True
        assert len(vlans) == 2
        assert vlans[0].vid == 1
        assert vlans[0].members == 0xFF   # all ports
        assert vlans[1].vid == 2
        assert vlans[1].members == 0x03   # ports 1,2

    def test_get_disabled(self, sw):
        sw._session.get.return_value = make_response(PORT_VLAN_DISABLED_HTML)
        enabled, vlans = sw.get_port_vlan()
        assert enabled is False
        assert vlans == []

    def test_set_enabled(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_port_vlan_enabled(True)
        params = sw._session.get.call_args.kwargs['params']
        assert params['pvlan_en'] == '1'
        assert params['pvlan_mode'] == 'Apply'

    def test_add_vlan(self, sw):
        sw._session.get.return_value = make_response('')
        sw.add_port_vlan(vid=3, member_ports=[1, 2, 3])
        url = sw._session.get.call_args.args[0]
        assert 'pvlanSet.cgi' in url
        params = sw._session.get.call_args.kwargs['params']
        assert ('vid', '3') in params
        assert ('selPorts', '1') in params
        assert ('selPorts', '2') in params
        assert ('selPorts', '3') in params
        assert ('pvlan_add', 'Apply') in params

    def test_delete_vlan(self, sw):
        sw._session.get.return_value = make_response('')
        sw.delete_port_vlan(vid=3)
        params = sw._session.get.call_args.kwargs['params']
        assert params['selVlans'] == '3'
        assert params['pvlan_del'] == 'Delete'


# ---------------------------------------------------------------------------
# VLAN: 802.1Q
# ---------------------------------------------------------------------------

class TestDot1qVlan:
    def test_get_enabled(self, sw):
        sw._session.get.return_value = make_response(DOT1Q_VLAN_HTML)
        enabled, vlans = sw.get_dot1q_vlans()
        assert enabled is True
        assert len(vlans) == 2

    def test_get_vlan_fields(self, sw):
        sw._session.get.return_value = make_response(DOT1Q_VLAN_HTML)
        enabled, vlans = sw.get_dot1q_vlans()
        v1 = vlans[0]
        assert v1.vid == 1
        assert v1.name == 'Default'
        assert v1.tagged_members == 0
        assert v1.untagged_members == 255
        v100 = vlans[1]
        assert v100.vid == 100
        assert v100.name == 'Management'
        assert v100.tagged_members == 128   # port 8

    def test_get_disabled(self, sw):
        sw._session.get.return_value = make_response(DOT1Q_VLAN_DISABLED_HTML)
        enabled, vlans = sw.get_dot1q_vlans()
        assert enabled is False

    def test_set_enabled(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_dot1q_enabled(True)
        params = sw._session.get.call_args.kwargs['params']
        assert params['qvlan_en'] == '1'
        assert params['qvlan_mode'] == 'Apply'

    def test_set_disabled(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_dot1q_enabled(False)
        params = sw._session.get.call_args.kwargs['params']
        assert params['qvlan_en'] == '0'

    def test_add_vlan_tagged_untagged(self, sw):
        sw._session.get.return_value = make_response('')
        sw.add_dot1q_vlan(vid=100, name='Corp', tagged_ports=[8], untagged_ports=[5])
        params = sw._session.get.call_args.kwargs['params']
        assert params['vid'] == '100'
        assert params['vname'] == 'Corp'
        assert params['selType_8'] == '1'  # tagged
        assert params['selType_5'] == '0'  # untagged
        assert params['selType_1'] == '2'  # not member
        assert params['qvlan_add'] == 'Add/Modify'

    def test_add_vlan_all_ports_default_not_member(self, sw):
        sw._session.get.return_value = make_response('')
        sw.add_dot1q_vlan(vid=50, tagged_ports=[1])
        params = sw._session.get.call_args.kwargs['params']
        # Ports 2-8 not in either list → not member
        for p in range(2, 9):
            assert params[f'selType_{p}'] == '2'
        assert params['selType_1'] == '1'  # tagged

    def test_delete_vlan(self, sw):
        sw._session.get.return_value = make_response('')
        sw.delete_dot1q_vlan(vid=100)
        params = sw._session.get.call_args.kwargs['params']
        assert params['selVlans'] == '100'
        assert params['qvlan_del'] == 'Delete'


# ---------------------------------------------------------------------------
# PVID
# ---------------------------------------------------------------------------

class TestPvid:
    def test_get_pvids(self, sw):
        sw._session.get.return_value = make_response(PVID_HTML)
        pvids = sw.get_pvids()
        assert pvids == [1, 1, 1, 100, 1, 1, 1, 1]

    def test_set_pvid_single_port(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_pvid([1], 10)
        params = sw._session.get.call_args.kwargs['params']
        assert params['pvid'] == '10'
        assert params['pbm'] == '1'  # bit 0 = port 1

    def test_set_pvid_multiple_ports(self, sw):
        sw._session.get.return_value = make_response('')
        sw.set_pvid([1, 2], 20)
        params = sw._session.get.call_args.kwargs['params']
        assert params['pvid'] == '20'
        assert params['pbm'] == '3'  # bits 0,1 = ports 1,2


# ---------------------------------------------------------------------------
# QoS
# ---------------------------------------------------------------------------

class TestQos:
    def test_get_qos_mode_and_priorities(self, sw):
        sw._session.get.return_value = make_response(QOS_HTML)
        mode, ports = sw.get_qos_settings()
        assert mode == QoSMode.PORT_BASED
        assert len(ports) == 8
        assert ports[0].priority == 1
        assert ports[0].port == 1
        assert ports[3].priority == 4

    def test_set_qos_mode_port_based(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_qos_mode(QoSMode.PORT_BASED)
        data = sw._session.post.call_args.kwargs['data']
        assert data['rd_qosmode'] == '0'   # PORT_BASED = 0

    def test_set_qos_mode_dot1p(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_qos_mode(QoSMode.DOT1P)
        data = sw._session.post.call_args.kwargs['data']
        assert data['rd_qosmode'] == '1'   # DOT1P = 1

    def test_set_qos_mode_dscp(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_qos_mode(QoSMode.DSCP)
        data = sw._session.post.call_args.kwargs['data']
        assert data['rd_qosmode'] == '2'   # DSCP = 2

    def test_set_qos_mode_uses_post(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_qos_mode(QoSMode.PORT_BASED)
        url = sw._session.post.call_args.args[0]
        assert 'qos_mode_set.cgi' in url

    def test_set_port_priority(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_port_priority([1, 2], priority=4)
        data = sw._session.post.call_args.kwargs['data']
        # priority 4 (highest) → form value 3 (0-based)
        assert data['port_queue'] == '3'
        assert data['sel_1'] == '1'
        assert data['sel_2'] == '1'
        assert data['apply'] == 'Apply'

    def test_set_port_priority_lowest(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_port_priority([1], priority=1)
        data = sw._session.post.call_args.kwargs['data']
        assert data['port_queue'] == '0'   # priority 1 → form value 0

    def test_set_port_priority_uses_post(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_port_priority([1], priority=2)
        url = sw._session.post.call_args.args[0]
        assert 'qos_port_priority_set.cgi' in url


# ---------------------------------------------------------------------------
# Bandwidth control
# ---------------------------------------------------------------------------

class TestBandwidthControl:
    def test_get_bandwidth(self, sw):
        sw._session.get.return_value = make_response(BANDWIDTH_HTML)
        entries = sw.get_bandwidth_control()
        assert len(entries) == 8
        assert entries[0].port == 1
        assert entries[0].ingress_rate == 1024
        assert entries[0].egress_rate == 512
        assert entries[1].ingress_rate == 0  # no limit

    def test_set_bandwidth(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_bandwidth_control([1, 2], ingress_kbps=1024, egress_kbps=512)
        data = sw._session.post.call_args.kwargs['data']
        assert data['igrRate'] == '1024'
        assert data['egrRate'] == '512'
        assert data['sel_1'] == '1'
        assert data['sel_2'] == '1'
        assert data['applay'] == 'Apply'   # note: firmware typo

    def test_set_bandwidth_uses_post(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_bandwidth_control([1], ingress_kbps=512, egress_kbps=0)
        url = sw._session.post.call_args.args[0]
        assert 'qos_bandwidth_set.cgi' in url

    def test_set_bandwidth_no_limit(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_bandwidth_control([3], ingress_kbps=0, egress_kbps=0)
        data = sw._session.post.call_args.kwargs['data']
        assert data['igrRate'] == '0'
        assert data['egrRate'] == '0'


# ---------------------------------------------------------------------------
# Storm control
# ---------------------------------------------------------------------------

class TestStormControl:
    def test_get_storm_control(self, sw):
        sw._session.get.return_value = make_response(STORM_HTML)
        entries = sw.get_storm_control()
        assert len(entries) == 8
        # Port 1: scInfo[0..2] = [5, 7, 1]
        p1 = entries[0]
        assert p1.port == 1
        assert p1.enabled is True
        assert p1.rate_index == 5
        assert p1.storm_types == 7   # all three types
        # Port 2: disabled
        p2 = entries[1]
        assert p2.enabled is False

    def test_set_storm_control_enabled(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_storm_control([1], rate_index=5,
                             storm_types=[StormType.BROADCAST, StormType.MULTICAST])
        data = sw._session.post.call_args.kwargs['data']
        assert ('state', '1') in data
        assert ('rate', '5') in data
        assert ('stormType', '4') in data   # broadcast
        assert ('stormType', '2') in data   # multicast
        assert ('sel_1', '1') in data
        assert ('applay', 'Apply') in data  # firmware typo

    def test_set_storm_control_disabled(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_storm_control([1, 2], enabled=False)
        data = sw._session.post.call_args.kwargs['data']
        assert ('state', '0') in data
        param_keys = [k for k, v in data]
        assert 'rate' not in param_keys

    def test_set_storm_all_types_by_default(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_storm_control([1], rate_index=3)
        data = sw._session.post.call_args.kwargs['data']
        storm_types = [v for k, v in data if k == 'stormType']
        assert set(storm_types) == {'1', '2', '4'}

    def test_set_storm_control_uses_post(self, sw):
        sw._session.post.return_value = make_response('')
        sw.set_storm_control([1], rate_index=3)
        url = sw._session.post.call_args.args[0]
        assert 'qos_storm_set.cgi' in url


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

class TestBackupRestore:
    def test_backup_returns_bytes(self, sw):
        binary_data = b'\x00\x01\x02\x03config_data'
        sw._session.get.return_value = make_response('', content=binary_data)
        result = sw.backup_config()
        assert result == binary_data

    def test_backup_calls_correct_cgi(self, sw):
        sw._session.get.return_value = make_response('', content=b'data')
        sw.backup_config()
        url = sw._session.get.call_args.args[0]
        assert 'config_back.cgi' in url

    def test_restore_uses_post(self, sw):
        sw._session.post.return_value = make_response('')
        sw.restore_config(b'config_data')
        assert sw._session.post.called
        url = sw._session.post.call_args.args[0]
        assert 'conf_restore.cgi' in url

    def test_restore_sends_multipart(self, sw):
        sw._session.post.return_value = make_response('')
        sw.restore_config(b'config_data')
        kwargs = sw._session.post.call_args.kwargs
        assert 'files' in kwargs
        assert 'configfile' in kwargs['files']


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------

class TestUrlConstruction:
    def test_url_helper(self):
        sw = Switch('10.1.1.239', password='test')
        assert sw._url('foo.htm') == 'http://10.1.1.239/foo.htm'
        assert sw._url('/foo.htm') == 'http://10.1.1.239/foo.htm'

    def test_url_with_leading_slash_trimmed(self):
        sw = Switch('192.168.1.1', password='test')
        assert sw._url('/SystemInfoRpm.htm') == 'http://192.168.1.1/SystemInfoRpm.htm'
