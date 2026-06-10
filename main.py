"""
main.py — HR & Payroll System API Server
Developed by: Shahid Fazaal
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import database as db
from routers import auth, companies, branches, users, employees, roster, attendance, payroll, settings, payroll_history, leave, documents, email_settings, warnings

app = FastAPI(title="HR & Payroll System API")

# Allow frontend to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all routers
app.include_router(auth.router,       prefix="/api/auth",       tags=["Auth"])
app.include_router(companies.router,  prefix="/api/companies",  tags=["Companies"])
app.include_router(branches.router,   prefix="/api/branches",   tags=["Branches"])
app.include_router(users.router,      prefix="/api/users",      tags=["Users"])
app.include_router(employees.router,  prefix="/api/employees",  tags=["Employees"])
app.include_router(roster.router,     prefix="/api/roster",     tags=["Roster"])
app.include_router(attendance.router, prefix="/api/attendance", tags=["Attendance"])
app.include_router(payroll.router,    prefix="/api/payroll",    tags=["Payroll"])
app.include_router(settings.router,        prefix="/api/settings",        tags=["Settings"])
app.include_router(payroll_history.router, prefix="/api/payroll-history", tags=["Payroll History"])
app.include_router(leave.router,           prefix="/api/leave",            tags=["Leave Management"])
app.include_router(documents.router,       prefix="/api/documents",        tags=["Documents"])
app.include_router(email_settings.router,  prefix="/api/email-settings",   tags=["Email Settings"])
app.include_router(warnings.router,        prefix="/api/warnings",         tags=["Warnings"])

@app.on_event("startup")
def startup():
    import threading
    # Run migration in background thread so port binds immediately
    threading.Thread(target=db.init_db, daemon=True).start()

@app.get("/health")
def health():
    from datetime import datetime
    return {"status": "ok", "time": datetime.utcnow().isoformat()}
