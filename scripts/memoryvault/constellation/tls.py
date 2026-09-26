"""The family certificate authority (V2 CP2, spec A6: HTTPS-everywhere default).

The installer runs `mvault tls init` once. It creates a small CA that only this
family's devices trust, and a server certificate for this box's names and LAN
addresses. The server then answers HTTPS itself on the TLS port — no proxy to
install, and the PIN never crosses Wi-Fi in the clear.

Phones and TVs install `ca.pem` once (served at /ca.pem) to trust it.

Keys are written 0600 and never leave this directory. Re-running init reuses
the CA (so devices keep trusting it) and reissues the server certificate only
when its names change or it is within 30 days of expiry.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .. import config

CA_DAYS = 3650
SERVER_DAYS = 397          # browsers reject leaf certs valid for longer
RENEW_WITHIN_DAYS = 30


def tls_dir() -> Path:
    override = os.environ.get("MEMORYVAULT_TLS_DIR")
    return Path(override) if override else config.LIBRARY_ROOT / ".tls"


def paths(d: Path | None = None) -> dict[str, Path]:
    d = d or tls_dir()
    return {"ca_cert": d / "ca.pem", "ca_key": d / "ca.key",
            "cert": d / "server.pem", "key": d / "server.key"}


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _write_private(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _write_public(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    os.chmod(path, 0o644)


def _key_pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def default_names() -> tuple[list[str], list[str]]:
    host = socket.gethostname()
    names = sorted({host, f"{host}.local", "localhost"})
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ips.add(str(info[4][0]))
    except OSError:
        pass
    # the address the default route uses, without sending a packet
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return names, sorted(i for i in ips if not i.startswith("127.") or i == "127.0.0.1")


def _load_ca(p):
    key = serialization.load_pem_private_key(p["ca_key"].read_bytes(), password=None)
    cert = x509.load_pem_x509_certificate(p["ca_cert"].read_bytes())
    return key, cert


def _make_ca(p, family_name: str):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                         f"{family_name} family (Constellation)")])
    now = _now()
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True,
                                         crl_sign=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False,
                                         decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            .sign(key, hashes.SHA256()))
    _write_private(p["ca_key"], _key_pem(key))
    _write_public(p["ca_cert"], cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


def _sans(cert) -> tuple[set[str], set[str]]:
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set(), set()
    return (set(ext.get_values_for_type(x509.DNSName)),
            {str(i) for i in ext.get_values_for_type(x509.IPAddress)})


def _needs_server_cert(p, names, ips) -> bool:
    if not (p["cert"].exists() and p["key"].exists()):
        return True
    cert = x509.load_pem_x509_certificate(p["cert"].read_bytes())
    have_dns, have_ip = _sans(cert)
    if set(names) != have_dns or set(ips) != have_ip:
        return True
    return cert.not_valid_after_utc - _now() < dt.timedelta(days=RENEW_WITHIN_DAYS)


def _make_server_cert(p, ca_key, ca_cert, names, ips):
    key = ec.generate_private_key(ec.SECP256R1())
    now = _now()
    san = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=SERVER_DAYS))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_private(p["key"], _key_pem(key))
    _write_public(p["cert"], cert.public_bytes(serialization.Encoding.PEM))


def init(names: list[str] | None = None, ips: list[str] | None = None,
         family_name: str = "Our", d: Path | None = None) -> dict:
    """Create or refresh the family CA + server cert. Returns what happened."""
    p = paths(d)
    dn, di = default_names()
    names = sorted(set(names or dn))
    ips = sorted(set(ips or di))
    for i in ips:
        ipaddress.ip_address(i)  # reject garbage before writing anything
    created_ca = False
    if p["ca_key"].exists() and p["ca_cert"].exists():
        ca_key, ca_cert = _load_ca(p)
    else:
        ca_key, ca_cert = _make_ca(p, family_name)
        created_ca = True
    reissued = created_ca or _needs_server_cert(p, names, ips)
    if reissued:
        _make_server_cert(p, ca_key, ca_cert, names, ips)
    return {"created_ca": created_ca, "reissued_server_cert": reissued,
            "names": names, "ips": ips, "dir": str(p["ca_cert"].parent)}


def configured(d: Path | None = None) -> bool:
    p = paths(d)
    return p["cert"].exists() and p["key"].exists()


def server_context(d: Path | None = None):
    import ssl

    p = paths(d)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(p["cert"], p["key"])
    return ctx
