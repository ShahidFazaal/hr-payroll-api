"""
attendance.py — Attendance router
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime, timedelta
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
            # Ensure key is always YYYY-MM-DD string
            key = str(r["work_date"])[:10]
            roster[key] = d

        # Get all punches (include one extra day for overnight checkouts)
        cur.execute("""
            SELECT DATE(punch_time)::text as punch_date,
                   MIN(punch_time) as first_punch,
                   MAX(punch_time) as last_punch,
                   COUNT(*) as punch_count,
                   array_agg(punch_time ORDER BY punch_time) as all_punches
            FROM attendance_logs
            WHERE employee_id = %s
              AND punch_time::date BETWEEN %s AND %s
            GROUP BY DATE(punch_time)
            ORDER BY DATE(punch_time)
        """, (employee_id, date_from, date_to))
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
        try:
            cur.execute("""
                SELECT lr.start_date::text, lr.end_date::text,
                       lt.name as leave_type, lr.is_paid, lr.is_half_day, lr.half_day_period
                FROM leave_requests lr
                JOIN leave_types lt ON lr.leave_type_id = lt.id
                WHERE lr.employee_id = %s AND lr.status = 'approved'
                  AND lr.start_date <= %s AND lr.end_date >= %s
            """, (employee_id, date_to, date_from))
            leaves = cur.fetchall()
        except Exception:
            leaves = []

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
            date_str  = str(current)[:10]
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


@router.get("/export-data")
def export_attendance_data(
    company_id: int,
    date_from: date,
    date_to: date,
    branch_id: Optional[int] = None,
    employee_id: Optional[int] = None,
    current_user=Depends(get_current_user)
):
    """
    Export attendance data for Excel.
    Returns structured data per employee per day including:
    - Roster info (shift times, day off)
    - Leave info
    - Punch data (check in/out, hours)
    - Status (Present, Absent, Day Off, On Leave, No Roster)
    - Late calculation
    - Comments (midnight punch, missing checkout etc.)
    """
    conn = db.get_conn()
    result = []

    with conn.cursor() as cur:
        # Get employees
        query = """
            SELECT e.id, e.full_name, e.employee_code, e.device_user_id,
                   b.name as branch_name, e.home_branch_id,
                   cs.late_threshold_minutes
            FROM employees e
            LEFT JOIN branches b ON e.home_branch_id = b.id
            LEFT JOIN company_settings cs ON e.company_id = cs.company_id
            WHERE e.company_id = %s AND e.is_active IS NOT FALSE
        """
        params = [company_id]
        if branch_id:
            query += " AND e.home_branch_id = %s"
            params.append(branch_id)
        if employee_id:
            query += " AND e.id = %s"
            params.append(employee_id)
        query += " ORDER BY e.full_name"
        cur.execute(query, params)
        employees = cur.fetchall()

        for emp in employees:
            emp_id = emp["id"]
            late_threshold = emp["late_threshold_minutes"] or 15

            # Get roster for period
            cur.execute("""
                SELECT work_date::text, is_day_off, shift_start::text, shift_end::text
                FROM weekly_roster
                WHERE employee_id = %s AND work_date BETWEEN %s AND %s
            """, (emp_id, date_from, date_to))
            roster = {r["work_date"]: dict(r) for r in cur.fetchall()}

            # Get punches grouped by date - include actual branch from punch
            cur.execute("""
                SELECT DATE(punch_time)::text as punch_date,
                       MIN(punch_time) as first_punch,
                       MAX(punch_time) as last_punch,
                       COUNT(*) as punch_count,
                       MODE() WITHIN GROUP (ORDER BY branch_id) as punch_branch_id
                FROM attendance_logs
                WHERE employee_id = %s
                  AND punch_time::date BETWEEN %s AND %s
                GROUP BY DATE(punch_time)
                ORDER BY DATE(punch_time)
            """, (emp_id, date_from, date_to))
            punches = {r["punch_date"]: dict(r) for r in cur.fetchall()}

            # Get branch names for punch branches
            branch_names = {}
            cur.execute("SELECT id, name FROM branches")
            for b in cur.fetchall():
                branch_names[b["id"]] = b["name"]

            # Get approved leaves
            try:
                cur.execute("""
                    SELECT lr.start_date::text, lr.end_date::text,
                           lt.name as leave_type, lr.is_paid, lr.is_half_day
                    FROM leave_requests lr
                    JOIN leave_types lt ON lr.leave_type_id = lt.id
                    WHERE lr.employee_id = %s AND lr.status = 'approved'
                      AND lr.start_date <= %s AND lr.end_date >= %s
                """, (emp_id, date_to, date_from))
                leaves_raw = cur.fetchall()
            except Exception:
                leaves_raw = []

            # Build leave map
            leave_map = {}
            for lv in leaves_raw:
                start = date.fromisoformat(lv["start_date"])
                end   = date.fromisoformat(lv["end_date"])
                curr  = start
                while curr <= end:
                    leave_map[str(curr)] = lv["leave_type"]
                    curr += timedelta(days=1)

            # Build all dates to show
            # Show: all roster days + all punch days (even without roster)
            all_dates = set()
            current_d = date_from
            while current_d <= date_to:
                ds = str(current_d)
                if ds in roster or ds in punches:
                    all_dates.add(ds)
                current_d += timedelta(days=1)

            emp_rows = []
            for ds in sorted(all_dates):
                roster_day = roster.get(ds)
                punch      = punches.get(ds)
                leave      = leave_map.get(ds)
                day_name   = date.fromisoformat(ds).strftime("%a")

                row = {
                    "employee_name": emp["full_name"],
                    "employee_code": emp["employee_code"] or emp["device_user_id"] or "",
                    "branch":        branch_names.get(punch.get("punch_branch_id") if punch else None,
                                     emp["branch_name"] or "") if punch else emp["branch_name"] or "",
                    "date":          ds,
                    "day":           day_name,
                    "roster":        "",
                    "check_in":      "",
                    "check_out":     "",
                    "hours":         "",
                    "status":        "",
                    "late":          "",
                    "comment":       "",
                }

                # Build roster column
                if roster_day:
                    if roster_day["is_day_off"]:
                        row["roster"] = "Day Off"
                    elif roster_day["shift_start"] and roster_day["shift_end"]:
                        row["roster"] = f"{roster_day['shift_start'][:5]} - {roster_day['shift_end'][:5]}"
                    else:
                        row["roster"] = "Working"
                else:
                    row["roster"] = "No Roster"

                # Process punch data
                if punch:
                    first = punch["first_punch"]
                    last  = punch["last_punch"]

                    comments = []
                    is_midnight_checkin = first and first.hour < 6

                    # Check if this is an overnight checkout from previous day
                    prev_date = str(date.fromisoformat(ds) - timedelta(days=1))
                    prev_punch = punches.get(prev_date)
                    is_prev_day_checkout = (
                        is_midnight_checkin and
                        prev_punch and
                        prev_punch["first_punch"] and
                        prev_punch["first_punch"].hour >= 6
                        # No punch_count check — even if 2 punches, first at midnight = overnight
                    )

                    if is_prev_day_checkout:
                        # First punch (midnight) = checkout from previous day
                        # Remaining punches = this day's own activity
                        overnight_checkout = first  # midnight punch = prev day checkout
                        co = overnight_checkout.strftime("%H:%M")

                        # Update previous day row
                        for prev_row in emp_rows:
                            if prev_row.get("date") == prev_date:
                                prev_row["check_out"] = co
                                if prev_punch["first_punch"]:
                                    h = (overnight_checkout - prev_punch["first_punch"]).total_seconds() / 3600
                                    prev_row["hours"] = round(h, 2)
                                prev_row["comment"] = (prev_row.get("comment", "") + " | ✓ Checkout linked from next day").strip(" | ")
                                prev_row["comment"] = prev_row["comment"].replace("Missing checkout | ", "").replace("Missing checkout", "").strip(" | ")

                        # This day: if more punches exist after midnight one, use them
                        if punch["punch_count"] >= 2 and last and last != first:
                            # Has own checkin/checkout after the overnight checkout
                            ci = last.strftime("%H:%M")  # last punch = own checkout
                            # Find second punch (own checkin) - need all_punches
                            row["check_in"]  = "—"  # no roster so hard to determine
                            row["check_out"] = ci
                            h = (last - first).total_seconds() / 3600
                            row["hours"] = round(h, 2)
                        else:
                            row["check_in"]  = ""
                            row["check_out"] = co
                            row["hours"]     = ""

                        comments.append("⚠ Midnight punch — overnight checkout linked to previous day")
                        row["check_in"]  = ci if punch["punch_count"] >= 2 else ""
                        row["check_out"] = last.strftime("%H:%M") if punch["punch_count"] >= 2 and last != first else co
                    else:
                        ci = first.strftime("%H:%M") if first else ""
                        co = last.strftime("%H:%M") if (last and last != first) else ""
                        row["check_in"]  = ci
                        row["check_out"] = co

                        if first and last and last != first:
                            hours = (last - first).total_seconds() / 3600
                            row["hours"] = round(hours, 2)

                        if is_midnight_checkin:
                            comments.append("⚠ Midnight checkin")
                        if punch["punch_count"] == 1:
                            comments.append("Missing checkout")
                            row["check_out"] = ""
                            row["hours"] = ""

                    row["comment"] = " | ".join(comments)

                # Determine status
                if roster_day:
                    if roster_day["is_day_off"]:
                        if punch:
                            row["status"] = "Day Off (Worked)"
                        else:
                            row["status"] = "Day Off"
                        row["late"] = "—"
                    elif leave:
                        row["status"] = f"On Leave ({leave})"
                        row["late"] = "—"
                    elif punch:
                        # Late calculation
                        if roster_day.get("shift_start") and punch.get("first_punch"):
                            shift_dt = datetime.combine(
                                date.fromisoformat(ds),
                                datetime.strptime(roster_day["shift_start"][:8], "%H:%M:%S").time()
                            )
                            late_mins = (punch["first_punch"] - shift_dt).total_seconds() / 60
                            if late_mins > late_threshold:
                                row["late"]   = f"{int(late_mins)}m late"
                                row["status"] = "Late"
                            else:
                                row["late"]   = "On time"
                                row["status"] = "Present"
                        else:
                            row["status"] = "Present"
                            row["late"]   = "No shift time"
                    else:
                        row["status"] = "Absent"
                        row["late"]   = "—"
                else:
                    # No roster
                    if punch:
                        row["status"] = "Punch (No Roster)"
                        row["late"]   = "No Roster"
                    # If no roster and no punch — we skip (already filtered above)

                emp_rows.append(row)

            # Add summary row
            if emp_rows:
                present = sum(1 for r in emp_rows if r["status"] in ("Present", "Late"))
                absent  = sum(1 for r in emp_rows if r["status"] == "Absent")
                day_off = sum(1 for r in emp_rows if "Day Off" in r["status"])
                on_leave= sum(1 for r in emp_rows if "On Leave" in r["status"])
                no_roster = sum(1 for r in emp_rows if "No Roster" in r["status"])
                total_h = sum(float(r["hours"]) for r in emp_rows if r["hours"] != "")
                summary = {
                    "employee_name": f"SUMMARY: {emp['full_name']}",
                    "employee_code": "",
                    "branch": "",
                    "date": "",
                    "day": "",
                    "roster": "",
                    "check_in": f"Present: {present}",
                    "check_out": f"Absent: {absent}",
                    "hours": f"{round(total_h, 2)}h total",
                    "status": f"Day Off: {day_off} | Leave: {on_leave} | No Roster: {no_roster}",
                    "late": "",
                    "comment": "─" * 30,
                    "_is_summary": True,
                }
                emp_rows.append(summary)
                result.extend(emp_rows)

    conn.close()
    return result
