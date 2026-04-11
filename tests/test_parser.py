"""
Unit tests for the JS-parsing helpers in sg108e.py.

These tests require no network access and no live switch.
"""

import sys
sys.path.insert(0, '/home/jfrancis/tplink')

import pytest
from sg108e import _extract_top_script, _extract_var, _js_to_py, _bits_to_ports, _ports_to_bits


# ---------------------------------------------------------------------------
# _js_to_py
# ---------------------------------------------------------------------------

class TestJsToPy:
    def test_integer(self):
        assert _js_to_py('42') == 42

    def test_negative_integer(self):
        assert _js_to_py('-5') == -5

    def test_float(self):
        assert _js_to_py('3.14') == pytest.approx(3.14)

    def test_hex_literal(self):
        assert _js_to_py('0xFF') == 255
        assert _js_to_py('0xff') == 255
        assert _js_to_py('0x0F') == 15
        assert _js_to_py('0x100') == 256

    def test_double_quoted_string(self):
        assert _js_to_py('"hello"') == 'hello'

    def test_single_quoted_string(self):
        assert _js_to_py("'world'") == 'world'

    def test_empty_string_double(self):
        assert _js_to_py('""') == ''

    def test_empty_string_single(self):
        assert _js_to_py("''") == ''

    def test_array_ints(self):
        assert _js_to_py('[1, 2, 3]') == [1, 2, 3]

    def test_array_empty(self):
        assert _js_to_py('[]') == []

    def test_array_strings(self):
        assert _js_to_py('["a", "b"]') == ['a', 'b']

    def test_array_single_quoted_strings(self):
        assert _js_to_py("['Default', 'Uplink']") == ['Default', 'Uplink']

    def test_array_mixed(self):
        result = _js_to_py('[1, "two", 3]')
        assert result == [1, 'two', 3]

    def test_array_with_hex(self):
        # Hex conversion happens before array parsing
        assert _js_to_py('[0xFF, 0x01]') == [255, 1]

    def test_object_simple(self):
        result = _js_to_py('{state: 1, count: 0}')
        assert result == {'state': 1, 'count': 0}

    def test_object_quoted_keys(self):
        result = _js_to_py('{"state": 1}')
        assert result == {'state': 1}

    def test_object_nested_array(self):
        result = _js_to_py('{vids: [1, 2], names: ["a", "b"]}')
        assert result == {'vids': [1, 2], 'names': ['a', 'b']}

    def test_object_single_quoted_values(self):
        result = _js_to_py("{names: ['Default', '']}")
        assert result == {'names': ['Default', '']}

    def test_bare_word(self):
        # Unparseable values are returned as-is
        result = _js_to_py('undefined')
        assert result == 'undefined'

    def test_whitespace_stripped(self):
        assert _js_to_py('  42  ') == 42

    def test_hex_inside_array(self):
        result = _js_to_py('[0xFF, 0x00, 0x0F]')
        assert result == [255, 0, 15]


# ---------------------------------------------------------------------------
# _extract_var
# ---------------------------------------------------------------------------

class TestExtractVar:
    # Helper: wrap content in minimal HTML
    @staticmethod
    def html(script_content):
        return f'<html><script>\n{script_content}\n</script></html>'

    def test_simple_integer(self):
        h = self.html('var lpEn = 1;')
        assert _extract_var(h, 'lpEn') == 1

    def test_simple_zero(self):
        h = self.html('var lpEn = 0;')
        assert _extract_var(h, 'lpEn') == 0

    def test_simple_string(self):
        h = self.html('var sysName = "MySwitch";')
        assert _extract_var(h, 'sysName') == 'MySwitch'

    def test_array_literal(self):
        h = self.html('var pPri = [1, 2, 3, 4];')
        assert _extract_var(h, 'pPri') == [1, 2, 3, 4]

    def test_object_literal(self):
        h = self.html('var ip_ds = {state: 0, ipStr: ["10.1.1.1"]};')
        result = _extract_var(h, 'ip_ds')
        assert result == {'state': 0, 'ipStr': ['10.1.1.1']}

    def test_missing_var_returns_none(self):
        h = self.html('var other = 1;')
        assert _extract_var(h, 'missing') is None

    def test_hex_in_array(self):
        h = self.html('var mbrs = [0xFF, 0x00, 0x0F];')
        assert _extract_var(h, 'mbrs') == [255, 0, 15]

    def test_new_array_syntax(self):
        h = self.html('var foo = new Array(10, 20, 30);')
        assert _extract_var(h, 'foo') == [10, 20, 30]

    def test_new_array_empty(self):
        h = self.html('var foo = new Array();')
        assert _extract_var(h, 'foo') == []

    def test_new_array_single(self):
        h = self.html('var foo = new Array(5);')
        assert _extract_var(h, 'foo') == [5]

    def test_multi_line_object(self):
        h = self.html(
            'var all_info = {\n'
            '  state:[1,1,1,1,1,1,1,1,0,0],\n'
            '  spd_cfg:[1,1,1,1,1,1,1,1,0,0]\n'
            '};'
        )
        result = _extract_var(h, 'all_info')
        assert result is not None
        assert result['state'] == [1, 1, 1, 1, 1, 1, 1, 1, 0, 0]
        assert result['spd_cfg'] == [1, 1, 1, 1, 1, 1, 1, 1, 0, 0]

    def test_does_not_match_prefix(self):
        # 'lpEnabled' should not match var named 'lpEn'
        h = self.html('var lpEnabled = 1;\nvar lpEn = 0;')
        assert _extract_var(h, 'lpEn') == 0

    def test_exact_name_match(self):
        h = self.html('var qosMode = 2;\nvar qosModeExtra = 99;')
        assert _extract_var(h, 'qosMode') == 2

    def test_scalar_no_semicolon(self):
        # Some pages may omit the semicolon
        h = self.html('var led = 1\n')
        assert _extract_var(h, 'led') == 1

    def test_object_with_bare_integer_keys_like_arrays(self):
        h = self.html(
            'var trunk_conf = {maxTrunkNum:2, portNum:8, '
            'portStr_g1:[0,0,0,0,0,0,0,0], portStr_g2:[0,0,0,0,0,0,0,0]};'
        )
        result = _extract_var(h, 'trunk_conf')
        assert result['maxTrunkNum'] == 2
        assert result['portNum'] == 8
        assert result['portStr_g1'] == [0, 0, 0, 0, 0, 0, 0, 0]

    def test_single_quoted_string_list_in_object(self):
        # This is the names array pattern from the real switch
        h = self.html("var qvlan_ds = {state:1, count:1, vids:[1], names:['Default'], "
                      "tagMbrs:[0], untagMbrs:[255]};")
        result = _extract_var(h, 'qvlan_ds')
        assert result is not None
        assert result['names'] == ['Default']
        assert result['untagMbrs'] == [255]

    def test_igmp_ds_pattern(self):
        h = self.html('var igmp_ds = {state:0, suppressionState:0, count:0};')
        result = _extract_var(h, 'igmp_ds')
        assert result == {'state': 0, 'suppressionState': 0, 'count': 0}

    def test_multiple_vars(self):
        h = self.html('var max_port_num = 8;\nvar all_info = {state:[1,0,0,0,0,0,0,0]};')
        assert _extract_var(h, 'max_port_num') == 8
        result = _extract_var(h, 'all_info')
        assert result['state'][0] == 1


