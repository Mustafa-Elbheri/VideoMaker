"""Secure TLS configuration for update connections.

The updater must work on normal home connections as well as Windows machines
where an antivirus product or an organisation proxy installs its own trusted
certificate.  Python's bundled OpenSSL configuration is not always identical
to the Windows trust configuration in frozen applications, so the context
below combines:

* OpenSSL's/default Python trust locations.
* certifi's CA bundle when the package is available.
* The Windows ROOT and CA certificate stores when running on Windows.

Certificate verification and hostname checking always remain enabled.  There
is intentionally no insecure fallback.
"""

from __future__ import annotations

import ssl
import sys
import threading

try:
    import certifi
except Exception:  # Optional dependency; Windows trust remains available.
    certifi = None


_CONTEXT_LOCK = threading.RLock()
_CONTEXT = None
_CONTEXT_SOURCES = ()


def _new_client_context():
    """Create a compatible, verification-enforcing client context.

    ``ssl.create_default_context`` enables OpenSSL's strict verification flags
    on recent Python releases.  Some legitimate certificates issued by
    antivirus HTTPS inspection or organisation proxies are accepted by
    Windows but rejected by those stricter OpenSSL-only checks.  Starting from
    ``PROTOCOL_TLS_CLIENT`` retains certificate and hostname verification while
    allowing the explicitly trusted Windows roots to be used as Windows does.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    if hasattr(ssl, "TLSVersion"):
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _load_default_certificates(context, sources):
    try:
        context.load_default_certs(ssl.Purpose.SERVER_AUTH)
        sources.append("python_default")
    except Exception:
        # The frozen application may not have usable OpenSSL default paths.
        pass


def _load_certifi_certificates(context, sources):
    if certifi is None:
        return
    try:
        context.load_verify_locations(cafile=certifi.where())
        sources.append("certifi")
    except Exception:
        pass


def _windows_certificate_pem():
    enum_certificates = getattr(ssl, "enum_certificates", None)
    if not callable(enum_certificates):
        return "", 0

    pem_certificates = []
    seen = set()
    for store_name in ("ROOT", "CA"):
        try:
            certificates = enum_certificates(store_name)
        except Exception:
            continue
        for certificate, encoding_type, _trust in certificates:
            if encoding_type != "x509_asn":
                continue
            certificate = bytes(certificate)
            if certificate in seen:
                continue
            seen.add(certificate)
            try:
                pem_certificates.append(ssl.DER_cert_to_PEM_cert(certificate))
            except Exception:
                continue
    return "\n".join(pem_certificates), len(pem_certificates)


def _load_windows_certificates(context, sources):
    if not sys.platform.startswith("win"):
        return

    cadata, certificate_count = _windows_certificate_pem()
    if not cadata:
        return
    try:
        context.load_verify_locations(cadata=cadata)
    except Exception:
        return

    # Certificates in the Windows intermediate CA store are trusted by the
    # operating system.  Allowing a loaded intermediate to act as the trust
    # anchor matches that behaviour without weakening hostname verification.
    partial_chain = getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    if partial_chain:
        try:
            context.verify_flags |= partial_chain
        except Exception:
            pass
    sources.append(f"windows_root_ca:{certificate_count}")


def create_update_ssl_context():
    sources = []
    context = _new_client_context()
    _load_default_certificates(context, sources)
    _load_certifi_certificates(context, sources)
    _load_windows_certificates(context, sources)
    return context, tuple(sources)


def get_update_ssl_context():
    global _CONTEXT, _CONTEXT_SOURCES
    with _CONTEXT_LOCK:
        if _CONTEXT is None:
            _CONTEXT, _CONTEXT_SOURCES = create_update_ssl_context()
        return _CONTEXT


def describe_update_ssl_context():
    get_update_ssl_context()
    return ",".join(_CONTEXT_SOURCES) if _CONTEXT_SOURCES else "none"


def reset_update_ssl_context_for_tests():
    global _CONTEXT, _CONTEXT_SOURCES
    with _CONTEXT_LOCK:
        _CONTEXT = None
        _CONTEXT_SOURCES = ()
