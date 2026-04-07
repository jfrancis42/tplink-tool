#!/usr/bin/env python3
"""
Configure 802.1Q VLANs on the TL-SG108E:

  Port 8 → trunk (tagged member of VLANs 5, 6, 7)
  Port 5 → access, VLAN 5 (untagged)
  Port 6 → access, VLAN 6 (untagged)
  Port 7 → access, VLAN 7 (untagged)

Then read back the configuration and verify it matches.
"""

import sys
sys.path.insert(0, '/home/jfrancis/tplink')

from sg108e import Switch, _bits_to_ports

HOST     = '10.1.1.239'
USERNAME = 'admin'
PASSWORD = 'b1gsecret'

TRUNK_PORT = 8
VLANS = {
    5: 5,   # vlan_id: access_port
    6: 6,
    7: 7,
}

# ---------------------------------------------------------------------------
# Expected state after configuration
# ---------------------------------------------------------------------------
EXPECTED_VLANS = {
    vid: {
        'tagged':   [TRUNK_PORT],
        'untagged': [access_port],
    }
    for vid, access_port in VLANS.items()
}
EXPECTED_PVIDS = {port: vid for vid, port in VLANS.items()}


def configure(sw: Switch):
    print('Enabling 802.1Q VLAN mode...')
    sw.set_dot1q_enabled(True)

    for vid, access_port in VLANS.items():
        print(f'  Adding VLAN {vid}: port {access_port} untagged, port {TRUNK_PORT} tagged')
        sw.add_dot1q_vlan(
            vid=vid,
            tagged_ports=[TRUNK_PORT],
            untagged_ports=[access_port],
        )

    for vid, access_port in VLANS.items():
        print(f'  Setting port {access_port} PVID → {vid}')
        sw.set_pvid([access_port], vid)

    print('Configuration applied.')


def verify(sw: Switch) -> bool:
    print('\nReading back configuration...')
    ok = True

    # --- 802.1Q enabled? ---
    enabled, vlans = sw.get_dot1q_vlans()
    if not enabled:
        print('FAIL  802.1Q mode is not enabled')
        ok = False
    else:
        print('PASS  802.1Q mode enabled')

    # Build a lookup: vid → entry
    vlan_map = {v.vid: v for v in vlans}

    for vid, expected in EXPECTED_VLANS.items():
        if vid not in vlan_map:
            print(f'FAIL  VLAN {vid} not found on switch')
            ok = False
            continue

        entry = vlan_map[vid]
        actual_tagged   = sorted(_bits_to_ports(entry.tagged_members))
        actual_untagged = sorted(_bits_to_ports(entry.untagged_members))
        exp_tagged      = sorted(expected['tagged'])
        exp_untagged    = sorted(expected['untagged'])

        if actual_tagged == exp_tagged and actual_untagged == exp_untagged:
            print(f'PASS  VLAN {vid}: tagged={actual_tagged} untagged={actual_untagged}')
        else:
            print(f'FAIL  VLAN {vid}:')
            print(f'        tagged   expected={exp_tagged} actual={actual_tagged}')
            print(f'        untagged expected={exp_untagged} actual={actual_untagged}')
            ok = False

    # --- PVIDs ---
    pvids = sw.get_pvids()   # 0-based list, index 0 = port 1
    for port, expected_pvid in EXPECTED_PVIDS.items():
        actual_pvid = pvids[port - 1] if len(pvids) >= port else None
        if actual_pvid == expected_pvid:
            print(f'PASS  Port {port} PVID={actual_pvid}')
        else:
            print(f'FAIL  Port {port} PVID expected={expected_pvid} actual={actual_pvid}')
            ok = False

    return ok


def main():
    with Switch(HOST, USERNAME, PASSWORD) as sw:
        configure(sw)
        success = verify(sw)

    if success:
        print('\nAll checks passed.')
        sys.exit(0)
    else:
        print('\nOne or more checks FAILED.')
        sys.exit(1)


if __name__ == '__main__':
    main()
