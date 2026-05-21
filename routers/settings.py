"""
settings.py — Company settings router
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import database as db
from routers.auth import get_current_user

router = APIRouter()

class SettingsUpdate(BaseModel):
    working_days_per_week: int = 6
    weekly_off_day: str = "Friday"
    late_threshold_minutes: int = 15
    early_departure_minutes: int = 15
    standard_hours_per_day: float = 8.0
    overtime_threshold_hours: float = 8.0
    language: str = "en"
    currency: str = "AED"

@router.get("/{company_id}")
def get_settings(company_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM company_settings WHERE company_id = %s", (company_id,))
        row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Settings not found.")
    return dict(row)

@router.put("/{company_id}")
def update_settings(company_id: int, data: SettingsUpdate, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["super_admin", "company_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO company_settings (
                company_id, working_days_per_week, weekly_off_day,
                late_threshold_minutes, early_departure_minutes,
                standard_hours_per_day, overtime_threshold_hours,
                language, currency
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (company_id) DO UPDATE SET
                working_days_per_week=%s, weekly_off_day=%s,
                late_threshold_minutes=%s, early_departure_minutes=%s,
                standard_hours_per_day=%s, overtime_threshold_hours=%s,
                language=%s, currency=%s
        """, (
            company_id, data.working_days_per_week, data.weekly_off_day,
            data.late_threshold_minutes, data.early_departure_minutes,
            data.standard_hours_per_day, data.overtime_threshold_hours,
            data.language, data.currency,
            data.working_days_per_week, data.weekly_off_day,
            data.late_threshold_minutes, data.early_departure_minutes,
            data.standard_hours_per_day, data.overtime_threshold_hours,
            data.language, data.currency
        ))
        conn.commit()
    conn.close()
    return {"message": "Settings updated."}
