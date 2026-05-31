"""
inbound_google.py — Google Calendar + Gmail helper for inbound persona bookings.

Unlike google_services.py in livekitbro (which reads persona from PERSONA env var),
this module accepts persona_data directly so any persona can be used.

Required env vars:
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
Optional:
  CLINIC_CALENDAR_ID  (default: "primary")
  CLINIC_EMAIL        (Gmail sender address)
  CLINIC_PHONE        (shown in email template)
"""

import base64
import logging
import os
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger("inbound-google")

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
]


def is_configured() -> bool:
    """Return True if all required Google OAuth env vars are present."""
    return all([
        os.getenv("GOOGLE_REFRESH_TOKEN"),
        os.getenv("GOOGLE_CLIENT_ID"),
        os.getenv("GOOGLE_CLIENT_SECRET"),
    ])


def _get_creds() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )


def create_calendar_event(
    persona_data: dict,
    patient_name: str,
    service: str,
    date_str: str,
    time_str: str,
    phone: str,
    email: str,
) -> dict:
    """Create a 1-hour appointment in Google Calendar using the persona's calendar template."""
    creds = _get_creds()
    cal = build("calendar", "v3", credentials=creds, cache_discovery=False)

    calendar_id = os.getenv("CLINIC_CALENDAR_ID", "primary")
    tz = "Asia/Kolkata"

    start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(hours=1)

    cal_tpl = persona_data.get("calendar", {})
    summary = cal_tpl.get("summary", "{service} — {name}").format(
        service=service, name=patient_name
    )
    desc = cal_tpl.get(
        "description", "Patient: {name}\nPhone: {phone}\nEmail: {email}\nService: {service}"
    ).format(name=patient_name, phone=phone, email=email, service=service)

    event = {
        "summary": summary,
        "description": desc,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": tz},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": tz},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 60},
                {"method": "popup", "minutes": 30},
            ],
        },
    }

    created = cal.events().insert(calendarId=calendar_id, body=event).execute()
    logger.info("Calendar event created: %s", created.get("htmlLink"))
    return created


def send_confirmation_email(
    persona_data: dict,
    patient_name: str,
    patient_email: str,
    service: str,
    date_str: str,
    time_str: str,
) -> bool:
    """Send HTML confirmation email to the caller using the persona's email template."""
    creds = _get_creds()
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)

    sender = os.getenv("CLINIC_EMAIL", "")
    clinic_phone = os.getenv("CLINIC_PHONE", "")

    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        disp = dt.strftime("%d %B %Y, %I:%M %p")
    except ValueError:
        disp = f"{date_str} at {time_str}"

    email_cfg = persona_data.get("email", {})
    html = email_cfg.get(
        "html_template",
        "<p>Dear <strong>{name}</strong>, your appointment for <strong>{service}</strong> is confirmed on {display_time}.</p>",
    ).format(
        name=patient_name,
        service=service,
        display_time=disp,
        clinic_phone=clinic_phone,
    )

    subject = email_cfg.get("subject", "Appointment Confirmed").format(
        service=service, name=patient_name, display_time=disp
    )
    sender_name = email_cfg.get("sender_name", persona_data.get("name", "AI Receptionist"))

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender}>" if sender else sender_name
    msg["To"] = patient_email
    msg.attach(MIMEText(html, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
    logger.info("Confirmation email sent to %s", patient_email)
    return True
