"""
leave.py — Leave Management Router
Handles leave types, balances, requests, public holidays, settings
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime, timedelta
import database as db
from routers.auth import get_current_user

router = APIRouter()


# ── Models ──────────────────────────────────────────────────────────────────

class LeaveTypeCreate(BaseModel):
    company_id: int
    name: str
    name_ar: Optional[str] = None
    is_paid: bool = True
    deduction_percentage: float = 0
    max_days_per_year: float = 30
    requires_document: bool = False
    allow_half_day: bool = True
    carry_forward: bool = False
    max_carry_days: int = 0

class LeaveRequestCreate(BaseModel):
    employee_id: int
    company_id: int
    branch_id: int
    leave_type_id: int
    start_date: date
    end_date: date
    is_half_day: bool = False
    half_day_period: Optional[str] = None  # 'morning' or 'evening'
    reason: Optional[str] = None
    notes: Optional[str] = None

class LeaveApprove(BaseModel):
    approved: bool
    reason: Optional[str] = None

class HolidayCreate(BaseModel):
    company_id: int
    name: str
    name_ar: Optional[str] = None
    date: date
    year: int

class LeaveSettingsUpdate(BaseModel):
    enable_pro_rata: bool = True
    pro_rata_rounding: str = 'half'
    enable_carry_forward: bool = False
    max_carry_days: int = 0
    enable_encashment: bool = False
    approval_levels: int = 1
    auto_approve_days: int = 0
    enable_public_holidays: bool = True
    enable_half_day: bool = True
    first_half_cutoff: str = '13:00:00'

class BalanceInit(BaseModel):
    company_id: int
    year: int


# ── Helpers ──────────────────────────────────────────────────────────────────

def count_working_days(start: date, end: date, holidays: list) -> float:
    """Count working days between dates excluding public holidays."""
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 6:  # Mon-Sat (adjust per company)
            if str(current) not in holidays:
                count += 1
        current += timedelta(days=1)
    return count


def get_holidays(conn, company_id: int, year: int) -> list:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT date::text FROM public_holidays
            WHERE company_id=%s AND year=%s AND is_active=TRUE
        """, (company_id, year))
        return [r["date"] for r in cur.fetchall()]


# ── Leave Types ──────────────────────────────────────────────────────────────

@router.get("/types")
def list_leave_types(company_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT * FROM leave_types
            WHERE company_id=%s AND is_active=TRUE
            ORDER BY name
        """, (company_id,))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

@router.post("/types")
def create_leave_type(data: LeaveTypeCreate, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO leave_types (company_id, name, name_ar, is_paid,
                deduction_percentage, max_days_per_year, requires_document,
                allow_half_day, carry_forward, max_carry_days)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (data.company_id, data.name, data.name_ar, data.is_paid,
              data.deduction_percentage, data.max_days_per_year,
              data.requires_document, data.allow_half_day,
              data.carry_forward, data.max_carry_days))
        lt_id = cur.fetchone()["id"]
        conn.commit()
    conn.close()
    return {"message": "Leave type created.", "id": lt_id}

@router.put("/types/{lt_id}")
def update_leave_type(lt_id: int, data: LeaveTypeCreate, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE leave_types SET name=%s, name_ar=%s, is_paid=%s,
                deduction_percentage=%s, max_days_per_year=%s,
                requires_document=%s, allow_half_day=%s,
                carry_forward=%s, max_carry_days=%s
            WHERE id=%s
        """, (data.name, data.name_ar, data.is_paid, data.deduction_percentage,
              data.max_days_per_year, data.requires_document, data.allow_half_day,
              data.carry_forward, data.max_carry_days, lt_id))
        conn.commit()
    conn.close()
    return {"message": "Leave type updated."}

