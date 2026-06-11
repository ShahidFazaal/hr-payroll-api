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


def parse_time(val) -> Optional[str]:
    """Convert any time value (HH:MM string or Excel decimal) to HH:MM string."""
    if val is None or val == '' or val is False:
        return None
    raw = str(val).strip()
    overnight = raw.endswith('+')
    raw = raw.rstrip('+').strip()
    if not raw:
        return None
    # Already HH:MM format
    if ':' in raw:
        parts = raw.split(':')
        try:
            result = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
            return result + ('+' if overnight else '')
        except Exception:
            return None
    # Excel decimal fraction e.g. 0.427 = 10:15
    try:
        frac = float(raw)
        if 0 <= frac < 1:
            total_min = round(frac * 24 * 60)
            h, m = divmod(total_min, 60)
            return f"{h:02d}:{m:02d}" + ('+' if overnight else '')
        # Sometimes Excel gives seconds or large floats
        if frac >= 1:
            total_min = round(frac * 24 * 60) % (24 * 60)
            h, m = divmod(total_min, 60)
            return f"{h:02d}:{m:02d}" + ('+' if overnight else '')
    except Exception:
        pass
    return None


@router.get("/transfer-employees")
def get_transfer_employees(week_start: date, branch_id: int,
                            current_user=Depends(get_current_user)):
    """Get employees transferring to/from a branch within a given week."""
    week_end = week_start + timedelta(days=6)
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.id as employee_id, e.full_name, e.employee_code,
                   t.effective_date, t.transfer_type,
                   t.from_branch_id, t.to_branch_id,
                   fb.name as from_branch_name,
                   tb.name as to_branch_name,
                   CASE WHEN t.to_branch_id = %s THEN 'incoming' ELSE 'outgoing' END as direction
            FROM employee_transfers t
            JOIN employees e ON t.employee_id = e.id
            JOIN branches fb ON t.from_branch_id = fb.id
            JOIN branches tb ON t.to_branch_id = tb.id
            WHERE (t.to_branch_id = %s OR t.from_branch_id = %s)
              AND t.status IN ('pending','confirmed','active')
              AND t.effective_date BETWEEN %s AND %s
              AND e.is_active IS NOT FALSE
        """, (branch_id, branch_id, branch_id, week_start, week_end))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


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


@router.delete("/entry")
def delete_roster_entry(employee_id: int, work_date: date,
                         current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM weekly_roster WHERE employee_id=%s AND work_date=%s",
                    (employee_id, work_date))
        conn.commit()
    conn.close()
    return {"message": "Roster entry deleted."}


@router.delete("/")
def clear_roster(week_start: date, branch_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM weekly_roster WHERE week_start_date=%s AND branch_id=%s",
                    (week_start, branch_id))
        conn.commit()
    conn.close()
    return {"message": "Roster cleared."}


@router.get("/export-excel")
def export_roster_excel(week_start: date, branch_id: int,
                         current_user=Depends(get_current_user)):
    """Export roster as Excel-friendly JSON for frontend to convert."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        # Get regular branch employees
        cur.execute("""
            SELECT e.id, e.full_name, e.employee_code, e.device_user_id,
                   FALSE as is_transfer
            FROM employees e
            WHERE e.home_branch_id = %s AND e.is_active IS NOT FALSE
        """, (branch_id,))
        employees = list(cur.fetchall())

        # Get employees transferring TO this branch
        # whose effective date falls within the roster week
        week_end = str(week_start + timedelta(days=6))
        cur.execute("""
            SELECT e.id, e.full_name, e.employee_code, e.device_user_id,
                   TRUE as is_transfer,
                   t.effective_date, t.from_branch_id,
                   fb.name as from_branch_name
            FROM employee_transfers t
            JOIN employees e ON t.employee_id = e.id
            JOIN branches fb ON t.from_branch_id = fb.id
            WHERE t.to_branch_id = %s
              AND t.status IN ('pending','confirmed','active')
              AND t.effective_date BETWEEN %s AND %s
              AND e.is_active IS NOT FALSE
              AND e.home_branch_id != %s
        """, (branch_id, str(week_start), week_end, branch_id))
        transfer_emps = cur.fetchall()

        # Merge - avoid duplicates
        existing_ids = {e['id'] for e in employees}
        for te in transfer_emps:
            if te['id'] not in existing_ids:
                employees.append(te)
                existing_ids.add(te['id'])

        employees = sorted(employees, key=lambda e: e['full_name'])

        cur.execute("""
            SELECT r.employee_id, r.work_date::text, r.is_day_off,
                   r.shift_start::text, r.shift_end::text, r.next_day_end
            FROM weekly_roster r
            WHERE r.branch_id = %s AND r.week_start_date = %s
        """, (branch_id, week_start))
        roster_rows = cur.fetchall()

        roster_map = {}
        for r in roster_rows:
            key = f"{r['employee_id']}_{r['work_date']}"
            roster_map[key] = r

        days = [str(week_start + timedelta(days=i)) for i in range(7)]

        rows = []
        for emp in employees:
            row = {
                "employee_code": emp["employee_code"] or emp["device_user_id"] or str(emp["id"]),
                "employee_name": emp["full_name"],
                "employee_id":   emp["id"],
            }
            for day in days:
                key = f"{emp['id']}_{day}"
                r = roster_map.get(key)
                if r is None:
                    row[f"{day}_status"] = ""
                elif r["is_day_off"]:
                    row[f"{day}_status"] = "Day Off"
                else:
                    row[f"{day}_status"] = "Working"
                if r and r["shift_start"] and r["shift_end"]:
                    end_str = r["shift_end"][:5]
                    if r.get("next_day_end"):
                        end_str += "+"
                    row[f"{day}_start"] = r["shift_start"][:5]
                    row[f"{day}_end"]   = end_str
                else:
                    row[f"{day}_start"] = ""
                    row[f"{day}_end"]   = ""
            rows.append(row)

    conn.close()
    return {"week_start": str(week_start), "days": days, "rows": rows}


