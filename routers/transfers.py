"""
transfers.py — Employee Transfer Module
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime
import database as db
from routers.auth import get_current_user

router = APIRouter()

REASON_CODES = {
    'OP': 'Operational Need',
    'PR': 'Promotion',
    'EM': 'Employee Request',
    'DS': 'Disciplinary',
    'TR': 'Training',
    'SU': 'Support / Cover',
    'CL': 'Branch Closure',
}

TRANSFER_TYPES = ['permanent', 'temporary', 'secondment']


class TransferCreate(BaseModel):
    employee_id:    int
    from_branch_id: int
    to_branch_id:   int
    transfer_type:  str = 'permanent'
    reason_code:    Optional[str] = 'OP'
    reason_notes:   Optional[str] = None
    effective_date: date
    return_date:    Optional[date] = None


class TransferAck(BaseModel):
    role: str  # 'from' or 'to'


# ── Migration ─────────────────────────────────────────────────────────────────

def migrate_transfers(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS employee_transfers (
                id               SERIAL PRIMARY KEY,
                employee_id      INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                from_branch_id   INTEGER REFERENCES branches(id),
                to_branch_id     INTEGER REFERENCES branches(id),
                transfer_type    TEXT NOT NULL DEFAULT 'permanent',
                reason_code      TEXT DEFAULT 'OP',
                reason_notes     TEXT,
                effective_date   DATE NOT NULL,
                return_date      DATE,
                transferred_by   INTEGER REFERENCES users(id),
                status           TEXT DEFAULT 'pending',
                from_manager_ack BOOLEAN DEFAULT FALSE,
                to_manager_ack   BOOLEAN DEFAULT FALSE,
                created_at       TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/pending-notifications")
def get_pending_notifications(branch_id: int, current_user=Depends(get_current_user)):
    """Get incoming and outgoing transfers for a branch manager."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT t.*,
                   e.full_name, e.employee_code,
                   fb.name as from_branch_name,
                   tb.name as to_branch_name,
                   u.full_name as transferred_by_name
            FROM employee_transfers t
            JOIN employees e ON t.employee_id = e.id
            JOIN branches fb ON t.from_branch_id = fb.id
            JOIN branches tb ON t.to_branch_id = tb.id
            LEFT JOIN users u ON t.transferred_by = u.id
            WHERE (t.from_branch_id = %s OR t.to_branch_id = %s)
              AND t.status IN ('pending', 'confirmed', 'active')
              AND t.effective_date >= CURRENT_DATE
            ORDER BY t.effective_date ASC
        """, (branch_id, branch_id))
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/")
def get_transfers(
    employee_id: Optional[int] = None,
    company_id:  Optional[int] = None,
    branch_id:   Optional[int] = None,
    status:      Optional[str] = None,
    current_user=Depends(get_current_user)
):
    conn = db.get_conn()
    with conn.cursor() as cur:
        query = """
            SELECT t.*,
                   e.full_name, e.employee_code,
                   fb.name as from_branch_name,
                   tb.name as to_branch_name,
                   u.full_name as transferred_by_name
            FROM employee_transfers t
            JOIN employees e  ON t.employee_id    = e.id
            JOIN branches fb  ON t.from_branch_id = fb.id
            JOIN branches tb  ON t.to_branch_id   = tb.id
            LEFT JOIN users u ON t.transferred_by  = u.id
            WHERE 1=1
        """
        params = []
        if employee_id:
            query += " AND t.employee_id = %s"; params.append(employee_id)
        if company_id:
            query += " AND e.company_id = %s"; params.append(company_id)
        if branch_id:
            query += " AND (t.from_branch_id=%s OR t.to_branch_id=%s)"
            params.extend([branch_id, branch_id])
        if status:
            query += " AND t.status = %s"; params.append(status)
        query += " ORDER BY t.effective_date DESC, t.created_at DESC"
        cur.execute(query, params)
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/")
def create_transfer(data: TransferCreate, current_user=Depends(get_current_user)):
    if data.from_branch_id == data.to_branch_id:
        raise HTTPException(status_code=400, detail="From and To branch must be different.")
    if data.transfer_type == 'temporary' and not data.return_date:
        raise HTTPException(status_code=400, detail="Return date required for temporary transfers.")
    if data.return_date and data.return_date <= data.effective_date:
        raise HTTPException(status_code=400, detail="Return date must be after effective date.")

    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            # Ensure table exists
            try:
                migrate_transfers(conn)
            except Exception:
                pass

            # Verify employee exists
            cur.execute("SELECT id, full_name, home_branch_id FROM employees WHERE id=%s",
                       (data.employee_id,))
            emp = cur.fetchone()
            if not emp:
                raise HTTPException(status_code=404, detail="Employee not found.")

            # Create transfer record
            cur.execute("""
                INSERT INTO employee_transfers
                    (employee_id, from_branch_id, to_branch_id, transfer_type,
                     reason_code, reason_notes, effective_date, return_date,
                     transferred_by, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
                RETURNING id
            """, (data.employee_id, data.from_branch_id, data.to_branch_id,
                  data.transfer_type, data.reason_code, data.reason_notes,
                  data.effective_date, data.return_date,
                  current_user["user_id"]))
            tid = cur.fetchone()["id"]

            # If effective date is today or past, update employee home branch immediately
            if data.effective_date <= date.today():
                cur.execute("""
                    UPDATE employees SET home_branch_id=%s WHERE id=%s
                """, (data.to_branch_id, data.employee_id))
                cur.execute("""
                    UPDATE employee_transfers SET status='active' WHERE id=%s
                """, (tid,))

            conn.commit()
    except HTTPException:
        conn.rollback(); conn.close(); raise
    except Exception as e:
        conn.rollback(); conn.close()
        raise HTTPException(status_code=500, detail=f"Failed to create transfer: {str(e)}")

    conn.close()
    return {"id": tid, "message": "Transfer created successfully."}


@router.put("/{transfer_id}/acknowledge")
def acknowledge_transfer(transfer_id: int, data: TransferAck,
                          current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        if data.role == 'from':
            cur.execute("""
                UPDATE employee_transfers SET from_manager_ack=TRUE WHERE id=%s
            """, (transfer_id,))
        elif data.role == 'to':
            cur.execute("""
                UPDATE employee_transfers SET to_manager_ack=TRUE WHERE id=%s
            """, (transfer_id,))
        # If both acknowledged, mark confirmed
        cur.execute("""
            UPDATE employee_transfers
            SET status='confirmed'
            WHERE id=%s AND from_manager_ack=TRUE AND to_manager_ack=TRUE
              AND status='pending'
        """, (transfer_id,))
        conn.commit()
    conn.close()
    return {"message": "Transfer acknowledged."}


@router.put("/{transfer_id}/activate")
def activate_transfer(transfer_id: int, current_user=Depends(get_current_user)):
    """Manually activate a transfer and update employee branch."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM employee_transfers WHERE id=%s", (transfer_id,))
        t = cur.fetchone()
        if not t:
            raise HTTPException(status_code=404, detail="Transfer not found.")
        # Update employee home branch
        cur.execute("UPDATE employees SET home_branch_id=%s WHERE id=%s",
                    (t["to_branch_id"], t["employee_id"]))
        cur.execute("UPDATE employee_transfers SET status='active' WHERE id=%s",
                    (transfer_id,))
        conn.commit()
    conn.close()
    return {"message": "Transfer activated. Employee branch updated."}


