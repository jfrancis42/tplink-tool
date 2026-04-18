"""
Python SDK for TP-Link Easy Smart managed switches.

Supports multiple switch families detected automatically at connection time.
Use ``make_switch()`` for new code; instantiate ``Switch`` or ``SwitchDE``
directly only when the model is known in advance.

Model support
-------------
Verified:
  TL-SG108E  (E-series, cookie sessions)     → Switch
  TL-SG1016DE (DE-series, IP-based sessions) → SwitchDE

Unverified (assumed compatible, same protocol family):
  TL-SG105E, TL-SG108PE, TL-SG116E          → Switch
  TL-SG1008DE, TL-SG1024DE                  → SwitchDE

Protocol
--------
The switch has no REST API or CLI.  Configuration is done through a
frameset-based HTTP web UI:

  GET  /<PageName>.htm       → HTML with current state embedded as JavaScript
                               variable declarations.
  GET  /<name>.cgi?param=val → Configuration write (query-string params).
                               Exceptions: logon.cgi (POST) and
                               conf_restore.cgi (POST multipart).

Session mechanisms differ by family:
  E-series:  H_P_SSID cookie (Max-Age=600 s); re-authenticated transparently.
  DE-series: client-IP tracking, no cookie; same TTL and re-auth behaviour.

Usage
-----
    from tplink_tool import make_switch, PortSpeed

    with make_switch('10.1.0.32', password='secret') as sw:
        info = sw.get_system_info()
        print(info.hardware)      # e.g. 'TL-SG1016DE 2.0'

        for p in sw.get_port_settings():
            print(p)
"""

from __future__ import annotations

import ast
import json
import re
import time
import warnings
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple, Type

import requests


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PortSpeed(IntEnum):
    AUTO   = 1
    M10H   = 2  # 10 Mbps half-duplex
    M10F   = 3  # 10 Mbps full-duplex
    M100H  = 4  # 100 Mbps half-duplex
    M100F  = 5  # 100 Mbps full-duplex
    M1000F = 6  # 1000 Mbps full-duplex

    def __str__(self):
        labels = {1: 'Auto', 2: '10M-Half', 3: '10M-Full',
                  4: '100M-Half', 5: '100M-Full', 6: '1000M-Full'}
        return labels.get(self.value, 'Unknown')


class QoSMode(IntEnum):
    PORT_BASED  = 0   # "Port Based"        (form value 0)
    DOT1P       = 1   # "802.1P Based"       (form value 1)
    DSCP        = 2   # "DSCP/802.1P Based"  (form value 2)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SystemInfo:
    description: str
    mac: str
    ip: str
    netmask: str
    gateway: str
    firmware: str
    hardware: str

    def __str__(self):
        return (f"SystemInfo({self.description}, MAC={self.mac}, "
                f"IP={self.ip}/{self.netmask}, GW={self.gateway}, "
                f"FW={self.firmware}, HW={self.hardware})")


@dataclass
class IPSettings:
    dhcp: bool
    ip: str
    netmask: str
    gateway: str


@dataclass
class PortInfo:
    port: int                    # 1-based
    enabled: bool
    speed_cfg: Optional[PortSpeed]  # configured speed (None = not applicable)
    speed_act: Optional[PortSpeed]  # actual negotiated speed
    fc_cfg: bool                 # flow-control configured
    fc_act: bool                 # flow-control actual
    trunk_id: int                # 0 = no trunk, 1+ = LAG group

    def __str__(self):
        s = f"Port {self.port:2d}: {'UP  ' if self.enabled else 'DOWN'}"
        if self.speed_act:
            s += f"  actual={self.speed_act}"
        if self.speed_cfg:
            s += f"  cfg={self.speed_cfg}"
        if self.fc_cfg:
            s += "  FC=on"
        if self.trunk_id:
            s += f"  LAG{self.trunk_id}"
        return s


@dataclass
class PortStats:
    port: int
    tx_pkts: int
    rx_pkts: int


@dataclass
class MirrorConfig:
    enabled: bool
    dest_port: int               # 1-based; 0 = not set
    mode: int                    # 0=ingress+egress, 1=ingress, 2=egress
    ingress_ports: List[int]     # 1-based port numbers
    egress_ports: List[int]


@dataclass
class TrunkConfig:
    max_groups: int
    port_count: int
    groups: Dict[int, List[int]] = field(default_factory=dict)  # group_id → [ports]


@dataclass
class IGMPConfig:
    enabled: bool
    report_suppression: bool
    group_count: int


@dataclass
class LoopPreventionConfig:
    enabled: bool


@dataclass
class MTUVlanConfig:
    enabled: bool
    port_count: int
    uplink_port: int


@dataclass
class PortVlanEntry:
    vid: int
    members: int       # bitmask (bit 0 = port 1)


@dataclass
class Dot1QVlanEntry:
    vid: int
    name: str
    tagged_members: int     # bitmask
    untagged_members: int   # bitmask


@dataclass
class QoSPortConfig:
    port: int
    priority: int   # 1=lowest … 4=highest


@dataclass
class BandwidthEntry:
    port: int
    ingress_rate: int   # kbps; 0 = no limit
    egress_rate: int    # kbps; 0 = no limit


class StormType(IntEnum):
    UNKNOWN_UNICAST = 1
    MULTICAST       = 2
    BROADCAST       = 4

    @classmethod
    def all(cls) -> List['StormType']:
        return [cls.UNKNOWN_UNICAST, cls.MULTICAST, cls.BROADCAST]


# Storm control rate index → kbps mapping.
# The firmware's qos_storm_set.cgi accepts kbps directly (not an index).
# The index is used only as a user-facing convenience; internally it is
# always converted to kbps before submission.
STORM_RATE_KBPS: Dict[int, int] = {
    1: 64,     2: 128,    3: 256,    4: 512,
    5: 1024,   6: 2048,   7: 4096,   8: 8192,
    9: 16384, 10: 32768, 11: 65536, 12: 131072,
}
# Reverse mapping: kbps → rate index (for parsing scInfo on read)
_KBPS_TO_RATE_INDEX: Dict[int, int] = {v: k for k, v in STORM_RATE_KBPS.items()}


@dataclass
class StormEntry:
    port: int
    enabled: bool
    rate_index: int       # index into STORM_RATE_KBPS; 0 = disabled
    storm_types: int      # bitmask: 1=unknown-unicast, 2=multicast, 4=broadcast


@dataclass
class CableDiagResult:
    port: int
    status: str    # 'OK', 'Open', 'Short', 'Unknown'
    length_m: int  # -1 = not available


# ---------------------------------------------------------------------------
# JS data extraction helpers
# ---------------------------------------------------------------------------

