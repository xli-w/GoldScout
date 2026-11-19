"""Opt-in IPv4 DNS-server override for remote connection hostnames."""

import ipaddress
import socket

from proxy_config import configured_connection_values


_DNS_QUERY_LIFETIME = 2.0
_MAX_ADDRESSES = 32


class DNSConfigurationError(ValueError):
    pass


def resolve_host(host, port, *, type=socket.SOCK_STREAM):
    value = configured_connection_values("TRADER_DNS_IPV4")["TRADER_DNS_IPV4"]
    if not isinstance(value, str):
        raise DNSConfigurationError("TRADER_DNS_IPV4 must be an IPv4 DNS server address")
    value = value.strip()
    if not value:
        return socket.getaddrinfo(host, port, type=type)

    try:
        server = ipaddress.ip_address(value)
    except ValueError:
        raise DNSConfigurationError("TRADER_DNS_IPV4 must be an IPv4 DNS server address") from None
    if (not isinstance(server, ipaddress.IPv4Address) or server.is_unspecified
            or server.is_multicast or server.is_reserved):
        raise DNSConfigurationError("TRADER_DNS_IPV4 must be a unicast IPv4 DNS server address")
    if type != socket.SOCK_STREAM:
        raise DNSConfigurationError("TRADER_DNS_IPV4 supports stream connections only")

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return socket.getaddrinfo(host, port, type=type, flags=socket.AI_NUMERICHOST)

    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        raise DNSConfigurationError("TRADER_DNS_IPV4 requires the declared dnspython dependency") from None

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [str(server)]
    resolver.timeout = 1.0
    records = []
    for record_type, family in (("A", socket.AF_INET), ("AAAA", socket.AF_INET6)):
        try:
            answer = resolver.resolve(host, record_type, lifetime=_DNS_QUERY_LIFETIME, search=False)
        except dns.resolver.NoAnswer:
            continue
        except dns.exception.DNSException:
            raise socket.gaierror(socket.EAI_FAIL, "Configured DNS resolution failed") from None
        for item in answer:
            address = item.address
            parsed = ipaddress.ip_address(address)
            if (family == socket.AF_INET) != (parsed.version == 4):
                raise socket.gaierror(socket.EAI_FAIL, "Configured DNS returned an invalid address")
            socket_address = (address, port) if family == socket.AF_INET else (address, port, 0, 0)
            records.append((family, type, socket.IPPROTO_TCP, "", socket_address))
            if len(records) > _MAX_ADDRESSES:
                raise socket.gaierror(socket.EAI_FAIL, "Configured DNS returned too many addresses")
    if not records:
        raise socket.gaierror(socket.EAI_NONAME, "Configured DNS returned no addresses")
    return records
