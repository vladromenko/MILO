# Offline Network

## Topology

Jetson creates `MILO-NET` on `10.42.0.1/24`. Pi joins with `10.42.0.2/24`; a
phone receives another address from NetworkManager. The Jetson administration
Ethernet is `10.43.0.1/24`; a workstation may use `10.43.0.2/24`. Neither robot
profile becomes a default internet route.

## Staged Activation

Keep a verified wired SSH connection to Jetson throughout commissioning. The
same `config/network.json` must exist on both hosts.

```bash
# Jetson
cd /home/vlad/MILO
sudo python3 scripts/setup_network.py jetson --install

# Pi, while still reachable on the original network
cd /home/vlados/MILO
sudo python3 scripts/setup_network.py pi --install
```

Activate Jetson first and Pi second:

```bash
sudo python3 /etc/milo-network/setup_network.py jetson --activate
sudo python3 /etc/milo-network/setup_network.py pi --activate
```

Each activation arms a 180-second rollback. During that window verify:

```bash
ssh vlad@10.43.0.1 true
ssh vlados@10.42.0.2 true
```

Connect a phone to `MILO-NET`, keep the connection when it reports no internet,
and open `http://10.42.0.1/`. Confirm only after all three paths work:

```bash
sudo python3 /etc/milo-network/setup_network.py jetson --confirm
sudo python3 /etc/milo-network/setup_network.py pi --confirm
```

If any path fails, allow rollback or invoke `--rollback` on the affected host.
Do not confirm a partially working topology.

## Workstation SSH Aliases

Example `~/.ssh/config` entries:

```sshconfig
Host milo-jetson
    HostName 10.43.0.1
    User vlad

Host milo-pi
    HostName 10.42.0.2
    User vlados
    ProxyJump milo-jetson
```

On macOS, assign `10.43.0.2/24` to the connected USB Ethernet interface. Find
the interface with `networksetup -listallhardwareports`, then use its actual name:

```bash
sudo ifconfig en7 inet 10.43.0.2 netmask 255.255.255.0 alias
```

## Security Model

Pi runtime endpoints bind to loopback. Jetson reaches them through strict-host-key
SSH tunnels, and application HTTP requests require the shared bearer token. The
phone panel additionally uses an access code, CSRF token, same-origin checks,
and a strict session cookie. `MILO-NET` is still a local research network: rotate
credentials before public demonstrations and do not bridge it to an untrusted LAN.

## Cold-Boot Check

Disconnect the administration Mac, remove power from both computers, wait for
shutdown, and power both together. The expected result is:

1. `MILO-NET` appears without another router.
2. Pi joins as `10.42.0.2`.
3. The phone panel opens at `http://10.42.0.1/`.
4. MILO remains stopped until the operator presses **Start MILO**.
