import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location('setup_network', Path(__file__).parents[1] / 'scripts/setup_network.py')
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


def test_install_profiles_do_not_disconnect_existing_wifi_or_add_default_routes():
    for role in ('jetson', 'pi'):
        config = network.profile(role, 'wlan0', 'abcdefghijklmnopqrstuvwx')
        assert config['connection']['autoconnect'] == 'false'
        assert config['ipv4']['never-default'] == 'true'
        assert 'gateway' not in config['ipv4']
        assert config['wifi-security']['key-mgmt'] == 'wpa-psk'
    assert network.admin_profile('enP8p1s0')['ipv4'] == {
        'method': 'manual', 'address1': '10.43.0.1/24', 'never-default': 'true'}


def test_robot_addresses_are_distinct_and_ap_uses_only_networkmanager_dhcp():
    assert network.profile('jetson', 'wlan0', 'abcdefghijklmnopqrstuvwx')['ipv4']['method'] == 'shared'
    assert network.profile('pi', 'wlan0', 'abcdefghijklmnopqrstuvwx')['ipv4']['method'] == 'manual'
    assert network.profile('jetson', 'wlan0', 'abcdefghijklmnopqrstuvwx')['ipv4']['address1'] == '10.42.0.1/24'
    assert network.profile('pi', 'wlan0', 'abcdefghijklmnopqrstuvwx')['ipv4']['address1'] == '10.42.0.2/24'


def test_network_configuration_is_replaced_atomically(tmp_path):
    path = tmp_path / 'connection'
    path.write_text('previous')
    network.write(path, 'complete new profile')
    assert path.read_text() == 'complete new profile'
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]
