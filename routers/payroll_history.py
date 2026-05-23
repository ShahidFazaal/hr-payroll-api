"""
payroll_history.py — Payroll batch management, approval workflow, history
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import database as db
from routers.auth import get_current_user

router = APIRouter()


class BatchSave(BaseModel):
    company_id: int
    period_start: str
    period_end: str
    records: list
    total_employees: int
    total_payroll: float


class RejectRequest(BaseModel):
    reason: str


class LogoUpdate(BaseModel):
    logo_base64: str


# ── Save batch ──────────────────────────────────────────────────────────────

@router.post("/batch/save")
def save_batch(data: BatchSave, current_user=Depends(get_current_user)):
    """Save or update a payroll batch and all its records."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        # Upsert batch
        cur.execute("""
            INSERT INTO payroll_batches
                (company_id, period_start, period_end, total_employees,
                 total_payroll, status, generated_by, generated_at)
            VALUES (%s,%s,%s,%s,%s,'draft',%s,NOW())
            ON CONFLICT (company_id, period_start, period_end)
            DO UPDATE SET
                total_employees=%s, total_payroll=%s,
                generated_by=%s, generated_at=NOW(),
                status=CASE WHEN payroll_batches.status IN ('finalized') THEN 'finalized'
                            ELSE 'draft' END
            RETURNING id
        """, (
            data.company_id, data.period_start, data.period_end,
            data.total_employees, data.total_payroll,
            current_user["user_id"],
            data.total_employees, data.total_payroll,
            current_user["user_id"],
        ))
        batch_id = cur.fetchone()["id"]

        # Delete existing records for this period first then reinsert
        cur.execute("""
            DELETE FROM payroll_records
            WHERE company_id=%s AND period_start=%s AND period_end=%s
              AND status NOT IN ('finalized')
        """, (data.company_id, data.period_start, data.period_end))

        # Save individual records fresh
        for r in data.records:
            cur.execute("""
                INSERT INTO payroll_records (
                    employee_id, company_id, period_start, period_end,
                    working_days, present_days, absent_days, late_count,
                    late_minutes, early_departure_count, overtime_hours,
                    basic_salary, total_allowances, variable_pay,
                    absent_deduction, late_deduction, warning_deduction,
                    manual_deduction, overtime_pay, final_salary,
                    notes, status, generated_by
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s
                )
            """, (
                r["employee_id"], data.company_id, data.period_start, data.period_end,
                r["working_days"], r["present_days"], r["absent_days"], r["late_count"],
                r.get("late_minutes", 0), r.get("early_departure_count", 0),
                r.get("overtime_hours", 0),
                r["basic_salary"], r["total_allowances"], r.get("variable_pay", 0),
                r["absent_deduction"], r.get("late_deduction", 0),
                r.get("warning_deduction", 0), r.get("manual_deduction", 0),
                r.get("overtime_pay", 0), r["final_salary"],
                r.get("notes", ""), current_user["user_id"]
            ))
        conn.commit()
    conn.close()
    return {"message": "Payroll saved.", "batch_id": batch_id}


# ── Submit for approval ──────────────────────────────────────────────────────

