from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text()


def test_single_installer_exposes_hardware_roles_and_never_starts_robot():
    text = read('install.sh')
    assert 'jetson) ROLE=brain' in text
    assert 'pi) ROLE=edge' in text
    assert 'scripts/bootstrap.sh "$ROLE"' in text
    assert 'scripts/preflight.py "$ROLE" --software-only' in text
    assert './milo start' not in text
    assert 'systemctl start' not in text


def test_installer_checks_vendor_platforms_before_building():
    text = read('scripts/install_system.sh')
    assert '/usr/local/cuda/bin/nvcc' in text
    assert '/opt/ros/jazzy/setup.bash' in text
    assert 'hailortcli' in text
    assert '/dev/hailo0' in text


def test_edge_agent_download_is_pinned_and_verified():
    text = read('scripts/install_edge_agent.sh')
    assert 'releases/download/v0.2.0/' in text
    assert 'e82ddda722bbcfb94afc494758c93f60c85bd092a8a03f903ca1ea2022445be8' in text
    assert 'sha256sum --check --status' in text
    assert 'check-libs' in text


def test_readme_has_hardware_before_fresh_install_and_both_commands():
    text = read('README.md')
    assert text.index('## Hardware') < text.index('## Install From A Fresh Clone')
    assert './install.sh jetson' in text
    assert './install.sh pi' in text
