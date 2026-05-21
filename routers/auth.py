"""
auth.py — Authentication router
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional
import database as db
import hashlib
import jwt
import os

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
SECRET_KEY = os.environ.get("JWT_SECRET", "hr_payroll_secret_key_2024")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 12


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def create_token(data: dict) -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    data.update({"exp": expire})
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT u.*, c.name as company_name, b.name as branch_name
            FROM users u
            LEFT JOIN companies c ON u.company_id = c.id
            LEFT JOIN branches b ON u.branch_id = b.id
            WHERE u.username = %s AND u.is_active = TRUE
        """, (form.username,))
        user = cur.fetchone()
    conn.close()

    if not user or user["password"] != hash_password(form.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    token = create_token({
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "company_id": user["company_id"],
        "branch_id": user["branch_id"],
        "full_name": user["full_name"],
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "role": user["role"],
            "company_id": user["company_id"],
            "company_name": user["company_name"],
            "branch_id": user["branch_id"],
            "branch_name": user["branch_name"],
        }
    }


@router.get("/me")
def get_me(current_user=Depends(get_current_user)):
    return current_user


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password")
def change_password(req: ChangePasswordRequest, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT password FROM users WHERE id = %s", (current_user["user_id"],))
        user = cur.fetchone()
        if not user or user["password"] != hash_password(req.old_password):
            conn.close()
            raise HTTPException(status_code=400, detail="Old password incorrect.")
        cur.execute("UPDATE users SET password = %s WHERE id = %s",
                    (hash_password(req.new_password), current_user["user_id"]))
        conn.commit()
    conn.close()
    return {"message": "Password changed successfully."}
