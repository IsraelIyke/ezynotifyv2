"""Public-destination validation and a DNS resolver that pins validated IPs.

Both HTTP and intercepted browser requests use this resolver, so a DNS rebind
cannot replace a preflight-checked public address with a private destination.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit
from aiohttp.abc import AbstractResolver


class UnsafeDestination(ValueError):
    pass


def public_ip(value):
    address = ipaddress.ip_address(value.split('%')[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast and not address.is_unspecified


def validate_url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            raise UnsafeDestination('Only public HTTP/HTTPS pages without credentials are supported.')
        if parsed.port not in (None,80,443):
            raise UnsafeDestination('Custom destination ports are not supported.')
        host=parsed.hostname.rstrip('.').lower()
        if host=='localhost' or host.endswith(('.local','.internal','.localhost')) or '%' in host:
            raise UnsafeDestination('Local and internal destinations are not supported.')
        try:
            if not public_ip(host):
                raise UnsafeDestination('Private or reserved IP addresses are blocked.')
        except ValueError as error:
            if isinstance(error, UnsafeDestination):
                raise
            if '.' not in host:
                raise UnsafeDestination('A public website hostname is required.') from None
        return urlunsplit((parsed.scheme,parsed.netloc,parsed.path or '/',parsed.query,''))
    except (ValueError,TypeError) as error:
        if isinstance(error, UnsafeDestination):raise
        raise UnsafeDestination('Invalid website URL.') from None


class PublicResolver(AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        records = await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM,family=family)
        addresses = {r[4][0] for r in records}
        if not addresses or any(not public_ip(address) for address in addresses):
            raise UnsafeDestination('The hostname resolves to a private or reserved address.')
        return [dict(hostname=host,host=record[4][0],port=port,family=record[0],proto=record[2],flags=socket.AI_NUMERICHOST) for record in records]

    async def close(self):
        pass
