# TL-SG108E Python SDK Reference

`sg108e.py` is a pure-Python library for reading and configuring a
TP-Link TL-SG108E managed switch.  It requires only the `requests` package.
A full test suite passes against hardware revision 6.0 (firmware 1.0.0 Build
20230218 Rel.50633); adjacent revisions are likely compatible.  Other TP-Link
managed switches that share the same web UI may also work, though this has
not been tested.

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Session management](#session-management)
- [Data types](#data-types)
- [API reference](#api-reference)
  - [System](#system)
  - [Ports](#ports)
  - [Port statistics](#port-statistics)
  - [Port mirroring](#port-mirroring)
  - [Port trunking (LAG)](#port-trunking-lag)
  - [IGMP snooping](#igmp-snooping)
  - [Loop prevention](#loop-prevention)
  - [MTU VLAN](#mtu-vlan)
  - [Port-based VLAN](#port-based-vlan)
  - [802.1Q VLAN](#8021q-vlan)
  - [QoS](#qos)
  - [Bandwidth control](#bandwidth-control)
  - [Storm control](#storm-control)
  - [Cable diagnostics](#cable-diagnostics)
  - [Maintenance](#maintenance)
- [Helper functions](#helper-functions)
- [Error handling](#error-handling)

---

## Installation

```bash
pip install requests
```

Copy `sg108e.py` into your project (no package installation needed).

---

## Quick start

```python
from sg108e import Switch, PortSpeed

with Switch('192.168.0.1', password='admin') as sw:
    # Read system info
    info = sw.get_system_info()
    print(info.firmware)          # "1.0.0 Build 20230218 Rel.50633"

    # Read all port states
    for port in sw.get_port_settings():
        print(port)

    # Enable a port and set speed
    sw.set_ports([1], enabled=True, speed=PortSpeed.AUTO)

    # Print TX/RX counters
    for s in sw.get_port_statistics():
        print(f'Port {s.port}: TX={s.tx_pkts}  RX={s.rx_pkts}')
```

---

## Session management

The switch uses a cookie-based session (`H_P_SSID`, TTL 600 s).  The SDK
handles this transparently:

- **Context manager** — recommended.  Logs in on entry, logs out on exit.
- **Manual** — call `login()` and `logout()` yourself.
- **Automatic re-auth** — the session is refreshed before it expires (after
  550 s), and if a mode-change operation resets the switch's web server the
  SDK detects the redirect to the login page and re-authenticates silently.

```python
# Context manager (recommended)
with Switch('192.168.0.1', password='secret') as sw:
    sw.set_led(True)

# Manual
sw = Switch('192.168.0.1', password='secret')
sw.login()
sw.set_led(True)
sw.logout()

# Custom timeout (seconds)
sw = Switch('192.168.0.1', password='secret', timeout=30.0)
```

---

## Data types

### Enumerations

```python
class PortSpeed(IntEnum):
    AUTO   = 1   # Auto-negotiate
    M10H   = 2   # 10 Mbps half-duplex
    M10F   = 3   # 10 Mbps full-duplex
    M100H  = 4   # 100 Mbps half-duplex
    M100F  = 5   # 100 Mbps full-duplex
    M1000F = 6   # 1000 Mbps full-duplex

class QoSMode(IntEnum):
    PORT_BASED = 0
    DOT1P      = 1
    DSCP       = 2

class StormType(IntEnum):
    UNKNOWN_UNICAST = 1
    MULTICAST       = 2
    BROADCAST       = 4
```

### Dataclasses

| Class | Fields |
|---|---|
| `SystemInfo` | `description`, `mac`, `ip`, `netmask`, `gateway`, `firmware`, `hardware` |
| `IPSettings` | `dhcp: bool`, `ip`, `netmask`, `gateway` |
| `PortInfo` | `port`, `enabled`, `speed_cfg`, `speed_act`, `fc_cfg`, `fc_act`, `trunk_id` |
| `PortStats` | `port`, `tx_pkts`, `rx_pkts` |
| `MirrorConfig` | `enabled`, `dest_port`, `mode`, `ingress_ports`, `egress_ports` |
| `TrunkConfig` | `max_groups`, `port_count`, `groups: Dict[int, List[int]]` |
| `IGMPConfig` | `enabled`, `report_suppression`, `group_count` |
| `MTUVlanConfig` | `enabled`, `port_count`, `uplink_port` |
| `PortVlanEntry` | `vid`, `members: int` (bitmask) |
| `Dot1QVlanEntry` | `vid`, `name`, `tagged_members: int`, `untagged_members: int` |
| `QoSPortConfig` | `port`, `priority` (1=lowest … 4=highest) |
| `BandwidthEntry` | `port`, `ingress_rate` (kbps), `egress_rate` (kbps) |
| `StormEntry` | `port`, `enabled`, `rate_index`, `storm_types` (bitmask) |
| `CableDiagResult` | `port`, `status` ('OK'/'Open'/'Short'/'Unknown'), `length_m` |

---

## API reference

### System

#### `get_system_info() → SystemInfo`

```python
info = sw.get_system_info()
print(info.description)   # "TL-SG108E"
print(info.mac)           # "AA:BB:CC:DD:EE:FF"
print(info.firmware)      # "1.0.0 Build 20230218 Rel.50633"
```

#### `set_device_description(description: str)`

```python
sw.set_device_description('lab-switch-1')
```

#### `get_ip_settings() → IPSettings`

```python
ip = sw.get_ip_settings()
print(ip.dhcp, ip.ip, ip.netmask, ip.gateway)
```

#### `set_ip_settings(ip=None, netmask=None, gateway=None, dhcp=None)`

Any `None` argument is left unchanged (re-read from the switch first).

```python
# Static IP
sw.set_ip_settings(ip='10.0.0.5', netmask='255.255.255.0', gateway='10.0.0.1', dhcp=False)

# Enable DHCP
sw.set_ip_settings(dhcp=True)
```

#### `get_led() → bool`

```python
if sw.get_led():
    print('LEDs are on')
```

#### `set_led(on: bool)`

```python
sw.set_led(False)   # turn off port LEDs
```

---

### Ports

#### `get_port_settings() → List[PortInfo]`

Returns one `PortInfo` per physical port.

```python
for p in sw.get_port_settings():
    state = 'up' if p.enabled else 'down'
    print(f'Port {p.port}: {state}  actual={p.speed_act}  cfg={p.speed_cfg}')
```

#### `set_port(port: int, *, enabled=None, speed=None, flow_control=None)`

Configure a single port.  Any `None` argument applies the sentinel "no change"
value (firmware value 7), so only specified parameters are altered.

```python
sw.set_port(1, speed=PortSpeed.AUTO)
sw.set_port(2, enabled=False)
sw.set_port(3, speed=PortSpeed.M100F, flow_control=True)
```

#### `set_ports(ports: List[int], *, enabled=None, speed=None, flow_control=None)`

Same as `set_port` but applies the same settings to multiple ports in one
request.

```python
# Disable ports 5–8
sw.set_ports([5, 6, 7, 8], enabled=False)

# Set ports 1–4 to auto with flow control off
sw.set_ports([1, 2, 3, 4], speed=PortSpeed.AUTO, flow_control=False)
```

---

### Port statistics

#### `get_port_statistics() → List[PortStats]`

```python
for s in sw.get_port_statistics():
    print(f'Port {s.port}: TX={s.tx_pkts:,}  RX={s.rx_pkts:,}')
```

#### `reset_port_statistics(port: Optional[int] = None)`

Pass a port number to clear one port, or `None` to clear all.

```python
sw.reset_port_statistics()      # clear all
sw.reset_port_statistics(3)     # clear port 3 only
```

---

### Port mirroring

#### `get_port_mirror() → MirrorConfig`

```python
m = sw.get_port_mirror()
if m.enabled:
    print(f'Destination: {m.dest_port}')
    print(f'Ingress src: {m.ingress_ports}')
    print(f'Egress src:  {m.egress_ports}')
```

#### `set_port_mirror(enabled, dest_port, ingress_ports, egress_ports)`

```python
# Mirror ingress+egress on port 1 to port 8
sw.set_port_mirror(
    enabled=True,
    dest_port=8,
    ingress_ports=[1],
    egress_ports=[1],
)

# Disable mirroring
sw.set_port_mirror(enabled=False, dest_port=1, ingress_ports=[], egress_ports=[])
```

---

### Port trunking (LAG)

#### `get_port_trunk() → TrunkConfig`

```python
tc = sw.get_port_trunk()
for gid, members in tc.groups.items():
    print(f'LAG{gid}: ports {members}')
```

#### `set_port_trunk(group_id: int, ports: List[int])`

Pass an empty list to delete the group.  `group_id` is 1 or 2.

```python
# Create LAG1 with ports 1 and 2
sw.set_port_trunk(1, [1, 2])

# Delete LAG1
sw.set_port_trunk(1, [])
```

---

### IGMP snooping

#### `get_igmp_snooping() → IGMPConfig`

```python
igmp = sw.get_igmp_snooping()
print(igmp.enabled, igmp.report_suppression)
```

#### `set_igmp_snooping(enabled: bool, report_suppression: bool = False)`

```python
sw.set_igmp_snooping(True)
sw.set_igmp_snooping(True, report_suppression=True)
sw.set_igmp_snooping(False)
```

---

### Loop prevention

#### `get_loop_prevention() → bool`

```python
print('Loop prevention:', sw.get_loop_prevention())
```

#### `set_loop_prevention(enabled: bool)`

```python
sw.set_loop_prevention(True)
```

---

### MTU VLAN

MTU VLAN mode designates one port as an uplink; all other ports can reach
the uplink but not each other.

#### `get_mtu_vlan() → MTUVlanConfig`

```python
mv = sw.get_mtu_vlan()
print(mv.enabled, mv.uplink_port)
```

#### `set_mtu_vlan(enabled: bool, uplink_port: Optional[int] = None)`

```python
sw.set_mtu_vlan(enabled=True, uplink_port=8)
sw.set_mtu_vlan(enabled=False)
```

---

### Port-based VLAN

Port-based VLAN is mutually exclusive with 802.1Q VLAN mode.

#### `get_port_vlan() → Tuple[bool, List[PortVlanEntry]]`

```python
enabled, vlans = sw.get_port_vlan()
for v in vlans:
    ports = _bits_to_ports(v.members)
    print(f'VLAN {v.vid}: ports {ports}')
```

#### `set_port_vlan_enabled(enabled: bool)`

```python
sw.set_port_vlan_enabled(True)
```

#### `add_port_vlan(vid: int, member_ports: List[int])`

```python
sw.add_port_vlan(2, [1, 2, 3])
sw.add_port_vlan(3, [4, 5, 6])
```

#### `delete_port_vlan(vid: int)`

```python
sw.delete_port_vlan(2)
```

---

### 802.1Q VLAN

#### `get_dot1q_vlans() → Tuple[bool, List[Dot1QVlanEntry]]`

```python
enabled, vlans = sw.get_dot1q_vlans()
for v in vlans:
    tagged   = _bits_to_ports(v.tagged_members)
    untagged = _bits_to_ports(v.untagged_members)
    print(f'VLAN {v.vid} ({v.name}): tagged={tagged}  untagged={untagged}')
```

#### `set_dot1q_enabled(enabled: bool)`

Toggling 802.1Q mode causes the switch to restart its web server.  The SDK
detects this and re-authenticates automatically.

```python
sw.set_dot1q_enabled(True)
sw.set_dot1q_enabled(False)
```

#### `add_dot1q_vlan(vid, name='', tagged_ports=None, untagged_ports=None)`

Creates or updates a VLAN.  Any port not in either list becomes a non-member.

```python
# VLAN 10: port 1 untagged (access), port 8 tagged (trunk)
sw.add_dot1q_vlan(10, name='servers', tagged_ports=[8], untagged_ports=[1])

# Rename an existing VLAN without changing membership
_, vlans = sw.get_dot1q_vlans()
v = next(x for x in vlans if x.vid == 10)
sw.add_dot1q_vlan(10, name='storage',
                  tagged_ports=_bits_to_ports(v.tagged_members),
                  untagged_ports=_bits_to_ports(v.untagged_members))
```

#### `delete_dot1q_vlan(vid: int)`

```python
sw.delete_dot1q_vlan(10)
```

#### `get_pvids() → List[int]`

Returns a list of PVIDs indexed from 0 (index 0 = port 1).

```python
pvids = sw.get_pvids()
for i, pvid in enumerate(pvids):
    print(f'Port {i+1}: PVID={pvid}')
```

#### `set_pvid(ports: List[int], pvid: int)`

```python
sw.set_pvid([1, 2], 10)    # set ports 1 and 2 to PVID 10
```

---

### QoS

#### `get_qos_settings() → Tuple[QoSMode, List[QoSPortConfig]]`

```python
mode, ports = sw.get_qos_settings()
mode_name = {QoSMode.PORT_BASED: 'port-based', QoSMode.DOT1P: '802.1p', QoSMode.DSCP: 'DSCP'}
print('Mode:', mode_name.get(mode, str(mode)))
for p in ports:
    print(f'Port {p.port}: priority {p.priority}')
```

#### `set_qos_mode(mode: QoSMode)`

```python
sw.set_qos_mode(QoSMode.PORT_BASED)
sw.set_qos_mode(QoSMode.DOT1P)
sw.set_qos_mode(QoSMode.DSCP)
```

#### `set_port_priority(ports: List[int], priority: int)`

Priority: 1=Lowest, 2=Normal, 3=Medium, 4=Highest.

```python
sw.set_port_priority([1, 2], priority=4)   # highest priority
sw.set_port_priority([5, 6, 7, 8], priority=1)
```

---

### Bandwidth control

#### `get_bandwidth_control() → List[BandwidthEntry]`

```python
for b in sw.get_bandwidth_control():
    ing = f'{b.ingress_rate:,} kbps' if b.ingress_rate else 'unlimited'
    eg  = f'{b.egress_rate:,} kbps'  if b.egress_rate  else 'unlimited'
    print(f'Port {b.port}: ingress={ing}  egress={eg}')
```

#### `set_bandwidth_control(ports, ingress_kbps=0, egress_kbps=0)`

`0` means no limit.  Both directions are set in one call.

```python
sw.set_bandwidth_control([3], ingress_kbps=1024, egress_kbps=512)
sw.set_bandwidth_control([3], ingress_kbps=0, egress_kbps=0)  # remove limits
```

---

### Storm control

Storm control uses rate **indexes** 1–12, not raw kbps values.  Import
`STORM_RATE_KBPS` to convert:

```python
from sg108e import STORM_RATE_KBPS
# {1: 64, 2: 128, 3: 256, 4: 512, 5: 1024, 6: 2048, 7: 4096,
#  8: 8192, 9: 16384, 10: 32768, 11: 65536, 12: 131072}
```

#### `get_storm_control() → List[StormEntry]`

```python
for s in sw.get_storm_control():
    if s.enabled:
        kbps = STORM_RATE_KBPS.get(s.rate_index, '?')
        print(f'Port {s.port}: {kbps} kbps  types={s.storm_types:#04x}')
```

#### `set_storm_control(ports, rate_index, storm_types)`

`rate_index=0` or `storm_types=[]` disables storm control.

```python
# Limit broadcast + multicast on port 1 to 1024 kbps (index 5)
sw.set_storm_control([1], rate_index=5,
                     storm_types=[StormType.BROADCAST, StormType.MULTICAST])

# Limit all traffic types on all ports to 64 kbps (index 1)
sw.set_storm_control(list(range(1, 9)), rate_index=1,
                     storm_types=StormType.all())

# Disable storm control on port 1
sw.set_storm_control([1], rate_index=0, storm_types=[])
```

---

### Cable diagnostics

#### `run_cable_diagnostic(ports=None) → List[CableDiagResult]`

`ports=None` tests all ports.

```python
results = sw.run_cable_diagnostic()
for r in results:
    print(f'Port {r.port}: {r.status}  {r.length_m} m')

# Test a single port
results = sw.run_cable_diagnostic([3])
```

`status` values: `'OK'`, `'Open'`, `'Short'`, `'Unknown'`.
`length_m` is `-1` when not available (e.g. link is up and healthy).

---

### Maintenance

#### `reboot()`

```python
sw.reboot()   # switch reboots; current session becomes invalid
```

#### `factory_reset()`

```python
sw.factory_reset()   # all configuration is lost
```

#### `backup_config() → bytes`

Returns the raw binary config file.

```python
data = sw.backup_config()
with open('switch-backup.bin', 'wb') as f:
    f.write(data)
print(f'Saved {len(data):,} bytes')
```

#### `restore_config(config_data: bytes)`

```python
with open('switch-backup.bin', 'rb') as f:
    data = f.read()
sw.restore_config(data)   # switch reboots after restore
```

#### `change_password(old_password, new_password, username=None)`

`username` defaults to the username the `Switch` was constructed with.

```python
sw.change_password('oldpass', 'newpass')
```

---

## Helper functions

These are module-level functions, not methods on `Switch`.

### `_bits_to_ports(mask: int, port_count: int = 8) → List[int]`

Convert a bitmask to a list of 1-based port numbers.
Bit 0 = port 1, bit 7 = port 8.

```python
from sg108e import _bits_to_ports
_bits_to_ports(0xFF)    # [1, 2, 3, 4, 5, 6, 7, 8]
_bits_to_ports(0x81)    # [1, 8]
_bits_to_ports(0x05)    # [1, 3]
```

### `_ports_to_bits(ports: List[int]) → int`

Inverse of `_bits_to_ports`.

```python
from sg108e import _ports_to_bits
_ports_to_bits([1, 8])      # 0x81 = 129
_ports_to_bits([1, 2, 3])   # 0x07 = 7
```

---

## Error handling

Most network errors propagate as `requests.exceptions.RequestException`
subclasses.  **Exception:** certain write operations (QoS mode, bandwidth
control, storm control) cause the switch to drop the TCP connection after
responding; the SDK catches `ConnectionError` in those cases, marks the
session expired, and returns silently rather than raising.

Authentication failures raise `RuntimeError` with a descriptive
message including the firmware's `errType` code:

| errType | Meaning |
|---------|---------|
| 1 | Bad username or password |
| 3 | Login limit reached (too many sessions) |
| 5 | Session timeout |

```python
from requests.exceptions import ConnectionError, Timeout

try:
    with Switch('192.168.0.1', password='wrong') as sw:
        sw.get_system_info()
except RuntimeError as e:
    print('Auth error:', e)
except ConnectionError:
    print('Switch unreachable')
except Timeout:
    print('Request timed out')
```
