"""Where an LLM endpoint actually is, so mature mode can say what it is doing.

Mature mode with an endpoint that leaves this machine means the prompts go to a
third party, which can keep them, refuse the answer, or act on the account that
sent them. Whether that is happening cannot be read off the provider name:
``remote`` is LM Studio on this machine in one install and a hosted gateway in
another. What decides is where the endpoint is.

This module answers only that question. It warns; it does not switch anything,
because changing the provider behind the user's back would fight every rule that
is keyed on the provider, mature mode included.

Classification is textual and never resolves DNS -- a name with no dot cannot be
a public host, loopback and the private ranges are this network, and everything
else counts as off-machine. An ambiguous address is deliberately treated as
off-machine, which is the direction that warns instead of staying quiet.
"""

import ipaddress
from collections.abc import Mapping
from urllib.parse import urlsplit

# Names that mean this machine without needing a lookup.
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain"})

# Providers whose endpoint is theirs, whatever URL is configured.
_HOSTED_PROVIDERS = frozenset({"openai", "anthropic"})


def endpoint_host(url) -> str:
    """The host in a base URL, without userinfo, port or brackets."""

    text = str(url or "").strip()
    if not text:
        return ""
    # Accept a bare "host:port" as well as a full URL.
    parsed = urlsplit(text if "//" in text else f"//{text}")
    return (parsed.hostname or "").strip("[]").casefold()


def is_local_endpoint(url) -> bool:
    """True when this URL cannot leave this machine or the local network."""

    host = endpoint_host(url)
    if not host:
        return False
    if host in _LOOPBACK_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A single-label name is mDNS or a container alias, never a public FQDN.
        return "." not in host
    return bool(address.is_loopback or address.is_private or address.is_link_local)


def routes_off_machine(provider, remote_url: str = "") -> bool:
    """True when a call leaves this machine for a service someone else runs."""

    name = str(provider or "").strip().casefold()
    if name in _HOSTED_PROVIDERS:
        return True
    if name == "remote":
        return not is_local_endpoint(remote_url)
    return False


def mature_mode_enabled(services) -> bool:
    """Read the mature-mode flag out of a settings mapping."""

    if not isinstance(services, Mapping):
        return False
    return bool(services.get("nsfw_mode", False))


def mature_endpoint_is_public(services) -> bool:
    """True when mature mode is on and an endpoint that answers is not on this machine.

    Two stages answer prompts: the pipeline's LLM, and the editing stage's when it was given
    an endpoint of its own. Both have to be asked. The second one is the case this missed:
    with the pipeline on a local model and corrections on a hosted API, mature prompts travel
    to that service while the pipeline's own provider looks entirely innocuous -- which is
    exactly the configuration where nobody expects the warning to be missing.

    What the settings screen warns about. Note that this is the *only* thing that happens:
    nothing downgrades the provider, so the user has to be told rather than protected from
    the provider they chose.
    """

    if not mature_mode_enabled(services):
        return False
    remote_url = services.get("llm_remote_url", "")
    stages = (services.get("llm_provider", "local"), services.get("revision_llm_provider", ""))
    return any(
        routes_off_machine(stage, remote_url) for stage in stages if str(stage or "").strip()
    )
