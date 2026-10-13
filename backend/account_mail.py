"""Security e-mails: sign-in codes, reset and invitation links, notices.

These are fixed texts, not editable templates, so nobody can turn them into a
convincing phishing message. They go through the normal delivery queue as kind
SECURITY and are sent at once; the body is wiped from the queue after sending.
"""

from datetime import datetime, timezone
import html
import uuid

import mailer
from database import get_connection

SYSTEM_NAME = "Onboard Sales Revenue System"


def _layout(title: str, paragraphs: list[str], button: tuple[str, str] | None = None,
            extra_html: str = "") -> str:
    body = "".join(f'<p style="margin:0 0 14px">{p}</p>' for p in paragraphs)
    action = ""
    if button:
        label, url = button
        action = (f'<p style="margin:22px 0"><a href="{html.escape(url)}" style="background:#0B4F8A;color:#fff;'
                  f'padding:11px 20px;border-radius:6px;text-decoration:none;font-weight:600">'
                  f'{html.escape(label)}</a></p>')
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#16202B;max-width:620px">'
        '<div style="color:#5B6778;font-size:13px">Himalaya Airlines · Onboard sales</div>'
        f'<h2 style="margin:6px 0 10px;font-size:20px">{html.escape(title)}</h2>'
        '<div style="height:3px;background:#0B4F8A;margin-bottom:18px"></div>'
        f'{body}{extra_html}{action}'
        '<p style="margin:18px 0 0">Regards,<br>Himalaya Airlines<br>' + SYSTEM_NAME + '</p>'
        '<p style="color:#8A96A8;font-size:12px;margin-top:22px">This is an automatic security message '
        f'from the Himalaya Airlines {SYSTEM_NAME}.</p></div>')


def _queue(connection, user: dict, subject: str, text: str, body_html: str, max_attempts: int = 3) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return connection.execute(
        """INSERT INTO notification_jobs
           (idempotency_key,kind,user_id,recipient_email,recipient_name,subject,body_text,body_html,
            status,max_attempts,next_attempt_at,created_by,created_at)
           VALUES (?,'SECURITY',?,?,?,?,?,?,'PENDING',?,?,'SYSTEM',?)""",
        (f"SECURITY:{uuid.uuid4()}", user["id"], user["email"], user["name"], subject, text, body_html,
         max_attempts, now, now)).lastrowid


def smtp_ready(connection) -> bool:
    cfg = mailer.load_smtp(connection)
    return bool(cfg.get("enabled") and cfg.get("host") and cfg.get("sender_email"))


def send_now(job_id: int) -> str:
    """Try to deliver one queued security message immediately. Returns its status."""
    mailer.deliver_due(get_connection, f"security:{job_id}", limit=1, job_ids=[job_id])
    connection = get_connection()
    try:
        row = connection.execute("SELECT status FROM notification_jobs WHERE id=?", (job_id,)).fetchone()
        return row["status"] if row else "MISSING"
    finally:
        connection.close()


def queue_code(connection, user, code: str, minutes: int) -> int:
    subject = "Your Himalaya Airlines verification code"
    text = (f"Dear {user['name']},\n\nYour verification code for the Himalaya Airlines {SYSTEM_NAME} is:\n\n"
            f"    {code}\n\nThis code will expire in {minutes} minutes.\n\n"
            "If you did not attempt to sign in, please contact the system administrator.\n")
    code_box = ('<div style="display:inline-block;background:#F0F3F7;border-radius:8px;padding:14px 22px;'
                'font-family:Consolas,monospace;font-size:28px;letter-spacing:6px;font-weight:700;'
                f'margin:4px 0 16px">{html.escape(code)}</div>')
    body = _layout("Sign-in verification code", [
        f"Dear {html.escape(user['name'])},",
        f"Your verification code for the Himalaya Airlines {SYSTEM_NAME} is:"], extra_html=code_box + (
        f'<p style="margin:0 0 14px">This code will expire in {minutes} minutes.</p>'
        '<p style="margin:0 0 14px">If you did not attempt to sign in, please contact the system '
        'administrator.</p>'))
    return _queue(connection, user, subject, text, body, max_attempts=2)


def queue_link(connection, user, url: str, kind: str, minutes: int) -> int:
    """kind: RESET or INVITE."""
    if kind == "INVITE":
        subject = "You have been invited to the Himalaya Airlines OBS system"
        title, button = "Account invitation", "Set your password"
        intro = (f"An account has been created for you in the {SYSTEM_NAME}. Choose your password "
                 "with the link below to start using it.")
        validity = f"This link is valid for {minutes // 60} hours and can be used once."
    else:
        subject = "Reset your Himalaya Airlines OBS password"
        title, button = "Password reset", "Choose a new password"
        intro = f"We received a request to reset the password of your {SYSTEM_NAME} account."
        validity = f"This link is valid for {minutes} minutes and can be used once."
    ignore = ("If you did not ask for this, you can ignore this e-mail - your password stays the same. "
              "If you receive such e-mails repeatedly, inform the system administrator."
              if kind != "INVITE" else "If you were not expecting this, inform the system administrator.")
    text = (f"Dear {user['name']},\n\n{intro}\n\n{validity}\n\n{url}\n\n{ignore}\n\n"
            f"Regards,\nHimalaya Airlines\n{SYSTEM_NAME}\n")
    body = _layout(title, [
        f"Dear {html.escape(user['name'])},", html.escape(intro), html.escape(validity),
        f'If the button does not work, copy this address into your browser:<br>'
        f'<a href="{html.escape(url)}">{html.escape(url)}</a>',
        html.escape(ignore)], button=(button, url))
    return _queue(connection, user, subject, text, body)


def queue_notice(connection, user, what: str, ip: str | None, when_utc: str) -> int:
    """'Your password was changed/reset' notice with time and address."""
    subject = "Your Himalaya Airlines OBS password was changed"
    text = (f"Dear {user['name']},\n\n{what} All other sessions were signed out.\n\n"
            f"Time (UTC): {when_utc}\nIP address: {ip or 'unknown'}\n\n"
            "If this was not you, reset your password immediately with 'Forgot password' on the sign-in "
            "page and contact the system administrator.\n")
    table = ('<table style="margin:4px 0 16px;border-collapse:collapse">'
             f'<tr><td style="color:#5B6778;padding:3px 16px 3px 0">Time (UTC)</td><td><b>{html.escape(when_utc)}</b></td></tr>'
             f'<tr><td style="color:#5B6778;padding:3px 16px 3px 0">IP address</td><td><b>{html.escape(ip or "unknown")}</b></td></tr>'
             '</table>')
    body = _layout("Security notice", [
        f"Dear {html.escape(user['name'])},", html.escape(what) + " All other sessions were signed out."],
        extra_html=table + '<p style="margin:0 0 14px"><b>If this was not you</b>, reset your password '
                           'immediately with “Forgot password” on the sign-in page and contact the system '
                           'administrator.</p>')
    return _queue(connection, user, subject, text, body)