@router.post("/batch/{batch_id}/submit")
def submit_batch(batch_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM payroll_batches WHERE id=%s", (batch_id,))
        batch = cur.fetchone()
        if not batch:
            conn.close()
            raise HTTPException(status_code=404, detail="Batch not found.")
        if batch["status"] not in ("draft", "rejected"):
            conn.close()
            raise HTTPException(status_code=400, detail=f"Cannot submit. Status is: {batch['status']}")
        cur.execute("""
            UPDATE payroll_batches
            SET status='submitted', submitted_by=%s, submitted_at=NOW()
            WHERE id=%s
        """, (current_user["user_id"], batch_id))
        conn.commit()
    conn.close()
    return {"message": "Payroll submitted for approval."}


# ── Approve ──────────────────────────────────────────────────────────────────

@router.post("/batch/{batch_id}/approve")
def approve_batch(batch_id: int, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to approve.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM payroll_batches WHERE id=%s", (batch_id,))
        batch = cur.fetchone()
        if not batch:
            conn.close()
            raise HTTPException(status_code=404, detail="Batch not found.")
        if batch["status"] != "submitted":
            conn.close()
            raise HTTPException(status_code=400, detail="Only submitted payrolls can be approved.")
        cur.execute("""
            UPDATE payroll_batches
            SET status='finalized', approved_by=%s,
                approved_at=NOW(), finalized_at=NOW()
            WHERE id=%s
        """, (current_user["user_id"], batch_id))
        cur.execute("""
            UPDATE payroll_records SET status='finalized'
            WHERE company_id=%s AND period_start=%s AND period_end=%s
        """, (batch["company_id"], batch["period_start"], batch["period_end"]))
        conn.commit()
    conn.close()
    return {"message": "Payroll approved and finalized."}


# ── Reject ───────────────────────────────────────────────────────────────────

@router.post("/batch/{batch_id}/reject")
def reject_batch(batch_id: int, data: RejectRequest, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to reject.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE payroll_batches
            SET status='rejected', rejected_reason=%s
            WHERE id=%s AND status='submitted'
        """, (data.reason, batch_id))
        conn.commit()
    conn.close()
    return {"message": "Payroll rejected."}


# ── List batches (history) ───────────────────────────────────────────────────

@router.get("/batch/list")
def list_batches(company_id: Optional[int] = None,
                  status: Optional[str] = None,
                  current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        query = """
            SELECT pb.*,
                c.name as company_name,
                ug.full_name as generated_by_name,
                ua.full_name as approved_by_name,
                us.full_name as submitted_by_name
            FROM payroll_batches pb
            LEFT JOIN companies c ON pb.company_id = c.id
            LEFT JOIN users ug ON pb.generated_by = ug.id
            LEFT JOIN users ua ON pb.approved_by = ua.id
            LEFT JOIN users us ON pb.submitted_by = us.id
            WHERE 1=1
        """
        params = []
        if current_user["role"] not in ["super_admin"]:
            query += " AND pb.company_id = %s"
            params.append(current_user["company_id"])
        elif company_id:
            query += " AND pb.company_id = %s"
            params.append(company_id)
        if status:
            query += " AND pb.status = %s"
            params.append(status)
        query += " ORDER BY pb.period_start DESC"
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]

        # Convert dates to strings
        for row in rows:
            for k, v in row.items():
                if hasattr(v, 'isoformat'):
                    row[k] = v.isoformat()
    conn.close()
    return rows


# ── Get batch detail with all records ───────────────────────────────────────

@router.get("/batch/{batch_id}/detail")
def get_batch_detail(batch_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT pb.*, c.name as company_name,
                COALESCE(c.logo_base64, '') as logo_base64,
                c.address as company_address,
                ug.full_name as generated_by_name,
                ua.full_name as approved_by_name
            FROM payroll_batches pb
            LEFT JOIN companies c ON pb.company_id = c.id
            LEFT JOIN users ug ON pb.generated_by = ug.id
            LEFT JOIN users ua ON pb.approved_by = ua.id
            WHERE pb.id = %s
        """, (batch_id,))
        batch = cur.fetchone()
        if not batch:
            conn.close()
            raise HTTPException(status_code=404, detail="Batch not found.")
        batch = dict(batch)
        for k, v in batch.items():
            if hasattr(v, 'isoformat'):
                batch[k] = v.isoformat()

        # Get all records for this batch
        cur.execute("""
            SELECT pr.*, e.full_name, e.full_name_ar, e.employee_code,
                   e.device_user_id, e.position, b.name as branch_name,
                   (SELECT json_agg(json_build_object('type', ea.allowance_type, 'amount', ea.amount))
                    FROM employee_allowances ea
                    WHERE ea.employee_id = pr.employee_id AND ea.is_active = TRUE) as allowances
            FROM payroll_records pr
            LEFT JOIN employees e ON pr.employee_id = e.id
            LEFT JOIN branches b ON e.home_branch_id = b.id
            WHERE pr.company_id = %s
              AND pr.period_start = %s
              AND pr.period_end = %s
            ORDER BY e.full_name
        """, (batch["company_id"], batch["period_start"], batch["period_end"]))
        records = []
        for r in cur.fetchall():
            d = dict(r)
            for k, v in d.items():
                if hasattr(v, 'isoformat'):
                    d[k] = v.isoformat()
            records.append(d)

        batch["records"] = records
    conn.close()
    return batch


# ── Pending approvals count (for dashboard) ─────────────────────────────────

@router.get("/batch/pending-count")
def pending_count(current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        return {"count": 0}
    conn = db.get_conn()
    with conn.cursor() as cur:
        query = "SELECT COUNT(*) as count FROM payroll_batches WHERE status='submitted'"
        params = []
        if current_user["role"] == "company_admin":
            query += " AND company_id=%s"
            params.append(current_user["company_id"])
        cur.execute(query, params)
        count = cur.fetchone()["count"]
    conn.close()
    return {"count": count}


# ── Logo upload ──────────────────────────────────────────────────────────────

@router.post("/company/{company_id}/logo")
def upload_logo(company_id: int, data: LogoUpdate, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE companies SET logo_base64=%s WHERE id=%s",
                    (data.logo_base64, company_id))
        conn.commit()
    conn.close()
    return {"message": "Logo updated."}
