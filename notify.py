"""
Pluggable notifier.

Set NOTIFY_METHOD to one of: print | twilio | pushover | telegram | ntfy | email
'print' (the default) just logs the digest and sends nothing — use it for testing.
Any failure falls back to printing so a run never dies on the notify step.
"""
import os
import smtplib
from email.mime.text import MIMEText

import requests


def send(message, subject="Reddit Stock Radar"):
    method = os.getenv("NOTIFY_METHOD", "print").lower()
    try:
        if method == "twilio":
            return _twilio(message)
        if method == "pushover":
            return _pushover(message, subject)
        if method == "telegram":
            return _telegram(message)
        if method == "ntfy":
            return _ntfy(message, subject)
        if method == "email":
            return _email(message, subject)
    except Exception as e:
        print(f"[notify] {method} failed: {e}")
    print(message)  # 'print' method and fallback


def _twilio(message):
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data={"From": os.environ["TWILIO_FROM"],
              "To": os.environ["ALERT_TO"],
              "Body": message},
        auth=(sid, token), timeout=30)
    r.raise_for_status()
    return r.json().get("sid")


def _pushover(message, subject):
    r = requests.post("https://api.pushover.net/1/messages.json", data={
        "token": os.environ["PUSHOVER_TOKEN"],
        "user": os.environ["PUSHOVER_USER"],
        "title": subject, "message": message}, timeout=30)
    r.raise_for_status()
    return "ok"


def _telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
        "chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": message}, timeout=30)
    r.raise_for_status()
    return "ok"


def _ntfy(message, subject):
    import json
    topic = os.environ["NTFY_TOPIC"]
    r = requests.post(
        "https://ntfy.sh/",
        data=json.dumps({"topic": topic, "title": subject,
                         "message": message, "markdown": True}),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return "ok"


def _email(message, subject):
    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["EMAIL_TO"]
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)
    return "ok"
