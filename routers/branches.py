"""
branches.py — Branches router
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import database as db
from routers.auth import get_current_user

router = APIRouter()

class BranchCreate(BaseModel):
    company_id: int
    name: str
    name_ar: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    device_ip: Optional[str] = None
    device_password: Optional[str] = None

@router.get("/")
def list_branches(company_id: Optional[int] = None, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        if current_user["role"] == "super_admin":
            if company_id:
                cur.execute("SELECT * FROM branches WHERE company_id=%s AND is_active=TRUE ORDER BY name", (company_id,))
            else:
                cur.execute("SELECT * FROM branches WHERE is_active=TRUE ORDER BY name")
        elif current_user["role"] == "company_admin":
            cur.execute("SELECT * FROM branches WHERE company_id=%s AND is_active=TRUE ORDER BY name",
                        (current_user["company_id"],))
        else:
            cur.execute("SELECT * FROM branches WHERE id=%s AND is_active=TRUE",
                        (current_user["branch_id"],))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

@router.post("/")
def create_branch(data: BranchCreate, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO branches (company_id, name, name_ar, address, phone, device_ip, device_password)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (data.company_id, data.name, data.name_ar, data.address,
              data.phone, data.device_ip, data.device_password))
        branch_id = cur.fetchone()["id"]
        conn.commit()
    conn.close()
    return {"message": "Branch created.", "id": branch_id}

@router.put("/{branch_id}")
def update_branch(branch_id: int, data: BranchCreate, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE branches SET name=%s, name_ar=%s, address=%s, phone=%s,
            device_ip=%s, device_password=%s WHERE id=%s
        """, (data.name, data.name_ar, data.address, data.phone,
              data.device_ip, data.device_password, branch_id))
        conn.commit()
    conn.close()
    return {"message": "Branch updated."}

@router.delete("/{branch_id}")
def delete_branch(branch_id: int, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE branches SET is_active=FALSE WHERE id=%s", (branch_id,))
        conn.commit()
    conn.close()
    return {"message": "Branch deactivated."}
