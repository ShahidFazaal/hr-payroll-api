"""
employees.py — Employees router
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
import database as db
from routers.auth import get_current_user

router = APIRouter()

class EmployeeCreate(BaseModel):
    company_id: int
    home_branch_id: int
    device_user_id: Optional[str] = None
    employee_code: Optional[str] = None
    full_name: str
    full_name_ar: Optional[str] = None
    position: Optional[str] = None
    join_date: Optional[date] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    basic_salary: float = 0

class AllowanceCreate(BaseModel):
    allowance_type: str
    amount: float

class EmployeePush(BaseModel):
    company_id: int
    branch_id: int
    employees: List[dict]

@router.get("/")
def list_employees(company_id: Optional[int] = None,
                   branch_id: Optional[int] = None,
                   current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        query = """
            SELECT e.*, c.name as company_name, b.name as branch_name
            FROM employees e
            LEFT JOIN companies c ON e.company_id = c.id
            LEFT JOIN branches b ON e.home_branch_id = b.id
            WHERE e.is_active = TRUE
        """
        params = []
        if current_user["role"] == "branch_manager":
            query += " AND e.home_branch_id = %s"
            params.append(current_user["branch_id"])
        elif current_user["role"] == "company_admin":
            query += " AND e.company_id = %s"
            params.append(current_user["company_id"])
        else:
            if company_id:
                query += " AND e.company_id = %s"
                params.append(company_id)
            if branch_id:
                query += " AND e.home_branch_id = %s"
                params.append(branch_id)

        query += " ORDER BY e.full_name"
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

@router.get("/{employee_id}")
def get_employee(employee_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.*, c.name as company_name, b.name as branch_name
            FROM employees e
            LEFT JOIN companies c ON e.company_id = c.id
            LEFT JOIN branches b ON e.home_branch_id = b.id
            WHERE e.id = %s
        """, (employee_id,))
        emp = cur.fetchone()
        if not emp:
            conn.close()
            raise HTTPException(status_code=404, detail="Employee not found.")

        # Get allowances
        cur.execute("SELECT * FROM employee_allowances WHERE employee_id = %s AND is_active = TRUE",
                    (employee_id,))
        allowances = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {**dict(emp), "allowances": allowances}

@router.post("/")
def create_employee(data: EmployeeCreate, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO employees (company_id, home_branch_id, device_user_id, employee_code,
                full_name, full_name_ar, position, join_date, phone, email, basic_salary)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (data.company_id, data.home_branch_id, data.device_user_id, data.employee_code,
              data.full_name, data.full_name_ar, data.position, data.join_date,
              data.phone, data.email, data.basic_salary))
        emp_id = cur.fetchone()["id"]
        conn.commit()
    conn.close()
    return {"message": "Employee created.", "id": emp_id}

@router.put("/{employee_id}")
def update_employee(employee_id: int, data: EmployeeCreate, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE employees SET company_id=%s, home_branch_id=%s, device_user_id=%s,
            employee_code=%s, full_name=%s, full_name_ar=%s, position=%s, join_date=%s,
            phone=%s, email=%s, basic_salary=%s WHERE id=%s
        """, (data.company_id, data.home_branch_id, data.device_user_id, data.employee_code,
              data.full_name, data.full_name_ar, data.position, data.join_date,
              data.phone, data.email, data.basic_salary, employee_id))
        conn.commit()
    conn.close()
    return {"message": "Employee updated."}

@router.post("/{employee_id}/allowances")
def add_allowance(employee_id: int, data: AllowanceCreate, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO employee_allowances (employee_id, allowance_type, amount)
            VALUES (%s,%s,%s) RETURNING id
        """, (employee_id, data.allowance_type, data.amount))
        allowance_id = cur.fetchone()["id"]
        conn.commit()
    conn.close()
    return {"message": "Allowance added.", "id": allowance_id}

@router.delete("/{employee_id}/allowances/{allowance_id}")
def delete_allowance(employee_id: int, allowance_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE employee_allowances SET is_active=FALSE WHERE id=%s", (allowance_id,))
        conn.commit()
    conn.close()
    return {"message": "Allowance removed."}

@router.post("/push-from-device")
def push_employees_from_device(data: EmployeePush, current_user=Depends(get_current_user)):
    """
    Called by the cloud agent to push employee list from ZK device.
    Inserts new employees, skips existing ones.
    """
    conn = db.get_conn()
    added = 0
    skipped = 0
    with conn.cursor() as cur:
        for emp in data.employees:
            device_user_id = str(emp.get("user_id", ""))
            name = emp.get("name", "Unknown")
            cur.execute("""
                SELECT id FROM employees
                WHERE company_id=%s AND device_user_id=%s
            """, (data.company_id, device_user_id))
            existing = cur.fetchone()
            if not existing:
                cur.execute("""
                    INSERT INTO employees (company_id, home_branch_id, device_user_id, full_name)
                    VALUES (%s,%s,%s,%s)
                """, (data.company_id, data.branch_id, device_user_id, name))
                added += 1
            else:
                skipped += 1
        conn.commit()
    conn.close()
    return {"message": f"Push complete. Added: {added}, Skipped (already exists): {skipped}"}