@router.delete("/types/{lt_id}")
def delete_leave_type(lt_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE leave_types SET is_active=FALSE WHERE id=%s", (lt_id,))
        conn.commit()
    conn.close()
    return {"message": "Leave type deactivated."}


# ── Leave Balances ───────────────────────────────────────────────────────────

@router.get("/balances")
def get_balances(company_id: int, year: int,
                  employee_id: Optional[int] = None,
                  current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        query = """
            SELECT lb.*, e.full_name, e.employee_code,
                   lt.name as leave_type_name, lt.is_paid
            FROM leave_balances lb
            JOIN employees e ON lb.employee_id = e.id
            JOIN leave_types lt ON lb.leave_type_id = lt.id
            WHERE e.company_id=%s AND lb.year=%s
        """
        params = [company_id, year]
        if employee_id:
            query += " AND lb.employee_id=%s"
            params.append(employee_id)
        query += " ORDER BY e.full_name, lt.name"
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

@router.post("/balances/initialize")
def initialize_balances(data: BalanceInit, current_user=Depends(get_current_user)):
    """
    Initialize leave balances for all employees for a given year.
    Respects pro-rata for employees who joined mid-year.
    """
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")

    conn = db.get_conn()
    created = 0
    with conn.cursor() as cur:
        # Get leave settings
        cur.execute("SELECT * FROM leave_settings WHERE company_id=%s", (data.company_id,))
        settings = cur.fetchone()
        enable_pro_rata = settings["enable_pro_rata"] if settings else True

        # Get leave types
        cur.execute("SELECT * FROM leave_types WHERE company_id=%s AND is_active=TRUE",
                    (data.company_id,))
        leave_types = cur.fetchall()

        # Get active employees
        cur.execute("""
            SELECT id, join_date FROM employees
            WHERE company_id=%s AND is_active=TRUE
        """, (data.company_id,))
        employees = cur.fetchall()

        for emp in employees:
            for lt in leave_types:
                entitled = float(lt["max_days_per_year"])

                # Pro-rata calculation
                if enable_pro_rata and emp["join_date"]:
                    join = emp["join_date"]
                    if isinstance(join, str):
                        join = date.fromisoformat(join)
                    if join.year == data.year:
                        months_remaining = 12 - join.month + 1
                        entitled = round((entitled / 12) * months_remaining * 2) / 2

                # Get carry forward from previous year
                cur.execute("""
                    SELECT remaining_days FROM leave_balances
                    WHERE employee_id=%s AND leave_type_id=%s AND year=%s
                """, (emp["id"], lt["id"], data.year - 1))
                prev = cur.fetchone()
                carried = 0
                if prev and lt["carry_forward"]:
                    max_carry = lt["max_carry_days"] or 0
                    carried = min(float(prev["remaining_days"]), max_carry)

                cur.execute("""
                    INSERT INTO leave_balances
                        (employee_id, leave_type_id, year, entitled_days,
                         carried_days, used_days, remaining_days)
                    VALUES (%s,%s,%s,%s,%s,0,%s)
                    ON CONFLICT (employee_id, leave_type_id, year)
                    DO UPDATE SET entitled_days=%s, carried_days=%s,
                        remaining_days=EXCLUDED.entitled_days + EXCLUDED.carried_days - leave_balances.used_days
                """, (emp["id"], lt["id"], data.year, entitled, carried,
                      entitled + carried, entitled, carried))
                created += 1

        conn.commit()
    conn.close()
    return {"message": f"Balances initialized for {len(employees)} employees.", "records": created}


# ── Leave Requests ───────────────────────────────────────────────────────────

@router.get("/requests")
def list_requests(company_id: Optional[int] = None,
                   branch_id: Optional[int] = None,
                   employee_id: Optional[int] = None,
                   status: Optional[str] = None,
                   year: Optional[int] = None,
                   current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        query = """
            SELECT lr.*, e.full_name, e.employee_code, e.department,
                   lt.name as leave_type_name, lt.is_paid as type_is_paid,
                   b.name as branch_name, c.name as company_name,
                   u.full_name as created_by_name,
                   ua.full_name as approved_by_name
            FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.id
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            JOIN branches b ON lr.branch_id = b.id
            JOIN companies c ON lr.company_id = c.id
            LEFT JOIN users u ON lr.created_by = u.id
            LEFT JOIN users ua ON lr.approved_by = ua.id
            WHERE 1=1
        """
        params = []

        if current_user["role"] == "branch_manager":
            query += " AND lr.branch_id=%s"
            params.append(current_user["branch_id"])
        elif current_user["role"] == "company_admin":
            query += " AND lr.company_id=%s"
            params.append(current_user["company_id"])
        else:
            if company_id:
                query += " AND lr.company_id=%s"
                params.append(company_id)
            if branch_id:
                query += " AND lr.branch_id=%s"
                params.append(branch_id)

        if employee_id:
            query += " AND lr.employee_id=%s"
            params.append(employee_id)
        if status:
            query += " AND lr.status=%s"
            params.append(status)
        if year:
            query += " AND EXTRACT(YEAR FROM lr.start_date)=%s"
            params.append(year)

        query += " ORDER BY lr.created_at DESC"
        cur.execute(query, params)
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            for k, v in d.items():
                if hasattr(v, 'isoformat'):
                    d[k] = v.isoformat()
            rows.append(d)
    conn.close()
    return rows

@router.post("/requests")
def create_request(data: LeaveRequestCreate, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        # Get leave type details
        cur.execute("SELECT * FROM leave_types WHERE id=%s", (data.leave_type_id,))
        lt = cur.fetchone()
        if not lt:
            conn.close()
            raise HTTPException(status_code=404, detail="Leave type not found.")

        # Get public holidays for the period
        holidays = get_holidays(conn, data.company_id, data.start_date.year)

        # Calculate days
        if data.is_half_day:
            days_count = 0.5
        else:
            days_count = count_working_days(data.start_date, data.end_date, holidays)

        if days_count <= 0:
            conn.close()
            raise HTTPException(status_code=400, detail="No working days in selected range.")

        # Check balance
        cur.execute("""
            SELECT remaining_days FROM leave_balances
            WHERE employee_id=%s AND leave_type_id=%s AND year=%s
        """, (data.employee_id, data.leave_type_id, data.start_date.year))
        balance = cur.fetchone()
        if balance and float(balance["remaining_days"]) < days_count:
            conn.close()
            raise HTTPException(status_code=400,
                detail=f"Insufficient leave balance. Available: {balance['remaining_days']} days, Requested: {days_count} days.")

        # Get approval settings
        cur.execute("SELECT approval_levels, auto_approve_days FROM leave_settings WHERE company_id=%s",
                    (data.company_id,))
        settings = cur.fetchone()
        auto_approve = settings["auto_approve_days"] if settings else 0

        # Determine initial status
        initial_status = "approved" if (
            current_user["role"] in ["super_admin", "company_admin"] or
            auto_approve == -1  # instant auto approve
        ) else "pending"

        cur.execute("""
            INSERT INTO leave_requests (
                employee_id, company_id, branch_id, leave_type_id,
                start_date, end_date, days_count, is_half_day,
                half_day_period, reason, status, is_paid,
                deduction_percentage, notes, created_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (data.employee_id, data.company_id, data.branch_id, data.leave_type_id,
              data.start_date, data.end_date, days_count, data.is_half_day,
              data.half_day_period, data.reason, initial_status,
              lt["is_paid"], lt["deduction_percentage"],
              data.notes, current_user["user_id"]))
        req_id = cur.fetchone()["id"]

        # If auto-approved deduct balance immediately
        if initial_status == "approved":
            cur.execute("""
                UPDATE leave_balances
                SET used_days = used_days + %s,
                    remaining_days = remaining_days - %s
                WHERE employee_id=%s AND leave_type_id=%s AND year=%s
            """, (days_count, days_count, data.employee_id,
                  data.leave_type_id, data.start_date.year))

        conn.commit()
    conn.close()
    return {"message": "Leave request created.", "id": req_id,
            "days_count": days_count, "status": initial_status}

@router.post("/requests/{req_id}/approve")
def approve_request(req_id: int, data: LeaveApprove, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin", "branch_manager"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM leave_requests WHERE id=%s", (req_id,))
        req = cur.fetchone()
        if not req:
            conn.close()
            raise HTTPException(status_code=404, detail="Request not found.")
        if req["status"] != "pending":
            conn.close()
            raise HTTPException(status_code=400, detail=f"Cannot action. Status is: {req['status']}")

        if data.approved:
            cur.execute("""
                UPDATE leave_requests
                SET status='approved', approved_by=%s, approved_at=NOW()
                WHERE id=%s
            """, (current_user["user_id"], req_id))
            # Deduct from balance
            cur.execute("""
                UPDATE leave_balances
                SET used_days = used_days + %s,
                    remaining_days = remaining_days - %s
                WHERE employee_id=%s AND leave_type_id=%s AND year=%s
            """, (req["days_count"], req["days_count"], req["employee_id"],
                  req["leave_type_id"],
                  req["start_date"].year if hasattr(req["start_date"], 'year') else
                  int(str(req["start_date"])[:4])))
            msg = "Leave approved."
        else:
            cur.execute("""
                UPDATE leave_requests
                SET status='rejected', rejected_reason=%s, approved_by=%s, approved_at=NOW()
                WHERE id=%s
            """, (data.reason, current_user["user_id"], req_id))
            msg = "Leave rejected."

        conn.commit()
    conn.close()
    return {"message": msg}

@router.delete("/requests/{req_id}")
def cancel_request(req_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM leave_requests WHERE id=%s", (req_id,))
        req = cur.fetchone()
        if not req:
            conn.close()
            raise HTTPException(status_code=404, detail="Not found.")

        # If already approved — restore balance
        if req["status"] == "approved":
            cur.execute("""
                UPDATE leave_balances
                SET used_days = used_days - %s,
                    remaining_days = remaining_days + %s
                WHERE employee_id=%s AND leave_type_id=%s AND year=%s
            """, (req["days_count"], req["days_count"], req["employee_id"],
                  req["leave_type_id"],
                  req["start_date"].year if hasattr(req["start_date"], 'year') else
                  int(str(req["start_date"])[:4])))

        cur.execute("UPDATE leave_requests SET status='cancelled' WHERE id=%s", (req_id,))
        conn.commit()
    conn.close()
    return {"message": "Leave cancelled and balance restored."}


# ── Public Holidays ──────────────────────────────────────────────────────────

@router.get("/holidays")
def list_holidays(company_id: int, year: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT * FROM public_holidays
            WHERE company_id=%s AND year=%s AND is_active=TRUE
            ORDER BY date
        """, (company_id, year))
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            for k, v in r.items():
                if hasattr(v, 'isoformat'):
                    r[k] = v.isoformat()
    conn.close()
    return rows

@router.post("/holidays")
def create_holiday(data: HolidayCreate, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO public_holidays (company_id, name, name_ar, date, year)
            VALUES (%s,%s,%s,%s,%s) RETURNING id
        """, (data.company_id, data.name, data.name_ar, data.date, data.year))
        h_id = cur.fetchone()["id"]
        conn.commit()
    conn.close()
    return {"message": "Holiday added.", "id": h_id}

@router.delete("/holidays/{h_id}")
def delete_holiday(h_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE public_holidays SET is_active=FALSE WHERE id=%s", (h_id,))
        conn.commit()
    conn.close()
    return {"message": "Holiday removed."}


# ── Leave Settings ───────────────────────────────────────────────────────────

@router.get("/settings/{company_id}")
def get_leave_settings(company_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM leave_settings WHERE company_id=%s", (company_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return {
                "company_id": company_id,
                "enable_pro_rata": True,
                "pro_rata_rounding": "half",
                "enable_carry_forward": False,
                "max_carry_days": 0,
                "enable_encashment": False,
                "approval_levels": 1,
                "auto_approve_days": 0,
                "enable_public_holidays": True,
                "enable_half_day": True,
                "first_half_cutoff": "13:00:00"
            }
        result = dict(row)
        for k, v in result.items():
            if hasattr(v, 'isoformat'):
                result[k] = v.isoformat()
    conn.close()
    return result

@router.put("/settings/{company_id}")
def update_leave_settings(company_id: int, data: LeaveSettingsUpdate,
                           current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO leave_settings (
                company_id, enable_pro_rata, pro_rata_rounding,
                enable_carry_forward, max_carry_days, enable_encashment,
                approval_levels, auto_approve_days, enable_public_holidays,
                enable_half_day, first_half_cutoff
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (company_id) DO UPDATE SET
                enable_pro_rata=%s, pro_rata_rounding=%s,
                enable_carry_forward=%s, max_carry_days=%s,
                enable_encashment=%s, approval_levels=%s,
                auto_approve_days=%s, enable_public_holidays=%s,
                enable_half_day=%s, first_half_cutoff=%s
        """, (
            company_id, data.enable_pro_rata, data.pro_rata_rounding,
            data.enable_carry_forward, data.max_carry_days, data.enable_encashment,
            data.approval_levels, data.auto_approve_days, data.enable_public_holidays,
            data.enable_half_day, data.first_half_cutoff,
            data.enable_pro_rata, data.pro_rata_rounding,
            data.enable_carry_forward, data.max_carry_days, data.enable_encashment,
            data.approval_levels, data.auto_approve_days, data.enable_public_holidays,
            data.enable_half_day, data.first_half_cutoff
        ))
        conn.commit()
    conn.close()
    return {"message": "Leave settings updated."}


# ── Leave Summary for Payroll ────────────────────────────────────────────────

@router.get("/payroll-summary")
def get_leave_payroll_summary(employee_id: int, date_from: date, date_to: date,
                               current_user=Depends(get_current_user)):
    """
    Get approved leaves for an employee in a date range.
    Used by payroll to determine which absences are paid/unpaid.
    """
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT lr.start_date, lr.end_date, lr.days_count,
                   lr.is_paid, lr.deduction_percentage,
                   lr.is_half_day, lr.half_day_period,
                   lt.name as leave_type
            FROM leave_requests lr
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.employee_id=%s
              AND lr.status='approved'
              AND lr.start_date <= %s
              AND lr.end_date >= %s
        """, (employee_id, date_to, date_from))
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            for k, v in d.items():
                if hasattr(v, 'isoformat'):
                    d[k] = v.isoformat()
            rows.append(d)
    conn.close()
    return rows
