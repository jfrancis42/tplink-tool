#!/usr/bin/env python3
"""
TL-SG108E Switch CLI

A Cisco IOS-inspired shell for the TP-Link TL-SG108E 8-port managed switch.
Modal design: exec → configure → interface / vlan sub-modes.

Usage:
    python3 cli.py <host> [--user USER] [--password PASSWORD]
    python3 cli.py 10.1.1.239 --password b1gsecret
"""

import cmd
import sys
import re
import argparse
import getpass
import textwrap
import requests

from tplink_tool import (
    Switch, PortSpeed, QoSMode, StormType, STORM_RATE_KBPS,
    _bits_to_ports, _ports_to_bits,
)

# ---------------------------------------------------------------------------
# Terminal colours
# ---------------------------------------------------------------------------
_USE_COLOR = sys.stdout.isatty()

def _c(code, s):
    return f'\033[{code}m{s}\033[0m' if _USE_COLOR else s

def green(s):  return _c('32', s)
def red(s):    return _c('31', s)
def yellow(s): return _c('33', s)
def cyan(s):   return _c('36', s)
def bold(s):   return _c('1',  s)
def dim(s):    return _c('2',  s)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ports(spec):
    """
    Parse a port specification into a sorted list of 1-based port numbers.
    Accepts: '1', '1,3,5', '1-4', '1-3,7', 'gi1', 'gi1-3'.
    Returns [] on parse error.
    """
    spec = spec.strip().lower().lstrip('gi').lstrip('port').strip()
    ports = set()
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            try:
                lo, hi = part.split('-', 1)
                ports.update(range(int(lo), int(hi) + 1))
            except ValueError:
                return []
        elif part.isdigit():
            ports.add(int(part))
        elif part:
            return []
    return sorted(ports)


def _port_range_str(ports):
    """Compress [1,2,3,5,6] → 'gi1-3,gi5-6'."""
    if not ports:
        return '-'
    out, start, prev = [], ports[0], ports[0]
    for p in ports[1:]:
        if p == prev + 1:
            prev = p
        else:
            out.append(f'gi{start}' if start == prev else f'gi{start}-{prev}')
            start = prev = p
    out.append(f'gi{start}' if start == prev else f'gi{start}-{prev}')
    return ','.join(out)


def _speed_str(spd):
    return str(spd) if spd else dim('link-down')


# ---------------------------------------------------------------------------
# Main CLI class
# ---------------------------------------------------------------------------

