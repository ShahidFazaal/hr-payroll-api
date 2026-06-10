"""
warnings.py — Warning letters with templates and PDF generation
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime
import database as db
from routers.auth import get_current_user
import json, re

router = APIRouter()

# ── Default Templates ────────────────────────────────────────────────────────

DEFAULT_TEMPLATES = [
    {
        "name": "Late Attendance Warning",
        "violation_type": "Late Attendance",
        "content_en": """Dear {{employee_name}},

This letter serves as a formal warning regarding your repeated late attendance at work.

It has been noted that you have been late to work on multiple occasions. Punctuality is a fundamental requirement of your employment, and such behavior is unacceptable.

Incident Date: {{incident_date}}
Number of Late Occurrences: {{late_count}}

You are hereby warned that any further instances of late attendance will result in more severe disciplinary action, up to and including termination of employment.

{{deduction_section}}

Please sign below to acknowledge receipt of this warning letter.

Issued by: {{issued_by}}
Date: {{date}}

Employee Signature: _______________________
Date: _______________________""",

        "content_ar": """عزيزي {{employee_name_ar}}،

يُعدّ هذا الخطاب بمثابة إنذار رسمي بشأن تأخرك المتكرر في الحضور إلى العمل.

لقد لوحظ أنك تأخرت عن العمل في مناسبات متعددة. إن الالتزام بمواعيد العمل متطلب أساسي في عقد عملك، وهذا السلوك غير مقبول.

تاريخ الحادثة: {{incident_date}}
عدد مرات التأخر: {{late_count}}

يُحذَّر بموجب هذا الخطاب بأن أي تأخر إضافي سيؤدي إلى اتخاذ إجراءات تأديبية أشد، قد تصل إلى إنهاء الخدمة.

{{deduction_section_ar}}

يرجى التوقيع أدناه إقراراً باستلام هذا الإنذار.

صادر من: {{issued_by}}
التاريخ: {{date}}

توقيع الموظف: _______________________
التاريخ: _______________________"""
    },
    {
        "name": "Absence Without Notice",
        "violation_type": "Unauthorized Absence",
        "content_en": """Dear {{employee_name}},

This letter serves as a formal warning regarding your unauthorized absence from work without prior notice or approval.

Incident Date: {{incident_date}}
Number of Absent Days: {{absent_days}}

Your absence without proper notification is a serious breach of company policy. You are required to notify your supervisor and HR department in advance of any planned absence, or as soon as possible in case of emergency.

{{deduction_section}}

Further unauthorized absences will result in more severe disciplinary action.

Issued by: {{issued_by}}
Date: {{date}}

Employee Signature: _______________________
Date: _______________________""",

        "content_ar": """عزيزي {{employee_name_ar}}،

يُعدّ هذا الخطاب بمثابة إنذار رسمي بشأن غيابك غير المصرح به عن العمل دون إشعار مسبق أو موافقة.

تاريخ الحادثة: {{incident_date}}
عدد أيام الغياب: {{absent_days}}

إن غيابك دون إشعار مناسب يُعدّ انتهاكاً خطيراً لسياسة الشركة.

{{deduction_section_ar}}

سيؤدي أي غياب غير مصرح به إضافي إلى اتخاذ إجراءات تأديبية أشد.

صادر من: {{issued_by}}
التاريخ: {{date}}

توقيع الموظف: _______________________
التاريخ: _______________________"""
    },
    {
        "name": "Misconduct Warning",
        "violation_type": "Misconduct",
        "content_en": """Dear {{employee_name}},

This letter serves as a formal warning regarding your conduct at the workplace.

Incident Date: {{incident_date}}
Description of Incident:
{{description}}

Your behavior as described above is a violation of company policies and professional standards. Such conduct cannot be tolerated in the workplace.

{{deduction_section}}

You are expected to immediately improve your conduct. Failure to do so will result in further disciplinary action.

Issued by: {{issued_by}}
Date: {{date}}

Employee Signature: _______________________
Date: _______________________""",

        "content_ar": """عزيزي {{employee_name_ar}}،

يُعدّ هذا الخطاب بمثابة إنذار رسمي بشأن سلوكك في مكان العمل.

تاريخ الحادثة: {{incident_date}}
وصف الحادثة:
{{description_ar}}

إن سلوكك كما هو موضح أعلاه يُعدّ انتهاكاً لسياسات الشركة والمعايير المهنية.

{{deduction_section_ar}}

من المتوقع منك تحسين سلوكك فوراً. وإلا ستُتخذ إجراءات تأديبية إضافية.

صادر من: {{issued_by}}
التاريخ: {{date}}

