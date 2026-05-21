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
    with conn.cursor() as cur:
        for entry in data.entries:
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
        conn.commit()
    conn.close()
    return {"message": f"Roster saved. {len(data.entries)} entries."}

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
