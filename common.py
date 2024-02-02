from dataclasses import dataclass
import ipaddress
from threading import local
from typing import List, Optional, Set, Tuple, TypeVar
import host
import json


T = TypeVar("T")


def str_to_list(input_str: str) -> List[int]:
    result: Set[int] = set()
    parts = input_str.split(',')

    for part in parts:
        if '-' in part:
            start, end = map(int, part.split('-'))
            result.update(range(start, end + 1))
        else:
            result.add(int(part))

    return sorted(result)


class RangeList:
    _range: List[Tuple[bool, List[int]]] = []
    initial_values: Optional[List[int]] = None

    def __init__(self, initial_values: Optional[List[int]] = None):
        self.initial_values = initial_values

    def _append(self, l: List[int], expand: bool) -> None:
        self._range.append((expand, l))

    def include(self, l: List[int]) -> None:
        self._append(l, True)

    def exclude(self, l: List[int]) -> None:
        self._append(l, False)

    def filter_list(self, initial: List[T]) -> List[T]:
        applied = set(range(len(initial)))
        if self.initial_values is not None:
            applied &= set(self.initial_values)

        for expand, l in self._range:
            if expand:
                applied = applied | set(l)
            else:
                applied = applied - set(l)
        return [initial[x] for x in sorted(applied) if x < len(initial)]


@dataclass
class IPRouteAddressInfoEntry:
    family: str
    local: str


@dataclass
class IPRouteAdressEntry:
    ifindex: int
    ifname: str
    flags: List[str]
    master: Optional[str]
    addr_info: List[IPRouteAddressInfoEntry]


def ipa(host: host.Host) -> str:
    return host.run("ip -json a").out


def ipa_to_entries(input: str) -> List[IPRouteAdressEntry]:
    j = json.loads(input)
    ret: List[IPRouteAdressEntry] = []
    for e in j:
        addr_infos = []
        for addr in e["addr_info"]:
            addr_infos.append(IPRouteAddressInfoEntry(addr["family"], addr["local"]))

        master = e["master"] if "master" in e else None

        ret.append(IPRouteAdressEntry(e["ifindex"], e["ifname"], e["flags"], master, addr_infos))
    return ret


def ipr(host: host.Host) -> str:
    return host.run("ip -json r").out


@dataclass
class IPRouteRouteEntry:
    dst: str
    dev: str


def ipr_to_entries(input: str) -> List[IPRouteRouteEntry]:
    j = json.loads(input)
    ret: List[IPRouteRouteEntry] = []
    for e in j:
        ret.append(IPRouteRouteEntry(e["dst"], e["dev"]))
    return ret


def ip_in_subnet(addr: str, subnet: str) -> bool:
    return ipaddress.ip_address(addr) in ipaddress.ip_network(subnet)


def extract_interfaces(input: str) -> List[str]:
    entries = ipa_to_entries(input)
    return [x.ifname for x in entries]


def find_port(host: host.Host, port_name: str) -> Optional[IPRouteAdressEntry]:
    entries = ipa_to_entries(ipa(host))
    for entry in entries:
        if entry.ifname == port_name:
            return entry
    return None


def route_to_port(host: host.Host, route: str) -> Optional[str]:
    entries = ipr_to_entries(ipr(host))
    for e in entries:
        if e.dst == route:
            return e.dev
    return None


def port_to_ip(host: host.Host, port_name: str) -> Optional[str]:
    entries = ipa_to_entries(ipa(host))
    for entry in entries:
        if entry.ifname == port_name:
            for addr in entry.addr_info:
                if addr.family == "inet":
                    return addr.local
    return None


def carrier_no_addr(host: host.Host) -> List[IPRouteAdressEntry]:
    def carrier_no_addr(intf: IPRouteAdressEntry) -> bool:
        return len(intf.addr_info) == 0 and "NO-CARRIER" not in intf.flags

    entries = ipa_to_entries(ipa(host))

    return [x for x in entries if carrier_no_addr(x)]