توقيع الموظف: _______________________
التاريخ: _______________________"""
    }
]


# ── Models ───────────────────────────────────────────────────────────────────

class TemplateCreate(BaseModel):
    company_id: int
    name: str
    violation_type: Optional[str] = None
    content_en: Optional[str] = None
    content_ar: Optional[str] = None

class WarningCreate(BaseModel):
    company_id: int
    employee_id: int
    template_id: Optional[int] = None
    letter_type: str
    violation_type: Optional[str] = None
    incident_date: Optional[date] = None
    description: Optional[str] = None
    description_ar: Optional[str] = None
    deduction_amount: Optional[float] = 0
    deduction_month: Optional[str] = None
    send_email: Optional[bool] = False
    employee_email: Optional[str] = None


# ── Helper: fill template parameters ─────────────────────────────────────────

def fill_template(content: str, params: dict) -> str:
    for key, value in params.items():
        content = content.replace(f"{{{{{key}}}}}", str(value or ""))
    return content


# ── Templates ─────────────────────────────────────────────────────────────────

@router.get("/templates")
def get_templates(company_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT * FROM warning_templates
            WHERE company_id = %s
            ORDER BY is_default DESC, name
        """, (company_id,))
        templates = cur.fetchall()
    conn.close()
    return [dict(t) for t in templates]


@router.post("/templates/seed")
def seed_default_templates(company_id: int, current_user=Depends(get_current_user)):
    """Seed the 3 default templates for a company."""
    conn = db.get_conn()
    created = 0
    with conn.cursor() as cur:
        # Check if already seeded
        cur.execute("SELECT COUNT(*) as c FROM warning_templates WHERE company_id=%s AND is_default=TRUE", (company_id,))
        if cur.fetchone()["c"] > 0:
            conn.close()
            return {"message": "Default templates already exist."}

        for t in DEFAULT_TEMPLATES:
            cur.execute("""
                INSERT INTO warning_templates
                    (company_id, name, violation_type, content_en, content_ar, is_default)
                VALUES (%s,%s,%s,%s,%s,TRUE)
            """, (company_id, t["name"], t["violation_type"], t["content_en"], t["content_ar"]))
            created += 1
        conn.commit()
    conn.close()
    return {"message": f"Created {created} default templates."}


@router.post("/templates")
def create_template(data: TemplateCreate, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO warning_templates
                (company_id, name, violation_type, content_en, content_ar, created_by)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING id
        """, (data.company_id, data.name, data.violation_type,
              data.content_en, data.content_ar, current_user["user_id"]))
        tid = cur.fetchone()["id"]
        conn.commit()
    conn.close()
    return {"id": tid, "message": "Template created."}


@router.put("/templates/{template_id}")
def update_template(template_id: int, data: TemplateCreate,
                    current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE warning_templates
            SET name=%s, violation_type=%s, content_en=%s, content_ar=%s
            WHERE id=%s
        """, (data.name, data.violation_type, data.content_en, data.content_ar, template_id))
        conn.commit()
    conn.close()
    return {"message": "Template updated."}


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM warning_templates WHERE id=%s AND is_default=FALSE", (template_id,))
        conn.commit()
    conn.close()
    return {"message": "Template deleted."}


@router.post("/templates/{template_id}/preview")
def preview_template(template_id: int, params: dict,
                     current_user=Depends(get_current_user)):
    """Fill template with sample/real params and return preview."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM warning_templates WHERE id=%s", (template_id,))
        t = cur.fetchone()
    conn.close()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found.")
    return {
        "content_en": fill_template(t["content_en"] or "", params),
        "content_ar": fill_template(t["content_ar"] or "", params),
    }


# ── Warning Letters ───────────────────────────────────────────────────────────

@router.get("/")
def get_warnings(
    company_id: int,
    employee_id: Optional[int] = None,
    current_user=Depends(get_current_user)
):
    conn = db.get_conn()
    with conn.cursor() as cur:
        if employee_id and (not company_id or company_id == 0):
            # Fetch by employee only
            query = """
                SELECT w.*, e.full_name, e.employee_code,
                       b.name as branch_name,
                       u.full_name as issued_by_name
                FROM warning_letters w
                JOIN employees e ON w.employee_id = e.id
                LEFT JOIN branches b ON e.home_branch_id = b.id
                LEFT JOIN users u ON w.issued_by = u.id
                WHERE w.employee_id = %s
            """
            params = [employee_id]
        else:
            query = """
                SELECT w.*, e.full_name, e.employee_code,
                       b.name as branch_name,
                       u.full_name as issued_by_name
                FROM warning_letters w
                JOIN employees e ON w.employee_id = e.id
                LEFT JOIN branches b ON e.home_branch_id = b.id
                LEFT JOIN users u ON w.issued_by = u.id
                WHERE w.company_id = %s
            """
            params = [company_id]
            if employee_id:
                query += " AND w.employee_id = %s"
                params.append(employee_id)
        query += " ORDER BY w.created_at DESC"
        cur.execute(query, params)
        warnings = cur.fetchall()
    conn.close()
    return [dict(w) for w in warnings]


@router.post("/")
def create_warning(data: WarningCreate, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    try:
     with conn.cursor() as cur:
        # Get employee info
        cur.execute("""
            SELECT e.*, b.name as branch_name, c.name as company_name
            FROM employees e
            LEFT JOIN branches b ON e.home_branch_id = b.id
            LEFT JOIN companies c ON e.company_id = c.id
            WHERE e.id = %s
        """, (data.employee_id,))
        emp = cur.fetchone()
        if not emp:
            raise HTTPException(status_code=404, detail="Employee not found.")

        # Get issuer name
        cur.execute("SELECT full_name FROM users WHERE id=%s", (current_user["user_id"],))
        issuer = cur.fetchone()
        issued_by_name = issuer["full_name"] if issuer else "HR Manager"

        # Build deduction section
        deduction_en = ""
        deduction_ar = ""
        if data.deduction_amount and data.deduction_amount > 0:
            deduction_en = f"""
