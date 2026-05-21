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
