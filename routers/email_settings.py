"""
email_settings.py — Email configuration and document expiry alerts
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
import database as db
from routers.auth import get_current_user
import json

router = APIRouter()

class EmailSettingsModel(BaseModel):
    provider: str = 'gmail'
    sendgrid_key: Optional[str] = None
    gmail_user: Optional[str] = None
    gmail_password: Optional[str] = None
    from_name: Optional[str] = None
    from_email: Optional[str] = None
    alert_recipients: Optional[list] = []
    alert_days: Optional[list] = [90, 60, 30, 7]

class TestEmailModel(BaseModel):
    to_email: str


def send_email(settings: dict, to_email: str, subject: str, body: str) -> bool:
    """Send email using configured provider."""
    try:
        if settings.get("provider") == "sendgrid":
            import urllib.request
            import urllib.parse
            payload = json.dumps({
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": settings.get("from_email", "hr@company.com"),
                         "name": settings.get("from_name", "HR System")},
                "subject": subject,
                "content": [{"type": "text/html", "value": body}]
            }).encode()
            req = urllib.request.Request(
                "https://api.sendgrid.com/v3/mail/send",
                data=payload,
                headers={
                    "Authorization": f"Bearer {settings['sendgrid_key']}",
                    "Content-Type": "application/json"
                }
            )
            urllib.request.urlopen(req)
            return True

        elif settings.get("provider") == "gmail":
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = f"{settings.get('from_name', 'HR')} <{settings['gmail_user']}>"
            msg["To"]      = to_email
            msg.attach(MIMEText(body, "html"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(settings["gmail_user"], settings["gmail_password"])
                server.sendmail(settings["gmail_user"], to_email, msg.as_string())
            return True

    except Exception as e:
        print(f">>> Email error: {e}")
        return False


@router.get("/{company_id}")
def get_email_settings(company_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM email_settings WHERE company_id=%s", (company_id,))
        row = cur.fetchone()
    conn.close()
    if not row:
        return {"company_id": company_id, "provider": "gmail",
                "alert_recipients": [], "alert_days": [90, 60, 30, 7]}
    d = dict(row)
    # Parse JSON fields
    for f in ["alert_recipients", "alert_days"]:
        if isinstance(d.get(f), str):
            try: d[f] = json.loads(d[f])
            except: d[f] = []
    # Hide password
    if d.get("gmail_password"):
        d["gmail_password"] = "••••••••"
    return d


@router.put("/{company_id}")
def save_email_settings(company_id: int, data: EmailSettingsModel,
                         current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO email_settings
                (company_id, provider, sendgrid_key, gmail_user, gmail_password,
                 from_name, from_email, alert_recipients, alert_days)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (company_id) DO UPDATE SET
                provider=%s, sendgrid_key=%s,
                from_name=%s, from_email=%s,
                alert_recipients=%s, alert_days=%s
        """, (
            company_id, data.provider, data.sendgrid_key, data.gmail_user,
            data.gmail_password, data.from_name, data.from_email,
            json.dumps(data.alert_recipients), json.dumps(data.alert_days),
            data.provider, data.sendgrid_key,
            data.from_name, data.from_email,
            json.dumps(data.alert_recipients), json.dumps(data.alert_days)
        ))
        # Update password only if provided and not masked
        if data.gmail_password and data.gmail_password != "••••••••":
            cur.execute(
                "UPDATE email_settings SET gmail_password=%s WHERE company_id=%s",
                (data.gmail_password, company_id)
            )
        conn.commit()
    conn.close()
    return {"message": "Email settings saved."}


@router.post("/{company_id}/test")
def test_email(company_id: int, data: TestEmailModel,
               current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM email_settings WHERE company_id=%s", (company_id,))
        row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=400, detail="No email settings configured.")
    settings = dict(row)
    ok = send_email(settings, data.to_email,
                    "✅ HR System — Test Email",
                    "<h2>Test email from HR & Payroll System</h2><p>Email configuration is working correctly.</p>")
    if ok:
        return {"message": "Test email sent successfully."}
    raise HTTPException(status_code=500, detail="Failed to send email. Check your settings.")