# ---------------------------------------------------------------------------
# _extract_top_script
# ---------------------------------------------------------------------------

class TestExtractTopScript:
    def test_basic(self):
        html = '<html><script>var x = 1;</script></html>'
        assert 'var x = 1;' in _extract_top_script(html)

    def test_no_script_returns_empty(self):
        html = '<html><body>no script</body></html>'
        assert _extract_top_script(html) == ''

    def test_returns_first_script_only(self):
        html = '<html><script>var first = 1;</script><script>var second = 2;</script></html>'
        result = _extract_top_script(html)
        assert 'first' in result
        assert 'second' not in result

    def test_script_with_type_attr(self):
        html = '<html><script type="text/javascript">var x = 1;</script></html>'
        assert 'var x = 1;' in _extract_top_script(html)


# ---------------------------------------------------------------------------
# _bits_to_ports
# ---------------------------------------------------------------------------

class TestBitsToPorts:
    def test_single_port_1(self):
        assert _bits_to_ports(0b00000001) == [1]

    def test_single_port_8(self):
        assert _bits_to_ports(0b10000000) == [8]

    def test_all_ports(self):
        assert _bits_to_ports(0xFF) == [1, 2, 3, 4, 5, 6, 7, 8]

    def test_no_ports(self):
        assert _bits_to_ports(0x00) == []

    def test_ports_1_and_8(self):
        assert _bits_to_ports(0b10000001) == [1, 8]

    def test_ports_2_4_6(self):
        mask = (1 << 1) | (1 << 3) | (1 << 5)  # bits for ports 2, 4, 6
        assert _bits_to_ports(mask) == [2, 4, 6]

    def test_result_is_sorted(self):
        # Result must always be in ascending order
        result = _bits_to_ports(0b10000001)
        assert result == sorted(result)

    def test_custom_port_count_4(self):
        result = _bits_to_ports(0xFF, port_count=4)
        assert result == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# _ports_to_bits
# ---------------------------------------------------------------------------

class TestPortsToBits:
    def test_port_1(self):
        assert _ports_to_bits([1]) == 0b00000001

    def test_port_8(self):
        assert _ports_to_bits([8]) == 0b10000000

    def test_all_ports(self):
        assert _ports_to_bits([1, 2, 3, 4, 5, 6, 7, 8]) == 0xFF

    def test_empty_list(self):
        assert _ports_to_bits([]) == 0

    def test_ports_1_and_8(self):
        assert _ports_to_bits([1, 8]) == 0b10000001

    def test_ports_2_4_6(self):
        expected = (1 << 1) | (1 << 3) | (1 << 5)
        assert _ports_to_bits([2, 4, 6]) == expected


# ---------------------------------------------------------------------------
# Round-trip: _bits_to_ports ↔ _ports_to_bits
# ---------------------------------------------------------------------------

class TestBitsPortsRoundTrip:
    @pytest.mark.parametrize('ports', [
        [1],
        [8],
        [1, 8],
        [1, 2, 3, 4, 5, 6, 7, 8],
        [],
        [2, 4, 6],
        [1, 3, 5, 7],
    ])
    def test_round_trip_ports_to_bits_to_ports(self, ports):
        mask = _ports_to_bits(ports)
        result = _bits_to_ports(mask)
        assert result == sorted(ports)

    @pytest.mark.parametrize('mask', [0x00, 0xFF, 0x01, 0x80, 0x55, 0xAA])
    def test_round_trip_bits_to_ports_to_bits(self, mask):
        ports = _bits_to_ports(mask)
        result = _ports_to_bits(ports)
        assert result == mask
