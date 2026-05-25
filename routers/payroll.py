"""
payroll.py — Payroll generation router
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime, timedelta
import database as db
from routers.auth import get_current_user

router = APIRouter()

class DeductionOverride(BaseModel):
    employee_id: int
    manual_deduction: float = 0
    warning_deduction: float = 0
    variable_pay: float = 0
    overtime_pay: float = 0
    notes: Optional[str] = None

class PayrollGenerate(BaseModel):
    company_id: int
    period_start: date
    period_end: date
    branch_ids: Optional[List[int]] = None
    employee_ids: Optional[List[int]] = None
    deduction_overrides: Optional[List[DeductionOverride]] = None

def calculate_payroll_for_employee(cur, employee_id, company_id,
                                    period_start, period_end,
                                    override: dict = None):
    """Core payroll calculation logic."""

    # Get employee details
    cur.execute("""
        SELECT e.*,
               COALESCE(cs.late_threshold_minutes, 15) as late_threshold_minutes,
               COALESCE(cs.standard_hours_per_day, 8) as standard_hours_per_day,
               COALESCE(cs.overtime_threshold_hours, 8) as overtime_threshold_hours,
               COALESCE(cs.working_days_per_week, 6) as working_days_per_week
        FROM employees e
        LEFT JOIN company_settings cs ON e.company_id = cs.company_id
        WHERE e.id = %s
    """, (employee_id,))
    emp = cur.fetchone()
    if not emp:
        return None
    emp = dict(emp)

    # Get allowances
    cur.execute("""
        SELECT SUM(amount) as total FROM employee_allowances
        WHERE employee_id = %s AND is_active = TRUE
    """, (employee_id,))
    allowance_row = cur.fetchone()
    total_allowances = float(allowance_row["total"] or 0)

    # Get roster for period
    cur.execute("""
        SELECT work_date, is_day_off, shift_start, shift_end, branch_id,
               COALESCE(next_day_end, FALSE) as next_day_end
        FROM weekly_roster
        WHERE employee_id = %s AND work_date BETWEEN %s AND %s
        ORDER BY work_date
    """, (employee_id, period_start, period_end))
    roster = {str(r["work_date"]): dict(r) for r in cur.fetchall()}

    # Get attendance for period across all branches
    cur.execute("""
        SELECT
            DATE(punch_time) as work_date,
            MIN(punch_time) as first_punch,
            MAX(punch_time) as last_punch,
            COUNT(*) as punch_count,
            b.name as branch_name
        FROM attendance_logs a
        LEFT JOIN branches b ON a.branch_id = b.id
        WHERE a.employee_id = %s
          AND a.punch_time >= %s AND a.punch_time < %s
        GROUP BY DATE(punch_time), a.branch_id, b.name
        ORDER BY work_date
    """, (employee_id, period_start, period_end + timedelta(days=1)))
    attendance = {}
    for row in cur.fetchall():
        d = str(row["work_date"])
        if d not in attendance:
            attendance[d] = []
        attendance[d].append(dict(row))

    # Calculate metrics
    working_days = 0
    day_off_days = 0
    present_days = 0
    absent_days = 0
    late_count = 0
    late_minutes = 0
    early_departure_count = 0
    overtime_hours = 0.0
    total_hours = 0.0
    late_threshold = emp.get("late_threshold_minutes", 15)
    std_hours = float(emp.get("standard_hours_per_day", 8))

    current = period_start
    while current <= period_end:
        date_str = str(current)
        roster_day = roster.get(date_str)

        if roster_day:
            if roster_day["is_day_off"]:
                day_off_days += 1
                current += timedelta(days=1)
                continue
            working_days += 1
            next_day_end = roster_day.get("next_day_end", False)
            att_day = attendance.get(date_str)

            # For overnight shifts - also check next day's punches
            next_date_str = str(current + timedelta(days=1))
            att_next = attendance.get(next_date_str) if next_day_end else None

            if att_day:
                present_days += 1
                first_punch = att_day[0]["first_punch"]

                # Overnight: last punch could be from next day
                if next_day_end and att_next:
                    last_punch = att_next[0]["last_punch"]
                else:
                    last_punch = att_day[0]["last_punch"]

                if first_punch and last_punch:
                    hours_worked = (last_punch - first_punch).total_seconds() / 3600
                    total_hours += hours_worked
                    if hours_worked > std_hours:
                        overtime_hours += hours_worked - std_hours

                # Late check
                if roster_day.get("shift_start") and first_punch:
                    shift_start_dt = datetime.combine(current,
                        datetime.strptime(str(roster_day["shift_start"]), "%H:%M:%S").time())
                    late_mins = (first_punch - shift_start_dt).total_seconds() / 60
                    if late_mins > late_threshold:
                        late_count += 1
                        late_minutes += int(late_mins)

                # Early departure (skip for overnight shifts)
                if not next_day_end and roster_day.get("shift_end") and last_punch:
                    shift_end_dt = datetime.combine(current,
                        datetime.strptime(str(roster_day["shift_end"]), "%H:%M:%S").time())
                    if last_punch < shift_end_dt - timedelta(minutes=15):
                        early_departure_count += 1

            elif att_next and next_day_end:
                # Grace window — uses company setting
                grace_hours = 6  # default, configurable in settings once column migrated
                checkout_time = att_next[0]["last_punch"]
                shift_end_next = datetime.combine(
                    current + timedelta(days=1),
                    datetime.strptime(str(roster_day["shift_end"]), "%H:%M:%S").time()
                ) if roster_day.get("shift_end") else None
                if shift_end_next and checkout_time <= shift_end_next + timedelta(hours=grace_hours):
                    present_days += 1
                else:
                    absent_days += 1
            else:
                absent_days += 1
        current += timedelta(days=1)

    # Salary calculation
    basic = float(emp["basic_salary"])
    daily_rate = basic / working_days if working_days > 0 else 0
    absent_deduction = absent_days * daily_rate
    late_deduction = 0  # manager enters manually
    override = override or {}
    manual_deduction = float(override.get("manual_deduction", 0))
    warning_deduction = float(override.get("warning_deduction", 0))
    variable_pay = float(override.get("variable_pay", 0))
    overtime_pay_amount = float(override.get("overtime_pay", 0))

    final_salary = (basic + total_allowances + variable_pay + overtime_pay_amount
                    - absent_deduction - late_deduction
                    - warning_deduction - manual_deduction)

    return {
        "employee_id": employee_id,
        "full_name": emp["full_name"],
        "employee_code": emp.get("employee_code"),
        "working_days": working_days,
        "day_off_days": day_off_days,
        "present_days": present_days,
        "absent_days": absent_days,
        "late_count": late_count,
        "late_minutes": late_minutes,
        "early_departure_count": early_departure_count,
        "overtime_hours": round(overtime_hours, 2),
        "total_hours": round(total_hours, 2),
        "basic_salary": basic,
        "total_allowances": total_allowances,
        "variable_pay": variable_pay,
        "absent_deduction": round(absent_deduction, 2),
        "late_deduction": late_deduction,
        "warning_deduction": warning_deduction,
        "manual_deduction": manual_deduction,
        "overtime_pay": overtime_pay_amount,
        "final_salary": round(final_salary, 2),
        "notes": override.get("notes", ""),
        "currency": "AED",
    }


@router.post("/generate")
def generate_payroll(data: PayrollGenerate, current_user=Depends(get_current_user)):
    """Generate payroll for a period. Returns list of employee payroll records."""
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            # Get employees
            query = """
                SELECT id FROM employees
                WHERE company_id = %s AND is_active IS NOT FALSE
            """
            params = [data.company_id]
            if data.branch_ids:
                query += " AND home_branch_id = ANY(%s)"
                params.append(data.branch_ids)
            if data.employee_ids:
                query += " AND id = ANY(%s)"
                params.append(data.employee_ids)
            cur.execute(query, params)
            employee_ids = [r["id"] for r in cur.fetchall()]

            # Build override map
            override_map = {}
            if data.deduction_overrides:
                for o in data.deduction_overrides:
                    override_map[o.employee_id] = o.dict()

            results = []
            for emp_id in employee_ids:
                try:
                    result = calculate_payroll_for_employee(
                        cur, emp_id, data.company_id,
                        data.period_start, data.period_end,
                        override_map.get(emp_id)
                    )
                    if result:
                        results.append(result)
                except Exception as e:
                    print(f">>> Payroll error for employee {emp_id}: {e}")
                    continue
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Payroll generation failed: {str(e)}")

    conn.close()
    return {
        "period_start": str(data.period_start),
        "period_end": str(data.period_end),
        "company_id": data.company_id,
        "total_employees": len(results),
        "total_payroll": round(sum(r["final_salary"] for r in results), 2),
        "currency": "AED",
        "records": results,
    }


@router.post("/save")
def save_payroll(data: PayrollGenerate, current_user=Depends(get_current_user)):
    """Generate and save payroll records to database."""
    result = generate_payroll(data, current_user)
    conn = db.get_conn()
    with conn.cursor() as cur:
        for r in result["records"]:
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
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                ON CONFLICT DO NOTHING
            """, (
                r["employee_id"], data.company_id, data.period_start, data.period_end,
                r["working_days"], r["present_days"], r["absent_days"], r["late_count"],
                r["late_minutes"], r["early_departure_count"], r["overtime_hours"],
                r["basic_salary"], r["total_allowances"], r["variable_pay"],
                r["absent_deduction"], r["late_deduction"], r["warning_deduction"],
                r["manual_deduction"], r["overtime_pay"], r["final_salary"],
                r["notes"], "saved", current_user["user_id"]
            ))
        conn.commit()
    conn.close()
    return {"message": "Payroll saved.", "records": len(result["records"])}


@router.get("/history")
def get_payroll_history(company_id: int,
                         period_start: Optional[date] = None,
                         current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        query = """
            SELECT pr.*, e.full_name, e.employee_code
            FROM payroll_records pr
            JOIN employees e ON pr.employee_id = e.id
            WHERE pr.company_id = %s
        """
        params = [company_id]
        if period_start:
            query += " AND pr.period_start = %s"
            params.append(period_start)
        query += " ORDER BY pr.generated_at DESC"
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows
