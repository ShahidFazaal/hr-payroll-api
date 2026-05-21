"""
database.py — PostgreSQL database for HR & Payroll System
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

DATABASE_URL = os.environ.get("HR_DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:

            # Companies
            cur.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id          SERIAL PRIMARY KEY,
                    name        TEXT NOT NULL,
                    name_ar     TEXT,
                    address     TEXT,
                    phone       TEXT,
                    email       TEXT,
                    logo_url    TEXT,
                    is_active   BOOLEAN DEFAULT TRUE,
                    created_at  TIMESTAMP DEFAULT NOW()
                );
            """)

            # Branches
            cur.execute("""
                CREATE TABLE IF NOT EXISTS branches (
                    id          SERIAL PRIMARY KEY,
                    company_id  INTEGER REFERENCES companies(id),
                    name        TEXT NOT NULL,
                    name_ar     TEXT,
                    address     TEXT,
                    phone       TEXT,
                    device_ip   TEXT,
                    device_password TEXT,
                    is_active   BOOLEAN DEFAULT TRUE,
                    created_at  TIMESTAMP DEFAULT NOW()
                );
            """)

            # Users (branch managers, company admins, super admin)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id          SERIAL PRIMARY KEY,
                    company_id  INTEGER REFERENCES companies(id),
                    branch_id   INTEGER REFERENCES branches(id),
                    username    TEXT UNIQUE NOT NULL,
                    password    TEXT NOT NULL,
                    full_name   TEXT,
                    email       TEXT,
                    role        TEXT DEFAULT 'branch_manager',
                    is_active   BOOLEAN DEFAULT TRUE,
                    created_at  TIMESTAMP DEFAULT NOW()
                );
            """)

            # Employees
            cur.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    id              SERIAL PRIMARY KEY,
                    company_id      INTEGER REFERENCES companies(id),
                    home_branch_id  INTEGER REFERENCES branches(id),
                    device_user_id  TEXT,
                    employee_code   TEXT,
                    full_name       TEXT NOT NULL,
                    full_name_ar    TEXT,
                    position        TEXT,
                    join_date       DATE,
                    phone           TEXT,
                    email           TEXT,
                    basic_salary    NUMERIC(10,2) DEFAULT 0,
                    is_active       BOOLEAN DEFAULT TRUE,
                    created_at      TIMESTAMP DEFAULT NOW()
                );
            """)

            # Employee Allowances
            cur.execute("""
                CREATE TABLE IF NOT EXISTS employee_allowances (
                    id              SERIAL PRIMARY KEY,
                    employee_id     INTEGER REFERENCES employees(id),
                    allowance_type  TEXT,
                    amount          NUMERIC(10,2) DEFAULT 0,
                    is_active       BOOLEAN DEFAULT TRUE
                );
            """)

            # Weekly Roster
            cur.execute("""
                CREATE TABLE IF NOT EXISTS weekly_roster (
                    id              SERIAL PRIMARY KEY,
                    employee_id     INTEGER REFERENCES employees(id),
                    branch_id       INTEGER REFERENCES branches(id),
                    week_start_date DATE NOT NULL,
                    work_date       DATE NOT NULL,
                    is_day_off      BOOLEAN DEFAULT FALSE,
                    shift_start     TIME,
                    shift_end       TIME,
                    notes           TEXT,
                    created_by      INTEGER REFERENCES users(id),
                    created_at      TIMESTAMP DEFAULT NOW(),
                    UNIQUE(employee_id, work_date)
                );
            """)

            # Attendance Logs (pushed from ZK devices)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS attendance_logs (
                    id              SERIAL PRIMARY KEY,
                    company_id      INTEGER REFERENCES companies(id),
                    branch_id       INTEGER REFERENCES branches(id),
                    device_user_id  TEXT NOT NULL,
                    employee_id     INTEGER REFERENCES employees(id),
                    punch_time      TIMESTAMP NOT NULL,
                    punch_type      TEXT,
                    status          INTEGER,
                    raw_data        TEXT,
                    pushed_at       TIMESTAMP DEFAULT NOW(),
                    UNIQUE(branch_id, device_user_id, punch_time)
                );
            """)

            # Warning Letters
            cur.execute("""
                CREATE TABLE IF NOT EXISTS warning_letters (
                    id              SERIAL PRIMARY KEY,
                    employee_id     INTEGER REFERENCES employees(id),
                    company_id      INTEGER REFERENCES companies(id),
                    issue_date      DATE NOT NULL,
                    reason          TEXT,
                    deduction_amount NUMERIC(10,2) DEFAULT 0,
                    issued_by       INTEGER REFERENCES users(id),
                    created_at      TIMESTAMP DEFAULT NOW()
                );
            """)

            # Payroll Records
            cur.execute("""
                CREATE TABLE IF NOT EXISTS payroll_records (
                    id                  SERIAL PRIMARY KEY,
                    employee_id         INTEGER REFERENCES employees(id),
                    company_id          INTEGER REFERENCES companies(id),
                    period_start        DATE NOT NULL,
                    period_end          DATE NOT NULL,
                    working_days        INTEGER DEFAULT 0,
                    present_days        INTEGER DEFAULT 0,
                    absent_days         INTEGER DEFAULT 0,
                    late_count          INTEGER DEFAULT 0,
                    late_minutes        INTEGER DEFAULT 0,
                    early_departure_count INTEGER DEFAULT 0,
                    overtime_hours      NUMERIC(5,2) DEFAULT 0,
                    basic_salary        NUMERIC(10,2) DEFAULT 0,
                    total_allowances    NUMERIC(10,2) DEFAULT 0,
                    variable_pay        NUMERIC(10,2) DEFAULT 0,
                    absent_deduction    NUMERIC(10,2) DEFAULT 0,
                    late_deduction      NUMERIC(10,2) DEFAULT 0,
                    warning_deduction   NUMERIC(10,2) DEFAULT 0,
                    manual_deduction    NUMERIC(10,2) DEFAULT 0,
                    overtime_pay        NUMERIC(10,2) DEFAULT 0,
                    final_salary        NUMERIC(10,2) DEFAULT 0,
                    notes               TEXT,
                    status              TEXT DEFAULT 'draft',
                    generated_by        INTEGER REFERENCES users(id),
                    generated_at        TIMESTAMP DEFAULT NOW()
                );
            """)

            # Settings per company
            cur.execute("""
                CREATE TABLE IF NOT EXISTS company_settings (
                    id                      SERIAL PRIMARY KEY,
                    company_id              INTEGER REFERENCES companies(id) UNIQUE,
                    working_days_per_week   INTEGER DEFAULT 6,
                    weekly_off_day          TEXT DEFAULT 'Friday',
                    late_threshold_minutes  INTEGER DEFAULT 15,
                    early_departure_minutes INTEGER DEFAULT 15,
                    standard_hours_per_day  NUMERIC(4,2) DEFAULT 8.0,
                    overtime_threshold_hours NUMERIC(4,2) DEFAULT 8.0,
                    language                TEXT DEFAULT 'en',
                    currency                TEXT DEFAULT 'AED'
                );
            """)

        conn.commit()