@router.post("/{company_id}/send-alerts")
def send_document_alerts(company_id: int, current_user=Depends(get_current_user)):
    """Manually trigger document expiry alerts."""
    conn = db.get_conn()
    sent = 0
    with conn.cursor() as cur:
        # Get email settings
        cur.execute("SELECT * FROM email_settings WHERE company_id=%s", (company_id,))
        es = cur.fetchone()
        if not es:
            raise HTTPException(status_code=400, detail="No email settings configured.")
        settings = dict(es)
        try:
            alert_days = json.loads(settings.get("alert_days") or "[90,60,30,7]")
            recipients = json.loads(settings.get("alert_recipients") or "[]")
        except:
            alert_days = [90, 60, 30, 7]
            recipients = []

        if not recipients:
            raise HTTPException(status_code=400, detail="No alert recipients configured.")

        # Get expiring documents
        placeholders = ",".join(["%s"] * len(alert_days))
        cur.execute(f"""
            SELECT d.*, e.full_name, e.employee_code,
                   b.name as branch_name,
                   d.expiry_date - CURRENT_DATE as days_remaining
            FROM employee_documents d
            JOIN employees e ON d.employee_id = e.id
            LEFT JOIN branches b ON e.home_branch_id = b.id
            WHERE e.company_id = %s AND d.status = 'active'
              AND d.expiry_date IS NOT NULL
              AND (d.expiry_date - CURRENT_DATE) = ANY(%s::int[])
            ORDER BY d.expiry_date ASC
        """, (company_id, alert_days))
        expiring = cur.fetchall()

        if not expiring:
            return {"message": "No documents expiring on alert days.", "sent": 0}

        # Build email body
        rows_html = ""
        for doc in expiring:
            days = doc["days_remaining"]
            color = "#cc0000" if days <= 0 else "#ff6600" if days <= 30 else "#cc8800"
            label = "EXPIRED" if days <= 0 else f"{days} days"
            rows_html += f"""
                <tr>
                    <td style="padding:8px;border:1px solid #ddd">{doc['full_name']}</td>
                    <td style="padding:8px;border:1px solid #ddd">{doc['branch_name'] or '—'}</td>
                    <td style="padding:8px;border:1px solid #ddd">{doc['document_type']}</td>
                    <td style="padding:8px;border:1px solid #ddd">{doc['document_number'] or '—'}</td>
                    <td style="padding:8px;border:1px solid #ddd">{doc['expiry_date']}</td>
                    <td style="padding:8px;border:1px solid #ddd;color:{color};font-weight:bold">{label}</td>
                </tr>"""

        body = f"""
        <div style="font-family:Arial,sans-serif;max-width:800px">
            <h2 style="color:#cc0000">⚠ Document Expiry Alert</h2>
            <p>The following employee documents require attention:</p>
            <table style="width:100%;border-collapse:collapse">
                <thead>
                    <tr style="background:#f0f0f0">
                        <th style="padding:8px;border:1px solid #ddd;text-align:left">Employee</th>
                        <th style="padding:8px;border:1px solid #ddd;text-align:left">Branch</th>
                        <th style="padding:8px;border:1px solid #ddd;text-align:left">Document</th>
                        <th style="padding:8px;border:1px solid #ddd;text-align:left">Number</th>
                        <th style="padding:8px;border:1px solid #ddd;text-align:left">Expiry Date</th>
                        <th style="padding:8px;border:1px solid #ddd;text-align:left">Status</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
            <p style="color:#888;font-size:12px">Generated by HR & Payroll System</p>
        </div>"""

        subject = f"⚠ Document Expiry Alert — {len(expiring)} document(s) require attention"

        for recipient in recipients:
            if send_email(settings, recipient, subject, body):
                sent += 1

    conn.close()
    return {"message": f"Alert sent to {sent} recipient(s).", "documents": len(expiring), "sent": sent}