def _extract_top_script(html: str) -> str:
    """Return the content of the first <script> block in the HTML."""
    m = re.search(r'<script[^>]*>\s*(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else ''


def _js_to_py(value_str: str) -> Any:
    """
    Convert a simple JavaScript value literal to a Python object.
    Handles: numbers, hex literals, strings, arrays, object literals.
    Not a general JS parser - only works for the patterns produced by this
    switch's firmware.
    """
    value_str = value_str.strip()

    # Hex literals: 0xFF → 255
    value_str = re.sub(
        r'\b0[xX]([0-9a-fA-F]+)\b',
        lambda m: str(int(m.group(1), 16)),
        value_str,
    )

    if value_str.startswith('{'):
        # Quote bare keys so json.loads can handle it.
        quoted = re.sub(r'(?<=[{,\n\s])([A-Za-z_]\w*)\s*:', r'"\1":', value_str)
        try:
            return json.loads(quoted)
        except json.JSONDecodeError:
            pass
        # Single-quoted strings (e.g. names:['Default','']) are valid Python
        # but not JSON.  Switch to ast.literal_eval on the key-quoted form.
        try:
            return ast.literal_eval(quoted)
        except Exception:
            return None

    if value_str.startswith('['):
        try:
            return json.loads(value_str)
        except json.JSONDecodeError:
            pass
        try:
            return ast.literal_eval(value_str)
        except Exception:
            return None

    # Quoted strings
    if (value_str.startswith('"') and value_str.endswith('"')) or \
       (value_str.startswith("'") and value_str.endswith("'")):
        return value_str[1:-1]

    # Integers / floats
    try:
        return int(value_str)
    except ValueError:
        pass
    try:
        return float(value_str)
    except ValueError:
        pass

    return value_str  # bare word / unparseable


def _extract_var(html: str, varname: str) -> Any:
    """
    Extract the value assigned to a JavaScript variable in *html*.
    Returns None if the variable is not found.
    Handles object literals {}, array literals [], and new Array(...) forms.
    """
    pattern = rf'\bvar\s+{re.escape(varname)}\s*='
    m = re.search(pattern, html)
    if not m:
        return None

    rest = html[m.end():].lstrip()
    if not rest:
        return None

    # new Array(...) → treat as [...]
    if re.match(r'new\s+Array\s*\(', rest, re.IGNORECASE):
        paren = rest.index('(')
        depth = 0
        for i, ch in enumerate(rest[paren:]):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    inner = rest[paren + 1: paren + i]
                    return _js_to_py('[' + inner + ']')
        return None

    first = rest[0]

    if first in ('{', '['):
        depth = 0
        for i, ch in enumerate(rest):
            if ch in ('{', '['):
                depth += 1
            elif ch in ('}', ']'):
                depth -= 1
                if depth == 0:
                    return _js_to_py(rest[:i + 1])
        return None

    # Simple scalar value up to the next ; or newline
    end = re.search(r'[;\n]', rest)
    raw = rest[:end.start()].strip() if end else rest.strip()
    return _js_to_py(raw)


def _bits_to_ports(mask: int, port_count: int = 16) -> List[int]:
    """Convert a port bitmask (bit 0 = port 1) to a list of 1-based port numbers."""
    return [i + 1 for i in range(port_count) if mask & (1 << i)]


def _ports_to_bits(ports: List[int]) -> int:
    """Convert a list of 1-based port numbers to a bitmask."""
    mask = 0
    for p in ports:
        mask |= (1 << (p - 1))
    return mask


# ---------------------------------------------------------------------------
# Main Switch class
# ---------------------------------------------------------------------------

class Switch:
    """
    Represents a TP-Link TL-SG108E managed switch.

    Use as a context manager for automatic login/logout::

        with Switch('10.1.1.239', password='secret') as sw:
            print(sw.get_system_info())

    Or manage the session manually::

        sw = Switch('10.1.1.239', password='secret')
        sw.login()
        ...
        sw.logout()
    """

    def __init__(
        self,
        host: str,
        username: str = 'admin',
        password: str = 'admin',
        timeout: float = 10.0,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session = requests.Session()
        self._logged_in = False
        self._login_time: float = 0.0
        # The switch sets Max-Age=600; re-auth before that
        self._session_ttl: float = 550.0
        self._port_count: int = 8  # updated from switch at login

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f'http://{self.host}/{path.lstrip("/")}'

    @staticmethod
    def _is_login_page(text: str) -> bool:
        """Return True if the response body is the login page (session expired/reset)."""
        return 'logon.cgi' in text and 'errType' in text

    def _get(self, path: str, **kwargs) -> requests.Response:
        """GET a page (for reading state), re-authenticating if the session was reset."""
        self._ensure_session()
        r = self._session.get(self._url(path), timeout=self.timeout, **kwargs)
        r.raise_for_status()
        if self._is_login_page(r.text):
            self._logged_in = False
            self.login()
            r = self._session.get(self._url(path), timeout=self.timeout, **kwargs)
            r.raise_for_status()
        return r

    def _cfg(self, path: str, params) -> requests.Response:
        """
        Send a configuration change via GET with query-string parameters.

        params may be a dict or a list of (key, value) tuples (use the list
        form when a key must appear multiple times, e.g. portid=1&portid=2).
        Re-authenticates automatically if the session was reset by a prior
        operation (e.g. VLAN mode change).

        Some CGIs (QoS, bandwidth) cause the switch to restart its internal
        web server immediately after processing the request, dropping the TCP
        connection before sending a response.  We treat ConnectionError as
        "write was applied; session needs re-authentication" rather than
        propagating it as a fatal exception.
        """
        self._ensure_session()
        try:
            r = self._session.get(self._url(path), params=params, timeout=self.timeout)
        except requests.exceptions.ConnectionError:
            # The switch dropped the connection after processing the write.
            # Mark the session as expired so the next call re-authenticates.
            self._logged_in = False
            return None
        r.raise_for_status()
        if self._is_login_page(r.text):
            self._logged_in = False
            self.login()
            r = self._session.get(self._url(path), params=params, timeout=self.timeout)
            r.raise_for_status()
        return r

    def _cfg_post(self, path: str, params) -> requests.Response:
        """
        Send a configuration change via POST with form-encoded body.

        Used for CGIs whose HTML forms have method=POST (QoS, bandwidth,
        storm control).  params may be a dict or a list of (key, value) tuples.
        Handles session expiry and TCP drops identically to _cfg.
        """
        self._ensure_session()
        try:
            r = self._session.post(self._url(path), data=params, timeout=self.timeout)
        except requests.exceptions.ConnectionError:
            self._logged_in = False
            return None
        r.raise_for_status()
        if self._is_login_page(r.text):
            self._logged_in = False
            self.login()
            r = self._session.post(self._url(path), data=params, timeout=self.timeout)
            r.raise_for_status()
        return r

    def _ensure_session(self):
        if not self._logged_in or (time.time() - self._login_time > self._session_ttl):
            self.login()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def login(self):
        """Authenticate with the switch."""
        r = self._session.post(
            self._url('logon.cgi'),
            data={'username': self.username, 'password': self.password, 'logon': 'Login'},
            timeout=self.timeout,
        )
        r.raise_for_status()

        # On success errType=0 is embedded in the page; on failure errType=1
        if 'errType' in r.text:
            m = re.search(r'errType\s*=\s*(\d+)', r.text)
            if m and int(m.group(1)) != 0:
                raise RuntimeError(
                    f'Login failed (errType={m.group(1)}). '
                    'Check username and password.'
                )

        # Confirm login succeeded.  Most firmware versions set H_P_SSID here;
        # some older versions omit the cookie but the session is still valid.
        if not any('H_P_SSID' in c.name for c in self._session.cookies):
            # Fall back: probe a benign page to verify the session is usable.
            probe = self._session.get(self._url('SystemInfoRpm.htm'), timeout=self.timeout)
            if self._is_login_page(probe.text):
                raise RuntimeError(
                    'Login did not return a session cookie and a subsequent '
                    'API probe confirmed the session is not active. '
                    'Check username and password.'
                )

        self._logged_in = True
        self._login_time = time.time()
        # Cache port count so add_dot1q_vlan works correctly on any port count
        try:
            r = self._session.get(self._url('PortSettingRpm.htm'), timeout=self.timeout)
            self._port_count = _extract_var(r.text, 'max_port_num') or 8
        except Exception:
            self._port_count = 8

    def logout(self):
        """Log out of the switch and clear the session."""
        if self._logged_in:
            try:
                self._session.get(self._url('Logout.htm'), timeout=self.timeout)
            except Exception:
                pass
        self._session.cookies.clear()
        self._logged_in = False

    @classmethod
    def _from_probe(
        cls,
        host: str,
        username: str,
        password: str,
        timeout: float,
        session: requests.Session,
        port_count: Optional[int] = None,
    ) -> 'Switch':
        """
        Construct from an already-authenticated probe session.

        Called by make_switch() to avoid a redundant second login.
        The probe session is reused as the live session; the object starts
        in the logged-in state with the TTL clock set to now.
        """
        obj = cls.__new__(cls)
        obj.host = host
        obj.username = username
        obj.password = password
        obj.timeout = timeout
        obj._session = session
        obj._logged_in = True
        obj._login_time = time.time()
        obj._session_ttl = 550.0
        obj._port_count = port_count or 8
        return obj

    def __enter__(self) -> 'Switch':
        self._ensure_session()
        return self

    def __exit__(self, *_):
        self.logout()

    # ------------------------------------------------------------------
    # Internal read helper
    # ------------------------------------------------------------------

    def _page(self, name: str) -> str:
        """Fetch <name>.htm and return its text."""
        return self._get(f'{name}.htm').text

    # ==================================================================
    # System
    # ==================================================================

    def get_system_info(self) -> SystemInfo:
        """Return device description, MAC, IP, firmware, hardware version."""
        html = self._page('SystemInfoRpm')
        ds = _extract_var(html, 'info_ds')
        if ds is None:
            raise RuntimeError('Could not parse SystemInfoRpm.htm')
        return SystemInfo(
            description=ds['descriStr'][0],
            mac=ds['macStr'][0],
            ip=ds['ipStr'][0],
            netmask=ds['netmaskStr'][0],
            gateway=ds['gatewayStr'][0],
            firmware=ds['firmwareStr'][0],
            hardware=ds['hardwareStr'][0],
        )

    def set_device_description(self, description: str):
        """Set the device description (up to 32 alphanumeric/hyphen/underscore chars)."""
        self._cfg('system_name_set.cgi', {'sysName': description})

    def get_ip_settings(self) -> IPSettings:
        """Return current IP configuration."""
        html = self._page('IpSettingRpm')
        ds = _extract_var(html, 'ip_ds')
        if ds is None:
            raise RuntimeError('Could not parse IpSettingRpm.htm')
        return IPSettings(
            dhcp=bool(ds.get('state', 0)),
            ip=ds['ipStr'][0],
            netmask=ds['netmaskStr'][0],
            gateway=ds['gatewayStr'][0],
        )

    def set_ip_settings(
        self,
        ip: Optional[str] = None,
        netmask: Optional[str] = None,
        gateway: Optional[str] = None,
        dhcp: Optional[bool] = None,
    ):
        """
        Change IP configuration.  Any parameter left as None is read from
        the current configuration and re-submitted unchanged.
        """
        current = self.get_ip_settings()
        use_dhcp = dhcp if dhcp is not None else current.dhcp
        self._cfg('ip_setting.cgi', {
            'dhcpSetting': 'enable' if use_dhcp else 'disable',
            'ip_address':  ip      or current.ip,
            'ip_netmask':  netmask or current.netmask,
            'ip_gateway':  gateway or current.gateway,
        })

    def get_led(self) -> bool:
        """Return True if the LEDs are enabled."""
        html = self._page('TurnOnLEDRpm')
        return bool(_extract_var(html, 'led'))

    def set_led(self, on: bool):
        """Enable or disable the port LEDs."""
        # Form field name is rd_led (radio button); led_cfg is the submit button name.
        self._cfg('led_on_set.cgi', {'rd_led': '1' if on else '0', 'led_cfg': 'Apply'})

    def change_password(self, old_password: str, new_password: str, username: Optional[str] = None):
        """Change the admin password."""
        self._cfg('usr_account_set.cgi', {
            'txt_username':   username or self.username,
            'txt_oldpwd':     old_password,
            'txt_userpwd':    new_password,
            'txt_confirmpwd': new_password,
        })

    def reboot(self):
        """Reboot the switch (will briefly lose connectivity)."""
        self._ensure_session()
        # Bypass _cfg: the firmware clears the session and redirects to the
        # login page as part of processing the reboot.  _cfg would detect that
        # redirect and attempt re-authentication against a switch that is
        # already offline.  Fire-and-forget is the right behaviour here.
        try:
            self._session.get(
                self._url('reboot.cgi'),
                params={'reboot_op': '1'},
                timeout=self.timeout,
            )
        except Exception:
            pass
        self._logged_in = False

    def factory_reset(self):
        """
        Reset to factory defaults.
        WARNING: This will reset the IP address to 192.168.0.1 and erase all config.
        """
        self._ensure_session()
        # Same fire-and-forget rationale as reboot().
        try:
            self._session.get(
                self._url('reset.cgi'),
                params={'reset_op': '1'},
                timeout=self.timeout,
            )
        except Exception:
            pass
        self._logged_in = False

    def backup_config(self) -> bytes:
        """Download the current configuration as raw bytes."""
        return self._get('config_back.cgi').content

    def restore_config(self, config_data: bytes):
        """
        Upload and restore a previously downloaded configuration.
        This is the only write operation that uses POST (multipart file upload).
        """
        self._ensure_session()
        self._session.post(
            self._url('conf_restore.cgi'),
            files={'configfile': ('config.bin', config_data, 'application/octet-stream')},
            timeout=self.timeout,
        ).raise_for_status()

    # ==================================================================
    # Port settings
    # ==================================================================

    def get_port_settings(self) -> List[PortInfo]:
        """Return per-port speed, flow-control, and trunk membership."""
        html = self._page('PortSettingRpm')
        n = _extract_var(html, 'max_port_num') or 8
        ai = _extract_var(html, 'all_info')
        if ai is None:
            raise RuntimeError('Could not parse PortSettingRpm.htm')

        ports = []
        for i in range(n):
            spd_cfg = ai['spd_cfg'][i] if i < len(ai['spd_cfg']) else 0
            spd_act = ai['spd_act'][i] if i < len(ai['spd_act']) else 0
            ports.append(PortInfo(
                port=i + 1,
                enabled=bool(ai['state'][i]),
                speed_cfg=PortSpeed(spd_cfg) if spd_cfg in PortSpeed._value2member_map_ else None,
                speed_act=PortSpeed(spd_act) if spd_act in PortSpeed._value2member_map_ else None,
                fc_cfg=bool(ai['fc_cfg'][i]),
                fc_act=bool(ai['fc_act'][i]),
                trunk_id=ai['trunk_info'][i] if i < len(ai['trunk_info']) else 0,
            ))
        return ports

    def set_port(
        self,
        port: int,
        *,
        enabled: Optional[bool] = None,
        speed: Optional[PortSpeed] = None,
        flow_control: Optional[bool] = None,
    ):
        """
        Configure a single port.  Parameters left as None are not changed
        (the switch uses value 7 as "no change" sentinel).
        """
        self.set_ports([port], enabled=enabled, speed=speed, flow_control=flow_control)

    def set_ports(
        self,
        ports: List[int],
        *,
        enabled: Optional[bool] = None,
        speed: Optional[PortSpeed] = None,
        flow_control: Optional[bool] = None,
    ):
        """
        Configure multiple ports at once.

        The switch applies the same settings to all selected ports.
        Unspecified parameters are left unchanged (sentinel value 7).
        """
        NO_CHANGE = '7'
        # Multiple portid values: pass as list of tuples
        params: list = [('portid', str(p)) for p in ports]
        params.append(('state',       str(int(enabled))      if enabled      is not None else NO_CHANGE))
        params.append(('speed',       str(int(speed))        if speed        is not None else NO_CHANGE))
        params.append(('flowcontrol', str(int(flow_control)) if flow_control is not None else NO_CHANGE))
        self._cfg('port_setting.cgi', params)

    # ==================================================================
    # Port statistics
    # ==================================================================

    def get_port_statistics(self) -> List[PortStats]:
        """Return TX and RX packet counts for each port."""
        html = self._page('PortStatisticsRpm')
        n = _extract_var(html, 'max_port_num') or 8
        ai = _extract_var(html, 'all_info')
        if ai is None:
            raise RuntimeError('Could not parse PortStatisticsRpm.htm')

        pkts = ai.get('pkts', [])
        # Firmware packs stats as pairs: [tx_p1, rx_p1, tx_p2, rx_p2, ...]
        # but the actual layout depends on firmware version - use stride of 2
        stats = []
        for i in range(n):
            tx = pkts[i * 2]     if len(pkts) > i * 2     else 0
            rx = pkts[i * 2 + 1] if len(pkts) > i * 2 + 1 else 0
            stats.append(PortStats(port=i + 1, tx_pkts=tx, rx_pkts=rx))
        return stats

    def reset_port_statistics(self, port: Optional[int] = None):
        """
        Reset packet counters.  Pass a port number to reset one port,
        or None (default) to reset all ports.
        """
        params: dict = {'op': '1'}
        if port is not None:
            params['portid'] = str(port)
        self._cfg('port_statistics_set.cgi', params)

    # ==================================================================
    # Port mirroring
    # ==================================================================

    def get_port_mirror(self) -> MirrorConfig:
        """Return the current port-mirroring configuration."""
        html = self._page('PortMirrorRpm')
        enabled  = bool(_extract_var(html, 'MirrEn')   or 0)
        dest     = (_extract_var(html, 'MirrPort') or 0)
        mode     = (_extract_var(html, 'MirrMode') or 0)
        mi       = _extract_var(html, 'mirr_info') or {'ingress': [], 'egress': []}
        n        = _extract_var(html, 'max_port_num') or 8

        ingress = [i + 1 for i, v in enumerate(mi.get('ingress', [])[:n]) if v]
        egress  = [i + 1 for i, v in enumerate(mi.get('egress',  [])[:n]) if v]
        return MirrorConfig(
            enabled=enabled,
            dest_port=dest,
            mode=mode,
            ingress_ports=ingress,
            egress_ports=egress,
        )

    def set_port_mirror(
        self,
        enabled: bool,
        dest_port: Optional[int] = None,
        ingress_ports: Optional[List[int]] = None,
        egress_ports: Optional[List[int]] = None,
    ):
        """
        Configure port mirroring.

        dest_port:     port number that receives the mirrored traffic (1-based)
        ingress_ports: ports whose ingress traffic is mirrored
        egress_ports:  ports whose egress traffic is mirrored

        Each mirrored port is submitted individually to mirrored_port_set.cgi
        with ingressState=1 and/or egressState=1 (0 = not monitored).
        """
        if enabled:
            self._cfg('mirror_enabled_set.cgi', {
                'state': '1', 'mirroringport': str(dest_port), 'mirrorenable': 'Apply',
            })
            ingress_set = set(ingress_ports or [])
            egress_set  = set(egress_ports  or [])
            for p in sorted(ingress_set | egress_set):
                self._cfg('mirrored_port_set.cgi', {
                    'mirroredport':    str(p),
                    'ingressState':    '1' if p in ingress_set else '0',
                    'egressState':     '1' if p in egress_set  else '0',
                    'mirrored_submit': 'Apply',
                })
        else:
            self._cfg('mirror_enabled_set.cgi', {'state': '0', 'mirrorenable': 'Apply'})

    # ==================================================================
    # Port trunking (LAG)
    # ==================================================================

    def get_port_trunk(self) -> TrunkConfig:
        """Return the current LAG (Link Aggregation Group) configuration."""
        html = self._page('PortTrunkRpm')
        tc = _extract_var(html, 'trunk_conf')
        if tc is None:
            raise RuntimeError('Could not parse PortTrunkRpm.htm')

        max_groups = tc.get('maxTrunkNum', 2)
        port_count = tc.get('portNum', 8)

        groups: Dict[int, List[int]] = {}
        for g in range(1, max_groups + 1):
            raw = tc.get(f'portStr_g{g}', [])
            members = [i + 1 for i, v in enumerate(raw[:port_count]) if v]
            if members:
                groups[g] = members

        return TrunkConfig(
            max_groups=max_groups,
            port_count=port_count,
            groups=groups,
        )

    def set_port_trunk(self, group_id: int, ports: List[int]):
        """
        Assign ports to a LAG group.  Pass an empty list to dissolve the group.

        group_id: 1 or 2 (TL-SG108E supports up to 2 LAG groups)
        ports: list of 1-based port numbers to assign
        """
        if ports:
            params: list = [('groupId', str(group_id)), ('setapply', 'Apply')]
            for p in ports:
                params.append(('portid', str(p)))
            self._cfg('port_trunk_set.cgi', params)
        else:
            self._cfg('port_trunk_display.cgi', {'chk_trunk': str(group_id), 'setDelete': 'Delete'})

    # ==================================================================
    # IGMP snooping
    # ==================================================================

    def get_igmp_snooping(self) -> IGMPConfig:
        """Return IGMP snooping configuration and current group table."""
        html = self._page('IgmpSnoopingRpm')
        ds = _extract_var(html, 'igmp_ds')
        if ds is None:
            raise RuntimeError('Could not parse IgmpSnoopingRpm.htm')
        return IGMPConfig(
            enabled=bool(ds.get('state', 0)),
            report_suppression=bool(ds.get('suppressionState', 0)),
            group_count=ds.get('count', 0),
        )

    def set_igmp_snooping(self, enabled: bool, report_suppression: bool = False):
        """Enable or disable IGMP snooping and report suppression."""
        self._cfg('igmpSnooping.cgi', {
            'igmp_mode':     '1' if enabled else '0',
            'reportSu_mode': '1' if report_suppression else '0',
        })

    # ==================================================================
    # Loop prevention
    # ==================================================================

    def get_loop_prevention(self) -> bool:
        """Return True if loop prevention is enabled."""
        html = self._page('LoopPreventionRpm')
        return bool(_extract_var(html, 'lpEn'))

    def set_loop_prevention(self, enabled: bool):
        """Enable or disable loop prevention."""
        self._cfg('loop_prevention_set.cgi', {'lpEn': '1' if enabled else '0'})

    # ==================================================================
    # VLAN
    # ==================================================================

    def get_mtu_vlan(self) -> MTUVlanConfig:
        """Return MTU VLAN (uplink-based VLAN isolation) configuration."""
        html = self._page('VlanMtuRpm')
        ds = _extract_var(html, 'mtu_ds')
        if ds is None:
            raise RuntimeError('Could not parse VlanMtuRpm.htm')
        return MTUVlanConfig(
            enabled=bool(ds.get('state', 0)),
            port_count=ds.get('portNum', 8),
            uplink_port=ds.get('uplinkPort', 1),
        )

    def set_mtu_vlan(self, enabled: bool, uplink_port: Optional[int] = None):
        """
        Configure MTU VLAN mode.

        In MTU VLAN mode all ports can talk to the uplink port but not to
        each other - useful for simple port isolation.
        """
        current = self.get_mtu_vlan()
        self._cfg('mtuVlanSet.cgi', {
            'mtu_en':     '1' if enabled else '0',
            'uplinkPort': str(uplink_port or current.uplink_port),
        })

    def get_port_vlan(self) -> Tuple[bool, List[PortVlanEntry]]:
        """
        Return port-based VLAN configuration.

        Returns (enabled, [PortVlanEntry, ...]).
        The members field is a bitmask; bit 0 = port 1.
        """
        html = self._page('VlanPortBasicRpm')
        ds = _extract_var(html, 'pvlan_ds')
        if ds is None:
            raise RuntimeError('Could not parse VlanPortBasicRpm.htm')

        enabled = bool(ds.get('state', 0))
        vids    = ds.get('vids', [])
        mbrs    = ds.get('mbrs', [])
        entries = [PortVlanEntry(vid=vids[i], members=mbrs[i])
                   for i in range(ds.get('count', 0))]
        return enabled, entries

    def set_port_vlan_enabled(self, enabled: bool):
        """
        Enable or disable port-based VLAN mode.

        The switch restarts its web server after this change; this method
        waits for it to recover before returning.
        """
        self._cfg('pvlanSet.cgi', {'pvlan_en': '1' if enabled else '0', 'pvlan_mode': 'Apply'})

    def add_port_vlan(self, vid: int, member_ports: List[int]):
        """
        Add or update a port-based VLAN entry.

        vid:          VLAN ID
        member_ports: list of 1-based port numbers that belong to this VLAN
        """
        params: list = [('vid', str(vid)), ('pvlan_add', 'Apply')]
        for p in member_ports:
            params.append(('selPorts', str(p)))
        self._cfg('pvlanSet.cgi', params)

    def delete_port_vlan(self, vid: int):
        """Remove a port-based VLAN entry by its VID."""
        self._cfg('pvlanSet.cgi', {'selVlans': str(vid), 'pvlan_del': 'Delete'})

    def get_dot1q_vlans(self) -> Tuple[bool, List[Dot1QVlanEntry]]:
        """
        Return 802.1Q VLAN configuration.

        Returns (enabled, [Dot1QVlanEntry, ...]).
        tagged_members and untagged_members are bitmasks (bit 0 = port 1).
        """
        html = self._page('Vlan8021QRpm')
        ds = _extract_var(html, 'qvlan_ds')
        if ds is None:
            raise RuntimeError('Could not parse Vlan8021QRpm.htm')

        enabled  = bool(ds.get('state', 0))
        vids     = ds.get('vids', [])
        names    = ds.get('names', [])
        tagMbrs  = ds.get('tagMbrs', [])
        untagMbrs = ds.get('untagMbrs', [])
        entries = [
            Dot1QVlanEntry(
                vid=vids[i],
                name=names[i] if i < len(names) else '',
                tagged_members=tagMbrs[i] if i < len(tagMbrs) else 0,
                untagged_members=untagMbrs[i] if i < len(untagMbrs) else 0,
            )
            for i in range(ds.get('count', 0))
        ]
        return enabled, entries

    def set_dot1q_enabled(self, enabled: bool):
        """
        Enable or disable 802.1Q VLAN mode.

        The switch restarts its web server after this change; this method
        waits for it to recover before returning.
        """
        self._cfg('qvlanSet.cgi', {'qvlan_en': '1' if enabled else '0', 'qvlan_mode': 'Apply'})

    def add_dot1q_vlan(
        self,
        vid: int,
        name: str = '',
        tagged_ports: Optional[List[int]] = None,
        untagged_ports: Optional[List[int]] = None,
    ):
        """
        Add or update an 802.1Q VLAN.

        Ports not listed in either tagged_ports or untagged_ports are set to
        "not member".  802.1Q mode must be enabled on the switch first.
        """
        # vid=100&vname=&selType_1=1&selType_2=0&...&selType_8=2&qvlan_add=Add%2FModify
        # selType values: 0=untagged, 1=tagged, 2=not-member
        tagged_set   = set(tagged_ports   or [])
        untagged_set = set(untagged_ports or [])
        params: dict = {'vid': str(vid), 'vname': name, 'qvlan_add': 'Add/Modify'}
        for i in range(1, self._port_count + 1):
            if i in tagged_set:
                params[f'selType_{i}'] = '1'
            elif i in untagged_set:
                params[f'selType_{i}'] = '0'
            else:
                params[f'selType_{i}'] = '2'
        self._cfg('qvlanSet.cgi', params)

    def delete_dot1q_vlan(self, vid: int):
        """Remove an 802.1Q VLAN."""
        self._cfg('qvlanSet.cgi', {'selVlans': str(vid), 'qvlan_del': 'Delete'})

    def get_pvids(self) -> List[int]:
        """Return the 802.1Q port VLAN ID (PVID) for each port (1-based list)."""
        html = self._page('Vlan8021QPvidRpm')
        ds = _extract_var(html, 'pvid_ds')
        if ds is None:
            raise RuntimeError('Could not parse Vlan8021QPvidRpm.htm')
        return ds.get('pvids', [])

    def set_pvid(self, ports: List[int], pvid: int):
        """
        Set the PVID for one or more ports (used to classify untagged ingress frames).

        ports: list of 1-based port numbers
        pvid:  VLAN ID to assign as the port VLAN ID

        Note: pbm is a port bitmask (bit 0 = port 1).  This is inferred from
        the field name; only single-port cases have been verified.
        """
        self._cfg('vlanPvidSet.cgi', {'pbm': str(_ports_to_bits(ports)), 'pvid': str(pvid)})

    # ==================================================================
    # QoS
    # ==================================================================

    def get_qos_settings(self) -> Tuple[QoSMode, List[QoSPortConfig]]:
        """
        Return the QoS mode and per-port priority settings.

        Returns (mode, [QoSPortConfig, ...]).
        mode: QoSMode.PORT_BASED, QoSMode.DOT1P, or QoSMode.DSCP
        """
        html = self._page('QosBasicRpm')
        raw_mode = _extract_var(html, 'qosMode')
        mode = QoSMode(raw_mode if raw_mode is not None else 2)
        n    = _extract_var(html, 'portNumber') or 8
        pPri = _extract_var(html, 'pPri') or []
        ports = [QoSPortConfig(port=i + 1, priority=pPri[i] if i < len(pPri) else 0)
                 for i in range(n)]
        return mode, ports

    def set_qos_mode(self, mode: QoSMode):
        """Set QoS scheduling mode (port-based, 802.1p, or DSCP).

        The form uses method=POST and the radio-button field name is
        rd_qosmode (not qos_mode).  Values: 0=PORT_BASED, 1=DOT1P, 2=DSCP.
        """
        self._cfg_post('qos_mode_set.cgi', {'rd_qosmode': str(int(mode)), 'qosmode': 'Apply'})

    def set_port_priority(self, ports: List[int], priority: int):
        """
        Set QoS priority for one or more ports (port-based QoS mode).

        ports:    list of 1-based port numbers
        priority: 1=lowest, 2=normal, 3=medium, 4=highest

        The select element uses 0-based option values (0–3), so priority is
        converted to 0-based before submission.  The form uses POST.
        """
        params: dict = {'port_queue': str(priority - 1), 'apply': 'Apply'}
        for p in ports:
            params[f'sel_{p}'] = '1'
        self._cfg_post('qos_port_priority_set.cgi', params)

    def get_bandwidth_control(self) -> List[BandwidthEntry]:
        """
        Return ingress/egress bandwidth limits for each port.

        A rate of 0 means no limit.
        Rates are in kbps (the switch web UI shows values like 512, 1024, etc).
        """
        html = self._page('QosBandWidthControlRpm')
        n     = _extract_var(html, 'portNumber') or 8
        bcInfo = _extract_var(html, 'bcInfo') or []
        # Layout: [ingress_p1, egress_p1, unused, ingress_p2, egress_p2, unused, ...]
        entries = []
        for i in range(n):
            base = i * 3
            ingress = bcInfo[base]     if len(bcInfo) > base     else 0
            egress  = bcInfo[base + 1] if len(bcInfo) > base + 1 else 0
            entries.append(BandwidthEntry(port=i + 1, ingress_rate=ingress, egress_rate=egress))
        return entries

    def set_bandwidth_control(self, ports: List[int], ingress_kbps: int = 0, egress_kbps: int = 0):
        """
        Set ingress/egress bandwidth limits for one or more ports.  0 = no limit.
        Valid non-zero values (kbps): 512, 1024, 2048, 4096, 8192, 16384,
        32768, 65536, 131072, 262144, 524288, 1000000 (1 Gbps).
        """
        # igrRate=1024&egrRate=512&sel_1=1&applay=Apply  (note: "applay" typo in firmware)
        params: dict = {'igrRate': str(ingress_kbps), 'egrRate': str(egress_kbps), 'applay': 'Apply'}
        for p in ports:
            params[f'sel_{p}'] = '1'
        self._cfg_post('qos_bandwidth_set.cgi', params)

    def get_storm_control(self) -> List[StormEntry]:
        """Return storm control configuration per port."""
        html = self._page('QosStormControlRpm')
        n      = _extract_var(html, 'portNumber') or 8
        scInfo = _extract_var(html, 'scInfo') or []
        # scInfo layout per port (stride=3): [rate_kbps, storm_type_mask, <unused>]
        # rate_kbps == 0 means disabled; the firmware stores kbps directly.
        entries = []
        for i in range(n):
            base  = i * 3
            kbps  = scInfo[base]     if len(scInfo) > base     else 0
            types = scInfo[base + 1] if len(scInfo) > base + 1 else 0
            enabled = kbps > 0
            rate_index = _KBPS_TO_RATE_INDEX.get(kbps, 0) if enabled else 0
            entries.append(StormEntry(
                port=i + 1,
                enabled=enabled,
                rate_index=rate_index,
                storm_types=types,
            ))
        return entries

    def set_storm_control(
        self,
        ports: List[int],
        rate_index: int = 1,
        storm_types: Optional[List[StormType]] = None,
        enabled: bool = True,
    ):
        """
        Configure storm control for one or more ports.

        rate_index:  1–12 (see STORM_RATE_KBPS for kbps values); ignored when enabled=False
        storm_types: list of StormType flags to limit; defaults to all three
        enabled:     False to disable storm control on the selected ports

        Example — limit broadcast + multicast at 1024 kbps on ports 1 and 2:
            sw.set_storm_control([1, 2], rate_index=5,
                                 storm_types=[StormType.BROADCAST, StormType.MULTICAST])
        """
        # state=1&rate=<kbps>&stormType=1&stormType=2&stormType=4&sel_1=1&applay=Apply
        # The form uses method=POST.  The rate parameter is kbps, not an index.
        if storm_types is None:
            storm_types = StormType.all()
        params: list = [('state', '1' if enabled else '0'), ('applay', 'Apply')]
        if enabled:
            kbps = STORM_RATE_KBPS.get(rate_index, 64)
            params.append(('rate', str(kbps)))
            for t in storm_types:
                params.append(('stormType', str(int(t))))
        for p in ports:
            params.append((f'sel_{p}', '1'))
        self._cfg_post('qos_storm_set.cgi', params)

    # ==================================================================
    # Cable diagnostics
    # ==================================================================

    def run_cable_diagnostic(self, ports: Optional[List[int]] = None) -> List[CableDiagResult]:
        """
        Run TDR-based cable diagnostics on the specified ports.

        If ports is None all ports are tested.  Note: the switch firmware
        tests one port at a time and may take several seconds.
        """
        html = self._page('CableDiagRpm')
        max_port = _extract_var(html, 'maxPort') or 8
        if ports is None:
            ports = list(range(1, max_port + 1))

        results = []
        for p in ports:
            r = self._cfg_post('cable_diag_get.cgi', {'portid': str(p)})
            # Response embeds updated cablestate/cablelength JS vars
            state  = _extract_var(r.text, 'cablestate')  or [] if r else []
            length = _extract_var(r.text, 'cablelength') or [] if r else []

            idx = p - 1
            raw_state  = state[idx]  if isinstance(state,  list) and idx < len(state)  else -1
            raw_length = length[idx] if isinstance(length, list) and idx < len(length) else -1

            status_map = {0: 'NoCable', 1: 'Normal', 2: 'Open', 3: 'Short',
                          4: 'OpenShort', 5: 'CrossCable', -1: 'NotTested'}
            results.append(CableDiagResult(
                port=p,
                status=status_map.get(raw_state, 'Unknown'),
                length_m=raw_length,
            ))
        return results

__version__ = '0.1.0'


# ===========================================================================
# TL-SG1016DE "Easy Smart" series
# ===========================================================================

class SwitchDE(Switch):
    """
    TP-Link TL-SG1016DE (and similar "Easy Smart DE" series) managed switch.

    Key differences from TL-SG108E (which Switch targets):

    - IP-based session: the switch authenticates by client IP; no cookie is
      issued.  Login response contains 'logonInfo' array, not 'errType'.
    - SystemInfoRpm.htm embeds data in HTML <span> elements, not JS vars.
    - IpSettingRpm.htm embeds current values in <input value=""> attrs.
    - PortSettingRpm.htm: flat configInfo[port*3] = (state, speed, fc);
      contains // comments that must be stripped before parsing.
    - 802.1Q VLAN CGIs renamed: vlan_8021q_based_enable.cgi /
      vlan_8021q_based_set.cgi; per-port params use tag_N (not selType_N).
    - Vlan8021QRpm.htm uses flat JS variables rather than a qvlan_ds dict.
    - PVIDs array is 1-indexed (index 0 is an unused padding zero).
    - PVID set CGI uses per-port checkboxes (portSelect_N), not a bitmask.
    - Loop prevention write CGI uses 'loopfunction' param, not 'lpEn'.
    - Bandwidth/storm data embedded as a comma string 'tmp_info' (same
      stride-3 layout as SG108E, but split() instead of a JS array).
    - MTU VLAN and port-based VLAN are not supported.
    - TurnOnLEDRpm.htm does not exist; LED methods are no-ops.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_js_comments(text: str) -> str:
        """Remove // single-line JS comments (e.g. //// separators in arrays)."""
        return re.sub(r'//[^\n\r]*', '', text)

    @staticmethod
    def _is_login_page(text: str) -> bool:
        return 'logon.cgi' in text and 'logonInfo' in text

    @staticmethod
    def _parse_tmp_info(html: str) -> List[int]:
        """Extract and split the 'tmp_info' comma string used for bandwidth/storm."""
        raw = _extract_var(html, 'tmp_info') or ''
        try:
            return [int(x) for x in raw.split(',') if x.strip()]
        except ValueError:
            return []

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def login(self):
        r = self._session.post(
            self._url('logon.cgi'),
            data={'username': self.username, 'password': self.password, 'logon': 'Login'},
            timeout=self.timeout,
        )
        r.raise_for_status()

        # DE login response: logonInfo = new Array( error_code, ... )
        # error_code 0 = success
        m = re.search(r'logonInfo\s*=\s*new\s+Array\s*\(\s*(\d+)', r.text)
        if m:
            if int(m.group(1)) != 0:
                raise RuntimeError(
                    f'Login failed (logonInfo[0]={m.group(1)}). '
                    'Check username and password.'
                )
        else:
            # Unexpected page — probe to confirm session is usable
            probe = self._session.get(self._url('SystemInfoRpm.htm'), timeout=self.timeout)
            if self._is_login_page(probe.text):
                raise RuntimeError(
                    'Login probe failed. Check username and password.'
                )

        self._logged_in = True
        self._login_time = time.time()
        self._port_count = 16  # DE is a fixed 16-port device

    @classmethod
    def _from_probe(
        cls,
        host: str,
        username: str,
        password: str,
        timeout: float,
        session: requests.Session,
        port_count: Optional[int] = None,
    ) -> 'SwitchDE':
        obj = super()._from_probe(
            host=host, username=username, password=password, timeout=timeout,
            session=session, port_count=port_count or 16,
        )
        return obj

    # ------------------------------------------------------------------
    # System info
    # ------------------------------------------------------------------

    def get_system_info(self) -> SystemInfo:
        html = self._page('SystemInfoRpm')

        def _span(id_: str) -> str:
            m = re.search(rf'id="{re.escape(id_)}"[^>]*>([^<]+)<', html)
            return m.group(1).strip() if m else ''

        return SystemInfo(
            description=_span('sp_devicetype'),
            mac=_span('sp_macaddress'),
            ip=_span('sp_ipaddress'),
            netmask=_span('sp_netmask'),
            gateway=_span('sp_gateway'),
            firmware=_span('sp_firewareversion'),   # deliberate typo in DE HTML
            hardware=_span('sp_hardwareversion'),
        )

    def get_ip_settings(self) -> IPSettings:
        html = self._page('IpSettingRpm')

        def _inp(id_: str) -> str:
            m = re.search(rf'id="{re.escape(id_)}"[^>]*value="([^"]*)"', html)
            return m.group(1).strip() if m else ''

        dhcp_m = re.search(r'id="check_dhcp"[^>]*value="([^"]*)"', html)
        dhcp = (dhcp_m.group(1) == 'enable') if dhcp_m else False
        return IPSettings(
            dhcp=dhcp,
            ip=_inp('txt_addr'),
            netmask=_inp('txt_mask'),
            gateway=_inp('txt_gateway'),
        )

    # ------------------------------------------------------------------
    # LED — not present on DE
    # ------------------------------------------------------------------

    def get_led(self) -> bool:
        return True     # DE LEDs are always on (hardware-controlled, not software)

    def set_led(self, on: bool):
        pass            # DE has no software LED control

    # ------------------------------------------------------------------
    # Port settings
    # ------------------------------------------------------------------

    def get_port_settings(self) -> List[PortInfo]:
        # Strip // comments before parsing; configInfo has //// separators
        html = self._strip_js_comments(self._page('PortSettingRpm'))
        n  = _extract_var(html, 'max_port_num') or self._port_count
        ci = _extract_var(html, 'configInfo')
        if ci is None:
            raise RuntimeError('Could not parse PortSettingRpm.htm')
        ports = []
        for i in range(n):
            base  = i * 3
            state = ci[base]     if len(ci) > base     else 1
            speed = ci[base + 1] if len(ci) > base + 1 else 1
            fc    = ci[base + 2] if len(ci) > base + 2 else 0
            ports.append(PortInfo(
                port=i + 1,
                enabled=bool(state),
                speed_cfg=PortSpeed(speed) if speed in PortSpeed._value2member_map_ else None,
                speed_act=None,   # not available in configInfo
                fc_cfg=bool(fc),
                fc_act=bool(fc),
                trunk_id=0,       # would require a separate page fetch
            ))
        return ports

    # ------------------------------------------------------------------
    # Port mirror
    # ------------------------------------------------------------------

    def get_port_mirror(self) -> MirrorConfig:
        html = self._strip_js_comments(self._page('PortMirrorRpm'))
        dest = _extract_var(html, 'config_port') or 0
        if not dest:
            return MirrorConfig(enabled=False, dest_port=0, mode=0,
                                ingress_ports=[], egress_ports=[])
        ci = _extract_var(html, 'configInfo') or []
        n  = self._port_count
        ingress = [i + 1 for i in range(n) if len(ci) > i * 2     and ci[i * 2]]
        egress  = [i + 1 for i in range(n) if len(ci) > i * 2 + 1 and ci[i * 2 + 1]]
        return MirrorConfig(enabled=True, dest_port=dest, mode=0,
                            ingress_ports=ingress, egress_ports=egress)

    # ------------------------------------------------------------------
    # Port trunk (LAG)
    # ------------------------------------------------------------------

    def get_port_trunk(self) -> TrunkConfig:
        html = self._strip_js_comments(self._page('PortTrunkRpm'))
        trunk_mem = _extract_var(html, 'trunkMem') or []
        # trunkMem: MAX_GROUPS groups × SLOTS_PER_GRP port-number slots
        # value = 1-based port number; 0 = empty slot
        MAX_GROUPS    = 8
        SLOTS_PER_GRP = 4
        groups: Dict[int, List[int]] = {}
        for g in range(MAX_GROUPS):
            members = [
                trunk_mem[g * SLOTS_PER_GRP + s]
                for s in range(SLOTS_PER_GRP)
                if (g * SLOTS_PER_GRP + s) < len(trunk_mem) and trunk_mem[g * SLOTS_PER_GRP + s]
            ]
            if members:
                groups[g + 1] = members
        return TrunkConfig(max_groups=MAX_GROUPS, port_count=self._port_count, groups=groups)

    # ------------------------------------------------------------------
    # IGMP snooping
    # ------------------------------------------------------------------

    def get_igmp_snooping(self) -> IGMPConfig:
        html = self._page('IgmpSnoopingRpm')
        # State embedded as literal integer in init functions, e.g.:
        #   function igmpSnoopingEnableInit() { ... if ( 1 ) { ...enable... }
        m_igmp = re.search(
            r'igmpSnoopingEnableInit\s*\(\s*\)[^{]*\{[^i]*if\s*\(\s*(\d+)\s*\)', html)
        m_supp = re.search(
            r'igmpReportMsgSuppressionEnableInit\s*\(\s*\)[^{]*\{[^i]*if\s*\(\s*(\d+)\s*\)', html)
        return IGMPConfig(
            enabled=bool(int(m_igmp.group(1))) if m_igmp else False,
            report_suppression=bool(int(m_supp.group(1))) if m_supp else False,
            group_count=0,
        )

    # ------------------------------------------------------------------
    # Loop prevention
    # ------------------------------------------------------------------

    def get_loop_prevention(self) -> bool:
        html = self._page('LoopPreventionRpm')
        m = re.search(r'loopfunction\.value\s*=\s*(\d+)', html)
        return bool(int(m.group(1))) if m else False

    def set_loop_prevention(self, enabled: bool):
        # DE uses 'loopfunction' param; SG108E uses 'lpEn'
        self._cfg_post('loop_prevention_set.cgi',
                       {'loopfunction': '1' if enabled else '0', 'apply': 'Apply'})

    # ------------------------------------------------------------------
    # VLAN — MTU and port-based are not supported on DE
    # ------------------------------------------------------------------

    def get_mtu_vlan(self) -> MTUVlanConfig:
        return MTUVlanConfig(enabled=False, port_count=self._port_count, uplink_port=1)

    def set_mtu_vlan(self, enabled: bool, uplink_port: Optional[int] = None):
        pass

    def get_port_vlan(self) -> Tuple[bool, List[PortVlanEntry]]:
        return False, []

    def set_port_vlan_enabled(self, enabled: bool):
        pass

    # ------------------------------------------------------------------
    # 802.1Q VLAN
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_port_str(s: str) -> int:
        """
        Convert a DE port-list string like '2,14-16' or '1-16' to a bitmask.
        '--' or empty means no ports (returns 0).
        """
        if not s or s.strip() == '--':
            return 0
        mask = 0
        for part in s.split(','):
            part = part.strip()
            if '-' in part:
                lo, hi = part.split('-', 1)
                for p in range(int(lo), int(hi) + 1):
                    mask |= 1 << (p - 1)
            elif part.isdigit():
                mask |= 1 << (int(part) - 1)
        return mask

    def get_dot1q_vlans(self) -> Tuple[bool, List[Dot1QVlanEntry]]:
        html      = self._page('Vlan8021QRpm')
        enabled   = bool(_extract_var(html, 'qEnable') or 0)
        vids      = _extract_var(html, 'qVIDs')          or []
        names     = _extract_var(html, 'qVNames')         or []
        tag_map   = _extract_var(html, 'qVTagMems_map')   or []
        untag_map = _extract_var(html, 'qVUnTagMems_map') or []
        tag_str   = _extract_var(html, 'qVTagMems_str')   or []
        untag_str = _extract_var(html, 'qVUnTagMems_str') or []

        def _members(map_val: int, str_val: str) -> int:
            # DE firmware stores VLAN 1 map as 0 even when all ports are members.
            # Fall back to parsing the human-readable string when map is 0.
            if map_val:
                return map_val
            return self._parse_port_str(str_val)

        def _norm_name(s: str) -> str:
            return '' if s == '--' else s

        entries = [
            Dot1QVlanEntry(
                vid=vids[i],
                name=_norm_name(names[i]) if i < len(names) else '',
                tagged_members=_members(
                    tag_map[i]   if i < len(tag_map)   else 0,
                    tag_str[i]   if i < len(tag_str)   else '--',
                ),
                untagged_members=_members(
                    untag_map[i] if i < len(untag_map) else 0,
                    untag_str[i] if i < len(untag_str) else '--',
                ),
            )
            for i in range(len(vids))
        ]
        return enabled, entries

    def set_dot1q_enabled(self, enabled: bool):
        self._cfg('vlan_8021q_based_enable.cgi', {'qvlan_en': '1' if enabled else '0'})

    def add_dot1q_vlan(
        self,
        vid: int,
        name: str = '',
        tagged_ports:   Optional[List[int]] = None,
        untagged_ports: Optional[List[int]] = None,
    ):
        tagged_set   = set(tagged_ports   or [])
        untagged_set = set(untagged_ports or [])
        # tag_N: 0=untagged, 1=tagged, 2=not-member  (cf. SG108E's selType_N)
        params: dict = {'qvlanid': str(vid), 'qvlanname': name, 'addModify': 'Add/Modify'}
        for i in range(1, self._port_count + 1):
            if i in tagged_set:
                params[f'tag_{i}'] = '1'
            elif i in untagged_set:
                params[f'tag_{i}'] = '0'
            else:
                params[f'tag_{i}'] = '2'
        self._cfg('vlan_8021q_based_set.cgi', params)

    def delete_dot1q_vlan(self, vid: int):
        raise NotImplementedError(
            'delete_dot1q_vlan is not supported on TL-SG1016DE'
        )

    # ------------------------------------------------------------------
    # PVID
    # ------------------------------------------------------------------

    def get_pvids(self) -> List[int]:
        html = self._page('Vlan8021QPvidRpm')
        raw = _extract_var(html, 'PVIDs') or []
        # raw[0] is a padding zero; raw[1..N] = PVID for ports 1..N
        # Return 0-indexed list (index 0 = port 1) to match SG108E convention
        return list(raw[1:]) if raw else []

    def set_pvid(self, ports: List[int], pvid: int):
        # DE uses individual portSelect_N checkboxes, not a bitmask
        params: list = [('Pvid', str(pvid)), ('apply', 'Apply')]
        for p in ports:
            params.append((f'portSelect_{p}', str(p)))
        self._cfg('vlan_port_pvid_set.cgi', params)

    # ------------------------------------------------------------------
    # QoS
    # ------------------------------------------------------------------

    def get_qos_settings(self) -> Tuple[QoSMode, List[QoSPortConfig]]:
        html = self._page('QosBasicRpm')
        raw_mode = _extract_var(html, 'qosMode')
        mode = QoSMode(raw_mode if raw_mode is not None else 0)
        n = _extract_var(html, 'portNumber') or self._port_count
        # Per-port priority not accessible without external JS; return zeros
        return mode, [QoSPortConfig(port=i + 1, priority=0) for i in range(n)]

    # ------------------------------------------------------------------
    # Bandwidth control
    # ------------------------------------------------------------------

    def get_bandwidth_control(self) -> List[BandwidthEntry]:
        html = self._page('QosBandWidthControlRpm')
        n    = _extract_var(html, 'portNumber') or self._port_count
        parts = self._parse_tmp_info(html)
        entries = []
        for i in range(n):
            base    = i * 3
            ingress = parts[base]     if len(parts) > base     else 0
            egress  = parts[base + 1] if len(parts) > base + 1 else 0
            entries.append(BandwidthEntry(port=i + 1, ingress_rate=ingress, egress_rate=egress))
        return entries

    # ------------------------------------------------------------------
    # Storm control
    # ------------------------------------------------------------------

    def get_storm_control(self) -> List[StormEntry]:
        html  = self._page('QosStormControlRpm')
        n     = _extract_var(html, 'portNumber') or self._port_count
        parts = self._parse_tmp_info(html)
        entries = []
        for i in range(n):
            base       = i * 3
            kbps       = parts[base]     if len(parts) > base     else 0
            types      = parts[base + 1] if len(parts) > base + 1 else 0
            enabled    = kbps > 0
            rate_index = _KBPS_TO_RATE_INDEX.get(kbps, 0) if enabled else 0
            entries.append(StormEntry(
                port=i + 1, enabled=enabled, rate_index=rate_index, storm_types=types))
        return entries


# ===========================================================================
# Model registry and auto-detection factory
# ===========================================================================

# ---------------------------------------------------------------------------
# Session-type detection
# ---------------------------------------------------------------------------

def _detect_session_type(r: requests.Response) -> str:
    """
    Determine session mechanism from a logon.cgi response.

    Returns 'cookie'   — switch uses H_P_SSID cookie sessions (SG108E family).
    Returns 'ip_based' — switch uses client-IP sessions, no cookie (DE family).
    """
    return 'cookie' if 'H_P_SSID' in r.cookies else 'ip_based'


def _check_login_success(r: requests.Response) -> bool:
    """
    Return True if the logon.cgi response indicates a successful login.

    Both known families embed ``logonInfo = new Array(errCode, ...)``.
    errCode 0 = success; non-zero = failure (wrong password, session limit, etc.).
    Falls back to True if the page format is unrecognised but the HTTP status
    is 200, so that an unknown future model still gets a chance to work.
    """
    m = re.search(r'logonInfo\s*=\s*new\s+Array\s*\(\s*(\d+)', r.text)
    if m:
        return int(m.group(1)) == 0
    # Unrecognised page but got a session cookie — probably OK
    if 'H_P_SSID' in r.cookies:
        return True
    return False


# ---------------------------------------------------------------------------
# SystemInfoRpm.htm parser — model-independent
# ---------------------------------------------------------------------------

def _parse_sysinfo_html(html: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract (hardware_version, firmware_version) from SystemInfoRpm.htm.

    Tries every known page format in order; returns (None, None) if the
    page cannot be parsed.  New formats should be added here as new models
    are reverse-engineered.

    Known formats
    -------------
    SG108E family  — JS object: ``var info_ds = {hardwareStr:['TL-SG108E 6.0'], ...}``
    DE family      — HTML spans: ``<span id="sp_hardwareversion">TL-SG1016DE 2.0</span>``
    """
    # --- SG108E-style: JS object with array-wrapped values ---
    # e.g. var info_ds = { hardwareStr:['TL-SG108E 6.0'], firmwareStr:['1.0.0 ...'], ... }
    info = _extract_var(html, 'info_ds')
    if isinstance(info, dict):
        def _first(key: str) -> Optional[str]:
            v = info.get(key)
            if isinstance(v, list) and v:
                return str(v[0])
            if isinstance(v, str):
                return v
            return None
        hw = _first('hardwareStr') or _first('hardware')
        fw = _first('firmwareStr') or _first('firmware')
        if hw:
            return hw, fw

    # --- DE-style: HTML <span> elements with named IDs ---
    def _span(id_: str) -> Optional[str]:
        m = re.search(rf'id="{re.escape(id_)}"[^>]*>([^<]+)<', html)
        return m.group(1).strip() if m else None

    hw = _span('sp_hardwareversion')
    fw = _span('sp_firewareversion')   # deliberate firmware typo in DE HTML
    if hw:
        return hw, fw

    return None, None


# ---------------------------------------------------------------------------
# Port-count inference from hardware version string
# ---------------------------------------------------------------------------

# Maps model prefixes (uppercase) to port count.
# Add entries here as new models are confirmed.
_MODEL_PORT_COUNT: Dict[str, int] = {
    # E-series (cookie-based sessions)
    'TL-SG105E':   5,
    'TL-SG108E':   8,
    'TL-SG108PE':  8,   # PoE variant — unverified
    'TL-SG116E':  16,   # unverified
    # DE-series (IP-based sessions)
    'TL-SG1008DE': 8,   # unverified
    'TL-SG1016DE': 16,
    'TL-SG1024DE': 24,  # unverified
}


def _port_count_from_hardware(hardware: Optional[str]) -> Optional[int]:
    """Return the port count for *hardware*, or None if the model is unknown."""
    if not hardware:
        return None
    hw = hardware.upper()
    for prefix, count in _MODEL_PORT_COUNT.items():
        if hw.startswith(prefix.upper()):
            return count
    return None


# ---------------------------------------------------------------------------
# Switch registry
# ---------------------------------------------------------------------------

# Each entry is (model_prefix, Switch_subclass).
# Checked in order; first prefix match wins.
# "unverified" means the protocol is assumed identical to a similar model
# but has not been tested against a live device.
_SWITCH_REGISTRY: List[Tuple[str, Type[Switch]]] = [
    # --- E-series (cookie-based, SG108E protocol) ---
    ('TL-SG105E',   Switch),     # 5-port  — unverified
    ('TL-SG108E',   Switch),     # 8-port  — verified
    ('TL-SG108PE',  Switch),     # 8-port PoE — unverified
    ('TL-SG116E',   Switch),     # 16-port — unverified
    # --- DE-series (IP-based sessions, SwitchDE protocol) ---
    ('TL-SG1008DE', SwitchDE),   # 8-port  — unverified
    ('TL-SG1016DE', SwitchDE),   # 16-port — verified
    ('TL-SG1024DE', SwitchDE),   # 24-port — unverified
]


def _lookup_class(hardware: Optional[str], session_type: str) -> Type[Switch]:
    """
    Return the Switch subclass for *hardware*.

    If the model string is not in the registry, falls back to a
    session-type heuristic and emits a RuntimeWarning so the caller
    knows to report the new model.
    """
    if hardware:
        hw = hardware.upper()
        for prefix, cls in _SWITCH_REGISTRY:
            if hw.startswith(prefix.upper()):
                return cls

    fallback = SwitchDE if session_type == 'ip_based' else Switch
    warnings.warn(
        f'Unknown switch model {hardware!r} (session_type={session_type!r}); '
        f'falling back to {fallback.__name__}. '
        'Behaviour may be incorrect. '
        'Please report this model at https://github.com/jfrancis42/ansible-tplink/issues.',
        RuntimeWarning,
        stacklevel=3,
    )
    return fallback


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def _resolve_model_override(model: str) -> Type[Switch]:
    """
    Resolve a ``model`` override string to a Switch subclass.

    Accepts:
    - A hardware-version prefix from the registry  (e.g. ``'TL-SG1016DE'``)
    - A Switch class name                          (e.g. ``'Switch'``, ``'SwitchDE'``)

    Raises ValueError with a helpful message if nothing matches.
    """
    # Class-name shorthand
    _BY_NAME: Dict[str, Type[Switch]] = {
        'switch':   Switch,
        'switchde': SwitchDE,
    }
    lo = model.strip().lower()
    if lo in _BY_NAME:
        return _BY_NAME[lo]

    # Registry prefix match (case-insensitive)
    for prefix, cls in _SWITCH_REGISTRY:
        if lo.startswith(prefix.lower()) or prefix.lower().startswith(lo):
            return cls

    valid_models = ', '.join(p for p, _ in _SWITCH_REGISTRY)
    raise ValueError(
        f'Unknown model override {model!r}. '
        f'Valid class names: Switch, SwitchDE. '
        f'Valid model prefixes: {valid_models}.'
    )


def make_switch(
    host: str,
    username: str = 'admin',
    password: str = 'admin',
    timeout: float = 10.0,
    model: Optional[str] = None,
) -> Switch:
    """
    Connect to a TP-Link managed switch and return the correct Switch subclass.

    Auto-detection sequence (2 HTTP requests, no redundant login):

      1. POST logon.cgi  — authenticates and reveals the session mechanism
                           (H_P_SSID cookie → SG108E family;
                            logonInfo only   → DE family).
      2. GET  SystemInfoRpm.htm — reads the hardware version string, which
                           is the ground-truth discriminator for class and
                           port count.

    The returned object is already logged in.  Use as a context manager::

        with make_switch('10.1.0.32', password='secret') as sw:
            print(sw.get_system_info())

    If the hardware version is not in the registry, the factory falls back
    to a session-type heuristic and emits RuntimeWarning.

    model override
    --------------
    If auto-detection fails or produces wrong results for a new / unusual
    switch, pass ``model`` to bypass the registry lookup entirely::

        # Force SG108E protocol for an unlisted E-series switch
        make_switch('10.1.0.99', password='secret', model='TL-SG108E')

        # Force DE protocol by class name
        make_switch('10.1.0.99', password='secret', model='SwitchDE')

    Valid values: any model prefix from _SWITCH_REGISTRY, or the class
    names ``'Switch'`` / ``'SwitchDE'`` (case-insensitive).
    """
    # Resolve override early so bad values fail before any network I/O
    override_cls: Optional[Type[Switch]] = None
    if model is not None:
        override_cls = _resolve_model_override(model)

    probe = requests.Session()

    try:
        r = probe.post(
            f'http://{host}/logon.cgi',
            data={'username': username, 'password': password, 'logon': 'Login'},
            timeout=timeout,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f'Cannot reach {host}: {exc}') from exc

    if not _check_login_success(r):
        raise RuntimeError(
            f'Login to {host} failed. '
            'Check username and password. '
            f'(logon.cgi status={r.status_code}, '
            f'response={r.text[:120]!r})'
        )

    if override_cls is not None:
        # Model forced by caller — skip sysinfo probe, infer port count from override
        port_count = _port_count_from_hardware(model)
        return override_cls._from_probe(
            host=host, username=username, password=password, timeout=timeout,
            session=probe, port_count=port_count,
        )

    session_type = _detect_session_type(r)

    # Read hardware version — the ground-truth model discriminator
    sysinfo_html = ''
    try:
        si = probe.get(f'http://{host}/SystemInfoRpm.htm', timeout=timeout)
        si.raise_for_status()
        sysinfo_html = si.text
    except requests.RequestException:
        pass   # factory still works via session-type fallback

    hardware, _firmware = _parse_sysinfo_html(sysinfo_html)
    port_count = _port_count_from_hardware(hardware)
    cls = _lookup_class(hardware, session_type)

    return cls._from_probe(
        host=host,
        username=username,
        password=password,
        timeout=timeout,
        session=probe,
        port_count=port_count,
    )
