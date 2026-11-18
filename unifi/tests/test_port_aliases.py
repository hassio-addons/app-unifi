"""Verify the fixed compatibility ports, including bidirectional UDP replies."""

import os
from pathlib import Path
import ipaddress
import json
import subprocess
import time
import unittest
from uuid import uuid4

from docker_fixture import run_container

APP = Path(__file__).resolve().parents[1]


class PortAliasBoundaryTest(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
    )
    def test_repeated_broadcasts_and_second_peer_receive_only_their_own_replies(self):
        image = os.environ["UNIFI_MONGO_TEST_IMAGE"]
        prefix = "unifi-broadcast-" + uuid4().hex
        network, proxy = prefix + "-net", prefix + "-proxy"
        clients = [prefix + "-peer1", prefix + "-peer2"]

        def docker(*args, timeout=30, check=True):
            return subprocess.run(
                ["docker", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=check,
            ).stdout

        try:
            docker("network", "create", "--internal", network)
            details = json.loads(docker("network", "inspect", network))[0]
            self.assertTrue(details["Internal"])
            subnet = ipaddress.ip_network(details["IPAM"]["Config"][0]["Subnet"])
            broadcast = str(subnet.broadcast_address)
            docker(
                "run",
                "-d",
                "--name",
                proxy,
                "--network",
                network,
                "--cap-drop=ALL",
                "--entrypoint=bash",
                image,
                "-euc",
                r"""
python3 - <<'UDP' &
import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.bind(('127.0.0.1',10003))
with open('/run/received-broadcasts','a',buffering=1) as log:
    while True:
        body,peer=s.recvfrom(4096)
        log.write(body.decode()+'\n')
        s.sendto(body,peer)
UDP
exec /usr/local/bin/unifi-os-port-alias 10001
""",
            )
            time.sleep(0.5)
            program = r"""
import socket,sys
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
s.bind(('0.0.0.0',0))
s.settimeout(3)
for index in range(2):
    body=(sys.argv[2]+'-'+str(index)).encode()
    s.sendto(body,(sys.argv[1],10001))
    response,peer=s.recvfrom(4096)
    assert response==body
    assert peer[1]==10001
s.settimeout(0.3)
try:
    extra=s.recvfrom(4096)
except socket.timeout:
    pass
else:
    raise AssertionError('Unexpected duplicate/misdirected reply')
s.close()
"""
            for index, name in enumerate(clients):
                docker(
                    "run",
                    "--rm",
                    "--name",
                    name,
                    "--network",
                    network,
                    "--cap-drop=ALL",
                    "--entrypoint=python3",
                    image,
                    "-c",
                    program,
                    broadcast,
                    "peer" + str(index),
                    timeout=15,
                )
            observed = docker(
                "exec", proxy, "cat", "/run/received-broadcasts"
            ).splitlines()
            self.assertEqual(observed, ["peer0-0", "peer0-1", "peer1-0", "peer1-1"])
        finally:
            for name in (proxy, *clients):
                docker("rm", "--force", name, check=False)
            docker("network", "rm", network, check=False)

    def test_alias_service_has_no_capabilities_or_privilege_escalation(self):
        text = (
            APP / "rootfs/etc/systemd/system/unifi-os-port-alias@.service"
        ).read_text()
        for setting in (
            "DynamicUser=yes",
            "NoNewPrivileges=yes",
            "CapabilityBoundingSet=",
            "ProtectSystem=strict",
            "RestrictNamespaces=yes",
        ):
            self.assertIn(setting, text)
        assignments = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith("CapabilityBoundingSet=")
        ]
        self.assertEqual(assignments, ["CapabilityBoundingSet="])

    def test_udp_lifetime_and_all_listener_children_are_bounded(self):
        text = (APP / "rootfs/usr/local/bin/unifi-os-port-alias").read_text()
        self.assertEqual(text.count("max-children="), 3)
        self.assertIn("options=(-T 30)", text)
        self.assertIn("UDP4-LISTEN:10001,reuseaddr,fork,max-children=64", text)
        self.assertEqual(text.count("options=()"), 2)
        self.assertIn('socat "${options[@]}"', text)

    @unittest.skipUnless(
        os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
    )
    def test_tcp_and_udp_aliases_relay_only_to_fixed_local_vendor_ports(self):
        run_container(
            r"""
if /usr/local/bin/unifi-os-port-alias '8443; touch /run/evil'; then exit 1; fi
test ! -e /run/evil
socat TCP-LISTEN:11443,reuseaddr,fork EXEC:/bin/cat &
socat TCP-LISTEN:8444,reuseaddr,fork EXEC:/bin/cat &
# Model a real UDP server: one bound socket replies to every peer/datagram.
# A forked RECVFROM/cat mock does not preserve repeated-peer behavior.
python3 - <<'UDP' &
import socket
server=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
server.bind(('127.0.0.1',10003))
while True:
    data,peer=server.recvfrom(4096)
    server.sendto(data,peer)
UDP
/usr/local/bin/unifi-os-port-alias 8443 &
/usr/local/bin/unifi-os-port-alias 8843 &
/usr/local/bin/unifi-os-port-alias 10001 &
for port in 8443 8843; do
    for ((attempt=0;attempt<50;attempt++)); do
        if reply=$(printf 'retained-tcp-binding\n' | socat -T2 - TCP:127.0.0.1:$port 2>/dev/null); then break; fi
        sleep 0.1
    done
    test "$reply" = retained-tcp-binding
done
for ((attempt=0;attempt<10;attempt++)); do
    reply=$(printf 'retained-udp-binding' | socat -T2 - UDP4:127.0.0.1:10001)
    [[ "$reply" != retained-udp-binding ]] || break
    sleep 0.1
done
test "$reply" = retained-udp-binding
python3 - <<'PY'
import socket
clients=[]
try:
    for index in range(3):
        client=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        client.settimeout(3)
        clients.append(client)
    for round in range(2):
        for index,client in enumerate(clients):
            message=f'peer-{index}-round-{round}'.encode()
            client.sendto(message,('127.0.0.1',10001))
            assert client.recvfrom(128)[0]==message
finally:
    for client in clients:client.close()
PY
""",
            timeout=30,
        )
