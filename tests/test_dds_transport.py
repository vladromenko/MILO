from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from milo_next import dds_tunnel


@pytest.mark.parametrize('role', ['client', 'server'])
def test_dds_profiles_use_only_loopback_tcp(role):
    root = ET.parse(Path(__file__).resolve().parents[1] / f'config/dds-{role}.xml')
    ns = {'d': 'http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles'}
    assert root.find('.//d:type', ns).text == 'TCPv4'
    assert root.find('.//d:useBuiltinTransports', ns).text == 'false'
    assert {e.text for e in root.findall('.//d:address', ns)} == {'127.0.0.1'}
    assert root.find('.//d:participant', ns).attrib['is_default_profile'] == 'true'


def test_dds_forward_is_authenticated_local_only(monkeypatch):
    monkeypatch.setattr(dds_tunnel, 'ssh_args', lambda: ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', 'edge'])
    seen = []
    monkeypatch.setattr(dds_tunnel.os, 'execvp', lambda *args: seen.append(args))
    dds_tunnel.main()
    args = seen[0][1]
    assert '127.0.0.1:15150:127.0.0.1:15150' in args
    assert '-N' in args and '-T' in args and 'StrictHostKeyChecking=yes' in args
