"""
attendance.py — Attendance router
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime
import database as db
from routers.auth import get_current_user

router = APIRouter()

class AttendanceLog(BaseModel):
    user_id: str
    punch_time: str
    punch_type: Optional[str] = None
    status: Optional[int] = None

class AttendancePush(BaseModel):
    company_id: int
    branch_id: int
    date_from: str
    date_to: str
    logs: List[AttendanceLog]

@router.post("/push")
def push_attendance(data: AttendancePush, current_user=Depends(get_current_user)):
    """Called by cloud agent to push attendance logs from ZK device."""
    conn = db.get_conn()
    added = 0
    skipped = 0
    with conn.cursor() as cur:
        for log in data.logs:
            try:
                punch_time = datetime.fromisoformat(str(log.punch_time))
                # Find employee by device_user_id
                cur.execute("""
                    SELECT id FROM employees
                    WHERE company_id = %s AND device_user_id = %s
                """, (data.company_id, str(log.user_id)))
                emp = cur.fetchone()
                employee_id = emp["id"] if emp else None

                cur.execute("""
                    INSERT INTO attendance_logs
                        (company_id, branch_id, device_user_id, employee_id,
                         punch_time, punch_type, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (branch_id, device_user_id, punch_time) DO NOTHING
                """, (data.company_id, data.branch_id, str(log.user_id),
                      employee_id, punch_time, log.punch_type, log.status))

                if cur.rowcount > 0:
                    added += 1
                else:
                    skipped += 1
            except Exception as e:
                skipped += 1
        conn.commit()
    conn.close()
    return {"message": f"Push complete.", "added": added, "skipped": skipped}

@router.get("/")
def get_attendance(company_id: Optional[int] = None,
                   branch_id: Optional[int] = None,
                   employee_id: Optional[int] = None,
                   date_from: Optional[date] = None,
                   date_to: Optional[date] = None,
                   current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        query = """
            SELECT a.*, e.full_name, e.employee_code, b.name as branch_name
            FROM attendance_logs a
            LEFT JOIN employees e ON a.employee_id = e.id
            LEFT JOIN branches b ON a.branch_id = b.id
            WHERE 1=1
        """
        params = []
        if current_user["role"] == "branch_manager":
            query += " AND a.branch_id = %s"
            params.append(current_user["branch_id"])
        else:
            if branch_id:
                query += " AND a.branch_id = %s"
                params.append(branch_id)
        if company_id:
            query += " AND a.company_id = %s"
            params.append(company_id)
        if employee_id:
            query += " AND a.employee_id = %s"
            params.append(employee_id)
        if date_from:
            query += " AND a.punch_time >= %s"
            params.append(date_from)
        if date_to:
            query += " AND a.punch_time < %s"
            params.append(date_to)
        query += " ORDER BY a.punch_time DESC LIMIT 5000"
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

@router.get("/summary")
def get_attendance_summary(employee_id: int,
                            date_from: date,
                            date_to: date,
                            current_user=Depends(get_current_user)):
    """Get per-day attendance summary for an employee across all branches."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                DATE(punch_time) as work_date,
                b.name as branch_name,
                MIN(punch_time) as first_punch,
                MAX(punch_time) as last_punch,
                COUNT(*) as punch_count
            FROM attendance_logs a
            LEFT JOIN branches b ON a.branch_id = b.id
            WHERE a.employee_id = %s
              AND a.punch_time >= %s
              AND a.punch_time < %s
            GROUP BY DATE(punch_time), b.name, a.branch_id
            ORDER BY work_date
        """, (employee_id, date_from, date_to))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@router.get("/daily-summary")
def get_daily_summary(employee_id: int,
                       date_from: date,
                       date_to: date,
                       current_user=Depends(get_current_user)):
    """
    Returns a daily attendance summary for one employee.
    Groups punches by date, matches with roster and leave records.
    """
    from datetime import timedelta

    conn = db.get_conn()
    with conn.cursor() as cur:

        # Get employee info + company settings
        cur.execute("""
            SELECT e.*, cs.late_threshold_minutes, cs.standard_hours_per_day
            FROM employees e
            LEFT JOIN company_settings cs ON e.company_id = cs.company_id
            WHERE e.id = %s
        """, (employee_id,))
        emp = cur.fetchone()
        if not emp:
            conn.close()
            raise HTTPException(status_code=404, detail="Employee not found.")

        late_threshold = emp["late_threshold_minutes"] or 15
        std_hours = float(emp["standard_hours_per_day"] or 8)

        # Get roster for period (include one extra day for overnight)
        cur.execute("""
            SELECT work_date::text, is_day_off, shift_start, shift_end
            FROM weekly_roster
            WHERE employee_id = %s AND work_date BETWEEN %s AND %s
        """, (employee_id, date_from, date_to))
        roster = {}
        for r in cur.fetchall():
            d = dict(r)
            d["next_day_end"] = d.get("next_day_end", False) or False
            roster[r["work_date"]] = d

        # Get all punches (include one extra day for overnight checkouts)
        cur.execute("""
            SELECT DATE(punch_time)::text as punch_date,
                   MIN(punch_time) as first_punch,
                   MAX(punch_time) as last_punch,
                   COUNT(*) as punch_count,
                   array_agg(punch_time ORDER BY punch_time) as all_punches
            FROM attendance_logs
            WHERE employee_id = %s
              AND punch_time::date BETWEEN %s AND (%s::date + interval '1 day')::date
            GROUP BY DATE(punch_time)
            ORDER BY DATE(punch_time)
        """, (employee_id, date_from, date_to, date_to))
        raw_punches = cur.fetchall()

        # Build punch map by date
        punches_by_date = {}
        for p in raw_punches:
            punches_by_date[p["punch_date"]] = {
                "first": p["first_punch"],
                "last": p["last_punch"],
                "count": p["punch_count"],
                "all": p["all_punches"],
            }

        # Get approved leaves for period
        cur.execute("""
            SELECT lr.start_date::text, lr.end_date::text,
                   lt.name as leave_type, lr.is_paid, lr.is_half_day, lr.half_day_period
            FROM leave_requests lr
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.employee_id = %s AND lr.status = 'approved'
              AND lr.start_date <= %s AND lr.end_date >= %s
        """, (employee_id, date_to, date_from))
        leaves = cur.fetchall()

        # Build leave date map
        leave_map = {}
        for lv in leaves:
            start = datetime.strptime(lv["start_date"], "%Y-%m-%d").date()
            end   = datetime.strptime(lv["end_date"], "%Y-%m-%d").date()
            curr  = start
            while curr <= end:
                leave_map[str(curr)] = {
                    "leave_type": lv["leave_type"],
                    "is_paid": lv["is_paid"],
                    "is_half_day": lv["is_half_day"],
                    "half_day_period": lv["half_day_period"],
                }
                curr += timedelta(days=1)

        # Build daily summary — roster days
        roster_days = []
        other_days  = []
        current = date_from
        while current <= date_to:
            date_str  = str(current)
            roster_day = roster.get(date_str)
            punch      = punches_by_date.get(date_str)
            leave      = leave_map.get(date_str)

            day_data = {
                "date":        date_str,
                "day_name":    current.strftime("%a"),
                "in_roster":   roster_day is not None,
                "is_day_off":  roster_day["is_day_off"] if roster_day else None,
                "shift_start": str(roster_day["shift_start"]) if roster_day and roster_day.get("shift_start") else None,
                "shift_end":   str(roster_day["shift_end"])   if roster_day and roster_day.get("shift_end")   else None,
                "check_in":    None,
                "check_out":   None,
                "hours_worked": None,
                "punch_count": 0,
                "punch_times": [],
                "late_minutes": 0,
                "status":      "no_roster",
                "leave":       None,
            }

            # Attach punch detail
            if punch:
                day_data["check_in"]    = punch["first"].isoformat() if punch["first"] else None
                day_data["check_out"]   = punch["last"].isoformat()  if punch["last"]  else None
                day_data["punch_count"] = punch["count"]
                day_data["punch_times"] = [p.isoformat() for p in (punch.get("all") or [])]
                if punch["first"] and punch["last"] and punch["first"] != punch["last"]:
                    hours = (punch["last"] - punch["first"]).total_seconds() / 3600
                    day_data["hours_worked"] = round(hours, 2)

            if roster_day:
                next_day_end  = roster_day.get("next_day_end", False)
                next_date_str = str(current + timedelta(days=1))
                punch_next    = punches_by_date.get(next_date_str) if next_day_end else None

                if roster_day["is_day_off"]:
                    day_data["status"] = "worked_on_day_off" if punch else "day_off"
                else:
                    if leave:
                        day_data["status"] = "on_leave"
                        day_data["leave"]  = leave
                    elif punch:
                        # Overnight: last punch is from next day
                        if next_day_end and punch_next:
                            day_data["check_out"]    = punch_next["last"].isoformat()
                            day_data["overnight"]    = True
                            if punch["first"] and punch_next["last"]:
                                hours = (punch_next["last"] - punch["first"]).total_seconds() / 3600
                                day_data["hours_worked"] = round(hours, 2)
                        if roster_day.get("shift_start") and punch["first"]:
                            shift_dt  = datetime.combine(current,
                                datetime.strptime(str(roster_day["shift_start"]), "%H:%M:%S").time())
                            late_mins = (punch["first"] - shift_dt).total_seconds() / 60
                            if late_mins > late_threshold:
                                day_data["late_minutes"] = int(late_mins)
                                day_data["status"]       = "late"
                            else:
                                day_data["status"] = "present"
                        else:
                            day_data["status"] = "present"
                        # Missing checkout (not for overnight)
                        if not next_day_end and punch["count"] == 1:
                            day_data["missing_checkout"] = True
                            if day_data["status"] == "present":
                                day_data["status"] = "missing_checkout"
                    else:
                        day_data["status"] = "absent"
                roster_days.append(day_data)

            elif punch:
                # Has punches but not in roster — show in other section
                day_data["status"] = "unrostered_punch"
                other_days.append(day_data)

            current += timedelta(days=1)

    conn.close()

    # Summary counts
    all_days = roster_days + other_days
    summary = {
        "working_days":      sum(1 for d in roster_days if not d["is_day_off"]),
        "day_off":           sum(1 for d in roster_days if d["status"] == "day_off"),
        "present":           sum(1 for d in roster_days if d["status"] in ("present","late","missing_checkout")),
        "absent":            sum(1 for d in roster_days if d["status"] == "absent"),
        "late":              sum(1 for d in roster_days if d["status"] == "late"),
        "on_leave":          sum(1 for d in roster_days if d["status"] == "on_leave"),
        "worked_on_day_off": sum(1 for d in roster_days if d["status"] == "worked_on_day_off"),
        "unrostered":        len(other_days),
        "total_hours":       round(sum(d["hours_worked"] or 0 for d in all_days), 2),
    }

    return {"days": roster_days, "other_days": other_days, "summary": summary}
