#!/usr/bin/env python3
"""Quick smoke-test of read operations against the live switch."""

import sys
sys.path.insert(0, '/home/jfrancis/tplink')

from tplink_switch import Switch, _bits_to_ports

HOST     = '10.1.1.239'
USERNAME = 'admin'
PASSWORD = 'b1gsecret'

def hr(title):
    print(f'\n{"=" * 60}')
    print(f'  {title}')
    print('=' * 60)

with Switch(HOST, USERNAME, PASSWORD) as sw:

    hr('System Info')
    info = sw.get_system_info()
    print(info)

    hr('IP Settings')
    ip = sw.get_ip_settings()
    print(ip)

    hr('LED state')
    print('LEDs on:', sw.get_led())

    hr('Port Settings')
    for p in sw.get_port_settings():
        print(p)

    hr('Port Statistics')
    for s in sw.get_port_statistics():
        print(f'Port {s.port:2d}: TX={s.tx_pkts:8d}  RX={s.rx_pkts:8d}')

    hr('Loop Prevention')
    print('Enabled:', sw.get_loop_prevention())

    hr('IGMP Snooping')
    igmp = sw.get_igmp_snooping()
    print(igmp)

    hr('Port Mirror')
    m = sw.get_port_mirror()
    print(m)

    hr('Port Trunk (LAG)')
    t = sw.get_port_trunk()
    print(t)

    hr('MTU VLAN')
    mv = sw.get_mtu_vlan()
    print(mv)

    hr('Port-based VLAN')
    enabled, vlans = sw.get_port_vlan()
    print('Enabled:', enabled)
    for v in vlans:
        ports = _bits_to_ports(v.members)
        print(f'  VID={v.vid}  ports={ports}')

    hr('802.1Q VLANs')
    enabled, vlans = sw.get_dot1q_vlans()
    print('Enabled:', enabled)
    for v in vlans:
        t_ports = _bits_to_ports(v.tagged_members)
        u_ports = _bits_to_ports(v.untagged_members)
        print(f'  VID={v.vid} name={v.name!r}  tagged={t_ports}  untagged={u_ports}')

    hr('PVIDs')
    pvids = sw.get_pvids()
    for i, pvid in enumerate(pvids):
        print(f'  Port {i+1}: PVID={pvid}')

    hr('QoS')
    mode, qos_ports = sw.get_qos_settings()
    print('Mode:', mode)
    for qp in qos_ports:
        print(f'  {qp}')

    hr('Bandwidth Control')
    for b in sw.get_bandwidth_control():
        print(f'  Port {b.port}: ingress={b.ingress_rate} kbps  egress={b.egress_rate} kbps')

    hr('Storm Control')
    for s in sw.get_storm_control():
        print(f'  Port {s.port}: BC={s.broadcast_rate}  MC={s.multicast_rate}  UU={s.unknown_unicast_rate}')
