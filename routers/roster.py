"""
roster.py — Weekly Roster router
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, timedelta
import database as db
from routers.auth import get_current_user

router = APIRouter()

class RosterEntry(BaseModel):
    employee_id: int
    branch_id: int
    work_date: date
    is_day_off: bool = False
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    next_day_end: bool = False
    notes: Optional[str] = None

class RosterBulk(BaseModel):
    entries: List[RosterEntry]

@router.get("/")
def get_roster(week_start: date, branch_id: Optional[int] = None,
               company_id: Optional[int] = None, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        query = """
            SELECT r.*, e.full_name, e.employee_code, b.name as branch_name
            FROM weekly_roster r
            JOIN employees e ON r.employee_id = e.id
            JOIN branches b ON r.branch_id = b.id
            WHERE r.week_start_date = %s
        """
        params = [week_start]
        if current_user["role"] == "branch_manager":
            query += " AND r.branch_id = %s"
            params.append(current_user["branch_id"])
        elif branch_id:
            query += " AND r.branch_id = %s"
            params.append(branch_id)
        if company_id:
            query += " AND e.company_id = %s"
            params.append(company_id)
        query += " ORDER BY e.full_name, r.work_date"
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

@router.post("/bulk")
def save_roster_bulk(data: RosterBulk, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    saved = 0
    with conn.cursor() as cur:
        for entry in data.entries:
            try:
                cur.execute("SAVEPOINT roster_entry")
                week_start = entry.work_date - timedelta(days=entry.work_date.weekday())
                cur.execute("""
                    INSERT INTO weekly_roster
                        (employee_id, branch_id, week_start_date, work_date,
                         is_day_off, shift_start, shift_end, notes, created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (employee_id, work_date)
                    DO UPDATE SET branch_id=%s, is_day_off=%s,
                        shift_start=%s, shift_end=%s, notes=%s
                """, (entry.employee_id, entry.branch_id, week_start, entry.work_date,
                      entry.is_day_off, entry.shift_start, entry.shift_end,
                      entry.notes, current_user["user_id"],
                      entry.branch_id, entry.is_day_off,
                      entry.shift_start, entry.shift_end, entry.notes))
                cur.execute("RELEASE SAVEPOINT roster_entry")
                saved += 1
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT roster_entry")
                print(f">>> Roster save error: {e}")
                continue

        # Try next_day_end updates separately
        for entry in data.entries:
            try:
                cur.execute("SAVEPOINT next_day")
                cur.execute("""
                    UPDATE weekly_roster SET next_day_end=%s
                    WHERE employee_id=%s AND work_date=%s
                """, (entry.next_day_end, entry.employee_id, entry.work_date))
                cur.execute("RELEASE SAVEPOINT next_day")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT next_day")

        conn.commit()
    conn.close()
    return {"message": f"Roster saved. {saved} entries."}

@router.delete("/")
def clear_roster(week_start: date, branch_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM weekly_roster
            WHERE week_start_date = %s AND branch_id = %s
        """, (week_start, branch_id))
        conn.commit()
    conn.close()
    return {"message": "Roster cleared."}


@router.get("/export-excel")
def export_roster_excel(week_start: date, branch_id: int,
                         current_user=Depends(get_current_user)):
    """Export roster as Excel-friendly JSON for frontend to convert."""
    from datetime import timedelta

    conn = db.get_conn()
    with conn.cursor() as cur:
        # Get all active employees for this branch
        cur.execute("""
            SELECT e.id, e.full_name, e.employee_code, e.device_user_id
            FROM employees e
            WHERE e.home_branch_id = %s AND e.is_active IS NOT FALSE
            ORDER BY e.full_name
        """, (branch_id,))
        employees = cur.fetchall()

        # Get existing roster for this week
        cur.execute("""
            SELECT r.employee_id, r.work_date::text, r.is_day_off,
                   r.shift_start::text, r.shift_end::text
            FROM weekly_roster r
            WHERE r.branch_id = %s AND r.week_start_date = %s
        """, (branch_id, week_start))
        roster_rows = cur.fetchall()

        # Build roster map
        roster_map = {}
        for r in roster_rows:
            key = f"{r['employee_id']}_{r['work_date']}"
            roster_map[key] = r

        # Build week dates
        days = []
        for i in range(7):
            days.append(str(week_start + timedelta(days=i)))

        # Build export rows
        rows = []
        for emp in employees:
            row = {
                "employee_code": emp["employee_code"] or emp["device_user_id"] or str(emp["id"]),
                "employee_name": emp["full_name"],
                "employee_id": emp["id"],
            }
            for day in days:
                key = f"{emp['id']}_{day}"
                r = roster_map.get(key)
                row[f"{day}_status"] = "Day Off" if (r and r["is_day_off"]) else "Working"
                row[f"{day}_start"]  = r["shift_start"][:5] if (r and r["shift_start"]) else ""
                row[f"{day}_end"]    = r["shift_end"][:5] if (r and r["shift_end"]) else ""
            rows.append(row)

    conn.close()
    return {"week_start": str(week_start), "days": days, "rows": rows}


@router.post("/import-excel")
def import_roster_excel(data: dict, current_user=Depends(get_current_user)):
    """
    Import roster from Excel data.
    Expects: { branch_id, week_start, rows: [{employee_code, day_status, day_start, day_end}] }
    Matches employees by employee_code or device_user_id.
    """
    from datetime import timedelta

    branch_id  = data.get("branch_id")
    week_start = data.get("week_start")
    rows       = data.get("rows", [])

    if not branch_id or not week_start:
        raise HTTPException(status_code=400, detail="branch_id and week_start required.")

    conn = db.get_conn()
    saved = 0
    errors = []

    with conn.cursor() as cur:
        for row in rows:
            emp_code = str(row.get("employee_code", "")).strip()
            if not emp_code:
                continue

            # Find employee by code or device_user_id
            cur.execute("""
                SELECT id FROM employees
                WHERE (employee_code = %s OR device_user_id = %s)
                  AND home_branch_id = %s
                  AND is_active IS NOT FALSE
                LIMIT 1
            """, (emp_code, emp_code, branch_id))
            emp = cur.fetchone()

            if not emp:
                errors.append(f"Employee code '{emp_code}' not found in this branch.")
                continue

            emp_id = emp["id"]

            # Process each day
            for day_str, status in row.get("days", {}).items():
                try:
                    work_date  = date.fromisoformat(day_str)
                    week_dt    = work_date - timedelta(days=work_date.weekday())
                    is_day_off = str(status.get("status", "Working")).strip().lower() in ("day off", "dayoff", "off")
                    shift_start = status.get("start") or None
                    shift_end   = status.get("end") or None

                    cur.execute("""
                        INSERT INTO weekly_roster
                            (employee_id, branch_id, week_start_date, work_date,
                             is_day_off, shift_start, shift_end, created_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (employee_id, work_date)
                        DO UPDATE SET
                            is_day_off=%s, shift_start=%s, shift_end=%s
                    """, (emp_id, branch_id, str(week_dt), day_str,
                          is_day_off, shift_start, shift_end, current_user["user_id"],
                          is_day_off, shift_start, shift_end))
                    saved += 1
                except Exception as e:
                    errors.append(f"Error on {emp_code} {day_str}: {str(e)}")

        conn.commit()
    conn.close()

    return {
        "message": f"Imported {saved} roster entries.",
        "saved": saved,
        "errors": errors,
    }