class SwitchCLI(cmd.Cmd):
    intro  = ''
    ruler  = '-'

    def __init__(self, sw: Switch, hostname: str):
        super().__init__()
        self.sw       = sw
        self._name    = hostname
        self._mode    = 'exec'
        self._if_ports = []   # list of port numbers being configured
        self._vlan_id  = None
        self._update_prompt()

    # ------------------------------------------------------------------
    # Mode / prompt management
    # ------------------------------------------------------------------

    def _update_prompt(self):
        n = self._name
        if self._mode == 'exec':
            self.prompt = bold(f'{n}# ')
        elif self._mode == 'config':
            self.prompt = bold(f'{n}(config)# ')
        elif self._mode == 'config-if':
            ps = _port_range_str(self._if_ports)
            self.prompt = bold(f'{n}(config-if-{ps})# ')
        elif self._mode == 'config-vlan':
            self.prompt = bold(f'{n}(config-vlan-{self._vlan_id})# ')

    def _enter(self, mode, **kw):
        self._mode = mode
        for k, v in kw.items():
            setattr(self, k, v)
        self._update_prompt()

    def _require(self, *modes):
        if self._mode not in modes:
            print(f'  % Command not available in {self._mode} mode')
            return False
        return True

    # ------------------------------------------------------------------
    # Abbreviation + 'no' dispatch
    # ------------------------------------------------------------------

    def onecmd(self, line):
        line = line.strip()
        if not line:
            return
        try:
            # 'no' prefix — route to _do_no
            if re.match(r'^no\b', line, re.IGNORECASE):
                return self._do_no(line[2:].strip())
            # 'do' prefix — run exec command from any mode
            if re.match(r'^do\b', line, re.IGNORECASE):
                saved = self._mode
                self._mode = 'exec'
                result = self.onecmd(line[2:].strip())
                self._mode = saved
                self._update_prompt()
                return result
            return super().onecmd(line)
        except requests.exceptions.ConnectionError:
            print(f'\n  % Connection to {self.sw.host} lost.')
            return True   # stop the cmdloop
        except requests.exceptions.Timeout:
            print(f'\n  % Connection to {self.sw.host} timed out.')
            return True
        except requests.exceptions.HTTPError as e:
            print(f'\n  % HTTP error: {e}')
            return True
        except RuntimeError as e:
            print(f'\n  % Switch error: {e}')

    def default(self, line):
        cmd_word, args, _ = self.parseline(line)
        if not cmd_word:
            return
        names = sorted(n[3:] for n in self.get_names() if n.startswith('do_'))
        matches = [n for n in names if n.startswith(cmd_word)]
        if len(matches) == 1:
            return getattr(self, f'do_{matches[0]}')(args)
        if cmd_word in matches:
            return getattr(self, f'do_{cmd_word}')(args)
        if matches:
            print(f'  % Ambiguous: {", ".join(matches)}')
        else:
            print(f'  % Unknown command: {cmd_word!r}  (type ? for help)')

    def _do_no(self, args):
        """Dispatch 'no <command> [args]'."""
        cmd_word, rest, _ = self.parseline(args)
        if not cmd_word:
            print('  % Incomplete command')
            return
        handler = getattr(self, f'_no_{cmd_word}', None)
        if handler:
            return handler(rest)
        # Try prefix match
        candidates = [n[4:] for n in dir(self) if n.startswith('_no_')]
        matches = [c for c in candidates if c.startswith(cmd_word)]
        if len(matches) == 1:
            return getattr(self, f'_no_{matches[0]}')(rest)
        if matches:
            print(f'  % Ambiguous no-form: {", ".join(matches)}')
        else:
            print(f'  % "no {cmd_word}" not supported')

    # ------------------------------------------------------------------
    # exit / end / quit
    # ------------------------------------------------------------------

    def do_exit(self, _):
        """Exit current mode (or disconnect if in exec)."""
        if self._mode in ('config-if', 'config-vlan'):
            self._enter('config')
        elif self._mode == 'config':
            self._enter('exec')
        else:
            return self._disconnect()

    def do_end(self, _):
        """Return directly to exec mode from any configuration mode."""
        self._enter('exec')

    def do_quit(self, _):
        """Disconnect from the switch."""
        return self._disconnect()

    def _disconnect(self):
        print('Bye.')
        return True

    def do_EOF(self, _):
        print()
        return self._disconnect()

    # ------------------------------------------------------------------
    # configure terminal
    # ------------------------------------------------------------------

    def do_configure(self, args):
        """configure terminal  — enter global configuration mode"""
        if not self._require('exec'):
            return
        sub = (args.split() or [''])[0]
        if not sub or 'terminal'.startswith(sub):
            self._enter('config')
        else:
            print('  Usage: configure terminal')

    def complete_configure(self, text, *_):
        return [s for s in ('terminal',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # interface
    # ------------------------------------------------------------------

    def do_interface(self, args):
        """interface {port N | gi N | range port N-M}  — configure port(s)"""
        if not self._require('config'):
            return
        args = args.strip()
        # Strip leading 'range' keyword
        args = re.sub(r'^range\s+', '', args, flags=re.IGNORECASE)
        # Strip leading 'port'/'gi' keywords
        args = re.sub(r'^(port|gi)\s*', '', args, flags=re.IGNORECASE)
        ports = _parse_ports(args)
        if not ports:
            print('  Usage: interface port <N>  or  interface range port <N>-<M>')
            return
        invalid = [p for p in ports if p < 1 or p > 8]
        if invalid:
            print(f'  % Invalid port(s): {invalid}')
            return
        self._enter('config-if', _if_ports=ports)

    def complete_interface(self, text, *_):
        return [s for s in ('port', 'range') if s.startswith(text)]

    # ------------------------------------------------------------------
    # vlan (config mode — enter VLAN sub-mode)
    # ------------------------------------------------------------------

    def do_vlan(self, args):
        """vlan <id>  — create/enter VLAN configuration sub-mode"""
        if not self._require('config'):
            return
        try:
            vid = int(args.strip())
            assert 1 <= vid <= 4094
        except (ValueError, AssertionError):
            print('  Usage: vlan <1-4094>')
            return
        self._enter('config-vlan', _vlan_id=vid)

    def _no_vlan(self, args):
        """no vlan <id>  — delete an 802.1Q VLAN"""
        if not self._require('config'):
            return
        try:
            vid = int(args.strip())
        except ValueError:
            print('  Usage: no vlan <id>')
            return
        self.sw.delete_dot1q_vlan(vid)
        print(f'  Deleted VLAN {vid}')

    # ------------------------------------------------------------------
    # name  (vlan sub-mode)
    # ------------------------------------------------------------------

    def do_name(self, args):
        """name <text>  — set VLAN name (in vlan config mode)"""
        if not self._require('config-vlan'):
            return
        enabled, vlans = self.sw.get_dot1q_vlans()
        vmap = {v.vid: v for v in vlans}
        v = vmap.get(self._vlan_id)
        tagged   = _bits_to_ports(v.tagged_members)   if v else []
        untagged = _bits_to_ports(v.untagged_members) if v else []
        self.sw.add_dot1q_vlan(self._vlan_id, name=args.strip(),
                               tagged_ports=tagged, untagged_ports=untagged)

    # ------------------------------------------------------------------
    # shutdown / no shutdown  (interface mode)
    # ------------------------------------------------------------------

    def do_shutdown(self, _):
        """shutdown  — administratively disable port(s)"""
        if not self._require('config-if'):
            return
        self.sw.set_ports(self._if_ports, enabled=False)
        print(f'  Port(s) {_port_range_str(self._if_ports)} disabled')

    def _no_shutdown(self, _):
        if not self._require('config-if'):
            return
        self.sw.set_ports(self._if_ports, enabled=True)
        print(f'  Port(s) {_port_range_str(self._if_ports)} enabled')

    # ------------------------------------------------------------------
    # speed  (interface mode)
    # ------------------------------------------------------------------

    _SPEED_MAP = {
        ('auto', ''):     PortSpeed.AUTO,
        ('1000', ''):     PortSpeed.M1000F,
        ('1000', 'full'): PortSpeed.M1000F,
        ('100', ''):      PortSpeed.M100F,
        ('100', 'full'):  PortSpeed.M100F,
        ('100', 'half'):  PortSpeed.M100H,
        ('10', ''):       PortSpeed.M10F,
        ('10', 'full'):   PortSpeed.M10F,
        ('10', 'half'):   PortSpeed.M10H,
    }

    def do_speed(self, args):
        """speed {auto|10|100|1000} [half|full]  — set port speed/duplex"""
        if not self._require('config-if'):
            return
        parts = args.lower().split()
        spd   = parts[0] if parts else ''
        dup   = parts[1] if len(parts) > 1 else ''
        ps = self._SPEED_MAP.get((spd, dup)) or self._SPEED_MAP.get((spd, ''))
        if ps is None:
            print('  Usage: speed {auto|10|100|1000} [half|full]')
            return
        self.sw.set_ports(self._if_ports, speed=ps)

    def complete_speed(self, text, *_):
        return [s for s in ('auto', '10', '100', '1000') if s.startswith(text)]

    # ------------------------------------------------------------------
    # flowcontrol / no flowcontrol  (interface mode)
    # ------------------------------------------------------------------

    def do_flowcontrol(self, _):
        """flowcontrol  — enable flow control on port(s)"""
        if not self._require('config-if'):
            return
        self.sw.set_ports(self._if_ports, flow_control=True)

    def _no_flowcontrol(self, _):
        if not self._require('config-if'):
            return
        self.sw.set_ports(self._if_ports, flow_control=False)

    # ------------------------------------------------------------------
    # switchport  (interface mode)
    # ------------------------------------------------------------------

    def do_switchport(self, args):
        """
        switchport access vlan <id>
        switchport trunk allowed vlan {add|remove} <id>
        switchport pvid <id>
        switchport mode {access|trunk}
        """
        if not self._require('config-if'):
            return
        parts = args.split()
        if not parts:
            print('  Usage: switchport {access|trunk|pvid|mode} ...')
            return
        sub = parts[0].lower()

        if sub == 'pvid':
            self._sw_pvid(parts[1:])
        elif sub == 'access':
            self._sw_access(parts[1:])
        elif sub == 'trunk':
            self._sw_trunk(parts[1:])
        elif sub == 'mode':
            self._sw_mode(parts[1:])
        else:
            print(f'  % Unknown switchport sub-command: {sub}')

    def complete_switchport(self, text, *_):
        return [s for s in ('access', 'trunk', 'pvid', 'mode') if s.startswith(text)]

    def _ensure_dot1q(self):
        """Enable 802.1Q mode if not already on."""
        enabled, _ = self.sw.get_dot1q_vlans()
        if not enabled:
            print('  Enabling 802.1Q VLAN mode...')
            self.sw.set_dot1q_enabled(True)

    def _sw_pvid(self, parts):
        if not parts or not parts[0].isdigit():
            print('  Usage: switchport pvid <vlan-id>')
            return
        vid = int(parts[0])
        for p in self._if_ports:
            self.sw.set_pvid([p], vid)

    def _sw_access(self, parts):
        """switchport access vlan <id> — set port as untagged on VLAN, update PVID."""
        if len(parts) < 2 or parts[0] != 'vlan' or not parts[1].isdigit():
            print('  Usage: switchport access vlan <id>')
            return
        vid = int(parts[1])
        self._ensure_dot1q()
        _, vlans = self.sw.get_dot1q_vlans()
        vmap = {v.vid: v for v in vlans}
        # Add each port as untagged on the target VLAN, remove from others
        for port in self._if_ports:
            for v in vlans:
                if v.vid == vid:
                    continue
                untagged = _bits_to_ports(v.untagged_members)
                if port in untagged:
                    untagged.remove(port)
                    tagged = _bits_to_ports(v.tagged_members)
                    self.sw.add_dot1q_vlan(v.vid, name=v.name,
                                           tagged_ports=tagged, untagged_ports=untagged)
            v = vmap.get(vid)
            tagged   = _bits_to_ports(v.tagged_members)   if v else []
            untagged = _bits_to_ports(v.untagged_members) if v else []
            if port not in untagged:
                untagged.append(port)
            if port in tagged:
                tagged.remove(port)
            self.sw.add_dot1q_vlan(vid, name=(v.name if v else ''),
                                   tagged_ports=tagged, untagged_ports=untagged)
            self.sw.set_pvid([port], vid)

    def _sw_trunk(self, parts):
        """switchport trunk allowed vlan {add|remove} <id>"""
        if len(parts) < 2:
            print('  Usage: switchport trunk allowed vlan {add|remove} <id>')
            return
        # strip 'allowed vlan' keywords
        while parts and parts[0].lower() in ('allowed', 'vlan'):
            parts = parts[1:]
        if len(parts) < 2 or not parts[1].isdigit():
            print('  Usage: switchport trunk allowed vlan {add|remove} <id>')
            return
        action = parts[0].lower()
        vid    = int(parts[1])
        if action not in ('add', 'remove'):
            print('  Usage: ... allowed vlan {add|remove} <id>')
            return
        self._ensure_dot1q()
        _, vlans = self.sw.get_dot1q_vlans()
        vmap = {v.vid: v for v in vlans}
        v = vmap.get(vid)
        tagged   = _bits_to_ports(v.tagged_members)   if v else []
        untagged = _bits_to_ports(v.untagged_members) if v else []
        for port in self._if_ports:
            if action == 'add':
                if port not in tagged:
                    tagged.append(port)
                if port in untagged:
                    untagged.remove(port)
            else:
                tagged   = [p for p in tagged   if p != port]
                untagged = [p for p in untagged if p != port]
        self.sw.add_dot1q_vlan(vid, name=(v.name if v else ''),
                               tagged_ports=tagged, untagged_ports=untagged)

    def _sw_mode(self, parts):
        """switchport mode {access|trunk} — informational only; no HW change."""
        if not parts:
            print('  Usage: switchport mode {access|trunk}')
            return
        m = parts[0].lower()
        if m not in ('access', 'trunk'):
            print('  % Mode must be "access" or "trunk"')
            return
        # On this switch mode is purely conceptual; membership is what matters.
        print(f'  Note: port mode is implicit — use switchport access/trunk vlan commands')

    # ------------------------------------------------------------------
    # channel-group / no channel-group  (interface mode)
    # ------------------------------------------------------------------

    def do_channel_group(self, args):
        """channel-group <1|2>  — assign port(s) to a LAG group"""
        if not self._require('config-if'):
            return
        parts = args.split()
        if not parts or not parts[0].isdigit():
            print('  Usage: channel-group {1|2}')
            return
        gid = int(parts[0])
        if gid not in (1, 2):
            print('  % Group ID must be 1 or 2')
            return
        tc = self.sw.get_port_trunk()
        current = tc.groups.get(gid, [])
        new_ports = sorted(set(current) | set(self._if_ports))
        self.sw.set_port_trunk(gid, new_ports)
        print(f'  LAG{gid} ports: {_port_range_str(new_ports)}')

    def _no_channel_group(self, args):
        parts = args.split()
        if not parts or not parts[0].isdigit():
            print('  Usage: no channel-group {1|2}')
            return
        gid = int(parts[0])
        tc = self.sw.get_port_trunk()
        current = tc.groups.get(gid, [])
        new_ports = [p for p in current if p not in self._if_ports]
        self.sw.set_port_trunk(gid, new_ports)
        print(f'  LAG{gid} ports: {_port_range_str(new_ports) if new_ports else "(none)"}')

    # ------------------------------------------------------------------
    # System config commands (config mode)
    # ------------------------------------------------------------------

    def do_hostname(self, args):
        """hostname <name>  — set device description"""
        if not self._require('config'):
            return
        name = args.strip()
        if not name:
            print('  Usage: hostname <name>')
            return
        self.sw.set_device_description(name)
        self._name = name
        self._update_prompt()

    def do_ip(self, args):
        """
        ip address <A.B.C.D> <mask>  — set static IP
        ip address dhcp              — enable DHCP
        """
        if not self._require('config'):
            return
        parts = args.split()
        if not parts:
            print('  Usage: ip address {<ip> <mask> | dhcp}')
            return
        if parts[0] == 'address':
            parts = parts[1:]
        if not parts:
            print('  Usage: ip address {<ip> <mask> | dhcp}')
            return
        if parts[0].lower() == 'dhcp':
            self.sw.set_ip_settings(dhcp=True)
            print('  DHCP enabled')
        elif len(parts) >= 2:
            self.sw.set_ip_settings(ip=parts[0], netmask=parts[1], dhcp=False)
            print(f'  IP set to {parts[0]} / {parts[1]}')
        else:
            print('  Usage: ip address <ip> <mask>  or  ip address dhcp')

    def _no_ip(self, args):
        """no ip address dhcp  — disable DHCP (requires static params)"""
        parts = args.split()
        if 'dhcp' in parts:
            current = self.sw.get_ip_settings()
            self.sw.set_ip_settings(ip=current.ip, netmask=current.netmask,
                                    gateway=current.gateway, dhcp=False)
            print('  DHCP disabled')
        else:
            print('  Usage: no ip address dhcp')

    def do_spanning_tree(self, _):
        """spanning-tree  — enable loop prevention"""
        if not self._require('config'):
            return
        self.sw.set_loop_prevention(True)
        print('  Loop prevention enabled')

    def _no_spanning_tree(self, _):
        if not self._require('config'):
            return
        self.sw.set_loop_prevention(False)
        print('  Loop prevention disabled')

    def do_igmp(self, args):
        """
        igmp snooping                  — enable IGMP snooping
        igmp snooping report-suppression
        """
        if not self._require('config'):
            return
        parts = args.lower().split()
        if not parts or parts[0] != 'snooping':
            print('  Usage: igmp snooping [report-suppression]')
            return
        suppression = 'report-suppression' in parts
        self.sw.set_igmp_snooping(True, report_suppression=suppression)
        print('  IGMP snooping enabled' + (' with report suppression' if suppression else ''))

    def _no_igmp(self, args):
        if not self._require('config'):
            return
        self.sw.set_igmp_snooping(False)
        print('  IGMP snooping disabled')

    def do_led(self, _):
        """led  — turn port LEDs on"""
        if not self._require('config'):
            return
        self.sw.set_led(True)
        print('  LEDs on')

    def _no_led(self, _):
        if not self._require('config'):
            return
        self.sw.set_led(False)
        print('  LEDs off')

    # ------------------------------------------------------------------
    # username (config mode)
    # ------------------------------------------------------------------

    def do_username(self, args):
        """username admin password <old-pw> <new-pw>  — change admin password"""
        if not self._require('config'):
            return
        parts = args.split()
        # Accept: username admin password <old> <new>
        # Strip exactly one leading 'admin' and one 'password' keyword
        if parts and parts[0].lower() == 'admin':
            parts = parts[1:]
        if parts and parts[0].lower() == 'password':
            parts = parts[1:]
        if len(parts) < 2:
            print('  Usage: username admin password <old-password> <new-password>')
            return
        old_pw, new_pw = parts[0], parts[1]
        self.sw.change_password(old_pw, new_pw)
        print('  Password changed.')

    # ------------------------------------------------------------------
    # qos (config mode — global QoS mode)
    # qos priority / bandwidth / storm-control (interface mode)
    # ------------------------------------------------------------------

    def do_qos(self, args):
        """
        (config)  qos mode {port-based|dot1p}    — set global QoS mode
        (if)      qos port-priority {1-4}         — set port priority
        """
        parts = args.lower().split()
        if not parts:
            print('  Usage: qos mode {port-based|dot1p}  OR  qos port-priority {1-4}')
            return
        sub = parts[0]

        if sub == 'mode':
            if not self._require('config'):
                return
            if len(parts) < 2:
                print('  Usage: qos mode {port-based|dot1p}')
                return
            m = parts[1]
            if m in ('port-based', 'port'):
                self.sw.set_qos_mode(QoSMode.PORT_BASED)
                print('  QoS mode: port-based')
            elif m in ('dot1p', '802.1p', 'dot1'):
                self.sw.set_qos_mode(QoSMode.DOT1P)
                print('  QoS mode: 802.1p')
            else:
                print('  % Unknown QoS mode; use port-based or dot1p')

        elif sub in ('port-priority', 'priority', 'pri'):
            if not self._require('config-if'):
                return
            if len(parts) < 2:
                print('  Usage: qos port-priority {1|2|3|4}')
                return
            try:
                pri = int(parts[1])
                assert 1 <= pri <= 4
            except (ValueError, AssertionError):
                print('  % Priority must be 1-4 (1=Lowest, 4=Highest)')
                return
            self.sw.set_port_priority(self._if_ports, pri)
            PRI = {1: 'Lowest', 2: 'Normal', 3: 'Medium', 4: 'Highest'}
            print(f'  Port(s) {_port_range_str(self._if_ports)} priority → {PRI[pri]}')
        else:
            print(f'  % Unknown qos sub-command: {sub}')

    def complete_qos(self, text, *_):
        subs = ['mode', 'port-priority']
        return [s for s in subs if s.startswith(text)]

    # ------------------------------------------------------------------
    # bandwidth  (interface mode)
    # ------------------------------------------------------------------

    def do_bandwidth(self, args):
        """
        bandwidth ingress <kbps>  — ingress rate limit (0 = unlimited)
        bandwidth egress  <kbps>  — egress  rate limit (0 = unlimited)
        """
        if not self._require('config-if'):
            return
        parts = args.lower().split()
        if len(parts) < 2:
            print('  Usage: bandwidth {ingress|egress} <kbps>  (0 = unlimited)')
            return
        direction, val = parts[0], parts[1]
        try:
            kbps = int(val)
            assert kbps >= 0
        except (ValueError, AssertionError):
            print('  % kbps must be a non-negative integer')
            return

        # Read current settings for the other direction
        bw_all = {b.port: b for b in self.sw.get_bandwidth_control()}
        for port in self._if_ports:
            cur = bw_all.get(port)
            ingress = cur.ingress_rate if cur else 0
            egress  = cur.egress_rate  if cur else 0
            if direction.startswith('in'):
                ingress = kbps
            elif direction.startswith('eg'):
                egress = kbps
            else:
                print(f'  % direction must be ingress or egress')
                return
            self.sw.set_bandwidth_control([port], ingress_kbps=ingress, egress_kbps=egress)
        dir_str = 'ingress' if direction.startswith('in') else 'egress'
        kbps_str = f'{kbps:,} kbps' if kbps else 'unlimited'
        print(f'  {dir_str.capitalize()} rate on {_port_range_str(self._if_ports)}: {kbps_str}')

    def _no_bandwidth(self, args):
        """no bandwidth {ingress|egress}  — remove rate limit"""
        parts = args.lower().split()
        if not parts:
            # Remove both limits
            for port in self._if_ports:
                self.sw.set_bandwidth_control([port], ingress_kbps=0, egress_kbps=0)
            print(f'  Bandwidth limits removed on {_port_range_str(self._if_ports)}')
            return
        direction = parts[0]
        bw_all = {b.port: b for b in self.sw.get_bandwidth_control()}
        for port in self._if_ports:
            cur = bw_all.get(port)
            ingress = cur.ingress_rate if cur else 0
            egress  = cur.egress_rate  if cur else 0
            if direction.startswith('in'):
                ingress = 0
            elif direction.startswith('eg'):
                egress = 0
            self.sw.set_bandwidth_control([port], ingress_kbps=ingress, egress_kbps=egress)
        dir_str = 'ingress' if direction.startswith('in') else 'egress'
        print(f'  {dir_str.capitalize()} limit removed on {_port_range_str(self._if_ports)}')

    def complete_bandwidth(self, text, *_):
        return [s for s in ('ingress', 'egress') if s.startswith(text)]

    # ------------------------------------------------------------------
    # storm-control  (interface mode)
    # ------------------------------------------------------------------

    _STORM_RATE_INDEX = {v: k for k, v in STORM_RATE_KBPS.items()}

    def do_storm_control(self, args):
        """
        storm-control {broadcast|multicast|unknown-unicast|all} rate <index|kbps>
          rate indexes: 1=64k 2=128k 3=256k 4=512k 5=1M 6=2M 7=4M 8=8M
                        9=16M 10=32M 11=64M 12=128M
        """
        if not self._require('config-if'):
            return
        parts = args.lower().split()
        if len(parts) < 3 or parts[-2] != 'rate':
            print('  Usage: storm-control {broadcast|multicast|unknown-unicast|all} rate <1-12>')
            return

        type_str = parts[0]
        rate_val  = parts[-1]

        type_map = {
            'broadcast':       StormType.BROADCAST,
            'multicast':       StormType.MULTICAST,
            'unknown-unicast': StormType.UNKNOWN_UNICAST,
            'unknown':         StormType.UNKNOWN_UNICAST,
            'all':             None,  # all three
        }
        matches = [k for k in type_map if k.startswith(type_str)]
        if not matches:
            print('  % Type must be broadcast, multicast, unknown-unicast, or all')
            return
        if len(matches) > 1 and type_str not in type_map:
            print(f'  % Ambiguous: {", ".join(matches)}')
            return
        chosen = type_map.get(type_str) or type_map.get(matches[0])

        try:
            rate = int(rate_val)
        except ValueError:
            print('  % Rate must be an integer (1-12 index)')
            return
        if rate not in STORM_RATE_KBPS:
            print(f'  % Rate index must be 1-12')
            return

        if chosen is None:
            storm_types = [StormType.BROADCAST, StormType.MULTICAST, StormType.UNKNOWN_UNICAST]
        else:
            storm_types = [chosen]

        self.sw.set_storm_control(self._if_ports, rate_index=rate, storm_types=storm_types)
        kbps = STORM_RATE_KBPS[rate]
        type_label = type_str if type_str in type_map else matches[0]
        print(f'  Storm control {type_label} on {_port_range_str(self._if_ports)}: '
              f'rate {rate} ({kbps:,} kbps)')

    def _no_storm_control(self, _):
        """no storm-control  — disable storm control on port(s)"""
        if not self._require('config-if'):
            return
        self.sw.set_storm_control(self._if_ports, rate_index=0, storm_types=[])
        print(f'  Storm control disabled on {_port_range_str(self._if_ports)}')

    def complete_storm_control(self, text, *_):
        return [s for s in ('broadcast', 'multicast', 'unknown-unicast', 'all')
                if s.startswith(text)]

    # ------------------------------------------------------------------
    # monitor session  (config mode — port mirroring)
    # ------------------------------------------------------------------

    def do_monitor(self, args):
        """
        monitor session 1 destination interface gi<N>
        monitor session 1 source interface gi<N> {rx|tx|both}
        no monitor session 1  — disable mirroring
        """
        if not self._require('config'):
            return
        parts = args.lower().split()
        # strip 'session 1'
        if len(parts) >= 2 and parts[0] == 'session':
            parts = parts[2:]  # discard 'session N'

        if not parts:
            print('  Usage: monitor session 1 {source|destination} interface gi<N> ...')
            return

        sub = parts[0]

        if sub.startswith('dest'):
            # destination interface gi<N>
            # strip 'interface'
            iface_parts = [p for p in parts[1:] if p not in ('interface',)]
            ports = _parse_ports(' '.join(iface_parts))
            if len(ports) != 1:
                print('  Usage: monitor session 1 destination interface gi<N>')
                return
            dest = ports[0]
            m = self.sw.get_port_mirror()
            self.sw.set_port_mirror(
                enabled=True,
                dest_port=dest,
                ingress_ports=m.ingress_ports,
                egress_ports=m.egress_ports,
            )
            print(f'  Mirror destination: gi{dest}')

        elif sub.startswith('src') or sub.startswith('sou'):
            # source interface gi<N> {rx|tx|both}
            # last token may be direction
            direction = 'both'
            iface_parts = parts[1:]
            if iface_parts and iface_parts[-1] in ('rx', 'tx', 'both'):
                direction = iface_parts[-1]
                iface_parts = iface_parts[:-1]
            iface_parts = [p for p in iface_parts if p not in ('interface',)]
            ports = _parse_ports(' '.join(iface_parts))
            if not ports:
                print('  Usage: monitor session 1 source interface gi<N> {rx|tx|both}')
                return
            m = self.sw.get_port_mirror()
            ingress = list(m.ingress_ports) if m.ingress_ports else []
            egress  = list(m.egress_ports)  if m.egress_ports  else []
            for p in ports:
                if direction in ('rx', 'both') and p not in ingress:
                    ingress.append(p)
                if direction in ('tx', 'both') and p not in egress:
                    egress.append(p)
            self.sw.set_port_mirror(
                enabled=True,
                dest_port=m.dest_port or 1,
                ingress_ports=ingress,
                egress_ports=egress,
            )
            print(f'  Mirror source(s) {_port_range_str(ports)} {direction}: set')
        else:
            print(f'  % Unknown monitor sub-command: {sub}')

    def _no_monitor(self, args):
        """no monitor session 1  — disable port mirroring"""
        if not self._require('config'):
            return
        self.sw.set_port_mirror(enabled=False, dest_port=1, ingress_ports=[], egress_ports=[])
        print('  Port mirroring disabled')

    def complete_monitor(self, text, *_):
        return [s for s in ('session',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # mtu-vlan  (config mode)
    # ------------------------------------------------------------------

    def do_mtu_vlan(self, args):
        """
        mtu-vlan uplink gi<N>  — enable MTU VLAN with specified uplink port
        mtu-vlan               — enable MTU VLAN (keep current uplink)
        """
        if not self._require('config'):
            return
        parts = args.lower().split()
        uplink = None
        if parts:
            # strip 'uplink' keyword, parse port
            iface_parts = [p for p in parts if p not in ('uplink', 'interface')]
            if iface_parts:
                ports = _parse_ports(iface_parts[0])
                if not ports:
                    print('  Usage: mtu-vlan uplink gi<N>')
                    return
                uplink = ports[0]
        self.sw.set_mtu_vlan(enabled=True, uplink_port=uplink)
        if uplink:
            print(f'  MTU VLAN enabled, uplink: gi{uplink}')
        else:
            print('  MTU VLAN enabled')

    def _no_mtu_vlan(self, _):
        """no mtu-vlan  — disable MTU VLAN"""
        if not self._require('config'):
            return
        self.sw.set_mtu_vlan(enabled=False)
        print('  MTU VLAN disabled')

    # ------------------------------------------------------------------
    # port-vlan (config mode — port-based VLAN)
    # ------------------------------------------------------------------

    def do_port_vlan(self, args):
        """
        port-vlan mode enable                — enable port-based VLAN mode
        port-vlan <id> members gi<N>[,<M>]  — add/update a port-based VLAN
        no port-vlan <id>                    — delete a port-based VLAN
        """
        if not self._require('config'):
            return
        parts = args.split()
        if not parts:
            print('  Usage: port-vlan {mode enable | <id> members <ports>}')
            return
        sub = parts[0].lower()
        if sub == 'mode':
            self.sw.set_port_vlan_enabled(True)
            print('  Port-based VLAN mode enabled')
        else:
            try:
                vid = int(sub)
            except ValueError:
                print('  Usage: port-vlan <id> members <ports>')
                return
            # expect 'members' keyword then port spec
            rest = parts[1:]
            if rest and rest[0].lower() == 'members':
                rest = rest[1:]
            ports = _parse_ports(','.join(rest))
            if not ports:
                print('  Usage: port-vlan <id> members gi<N>[,gi<M>]')
                return
            self.sw.add_port_vlan(vid, ports)
            print(f'  Port-based VLAN {vid} members: {_port_range_str(ports)}')

    def _no_port_vlan(self, args):
        """no port-vlan <id>  — delete a port-based VLAN"""
        if not self._require('config'):
            return
        parts = args.split()
        sub = parts[0].lower() if parts else ''
        if sub == 'mode':
            self.sw.set_port_vlan_enabled(False)
            print('  Port-based VLAN mode disabled')
            return
        try:
            vid = int(sub)
        except ValueError:
            print('  Usage: no port-vlan <id>')
            return
        self.sw.delete_port_vlan(vid)
        print(f'  Port-based VLAN {vid} deleted')

    # ------------------------------------------------------------------
    # reload
    # ------------------------------------------------------------------

    def do_reload(self, _):
        """reload  — reboot the switch"""
        if not self._require('exec'):
            return
        ans = input('  Proceed with reload? [y/N] ').strip().lower()
        if ans == 'y':
            self.sw.reboot()
            print('  Reloading...')
            return True
        else:
            print('  Reload cancelled')

    # ------------------------------------------------------------------
    # clear counters  (exec mode)
    # ------------------------------------------------------------------

    def do_clear(self, args):
        """clear counters [gi<N>]  — reset port statistics"""
        if not self._require('exec'):
            return
        parts = args.lower().split()
        if not parts or not parts[0].startswith('count'):
            print('  Usage: clear counters [gi<N>]')
            return
        port = None
        if len(parts) >= 2:
            ports = _parse_ports(parts[1])
            if not ports:
                print(f'  % Invalid port: {parts[1]}')
                return
            port = ports[0]
        self.sw.reset_port_statistics(port)
        if port:
            print(f'  Counters cleared for gi{port}')
        else:
            print('  All port counters cleared')

    def complete_clear(self, text, *_):
        return [s for s in ('counters',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # test cable-diagnostics  (exec mode)
    # ------------------------------------------------------------------

    def do_test(self, args):
        """test cable-diagnostics interface gi<N>[,<M>]  — run TDR cable test"""
        if not self._require('exec'):
            return
        parts = args.lower().split()
        # strip keywords: cable-diagnostics / cable-diag / tdr / interface
        iface_parts = [p for p in parts
                       if p not in ('cable-diagnostics', 'cable-diag', 'tdr',
                                    'interface', 'cable')]
        ports = _parse_ports(','.join(iface_parts)) if iface_parts else None
        print('  Running cable diagnostics...')
        results = self.sw.run_cable_diagnostic(ports)
        print(f'\n  {"Port":<6}  {"Status":<16}  Length')
        print(f'  {"------":<6}  {"----------------":<16}  ------')
        for r in results:
            length = f'{r.length_m} m' if r.length_m is not None else '--'
            status = r.status or '--'
            print(f'  gi{r.port:<4}  {status:<16}  {length}')
        print()

    def complete_test(self, text, *_):
        return [s for s in ('cable-diagnostics',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # copy  (exec mode — backup / restore config)
    # ------------------------------------------------------------------

    def do_copy(self, args):
        """
        copy running-config <file>  — save config backup to file
        copy <file> running-config  — restore config from file
        """
        if not self._require('exec'):
            return
        parts = args.split()
        if len(parts) < 2:
            print('  Usage: copy running-config <file>  |  copy <file> running-config')
            return
        src, dst = parts[0].lower(), parts[1].lower()

        if src == 'running-config':
            # Backup
            filename = parts[1]  # use original case
            data = self.sw.backup_config()
            with open(filename, 'wb') as f:
                f.write(data)
            print(f'  Config saved to {filename!r} ({len(data):,} bytes)')

        elif dst == 'running-config':
            # Restore
            filename = parts[0]  # use original case
            try:
                with open(filename, 'rb') as f:
                    data = f.read()
            except FileNotFoundError:
                print(f'  % File not found: {filename!r}')
                return
            ans = input(f'  Restore from {filename!r}? This will reboot the switch. [y/N] ').strip().lower()
            if ans != 'y':
                print('  Cancelled')
                return
            self.sw.restore_config(data)
            print('  Config restored. Switch is rebooting...')
            return True  # disconnect

        else:
            print('  Usage: copy running-config <file>  |  copy <file> running-config')

    def complete_copy(self, text, *_):
        return [s for s in ('running-config',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # write erase  (exec mode — factory reset)
    # ------------------------------------------------------------------

    def do_write(self, args):
        """write erase  — factory reset the switch"""
        if not self._require('exec'):
            return
        if not args.strip().lower().startswith('er'):
            print('  Usage: write erase')
            return
        ans = input('  Factory reset? ALL configuration will be lost. [y/N] ').strip().lower()
        if ans == 'y':
            self.sw.factory_reset()
            print('  Factory reset initiated. Switch is rebooting...')
            return True
        else:
            print('  Cancelled')

    def complete_write(self, text, *_):
        return [s for s in ('erase',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # show
    # ------------------------------------------------------------------

    def do_show(self, args):
        """
        show version
        show interfaces [brief | port <N> | counters]
        show vlan [brief | <id>]
        show ip
        show running-config
        show qos [bandwidth | storm-control]
        show spanning-tree
        show port-mirror
        show etherchannel
        """
        parts = args.split() if args else []
        if not parts:
            print('  Usage: show <subcommand>  (type "show ?" for list)')
            return
        sub = parts[0].lower()
        SUBS = {
            'version':        lambda: self._show_version(),
            'interfaces':     lambda: self._show_interfaces(parts[1:]),
            'vlan':           lambda: self._show_vlan(parts[1:]),
            'ip':             lambda: self._show_ip(parts[1:]),
            'running-config': lambda: self._show_running_config(),
            'qos':            lambda: self._show_qos(parts[1:]),
            'spanning-tree':  lambda: self._show_spanning_tree(),
            'port-mirror':    lambda: self._show_port_mirror(),
            'etherchannel':   lambda: self._show_etherchannel(),
            'mtu-vlan':       lambda: self._show_mtu_vlan(),
            'cable-diag':     lambda: self._show_cable_diag(parts[1:]),
        }
        matches = [k for k in SUBS if k.startswith(sub)]
        if len(matches) == 1:
            SUBS[matches[0]]()
        elif sub in matches:
            SUBS[sub]()
        elif matches:
            print(f'  % Ambiguous: {", ".join(sorted(matches))}')
        elif sub == '?':
            print('  Available: ' + ', '.join(sorted(SUBS)))
        else:
            print(f'  % Unknown: show {sub}')

    def complete_show(self, text, *_):
        subs = ['version', 'interfaces', 'vlan', 'ip', 'running-config',
                'qos', 'spanning-tree', 'port-mirror', 'etherchannel',
                'mtu-vlan', 'cable-diag']
        return [s for s in subs if s.startswith(text)]

    # ---- show version ----

    def _show_version(self):
        info = self.sw.get_system_info()
        ip   = self.sw.get_ip_settings()
        print(f'\n  {bold(info.description)}')
        print(f'  Hardware : {info.hardware}')
        print(f'  Firmware : {info.firmware}')
        print(f'  MAC      : {info.mac}')
        print(f'  IP       : {info.ip} / {info.netmask}'
              f'  {"(DHCP)" if ip.dhcp else "(static)"}')
        print(f'  Gateway  : {info.gateway}\n')

    # ---- show interfaces ----

    def _show_interfaces(self, args):
        sub  = args[0].lower() if args else 'brief'
        ports = self.sw.get_port_settings()

        if sub == 'counters':
            stats = self.sw.get_port_statistics()
            smap  = {s.port: s for s in stats}
            print(f'\n  {"Port":<6}  {"TX Pkts":>12}  {"RX Pkts":>12}')
            print(f'  {"------":<6}  {"----------":>12}  {"----------":>12}')
            for p in ports:
                s = smap.get(p.port)
                print(f'  gi{p.port:<4}  {(s.tx_pkts if s else 0):>12,}  '
                      f'{(s.rx_pkts if s else 0):>12,}')
            print()
            return

        if sub == 'port' and len(args) >= 2:
            try:
                n = int(args[1])
                ports = [p for p in ports if p.port == n]
            except ValueError:
                pass

        # Brief table (default)
        hdr = f'  {"Port":<6}  {"Status":<8}  {"Actual":<12}  {"Config":<10}  {"FC":<5}  LAG'
        print(f'\n{hdr}')
        print('  ' + '-' * (len(hdr) - 2))
        for p in ports:
            status = green('up  ') if p.enabled else red('down')
            actual = _speed_str(p.speed_act) if p.speed_act else dim('--')
            cfg    = str(p.speed_cfg) if p.speed_cfg else '--'
            fc     = 'on' if p.fc_cfg else 'off'
            lag    = f'LAG{p.trunk_id}' if p.trunk_id else '--'
            print(f'  gi{p.port:<4}  {status:<8}  {actual:<12}  {cfg:<10}  {fc:<5}  {lag}')
        print()

    # ---- show vlan ----

    def _show_vlan(self, args):
        sub = args[0].lower() if args else 'brief'

        # Port-based VLAN
        pv_enabled, pv_entries = self.sw.get_port_vlan()
        q_enabled,  q_entries  = self.sw.get_dot1q_vlans()

        if sub == 'brief' or sub not in [str(v.vid) for v in q_entries]:
            mode = ('802.1Q' if q_enabled else
                    'port-based' if pv_enabled else 'none')
            print(f'\n  VLAN mode: {bold(mode)}\n')

        if q_enabled:
            pvids = self.sw.get_pvids()
            print(f'  {"VLAN":<6}  {"Name":<16}  {"Tagged Ports":<20}  Untagged Ports')
            print(f'  {"----":<6}  {"----------------":<16}  {"--------------------":<20}  ---------------')
            for v in q_entries:
                t = _port_range_str(_bits_to_ports(v.tagged_members))
                u = _port_range_str(_bits_to_ports(v.untagged_members))
                print(f'  {v.vid:<6}  {(v.name or ""):<16}  {t:<20}  {u}')
            print()
            print(f'  Port PVIDs:  ' +
                  '  '.join(f'gi{i+1}:{pvids[i]}' for i in range(len(pvids))))
            print()
        elif pv_enabled:
            print(f'  {"VLAN":<6}  Member Ports')
            print(f'  {"----":<6}  -------------------------')
            for v in pv_entries:
                pts = _port_range_str(_bits_to_ports(v.members))
                print(f'  {v.vid:<6}  {pts}')
            print()
        else:
            print('  No VLAN configuration active.\n')

    # ---- show ip ----

    def _show_ip(self, args):
        ip = self.sw.get_ip_settings()
        print(f'\n  IP Address : {ip.ip}')
        print(f'  Subnet Mask: {ip.netmask}')
        print(f'  Gateway    : {ip.gateway}')
        print(f'  DHCP       : {"enabled" if ip.dhcp else "disabled"}\n')

    # ---- show running-config ----

    def _show_running_config(self):
        info  = self.sw.get_system_info()
        ip    = self.sw.get_ip_settings()
        ports = self.sw.get_port_settings()
        loop  = self.sw.get_loop_prevention()
        igmp  = self.sw.get_igmp_snooping()
        led   = self.sw.get_led()
        q_en, q_vlans = self.sw.get_dot1q_vlans()
        pvids = self.sw.get_pvids() if q_en else []

        def line(s=''):
            print(s)

        line('!')
        line(f'hostname {info.description}')
        line('!')
        if ip.dhcp:
            line('ip address dhcp')
        else:
            line(f'ip address {ip.ip} {ip.netmask}')
            line(f'ip default-gateway {ip.gateway}')
        line('!')
        if loop:  line('spanning-tree')
        if igmp.enabled:
            line('igmp snooping' +
                 (' report-suppression' if igmp.report_suppression else ''))
        if not led: line('no led')
        line('!')
        if q_en:
            for v in q_vlans:
                line(f'vlan {v.vid}')
                if v.name:
                    line(f' name {v.name}')
                line('!')
        for p in ports:
            line(f'interface gi{p.port}')
            if not p.enabled:
                line(' shutdown')
            if p.speed_cfg and p.speed_cfg != PortSpeed.AUTO:
                line(f' speed {p.speed_cfg}')
            if p.fc_cfg:
                line(' flowcontrol')
            if p.trunk_id:
                line(f' channel-group {p.trunk_id}')
            if q_en and pvids:
                pvid = pvids[p.port - 1] if p.port <= len(pvids) else 1
                for v in q_vlans:
                    u = _bits_to_ports(v.untagged_members)
                    t = _bits_to_ports(v.tagged_members)
                    if p.port in u:
                        # The switch firmware cannot remove ports from VLAN 1;
                        # all ports are permanently untagged members of it.
                        # Only show VLAN 1 untagged when PVID=1, i.e. when it
                        # is actually operative for this port.
                        if v.vid == 1 and pvid != 1:
                            continue
                        line(f' switchport access vlan {v.vid}')
                    elif p.port in t:
                        line(f' switchport trunk allowed vlan add {v.vid}')
                line(f' switchport pvid {pvid}')
            line('!')
        line('end')

    # ---- show qos ----

    def _show_qos(self, args):
        sub  = args[0].lower() if args else 'all'
        mode, qos_ports = self.sw.get_qos_settings()

        if sub in ('all', 'basic', 'ba'):
            mode_str = 'Port-based' if mode == QoSMode.PORT_BASED else '802.1p'
            print(f'\n  QoS mode: {mode_str}')
            PRI = {1: 'Lowest', 2: 'Normal', 3: 'Medium', 4: 'Highest'}
            print(f'\n  {"Port":<6}  Priority')
            print(f'  {"------":<6}  --------')
            for qp in qos_ports:
                print(f'  gi{qp.port:<4}  {PRI.get(qp.priority, str(qp.priority))}')
            print()

        if sub in ('all', 'bandwidth', 'ban', 'ba'):
            bw = self.sw.get_bandwidth_control()
            print(f'  {"Port":<6}  {"Ingress":>12}  {"Egress":>12}')
            print(f'  {"------":<6}  {"------------":>12}  {"------------":>12}')
            for b in bw:
                ig = f'{b.ingress_rate:,} kbps' if b.ingress_rate else 'unlimited'
                eg = f'{b.egress_rate:,}  kbps' if b.egress_rate  else 'unlimited'
                print(f'  gi{b.port:<4}  {ig:>12}  {eg:>12}')
            print()

        if sub in ('all', 'storm-control', 'storm', 'sto'):
            sc = self.sw.get_storm_control()
            print(f'  {"Port":<6}  {"Enabled":<8}  Rate idx  Storm Types')
            print(f'  {"------":<6}  {"-------":<8}  --------  -----------')
            for s in sc:
                if s.enabled:
                    kbps  = STORM_RATE_KBPS.get(s.rate_index, '?')
                    types = []
                    if s.storm_types & 1: types.append('UU')
                    if s.storm_types & 2: types.append('MC')
                    if s.storm_types & 4: types.append('BC')
                    print(f'  gi{s.port:<4}  {"yes":<8}  '
                          f'{kbps:>5} kbps  {",".join(types)}')
                else:
                    print(f'  gi{s.port:<4}  {"no":<8}  --')
            print()

    # ---- show spanning-tree ----

    def _show_spanning_tree(self):
        en = self.sw.get_loop_prevention()
        print(f'\n  Loop prevention: '
              f'{green("enabled") if en else red("disabled")}\n')

    # ---- show port-mirror ----

    def _show_port_mirror(self):
        m = self.sw.get_port_mirror()
        print(f'\n  Port mirroring: '
              f'{green("enabled") if m.enabled else red("disabled")}')
        if m.enabled:
            print(f'  Destination  : gi{m.dest_port}')
            print(f'  Ingress src  : {_port_range_str(m.ingress_ports)}')
            print(f'  Egress src   : {_port_range_str(m.egress_ports)}')
        print()

    # ---- show etherchannel ----

    def _show_etherchannel(self):
        tc = self.sw.get_port_trunk()
        print()
        if not tc.groups:
            print('  No LAG groups configured.\n')
            return
        for gid, members in sorted(tc.groups.items()):
            print(f'  LAG{gid}: {_port_range_str(members)}')
        print()

    # ---- show mtu-vlan ----

    def _show_mtu_vlan(self):
        mv = self.sw.get_mtu_vlan()
        print(f'\n  MTU VLAN: {"enabled" if mv.enabled else "disabled"}')
        if mv.enabled and mv.uplink_port:
            print(f'  Uplink  : gi{mv.uplink_port}')
        print()

    # ---- show cable-diag ----

    def _show_cable_diag(self, args):
        """show cable-diag [gi<N>]  — run TDR and display results"""
        ports = None
        if args:
            ports = _parse_ports(args[0])
        print('  Running cable diagnostics...')
        results = self.sw.run_cable_diagnostic(ports)
        print(f'\n  {"Port":<6}  {"Status":<16}  Length')
        print(f'  {"------":<6}  {"----------------":<16}  ------')
        for r in results:
            length = f'{r.length_m} m' if r.length_m is not None else '--'
            status = r.status or '--'
            print(f'  gi{r.port:<4}  {status:<16}  {length}')
        print()

    # ------------------------------------------------------------------
    # help
    # ------------------------------------------------------------------

    def do_help(self, arg):
        MODE_HELP = {
            'exec': [
                ('show version',             'System info and firmware'),
                ('show interfaces',          'Port status table'),
                ('show interfaces counters', 'TX/RX packet counters'),
                ('show vlan',                '802.1Q / port-based VLAN status'),
                ('show ip',                  'IP address configuration'),
                ('show running-config',      'Full configuration listing'),
                ('show qos',                 'QoS, bandwidth, storm-control'),
                ('show spanning-tree',       'Loop prevention state'),
                ('show port-mirror',         'Port mirroring configuration'),
                ('show etherchannel',        'LAG / trunk group membership'),
                ('show mtu-vlan',            'MTU VLAN configuration'),
                ('show cable-diag [gi<N>]',  'Run TDR cable diagnostics'),
                ('clear counters [gi<N>]',   'Reset port statistics'),
                ('test cable-diagnostics interface gi<N>', 'Run TDR cable test'),
                ('copy running-config <file>', 'Save config to file'),
                ('copy <file> running-config', 'Restore config from file'),
                ('configure terminal',       'Enter configuration mode'),
                ('reload',                   'Reboot the switch'),
                ('write erase',              'Factory reset'),
                ('exit / quit',              'Disconnect'),
            ],
            'config': [
                ('interface port <N>',                'Configure a port'),
                ('interface range port <N>-<M>',      'Configure multiple ports'),
                ('vlan <id>',                         'Create / enter 802.1Q VLAN config'),
                ('no vlan <id>',                      'Delete an 802.1Q VLAN'),
                ('hostname <name>',                   'Set device description'),
                ('ip address <ip> <mask>',            'Set static IP'),
                ('ip address dhcp',                   'Enable DHCP'),
                ('no ip address dhcp',                'Disable DHCP'),
                ('[no] spanning-tree',                'Loop prevention on/off'),
                ('[no] igmp snooping',                'IGMP snooping on/off'),
                ('[no] led',                          'Port LEDs on/off'),
                ('qos mode {port-based|dot1p}',       'Set global QoS mode'),
                ('monitor session 1 destination gi<N>', 'Set mirror destination'),
                ('monitor session 1 source gi<N> {rx|tx|both}', 'Set mirror source'),
                ('no monitor session 1',              'Disable port mirroring'),
                ('mtu-vlan uplink gi<N>',             'Enable MTU VLAN'),
                ('no mtu-vlan',                       'Disable MTU VLAN'),
                ('port-vlan mode enable',             'Enable port-based VLAN mode'),
                ('port-vlan <id> members gi<N>',      'Add/update port-based VLAN'),
                ('no port-vlan <id>',                 'Delete port-based VLAN'),
                ('username admin password <old> <new>', 'Change admin password'),
                ('do show ...',                       'Run a show command'),
                ('end',                               'Return to exec mode'),
            ],
            'config-if': [
                ('[no] shutdown',                              'Enable / disable port'),
                ('speed {auto|10|100|1000} [half|full]',       'Speed and duplex'),
                ('[no] flowcontrol',                           'Flow control on/off'),
                ('switchport access vlan <id>',                'Access-mode VLAN'),
                ('switchport trunk allowed vlan add <id>',     'Add tagged VLAN'),
                ('switchport trunk allowed vlan remove <id>',  'Remove tagged VLAN'),
                ('switchport pvid <id>',                       'Port VLAN ID'),
                ('[no] channel-group {1|2}',                   'Add/remove LAG group'),
                ('qos port-priority {1|2|3|4}',               'Port QoS priority (1=low, 4=high)'),
                ('bandwidth ingress <kbps>',                   'Ingress rate limit'),
                ('bandwidth egress <kbps>',                    'Egress rate limit'),
                ('no bandwidth',                               'Remove all rate limits'),
                ('storm-control {bc|mc|uu|all} rate <1-12>',  'Storm control'),
                ('no storm-control',                           'Disable storm control'),
                ('do show ...',                                'Run a show command'),
                ('exit',                                       'Back to config mode'),
                ('end',                                        'Back to exec mode'),
            ],
            'config-vlan': [
                ('name <text>',   'Set VLAN name'),
                ('do show ...',   'Run a show command'),
                ('exit',          'Back to config mode'),
                ('end',           'Back to exec mode'),
            ],
        }
        cmds = MODE_HELP.get(self._mode, [])
        w = max(len(c) for c, _ in cmds) + 2
        print(f'\n  Commands available in {bold(self._mode)} mode:\n')
        for cmd_str, desc in cmds:
            print(f'    {cmd_str:<{w}}  {dim(desc)}')
        print()
        print('  Abbreviations are supported (e.g. "conf t", "sh int").')
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description='TL-SG108E Switch CLI')
    ap.add_argument('host',                    help='Switch IP address')
    ap.add_argument('-u', '--user',     default='admin', help='Username (default: admin)')
    ap.add_argument('-p', '--password', default=None,    help='Password (prompted if omitted)')
    args = ap.parse_args()

    password = args.password or getpass.getpass(f'Password for {args.user}@{args.host}: ')

    print(f'Connecting to {args.host}...', end=' ', flush=True)
    try:
        sw = Switch(args.host, args.user, password)
        sw.login()
        info = sw.get_system_info()
        hostname = re.sub(r'[^A-Za-z0-9_\-]', '-', info.description) or 'switch'
        print(green('OK'))
        print(f'  {info.description}  |  FW: {info.firmware}  |  IP: {info.ip}')
        print()
    except Exception as e:
        print(red('FAILED'))
        print(f'  {e}')
        sys.exit(1)

    cli = SwitchCLI(sw, hostname)
    print("Type ? for help.  Type 'exit' to disconnect.\n")

    try:
        cli.cmdloop()
    except KeyboardInterrupt:
        print()
    finally:
        try:
            sw.logout()
        except Exception:
            pass


if __name__ == '__main__':
    main()
