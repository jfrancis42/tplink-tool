# tplink-tool

A Python SDK and interactive CLI for **TP-Link managed switches** — no
browser required.

These switches have no REST API, no SSH, and no serial console.  This
project reverse-engineers the HTTP web UI shared by many TP-Link managed
switch models to provide a clean Python interface and a Cisco IOS-inspired
shell.

## Verified hardware

The following hardware has been confirmed working end-to-end with all
read and write operations:

| Model | Hardware version | Firmware | Protocol |
|-------|-----------------|----------|----------|
| TL-SG108E | v6.0 | 1.0.0 Build 20230218 Rel.50633 | Cookie-based (`Switch`) |
| TL-SG1016DE | v2.0 | 1.0.1 Build 20151218 Rel.58739 | IP-based (`SwitchDE`) |

Other TP-Link Easy Smart and DE-series models with the same web UI are
expected to work.  See the model override section below if autodetection
fails on an untested model.

## Installation

```bash
pip install tplink-tool
```

After installation, the `tplink` CLI command is available and the
`tplink_tool` Python package is importable:

```python
from tplink_tool import Switch, PortSpeed
```

The legacy module name `tplink_switch` is still importable as a
backward-compatibility shim.

## Files

| File | Purpose |
|---|---|
| `src/tplink_tool/__init__.py` | Python SDK (`Switch` class, all read/write operations) |
| `src/tplink_tool/_cli.py` | CLI entry point (installed as `tplink` command) |
| `cli.py` | Standalone CLI script (same as `tplink` command, for direct use) |
| `tplink_switch.py` | Backward-compatibility shim (imports from `tplink_tool`) |
| `docs/sdk.md` | SDK programmer reference |
| `docs/cli.md` | CLI user guide |

## Requirements

```bash
pip install tplink-tool
```

No other dependencies beyond `requests` (installed automatically).

## Quick start — SDK

Use `make_switch()` as the primary entry point.  It auto-detects the
switch model and returns the correct subclass:

```python
from tplink_tool import make_switch, PortSpeed

with make_switch('192.168.0.1', password='admin') as sw:
    # Read system info
    print(sw.get_system_info())

    # Port status
    for port in sw.get_port_settings():
        print(port)

    # Configure a port
    sw.set_port(1, speed=PortSpeed.AUTO, flow_control=False)

    # 802.1Q VLANs
    sw.set_dot1q_enabled(True)
    sw.add_dot1q_vlan(10, name='servers', tagged_ports=[8], untagged_ports=[1])
    sw.set_pvid([1], 10)

    # Persist to flash (no-op on SG108E which auto-saves; required on SG1016DE)
    sw.save_config()
```

### Model override

If autodetection fails (unsupported or misidentified hardware), pass
the `model` argument to force a specific class or model prefix:

```python
# Force by hardware model prefix (from the support table above)
with make_switch('192.168.0.1', password='admin', model='TL-SG1016DE') as sw:
    ...

# Force by class name
with make_switch('192.168.0.1', password='admin', model='SwitchDE') as sw:
    ...

with make_switch('192.168.0.1', password='admin', model='Switch') as sw:
    ...
```

You can also import and instantiate the classes directly if you need
low-level control:

```python
from tplink_tool import Switch, SwitchDE, PortSpeed
```

See [docs/sdk.md](docs/sdk.md) for the full API reference.

## Quick start — CLI

```bash
tplink 192.168.0.1
# or: python3 cli.py 192.168.0.1
```

```
TL-SG108E# show interfaces
TL-SG108E# configure terminal
TL-SG108E(config)# vlan 10
TL-SG108E(config-vlan-10)# name servers
TL-SG108E(config-vlan-10)# exit
TL-SG108E(config)# interface port 1
TL-SG108E(config-if-gi1)# switchport access vlan 10
TL-SG108E(config-if-gi1)# exit
TL-SG108E(config)# end
TL-SG108E# show vlan
TL-SG108E# write memory
```

Commands can be abbreviated to their shortest unambiguous prefix (`conf t`,
`sh int`, `sw acc vl 10`, etc.).

See [docs/cli.md](docs/cli.md) for the full command reference.

## What is supported

### Read operations
- System info (firmware, MAC, IP)
- IP settings
- LED state
- Port settings (speed, duplex, flow control, trunk membership)
- Port statistics (TX/RX packet counters)
- Port mirroring
- Port trunking / LAG
- IGMP snooping
- Loop prevention
- MTU VLAN
- Port-based VLAN
- 802.1Q VLAN (membership, PVIDs)
- QoS (mode, per-port priority)
- Bandwidth control (ingress/egress rate limiting)
- Storm control
- Cable diagnostics (TDR) — **see firmware note below**

### Write operations
Everything listed above, plus:
- Config backup and restore
- Save running config to flash (`save_config()`)
- Factory reset
- Reboot
- Password change

> **Note:** Flash-write behaviour is model-specific.  TL-SG108E
> auto-saves every write to flash; no explicit save is needed.  TL-SG1016DE
> does not auto-save — call `sw.save_config()` after write operations, or
> use `write memory` in the CLI.  The Ansible collection calls
> `save_config()` automatically after every write task, so no extra step is
> needed there.

## Known firmware issues

### Cable diagnostics (TDR) — TL-SG108E v6.0, firmware 1.0.0 Build 20230218 Rel.50633

The `run_cable_diagnostic()` method is implemented and the status codes
are correct per the firmware's own JavaScript:

| Code | Meaning |
|------|---------|
| 0 | NoCable |
| 1 | Normal |
| 2 | Open (unterminated) |
| 3 | Short |
| 4 | OpenShort |
| 5 | CrossCable |
| -1 | NotTested |

However, on the tested firmware the `cable_diag_get.cgi` POST handler
silently drops the TCP connection before sending any HTTP response.  The
GET endpoint returns cached state, which is initialized to -1 (NotTested)
and never changes because the write side is non-functional.

All diagnostic results therefore return `NotTested` on this firmware.
The code is correct and ready; the limitation is a firmware bug.  If
you test on a different firmware version and TDR works, please open an
issue.

## Protocol notes

TP-Link Easy Smart switches use a frameset-based HTTP UI on port 80.
Two protocol variants are supported:

### Cookie-based protocol (TL-SG1xx Easy Smart series — `Switch` class)

- **Reads**: `GET /<Page>.htm` — state is embedded as JavaScript variable
  declarations in the first `<script>` block.
- **Writes**: most are `GET /<name>.cgi?param=value`; a few use POST.
- **Session**: cookie-based (`H_P_SSID`, TTL 600 s).  The SDK
  re-authenticates transparently before expiry and after mode-change
  operations that restart the switch's web server.

### IP-based protocol (TL-SG1xxDE DE-series — `SwitchDE` class)

- Same HTML/CGI structure, but the session is tracked server-side by the
  client IP address rather than a cookie.
- The switch allows only one admin session at a time; a second login from
  a different IP forces the existing session off.
- VLAN 1 membership is immutable on TL-SG1016DE firmware — the firmware
  always reports all ports as VLAN 1 members and ignores write attempts.

## License

[GNU General Public License v3.0](LICENSE)
