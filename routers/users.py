"""
users.py — Users router
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import database as db
from routers.auth import get_current_user, hash_password

router = APIRouter()

class UserCreate(BaseModel):
    company_id: Optional[int] = None
    branch_id: Optional[int] = None
    username: str
    password: str
    full_name: str
    email: Optional[str] = None
    role: str = "branch_manager"

class UserUpdate(BaseModel):
    full_name: str
    email: Optional[str] = None
    role: str
    branch_id: Optional[int] = None
    is_active: bool = True

@router.get("/")
def list_users(current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        if current_user["role"] == "super_admin":
            cur.execute("""
                SELECT u.*, c.name as company_name, b.name as branch_name
                FROM users u
                LEFT JOIN companies c ON u.company_id = c.id
                LEFT JOIN branches b ON u.branch_id = b.id
                ORDER BY u.created_at DESC
            """)
        else:
            cur.execute("""
                SELECT u.*, c.name as company_name, b.name as branch_name
                FROM users u
                LEFT JOIN companies c ON u.company_id = c.id
                LEFT JOIN branches b ON u.branch_id = b.id
                WHERE u.company_id = %s
                ORDER BY u.created_at DESC
            """, (current_user["company_id"],))
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            d.pop("password", None)  # never return password
            rows.append(d)
    conn.close()
    return rows

@router.post("/setup-admin")
def setup_first_admin(data: UserCreate):
    """
    One-time endpoint to create the first super admin.
    Only works if no users exist yet.
    """
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) as count FROM users")
        count = cur.fetchone()["count"]
        if count > 0:
            conn.close()
            raise HTTPException(status_code=403,
                detail="Admin already exists. Use normal login.")
        try:
            cur.execute("""
                INSERT INTO users (company_id, branch_id, username, password,
                                   full_name, email, role)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (None, None, data.username, hash_password(data.password),
                  data.full_name, data.email, "super_admin"))
            user_id = cur.fetchone()["id"]
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail=str(e))
    conn.close()
    return {"message": "Super admin created successfully.", "id": user_id}


@router.post("/")
def create_user(data: UserCreate, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        try:
            cur.execute("""
                INSERT INTO users (company_id, branch_id, username, password, full_name, email, role)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (data.company_id, data.branch_id, data.username,
                  hash_password(data.password), data.full_name, data.email, data.role))
            user_id = cur.fetchone()["id"]
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail=f"Username already exists or error: {e}")
    conn.close()
    return {"message": "User created.", "id": user_id}

@router.put("/{user_id}")
def update_user(user_id: int, data: UserUpdate, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE users SET full_name=%s, email=%s, role=%s, branch_id=%s, is_active=%s
            WHERE id=%s
        """, (data.full_name, data.email, data.role, data.branch_id, data.is_active, user_id))
        conn.commit()
    conn.close()
    return {"message": "User updated."}

@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, new_password: str, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET password=%s WHERE id=%s",
                    (hash_password(new_password), user_id))
        conn.commit()
    conn.close()
    return {"message": "Password reset."}