Salary Deduction:
A deduction of AED {data.deduction_amount:,.2f} will be applied to your salary for {data.deduction_month or 'the current month'}.
"""
            deduction_ar = f"""
خصم من الراتب:
سيتم خصم مبلغ {data.deduction_amount:,.2f} درهم إماراتي من راتبك لشهر {data.deduction_month or 'الشهر الحالي'}.
"""

        # Fill template if provided
        content_en = ""
        content_ar = ""
        if data.template_id:
            cur.execute("SELECT * FROM warning_templates WHERE id=%s", (data.template_id,))
            tmpl = cur.fetchone()
            if tmpl:
                params_map = {
                    "employee_name":     emp["full_name"],
                    "employee_name_ar":  emp.get("full_name_ar") or emp["full_name"],
                    "branch":            emp["branch_name"] or "",
                    "company_name":      emp["company_name"] or "",
                    "date":              str(date.today()),
                    "incident_date":     str(data.incident_date or date.today()),
                    "description":       data.description or "",
                    "description_ar":    data.description_ar or data.description or "",
                    "issued_by":         issued_by_name,
                    "deduction_section": deduction_en,
                    "deduction_section_ar": deduction_ar,
                    "late_count":        "",
                    "absent_days":       "",
                }
                content_en = fill_template(tmpl["content_en"] or "", params_map)
                content_ar = fill_template(tmpl["content_ar"] or "", params_map)

        # Save warning letter
        cur.execute("""
            INSERT INTO warning_letters
                (company_id, employee_id, template_id, letter_type, violation_type,
                 incident_date, description, description_ar, deduction_amount,
                 deduction_month, issued_by, status, sent_to_employee)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'issued',%s)
            RETURNING id
        """, (data.company_id, data.employee_id, data.template_id,
              data.letter_type, data.violation_type, data.incident_date,
              data.description, data.description_ar, data.deduction_amount,
              data.deduction_month, current_user["user_id"],
              data.send_email))
        wid = cur.fetchone()["id"]

        # Send email if requested
        if data.send_email and data.employee_email:
            cur.execute("SELECT * FROM email_settings WHERE company_id=%s", (data.company_id,))
            es = cur.fetchone()
            if es:
                from routers.email_settings import send_email as send_fn
                body = f"""
                <div style="font-family:Arial,sans-serif;max-width:700px">
                <h2>Warning Letter — {data.letter_type}</h2>
                <pre style="white-space:pre-wrap;font-family:Arial">{content_en}</pre>
                </div>"""
                send_fn(dict(es), data.employee_email,
                        f"Warning Letter — {data.letter_type}", body)

        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=f"Failed to create warning: {str(e)}")

    conn.close()
    return {
        "id": wid,
        "message": "Warning letter issued.",
        "content_en": content_en,
        "content_ar": content_ar,
        "employee_name": emp["full_name"],
        "branch": emp["branch_name"],
        "company_name": emp["company_name"],
        "issued_by": issued_by_name,
    }


@router.get("/pending-deductions")
def get_pending_deductions(company_id: int, current_user=Depends(get_current_user)):
    """Get warning letters with unapplied deductions for payroll confirmation."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT w.*, e.full_name, e.employee_code
            FROM warning_letters w
            JOIN employees e ON w.employee_id = e.id
            WHERE w.company_id = %s
              AND w.deduction_amount > 0
              AND w.deduction_applied = FALSE
            ORDER BY w.created_at DESC
        """, (company_id,))
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/{warning_id}/apply-deduction")
def apply_deduction(warning_id: int, current_user=Depends(get_current_user)):
    """Mark deduction as applied (called from payroll confirmation)."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE warning_letters
            SET deduction_applied=TRUE
            WHERE id=%s
        """, (warning_id,))
        conn.commit()
    conn.close()
    return {"message": "Deduction marked as applied."}


@router.delete("/{warning_id}")
def delete_warning(warning_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM warning_letters WHERE id=%s", (warning_id,))
        conn.commit()
    conn.close()
    return {"message": "Warning letter deleted."}
