"""
documents.py — Employee document tracking with expiry alerts
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime, timedelta
import database as db
from routers.auth import get_current_user
import json

router = APIRouter()

DOCUMENT_TYPES = [
    "Passport", "Visa/Residence Permit", "Emirates ID",
    "Labor Card", "Health Insurance", "Employment Contract",
    "Educational Certificate", "Professional License", "Other"
]

class DocumentCreate(BaseModel):
    employee_id: int
    document_type: str
    document_number: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    issuing_country: Optional[str] = None
    notes: Optional[str] = None
    file_base64: Optional[str] = None
    file_name: Optional[str] = None

class DocumentUpdate(BaseModel):
    document_type: Optional[str] = None
    document_number: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    issuing_country: Optional[str] = None
    notes: Optional[str] = None
    file_base64: Optional[str] = None
    file_name: Optional[str] = None
    status: Optional[str] = None


@router.get("/types")
def get_document_types():
    return DOCUMENT_TYPES


@router.get("/")
def get_documents(
    company_id: Optional[int] = None,
    employee_id: Optional[int] = None,
    expiry_status: Optional[str] = None,  # expired, 30days, 60days, 90days, ok
    document_type: Optional[str] = None,
    current_user=Depends(get_current_user)
):
    conn = db.get_conn()
    with conn.cursor() as cur:
        query = """
            SELECT d.*, e.full_name, e.employee_code,
                   b.name as branch_name, e.home_branch_id,
                   CASE
                       WHEN d.expiry_date < CURRENT_DATE THEN 'expired'
                       WHEN d.expiry_date <= CURRENT_DATE + 30 THEN '30days'
                       WHEN d.expiry_date <= CURRENT_DATE + 60 THEN '60days'
                       WHEN d.expiry_date <= CURRENT_DATE + 90 THEN '90days'
                       ELSE 'ok'
                   END as expiry_status,
                   d.expiry_date - CURRENT_DATE as days_remaining
            FROM employee_documents d
            JOIN employees e ON d.employee_id = e.id
            LEFT JOIN branches b ON e.home_branch_id = b.id
            WHERE d.status = 'active'
        """
        params = []
        if company_id:
            query += " AND e.company_id = %s"
            params.append(company_id)
        if employee_id:
            query += " AND d.employee_id = %s"
            params.append(employee_id)
        if document_type:
            query += " AND d.document_type = %s"
            params.append(document_type)
        if expiry_status == 'expired':
            query += " AND d.expiry_date < CURRENT_DATE"
        elif expiry_status == '30days':
            query += " AND d.expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 30"
        elif expiry_status == '60days':
            query += " AND d.expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 60"
        elif expiry_status == '90days':
            query += " AND d.expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 90"

        query += " ORDER BY d.expiry_date ASC NULLS LAST"
        cur.execute(query, params)
        docs = cur.fetchall()

    conn.close()
    return [dict(d) for d in docs]


@router.get("/summary")
def get_document_summary(company_id: int, current_user=Depends(get_current_user)):
    """Get expiry summary counts for dashboard."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE expiry_date < CURRENT_DATE) as expired,
                COUNT(*) FILTER (WHERE expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 30) as days_30,
                COUNT(*) FILTER (WHERE expiry_date BETWEEN CURRENT_DATE + 1 AND CURRENT_DATE + 60) as days_60,
                COUNT(*) FILTER (WHERE expiry_date BETWEEN CURRENT_DATE + 1 AND CURRENT_DATE + 90) as days_90,
                COUNT(*) as total
            FROM employee_documents d
            JOIN employees e ON d.employee_id = e.id
            WHERE e.company_id = %s AND d.status = 'active'
              AND d.expiry_date IS NOT NULL
        """, (company_id,))
        summary = cur.fetchone()
    conn.close()
    return dict(summary)


@router.post("/")
def create_document(data: DocumentCreate, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO employee_documents
                (employee_id, document_type, document_number, issue_date,
                 expiry_date, issuing_country, notes, file_base64,
                 file_name, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (data.employee_id, data.document_type, data.document_number,
              data.issue_date, data.expiry_date, data.issuing_country,
              data.notes, data.file_base64, data.file_name,
              current_user["user_id"]))
        doc_id = cur.fetchone()["id"]
        conn.commit()
    conn.close()
    return {"id": doc_id, "message": "Document added successfully."}


@router.put("/{doc_id}")
def update_document(doc_id: int, data: dict,
                    current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        # Filter only valid document fields
        allowed = {"document_type","document_number","issue_date","expiry_date",
                   "issuing_country","notes","status"}
        fields = {k: v for k, v in data.items()
                  if k in allowed and v != "" and v is not None}
        # Allow empty string for nullable fields
        for k in ["document_number","issuing_country","notes","expiry_date","issue_date"]:
            if k in data and data[k] == "":
                fields[k] = None
        if not fields:
            conn.close()
            return {"message": "Nothing to update."}
        set_clause = ", ".join(f"{k}=%s" for k in fields)
        cur.execute(
            f"UPDATE employee_documents SET {set_clause} WHERE id=%s",
            list(fields.values()) + [doc_id]
        )
        conn.commit()
    conn.close()
    return {"message": "Document updated."}


@router.delete("/{doc_id}")
def delete_document(doc_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE employee_documents SET status='archived' WHERE id=%s",
            (doc_id,)
        )
        conn.commit()
    conn.close()
    return {"message": "Document archived."}


@router.get("/employee/{employee_id}")
def get_employee_documents(employee_id: int, current_user=Depends(get_current_user)):
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT *,
                CASE
                    WHEN expiry_date < CURRENT_DATE THEN 'expired'
                    WHEN expiry_date <= CURRENT_DATE + 30 THEN '30days'
                    WHEN expiry_date <= CURRENT_DATE + 60 THEN '60days'
                    WHEN expiry_date <= CURRENT_DATE + 90 THEN '90days'
                    ELSE 'ok'
                END as expiry_status,
                expiry_date - CURRENT_DATE as days_remaining
            FROM employee_documents
            WHERE employee_id = %s AND status = 'active'
            ORDER BY expiry_date ASC NULLS LAST
        """, (employee_id,))
        docs = cur.fetchall()
    conn.close()
    return [dict(d) for d in docs]
