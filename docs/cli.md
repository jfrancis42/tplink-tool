# TL-SG108E CLI User Guide

`cli.py` is an interactive command-line interface for the TP-Link TL-SG108E,
modelled after Cisco IOS.  It lets you read and configure the switch without
opening a browser.

## Contents

- [Starting the CLI](#starting-the-cli)
- [Modal structure](#modal-structure)
- [Abbreviations](#abbreviations)
- [Exec mode](#exec-mode)
- [Config mode](#config-mode)
- [Interface mode](#interface-mode)
- [VLAN mode](#vlan-mode)
- [Common workflows](#common-workflows)

---

## Starting the CLI

```bash
python3 cli.py <host> [--user USER] [--password PASSWORD]
```

```bash
# Password prompted interactively (recommended)
python3 cli.py 192.168.0.1

# Password on command line
python3 cli.py 192.168.0.1 --password secret

# Non-default username
python3 cli.py 192.168.0.1 --user admin --password secret
```

On success the prompt appears with the switch's device description as the
hostname:

```
Connecting to 192.168.0.1... OK
  TL-SG108E  |  FW: 1.0.0 Build 20230218 Rel.50633  |  IP: 192.168.0.1

Type ? for help.  Type 'exit' to disconnect.

TL-SG108E#
```

---

## Modal structure

```
exec                          ← login lands here
 └─ configure terminal
     └─ config                ← global configuration
         ├─ interface port N
         │   └─ config-if     ← per-port configuration
         └─ vlan N
             └─ config-vlan   ← VLAN name configuration
```

| Command | Effect |
|---|---|
| `configure terminal` | Enter config mode |
| `interface port <N>` | Enter interface mode for port N |
| `vlan <id>` | Enter VLAN config mode |
| `exit` | Go up one level |
| `end` | Return directly to exec mode |
| `do <command>` | Run any exec-mode command from config/if/vlan mode |

> **Save your work:** The switch does not auto-save configuration changes.
> After any configuration session, run `write memory` from exec mode to
> persist changes to flash.  Changes not saved are lost on power cycle.

---

## Abbreviations

Any command can be abbreviated to the shortest unambiguous prefix.

```
sh ver          → show version
conf t          → configure terminal
int port 3      → interface port 3
sw acc vl 10    → switchport access vlan 10
```

If an abbreviation is ambiguous, the CLI lists the candidates:

```
TL-SG108E# sh s
  % Ambiguous: spanning-tree, storm-control
```

---

## Exec mode

### show version

```
TL-SG108E# show version

  TL-SG108E
  Hardware : TL-SG108E 6.0
  Firmware : 1.0.0 Build 20230218 Rel.50633
  MAC      : AA:BB:CC:DD:EE:FF
  IP       : 192.168.0.1 / 255.255.255.0  (static)
  Gateway  : 192.168.0.254
```

### show interfaces

```
TL-SG108E# show interfaces

  Port    Status    Actual        Config      FC     LAG
  ------  --------  ------------ ----------  -----  ---
  gi1     up        1000M-Full    Auto        off    --
  gi2     down      --            Auto        off    --
  ...
```

```
TL-SG108E# show interfaces port 1    ← single port
TL-SG108E# show interfaces counters  ← TX/RX packet counts
```

### show vlan

```
TL-SG108E# show vlan

  VLAN mode: 802.1Q

  VLAN    Name              Tagged Ports          Untagged Ports
  ----    ----------------  --------------------  ---------------
  1       Default           --                    gi1-8
  10      servers           gi8                   gi1
  20      cameras           gi8                   gi2,gi3

  Port PVIDs:  gi1:10  gi2:20  gi3:20  gi4:1  gi5:1  gi6:1  gi7:1  gi8:1
```

### show ip

```
TL-SG108E# show ip

  IP Address : 192.168.0.1
  Subnet Mask: 255.255.255.0
  Gateway    : 192.168.0.254
  DHCP       : disabled
```

### show qos

```
TL-SG108E# show qos           ← all QoS info
TL-SG108E# show qos bandwidth ← bandwidth limits only
TL-SG108E# show qos storm     ← storm control only
```

### show spanning-tree

```
TL-SG108E# show spanning-tree

  Loop prevention: enabled
```

### show port-mirror

```
TL-SG108E# show port-mirror

  Port mirroring: enabled
  Destination  : gi8
  Ingress src  : gi1-3
  Egress src   : gi1-3
```

### show etherchannel

```
TL-SG108E# show etherchannel

  LAG1: gi1-2
```

### show mtu-vlan

```
TL-SG108E# show mtu-vlan

  MTU VLAN: enabled
  Uplink  : gi8
```

### show cable-diag

Runs TDR diagnostics and prints results.

```
TL-SG108E# show cable-diag
  Running cable diagnostics...

  Port    Status            Length
  ------  ----------------  ------
  gi1     OK                --
  gi2     Open              14 m
  gi3     OK                --
  ...

TL-SG108E# show cable-diag gi2    ← single port
```

### show running-config

Prints the full switch configuration in CLI syntax.

```
TL-SG108E# show running-config
!
hostname TL-SG108E
!
ip address 192.168.0.1 255.255.255.0
ip default-gateway 192.168.0.254
!
spanning-tree
igmp snooping
!
vlan 10
 name servers
!
interface gi1
 switchport access vlan 10
 switchport pvid 10
!
...
end
```

### clear counters

```
TL-SG108E# clear counters          ← all ports
TL-SG108E# clear counters gi3      ← single port
```

### test cable-diagnostics

```
TL-SG108E# test cable-diagnostics interface gi1
  Running cable diagnostics...

  Port    Status            Length
  ------  ----------------  ------
  gi1     OK                --
```

### copy — backup and restore

```
TL-SG108E# copy running-config backup.bin
  Config saved to 'backup.bin' (12345 bytes)

TL-SG108E# copy backup.bin running-config
  Restore from 'backup.bin'? This will reboot the switch. [y/N] y
  Config restored. Switch is rebooting...
```

### reload

```
TL-SG108E# reload
  Proceed with reload? [y/N] y
  Reloading...
```

### write memory — save config to flash

Persist the running configuration to flash so changes survive a power cycle.
Run this after any configuration session.

```
TL-SG108E# write memory
  Configuration saved.
```

### write erase — factory reset

```
TL-SG108E# write erase
  Factory reset? ALL configuration will be lost. [y/N] y
  Factory reset initiated. Switch is rebooting...
```

---

## Config mode

Enter with `configure terminal` from exec mode.

### hostname

```
TL-SG108E(config)# hostname lab-sw-1
lab-sw-1(config)#
```

### ip address

```
TL-SG108E(config)# ip address 10.0.0.5 255.255.255.0
TL-SG108E(config)# no ip address dhcp         ← disable DHCP, keep existing IP
TL-SG108E(config)# ip address dhcp            ← enable DHCP
```

### spanning-tree (loop prevention)

```
TL-SG108E(config)# spanning-tree
TL-SG108E(config)# no spanning-tree
```

### igmp snooping

```
TL-SG108E(config)# igmp snooping
TL-SG108E(config)# igmp snooping report-suppression
TL-SG108E(config)# no igmp snooping
```

### led

```
TL-SG108E(config)# led
TL-SG108E(config)# no led
```

### qos mode

```
TL-SG108E(config)# qos mode port-based
TL-SG108E(config)# qos mode dot1p
```

### monitor session — port mirroring

```
TL-SG108E(config)# monitor session 1 destination interface gi8
TL-SG108E(config)# monitor session 1 source interface gi1 both
TL-SG108E(config)# monitor session 1 source interface gi2 rx
TL-SG108E(config)# monitor session 1 source interface gi3 tx
TL-SG108E(config)# no monitor session 1
```

Direction: `rx` (ingress), `tx` (egress), `both` (default).

### mtu-vlan

```
TL-SG108E(config)# mtu-vlan uplink gi8
TL-SG108E(config)# no mtu-vlan
```

### 802.1Q VLAN

```
TL-SG108E(config)# vlan 10
TL-SG108E(config-vlan-10)# name servers
TL-SG108E(config-vlan-10)# exit

TL-SG108E(config)# no vlan 10
```

### Port-based VLAN

```
TL-SG108E(config)# port-vlan mode enable
TL-SG108E(config)# port-vlan 2 members gi1,gi2,gi3
TL-SG108E(config)# no port-vlan 2
TL-SG108E(config)# no port-vlan mode      ← disable port-based VLAN mode
```

### username — change password

```
TL-SG108E(config)# username admin password oldpassword newpassword
  Password changed.
```

---

## Interface mode

Enter with `interface port <N>` or `interface range port <N>-<M>` from config
mode.

```
TL-SG108E(config)# interface port 3
TL-SG108E(config-if-gi3)#

TL-SG108E(config)# interface range port 1-4
TL-SG108E(config-if-gi1-4)#
```

### shutdown / no shutdown

```
TL-SG108E(config-if-gi3)# shutdown
TL-SG108E(config-if-gi3)# no shutdown
```

### speed

```
TL-SG108E(config-if-gi1)# speed auto
TL-SG108E(config-if-gi1)# speed 100 full
TL-SG108E(config-if-gi1)# speed 100 half
TL-SG108E(config-if-gi1)# speed 10 full
TL-SG108E(config-if-gi1)# speed 1000
```

### flowcontrol

```
TL-SG108E(config-if-gi1)# flowcontrol
TL-SG108E(config-if-gi1)# no flowcontrol
```

### switchport

```
# Access port (untagged on VLAN, PVID updated automatically)
TL-SG108E(config-if-gi1)# switchport access vlan 10

# Trunk port (add/remove tagged VLANs)
TL-SG108E(config-if-gi8)# switchport trunk allowed vlan add 10
TL-SG108E(config-if-gi8)# switchport trunk allowed vlan remove 10

# Set PVID explicitly
TL-SG108E(config-if-gi1)# switchport pvid 10
```

### channel-group — LAG

```
TL-SG108E(config-if-gi1)# channel-group 1
TL-SG108E(config-if-gi1)# no channel-group 1
```

### qos port-priority

Priority 1=Lowest, 2=Normal, 3=Medium, 4=Highest.

```
TL-SG108E(config-if-gi1)# qos port-priority 4
TL-SG108E(config-if-gi8)# qos port-priority 1
```

### bandwidth

```
TL-SG108E(config-if-gi3)# bandwidth ingress 1024
TL-SG108E(config-if-gi3)# bandwidth egress 512
TL-SG108E(config-if-gi3)# no bandwidth            ← remove both limits
TL-SG108E(config-if-gi3)# no bandwidth ingress    ← remove ingress limit only
```

Rate is in kbps; `0` means unlimited.

### storm-control

Rate indexes 1–12 map to: 64 / 128 / 256 / 512 / 1024 / 2048 / 4096 /
8192 / 16384 / 32768 / 65536 / 131072 kbps.

```
TL-SG108E(config-if-gi1)# storm-control broadcast rate 5
TL-SG108E(config-if-gi1)# storm-control multicast rate 4
TL-SG108E(config-if-gi1)# storm-control unknown-unicast rate 3
TL-SG108E(config-if-gi1)# storm-control all rate 5
TL-SG108E(config-if-gi1)# no storm-control
```

---

## VLAN mode

```
TL-SG108E(config)# vlan 10
TL-SG108E(config-vlan-10)# name servers
TL-SG108E(config-vlan-10)# exit
```

Only `name` is configurable here.  Port membership is managed with
`switchport` commands in interface mode.

---

## Common workflows

### Configure a trunk and access ports

Goal: port 8 = trunk, ports 5/6/7 = access on VLANs 5/6/7.

```
conf t

vlan 5
 name vlan5
!
vlan 6
 name vlan6
!
vlan 7
 name vlan7
!

interface port 5
 switchport access vlan 5
!
interface port 6
 switchport access vlan 6
!
interface port 7
 switchport access vlan 7
!
interface port 8
 switchport trunk allowed vlan add 5
 switchport trunk allowed vlan add 6
 switchport trunk allowed vlan add 7
!
end

show vlan
write memory
```

### Add a new access port to an existing trunk

Goal: add port 4 untagged on VLAN 4, include VLAN 4 on the trunk at port 8.

```
conf t
vlan 4
 exit
interface port 4
 switchport access vlan 4
 exit
interface port 8
 switchport trunk allowed vlan add 4
 exit
end
show vlan
write memory
```

### Rate-limit a port

```
conf t
interface port 3
 bandwidth ingress 1024
 bandwidth egress 512
end
show qos bandwidth
write memory
```

### Mirror a port for capture

```
conf t
monitor session 1 destination interface gi8
monitor session 1 source interface gi1 both
end
show port-mirror
write memory
```

### Set up storm control on all access ports

```
conf t
interface range port 1-7
 storm-control all rate 5
end
show qos storm
write memory
```

### Back up and restore configuration

```
TL-SG108E# copy running-config /tmp/switch-backup-$(date +%Y%m%d).bin

TL-SG108E# copy /tmp/switch-backup-20250101.bin running-config
```

### Factory reset and reconfigure

```
TL-SG108E# write erase
```

After the switch reboots, reconnect at its factory default IP (192.168.0.1
with password `admin`) and reconfigure.
