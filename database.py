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
                    logo_base64 TEXT,
                    is_active   BOOLEAN DEFAULT TRUE,
                    created_at  TIMESTAMP DEFAULT NOW()
                );
            """)
            # Ensure logo columns exist on existing tables
            cur.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_url TEXT")
            cur.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_base64 TEXT")

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
                    generated_at        TIMESTAMP DEFAULT NOW(),
                    submitted_by        INTEGER REFERENCES users(id),
                    submitted_at        TIMESTAMP,
                    approved_by         INTEGER REFERENCES users(id),
                    approved_at         TIMESTAMP,
                    rejected_reason     TEXT,
                    finalized_at        TIMESTAMP
                );
            """)

            # Payroll Batches — group all employees for one period
            cur.execute("""
                CREATE TABLE IF NOT EXISTS payroll_batches (
                    id              SERIAL PRIMARY KEY,
                    company_id      INTEGER REFERENCES companies(id),
                    period_start    DATE NOT NULL,
                    period_end      DATE NOT NULL,
                    total_employees INTEGER DEFAULT 0,
                    total_payroll   NUMERIC(12,2) DEFAULT 0,
                    status          TEXT DEFAULT 'draft',
                    generated_by    INTEGER REFERENCES users(id),
                    generated_at    TIMESTAMP DEFAULT NOW(),
                    submitted_by    INTEGER REFERENCES users(id),
                    submitted_at    TIMESTAMP,
                    approved_by     INTEGER REFERENCES users(id),
                    approved_at     TIMESTAMP,
                    rejected_reason TEXT,
                    finalized_at    TIMESTAMP,
                    UNIQUE(company_id, period_start, period_end)
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


def migrate_db():
    """
    Run safe migrations on every startup.
    Column additions run individually.
    Leave tables created together in one transaction (FK dependencies).
    """

    # ── Create leave tables in one transaction ──────────────────────────────
    try:
        conn = get_conn()
        print(">>> migrate_db: creating leave tables...")
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS leave_types (
                    id                   SERIAL PRIMARY KEY,
                    company_id           INTEGER REFERENCES companies(id),
                    name                 TEXT NOT NULL,
                    name_ar              TEXT,
                    is_paid              BOOLEAN DEFAULT TRUE,
                    deduction_percentage NUMERIC(5,2) DEFAULT 0,
                    max_days_per_year    NUMERIC(5,1) DEFAULT 30,
                    requires_document    BOOLEAN DEFAULT FALSE,
                    allow_half_day       BOOLEAN DEFAULT TRUE,
                    carry_forward        BOOLEAN DEFAULT FALSE,
                    max_carry_days       INTEGER DEFAULT 0,
                    is_active            BOOLEAN DEFAULT TRUE,
                    created_at           TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS leave_balances (
                    id             SERIAL PRIMARY KEY,
                    employee_id    INTEGER REFERENCES employees(id),
                    leave_type_id  INTEGER REFERENCES leave_types(id),
                    year           INTEGER NOT NULL,
                    entitled_days  NUMERIC(5,1) DEFAULT 0,
                    used_days      NUMERIC(5,1) DEFAULT 0,
                    carried_days   NUMERIC(5,1) DEFAULT 0,
                    remaining_days NUMERIC(5,1) DEFAULT 0,
                    UNIQUE(employee_id, leave_type_id, year)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS public_holidays (
                    id         SERIAL PRIMARY KEY,
                    company_id INTEGER REFERENCES companies(id),
                    name       TEXT NOT NULL,
                    name_ar    TEXT,
                    date       DATE NOT NULL,
                    year       INTEGER NOT NULL,
                    is_active  BOOLEAN DEFAULT TRUE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS leave_requests (
                    id                   SERIAL PRIMARY KEY,
                    employee_id          INTEGER REFERENCES employees(id),
                    company_id           INTEGER REFERENCES companies(id),
                    branch_id            INTEGER REFERENCES branches(id),
                    leave_type_id        INTEGER REFERENCES leave_types(id),
                    start_date           DATE NOT NULL,
                    end_date             DATE NOT NULL,
                    days_count           NUMERIC(5,1) NOT NULL,
                    is_half_day          BOOLEAN DEFAULT FALSE,
                    half_day_period      TEXT,
                    reason               TEXT,
                    status               TEXT DEFAULT 'pending',
                    is_paid              BOOLEAN DEFAULT TRUE,
                    deduction_percentage NUMERIC(5,2) DEFAULT 0,
                    approved_by          INTEGER REFERENCES users(id),
                    approved_at          TIMESTAMP,
                    rejected_reason      TEXT,
                    notes                TEXT,
                    created_by           INTEGER REFERENCES users(id),
                    created_at           TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS leave_settings (
                    id                     SERIAL PRIMARY KEY,
                    company_id             INTEGER REFERENCES companies(id) UNIQUE,
                    enable_pro_rata        BOOLEAN DEFAULT TRUE,
                    pro_rata_rounding      TEXT DEFAULT 'half',
                    enable_carry_forward   BOOLEAN DEFAULT FALSE,
                    max_carry_days         INTEGER DEFAULT 0,
                    enable_encashment      BOOLEAN DEFAULT FALSE,
                    approval_levels        INTEGER DEFAULT 1,
                    auto_approve_days      INTEGER DEFAULT 0,
                    enable_public_holidays BOOLEAN DEFAULT TRUE,
                    enable_half_day        BOOLEAN DEFAULT TRUE,
                    first_half_cutoff      TIME DEFAULT '13:00:00'
                )
            """)
        conn.commit()
        conn.close()
        print(">>> migrate_db: leave tables OK")
    except Exception as e:
        print(f">>> migrate_db ERROR: {e}")
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass

    # ── New tables (each in own transaction) ────────────────────────────────
    new_tables = [
        """CREATE TABLE IF NOT EXISTS employee_documents (
            id              SERIAL PRIMARY KEY,
            employee_id     INTEGER REFERENCES employees(id) ON DELETE CASCADE,
            document_type   TEXT NOT NULL,
            document_number TEXT,
            issue_date      DATE,
            expiry_date     DATE,
            issuing_country TEXT,
            notes           TEXT,
            file_base64     TEXT,
            file_name       TEXT,
            status          TEXT DEFAULT 'active',
            created_by      INTEGER,
            created_at      TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS warning_templates (
            id              SERIAL PRIMARY KEY,
            company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            violation_type  TEXT,
            content_en      TEXT,
            content_ar      TEXT,
            is_default      BOOLEAN DEFAULT FALSE,
            created_by      INTEGER,
            created_at      TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS warning_letters (
            id                  SERIAL PRIMARY KEY,
            company_id          INTEGER REFERENCES companies(id),
            employee_id         INTEGER REFERENCES employees(id),
            template_id         INTEGER,
            letter_type         TEXT NOT NULL,
            violation_type      TEXT,
            incident_date       DATE,
            description         TEXT,
            description_ar      TEXT,
            deduction_amount    NUMERIC(10,2) DEFAULT 0,
            deduction_applied   BOOLEAN DEFAULT FALSE,
            deduction_month     TEXT,
            issued_by           INTEGER,
            sent_to_employee    BOOLEAN DEFAULT FALSE,
            sent_at             TIMESTAMP,
            status              TEXT DEFAULT 'draft',
            created_at          TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS email_settings (
            id              SERIAL PRIMARY KEY,
            company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE UNIQUE,
            provider        TEXT DEFAULT 'gmail',
            sendgrid_key    TEXT,
            gmail_user      TEXT,
            gmail_password  TEXT,
            from_name       TEXT,
            from_email      TEXT,
            alert_recipients TEXT DEFAULT '[]',
            alert_days      TEXT DEFAULT '[90,60,30,7]',
            created_at      TIMESTAMP DEFAULT NOW()
        )""",
    ]
    for sql in new_tables:
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f">>> Table migration note: {e}")
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass

    # ── Column migrations (each in own transaction) ──────────────────────────
    columns = [
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_base64 TEXT",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_url TEXT",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS department TEXT",
        "ALTER TABLE payroll_records ADD COLUMN IF NOT EXISTS submitted_by INTEGER",
        "ALTER TABLE payroll_records ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMP",
        "ALTER TABLE payroll_records ADD COLUMN IF NOT EXISTS approved_by INTEGER",
        "ALTER TABLE payroll_records ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP",
        "ALTER TABLE payroll_records ADD COLUMN IF NOT EXISTS rejected_reason TEXT",
        "ALTER TABLE payroll_records ADD COLUMN IF NOT EXISTS finalized_at TIMESTAMP",
    ]
    for sql in columns:
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            conn.close()
        except Exception:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass


# Run on startup
init_db()
migrate_db()
