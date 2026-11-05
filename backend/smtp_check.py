"""SMTP diagnostic, run INSIDE the backend container (smtp-check.bat does this).

It uses exactly the settings and password stored in OBS and reports each step:
DNS, TCP connection, TLS, login. Nothing is changed and no e-mail is sent.
The password is never printed.
"""

import os
import smtplib
import socket
import ssl
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or "/app")
sys.path.insert(0, "/app")

from database import get_connection  # noqa: E402
import mailer  # noqa: E402

connection = get_connection()
cfg = mailer.load_smtp(connection)
connection.close()

print("=" * 60)
print("OBS SMTP check")
print("=" * 60)
for key in ("enabled", "host", "port", "security", "auth_enabled", "username", "sender_email", "timeout_seconds"):
    print(f"  {key:16} {cfg.get(key)!r}")
print(f"  {'password':16} {'stored (' + str(len(mailer.smtp_password(cfg) or '')) + ' characters)' if cfg.get('password_enc') else 'NOT STORED'}")
print()

host = (cfg.get("host") or "").strip()
port = int(cfg.get("port") or 587)

print("1. DNS")
try:
    print("   ", host, "->", ", ".join(sorted({a[4][0] for a in socket.getaddrinfo(host, port)})))
except Exception as exc:  # noqa: BLE001
    print("    FAILED:", exc)
    print("    The container cannot resolve names. Check Docker's DNS / the server's internet access.")
    sys.exit(1)

print("2. TCP connection")
for p in sorted({port, 587, 465}):
    try:
        with socket.create_connection((host, p), timeout=10):
            print(f"    port {p}: open")
    except Exception as exc:  # noqa: BLE001
        print(f"    port {p}: BLOCKED ({type(exc).__name__}: {exc})")

print("3. TLS and login (the same code OBS uses)")
result = mailer.test_connection(cfg)
print("    stages :", " -> ".join(result["stages"]) or "(none)")
print("    result :", "OK" if result["ok"] else "FAILED")
print("    message:", result["message"])

if not result["ok"] and "AUTHENTICATED" not in result["stages"] and "TLS" in result["stages"]:
    print()
    print("4. Raw login attempt with the server's own replies")
    password = mailer.smtp_password(cfg) or ""
    if mailer._is_google(host):
        password = "".join(password.split())
    try:
        if cfg.get("security") == "ssl":
            s = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(host, port, timeout=20)
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
        s.ehlo()
        print("    AUTH methods offered:", s.esmtp_features.get("auth"))
        try:
            s.login((cfg.get("username") or "").strip(), password)
            print("    login: OK")
        except smtplib.SMTPAuthenticationError as exc:
            print("    login: REFUSED", exc.smtp_code, (exc.smtp_error or b"").decode(errors="replace"))
        except smtplib.SMTPServerDisconnected as exc:
            print("    login: server closed the connection:", exc)
        s.close()
    except Exception:  # noqa: BLE001
        traceback.print_exc()

print()
print("Done. Copy everything above into the chat.")
