"""
file_storage.py — Document upload with Cloudinary or Neon base64
"""
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from typing import Optional
import database as db
from routers.auth import get_current_user
import base64, json, urllib.request, urllib.parse, hashlib, hmac, time

router = APIRouter()

SIZE_THRESHOLD = 1 * 1024 * 1024  # 1MB


def get_storage_settings(company_id: int) -> dict:
    """Get storage configuration for a company."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        try:
            cur.execute("""
                SELECT doc_storage_mode,
                       cloudinary_cloud_name,
                       cloudinary_api_key,
                       cloudinary_api_secret
                FROM company_settings WHERE company_id = %s
            """, (company_id,))
            row = cur.fetchone()
        except Exception:
            row = None
    conn.close()
    return dict(row) if row else {}


def upload_to_cloudinary(file_bytes: bytes, filename: str,
                          folder: str, settings: dict) -> dict:
    """Upload file to Cloudinary and return URL and public_id."""
    cloud_name  = settings.get("cloudinary_cloud_name", "")
    api_key     = settings.get("cloudinary_api_key", "")
    api_secret  = settings.get("cloudinary_api_secret", "")

    if not all([cloud_name, api_key, api_secret]):
        raise HTTPException(status_code=400,
            detail="Cloudinary not configured. Please set up in Settings.")

    timestamp   = str(int(time.time()))
    public_id   = f"{folder}/{filename}_{timestamp}"
    resource_type = "raw"  # supports PDF and images

    # Generate signature
    sign_str = f"folder={folder}&public_id={public_id}&timestamp={timestamp}{api_secret}"
    signature = hashlib.sha1(sign_str.encode()).hexdigest()

    # Build multipart form
    boundary = "----WebKitFormBoundary" + hashlib.md5(str(time.time()).encode()).hexdigest()

    body_parts = []
    for key, val in [
        ("api_key", api_key),
        ("timestamp", timestamp),
        ("public_id", public_id),
        ("folder", folder),
        ("signature", signature),
    ]:
        body_parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}"
        )

    body_parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/octet-stream\r\n"
    )

    body_start = "\r\n".join(body_parts).encode() + b"\r\n" + file_bytes
    body_end   = f"\r\n--{boundary}--\r\n".encode()
    body       = body_start + body_end

    url = f"https://api.cloudinary.com/v1_1/{cloud_name}/{resource_type}/upload"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            return {
                "url":        result.get("secure_url"),
                "public_id":  result.get("public_id"),
                "size":       result.get("bytes", len(file_bytes)),
                "storage":    "cloudinary",
            }
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        raise HTTPException(status_code=500, detail=f"Cloudinary upload failed: {err}")


@router.post("/upload")
async def upload_document(
    employee_id: int = Form(...),
    company_id:  int = Form(...),
    document_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    current_user=Depends(get_current_user)
):
    """Upload a document file for an employee."""
    file_bytes = await file.read()
    file_size  = len(file_bytes)
    filename   = file.filename or "document"

    # Get employee info for folder name
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT full_name, employee_code, device_user_id FROM employees WHERE id=%s",
                    (employee_id,))
        emp = cur.fetchone()
    conn.close()

    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found.")

    # Build folder name: EmployeeName_Code
    safe_name = (emp["full_name"] or "Employee").replace(" ", "_").replace("/", "_")
    code      = emp["employee_code"] or emp["device_user_id"] or str(employee_id)
    folder    = f"hr-system/employees/{safe_name}_{code}"

    # Get storage settings
    settings = get_storage_settings(company_id)
    mode     = settings.get("doc_storage_mode", "cloudinary")

    result = {}

    if mode == "cloudinary":
        # Always use Cloudinary
        result = upload_to_cloudinary(file_bytes, filename, folder, settings)

    elif mode == "cloudinary_neon":
        # Smart: small files → Neon, large → Cloudinary
        if file_size <= SIZE_THRESHOLD:
            # Store as base64 in database
            b64 = base64.b64encode(file_bytes).decode()
            result = {
                "url":       None,
                "public_id": None,
                "size":      file_size,
                "storage":   "neon",
                "base64":    b64,
                "mime_type": file.content_type,
            }
        else:
            result = upload_to_cloudinary(file_bytes, filename, folder, settings)
    else:
        raise HTTPException(status_code=400, detail="Invalid storage mode.")

    # Save to database
    conn = db.get_conn()
    with conn.cursor() as cur:
        if document_id:
            # Update existing document record
            cur.execute("""
                UPDATE employee_documents
                SET file_url=%s, file_public_id=%s, file_size=%s,
                    storage_type=%s, file_name=%s,
                    file_base64=%s
                WHERE id=%s
            """, (
                result.get("url"), result.get("public_id"),
                result.get("size"), result.get("storage"),
                filename, result.get("base64"),
                document_id
            ))
        else:
            # Create new document record
            cur.execute("""
                INSERT INTO employee_documents
                    (employee_id, document_type, file_url, file_public_id,
                     file_size, storage_type, file_name, file_base64, created_by)
                VALUES (%s, 'Uploaded File', %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                employee_id,
                result.get("url"), result.get("public_id"),
                result.get("size"), result.get("storage"),
                filename, result.get("base64"),
                current_user["user_id"]
            ))
            document_id = cur.fetchone()["id"]
        conn.commit()
    conn.close()

    return {
        "message":     "File uploaded successfully.",
        "document_id": document_id,
        "file_url":    result.get("url"),
        "storage":     result.get("storage"),
        "file_name":   filename,
        "file_size":   file_size,
    }


@router.delete("/document/{doc_id}/file")
def delete_document_file(doc_id: int, current_user=Depends(get_current_user)):
    """Remove file from a document record."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE employee_documents
            SET file_url=NULL, file_public_id=NULL,
                file_base64=NULL, storage_type=NULL, file_name=NULL
            WHERE id=%s
        """, (doc_id,))
        conn.commit()
    conn.close()
    return {"message": "File removed."}


@router.get("/cloudinary-settings/{company_id}")
def get_cloudinary_settings(company_id: int, current_user=Depends(get_current_user)):
    """Get storage settings for a company."""
    settings = get_storage_settings(company_id)
    # Hide secret
    if settings.get("cloudinary_api_secret"):
        settings["cloudinary_api_secret"] = "••••••••"
    return settings


@router.put("/cloudinary-settings/{company_id}")
def save_cloudinary_settings(company_id: int, data: dict,
                               current_user=Depends(get_current_user)):
    """Save Cloudinary/storage settings."""
    conn = db.get_conn()
    with conn.cursor() as cur:
        try:
            cur.execute("SAVEPOINT cloud_save")
            cur.execute("""
                UPDATE company_settings SET
                    doc_storage_mode=%s,
                    cloudinary_cloud_name=%s,
                    cloudinary_api_key=%s
                WHERE company_id=%s
            """, (
                data.get("doc_storage_mode", "cloudinary"),
                data.get("cloudinary_cloud_name"),
                data.get("cloudinary_api_key"),
                company_id
            ))
            # Only update secret if not masked
            if data.get("cloudinary_api_secret") and data["cloudinary_api_secret"] != "••••••••":
                cur.execute("""
                    UPDATE company_settings
                    SET cloudinary_api_secret=%s WHERE company_id=%s
                """, (data["cloudinary_api_secret"], company_id))
            cur.execute("RELEASE SAVEPOINT cloud_save")
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT cloud_save")
            raise HTTPException(status_code=500, detail=str(e))
        conn.commit()
    conn.close()
    return {"message": "Storage settings saved."}