@router.put("/{transfer_id}/complete")
def complete_transfer(transfer_id: int, current_user=Depends(get_current_user)):
    """Complete a temporary transfer - return employee to original branch."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM employee_transfers WHERE id=%s", (transfer_id,))
        t = cur.fetchone()
        if not t:
            raise HTTPException(status_code=404, detail="Transfer not found.")
        if t["transfer_type"] == 'temporary':
            # Return to original branch
            cur.execute("UPDATE employees SET home_branch_id=%s WHERE id=%s",
                        (t["from_branch_id"], t["employee_id"]))
        cur.execute("UPDATE employee_transfers SET status='completed' WHERE id=%s",
                    (transfer_id,))
        conn.commit()
    conn.close()
    return {"message": "Transfer completed."}


@router.delete("/{transfer_id}")
def cancel_transfer(transfer_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE employee_transfers SET status='cancelled' WHERE id=%s
        """, (transfer_id,))
        conn.commit()
    conn.close()
    return {"message": "Transfer cancelled."}


@router.get("/upcoming-returns")
def get_upcoming_returns(days_ahead: int = 7, current_user=Depends(get_current_user)):
    """Get temporary transfers due to return within X days."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT t.*,
                   e.full_name, e.employee_code,
                   fb.name as from_branch_name,
                   tb.name as to_branch_name
            FROM employee_transfers t
            JOIN employees e ON t.employee_id = e.id
            JOIN branches fb ON t.from_branch_id = fb.id
            JOIN branches tb ON t.to_branch_id = tb.id
            WHERE t.transfer_type = 'temporary'
              AND t.status = 'active'
              AND t.return_date BETWEEN CURRENT_DATE AND CURRENT_DATE + %s
            ORDER BY t.return_date ASC
        """, (days_ahead,))
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]
