"""
companies.py — Companies router
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import database as db
from routers.auth import get_current_user

router = APIRouter()

class CompanyCreate(BaseModel):
    name: str
    name_ar: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

@router.get("/")
def list_companies(current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        if current_user["role"] == "super_admin":
            cur.execute("SELECT * FROM companies WHERE is_active = TRUE ORDER BY name")
        else:
            cur.execute("SELECT * FROM companies WHERE id = %s AND is_active = TRUE",
                        (current_user["company_id"],))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

@router.post("/")
def create_company(data: CompanyCreate, current_user=Depends(get_current_user)):
    if current_user["role"] != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admin can create companies.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO companies (name, name_ar, address, phone, email)
            VALUES (%s,%s,%s,%s,%s) RETURNING id
        """, (data.name, data.name_ar, data.address, data.phone, data.email))
        company_id = cur.fetchone()["id"]
        # Create default settings
        cur.execute("INSERT INTO company_settings (company_id) VALUES (%s)", (company_id,))
        conn.commit()
    conn.close()
    return {"message": "Company created.", "id": company_id}

@router.put("/{company_id}")
def update_company(company_id: int, data: CompanyCreate, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE companies SET name=%s, name_ar=%s, address=%s, phone=%s, email=%s
            WHERE id=%s
        """, (data.name, data.name_ar, data.address, data.phone, data.email, company_id))
        conn.commit()
    conn.close()
    return {"message": "Company updated."}

@router.delete("/{company_id}")
def delete_company(company_id: int, current_user=Depends(get_current_user)):
    if current_user["role"] != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admin can delete companies.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE companies SET is_active = FALSE WHERE id = %s", (company_id,))
        conn.commit()
    conn.close()
    return {"message": "Company deactivated."}