@router.post("/import-excel")
def import_roster_excel(data: dict, current_user=Depends(get_current_user)):
    """Import roster from Excel. Handles decimal time values from Excel."""
    branch_id  = data.get("branch_id")
    week_start = data.get("week_start")
    rows       = data.get("rows", [])

    if not branch_id or not week_start:
        raise HTTPException(status_code=400, detail="branch_id and week_start required.")

    conn = db.get_conn()
    saved  = 0
    errors = []

    with conn.cursor() as cur:
        for row in rows:
            emp_code = str(row.get("employee_code", "")).strip()
            if not emp_code:
                continue

            # Find employee across whole company (not restricted to home_branch)
            cur.execute("""
                SELECT id FROM employees
                WHERE (employee_code = %s OR device_user_id = %s)
                  AND is_active IS NOT FALSE
                LIMIT 1
            """, (emp_code, emp_code))
            emp = cur.fetchone()

            if not emp:
                errors.append(f"Employee code '{emp_code}' not found.")
                continue

            emp_id = emp["id"]

            for day_str, entry in row.get("days", {}).items():
                try:
                    work_date = date.fromisoformat(day_str)
                    week_dt   = work_date - timedelta(days=work_date.weekday())

                    status_val   = str(entry.get("status", "") or "").strip()
                    status_lower = status_val.lower()
                    is_day_off   = status_lower in ("day off", "dayoff", "off", "holiday")

                    # Convert Excel decimal times to HH:MM
                    raw_end      = entry.get("end", "") or ""
                    next_day_end = str(raw_end).strip().endswith("+")
                    shift_start  = parse_time(entry.get("start", ""))
                    shift_end    = parse_time(raw_end)
                    # Strip + from shift_end before storing
                    if shift_end and shift_end.endswith("+"):
                        shift_end = shift_end[:-1]

                    try:
                        cur.execute("SAVEPOINT rentry")
                        cur.execute("""
                            INSERT INTO weekly_roster
                                (employee_id, branch_id, work_date, week_start_date,
                                 is_day_off, shift_start, shift_end, next_day_end, created_by)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (employee_id, work_date) DO UPDATE SET
                                is_day_off=%s, shift_start=%s, shift_end=%s,
                                next_day_end=%s, branch_id=%s, week_start_date=%s
                        """, (emp_id, branch_id, work_date, week_dt,
                              is_day_off, shift_start, shift_end, next_day_end,
                              current_user["user_id"],
                              is_day_off, shift_start, shift_end,
                              next_day_end, branch_id, week_dt))
                        cur.execute("RELEASE SAVEPOINT rentry")
                        saved += 1
                    except Exception as ex:
                        cur.execute("ROLLBACK TO SAVEPOINT rentry")
                        errors.append(f"Error on {emp_code} {day_str}: {str(ex)}")

                except Exception as ex:
                    errors.append(f"Error on {emp_code} {day_str}: {str(ex)}")

        conn.commit()
    conn.close()

    return {
        "message": f"Imported {saved} roster entries.",
        "saved":   saved,
        "errors":  errors,
    }
