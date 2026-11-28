
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.database import Base, engine, get_db
from backend.models import Employee, Attendance, LeaveRequest
from backend.schemas import EmployeeCreate, EmployeeUpdate, EmployeeOut, LeaveCreate, LeaveReview, LeaveOut

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Attendance & Leave Management API")


@app.get("/")
def home():
    return {"message": "Attendance & Leave Management API is running", "docs": "/docs"}


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "connected", "database": "PostgreSQL"}


# EMPLOYEES
@app.post("/employees/", response_model=EmployeeOut, status_code=201)
def create_employee(data: EmployeeCreate, db: Session = Depends(get_db)):
    employee = db.query(Employee).filter(
        (Employee.employee_code == data.employee_code) | (Employee.email == str(data.email))
    ).first()
    if employee:
        raise HTTPException(status_code=409, detail="Employee code or email already exists")

    employee = Employee(**data.model_dump())
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


@app.get("/employees/", response_model=list[EmployeeOut])
def list_employees(active_only: bool = False, db: Session = Depends(get_db)):
    query = db.query(Employee)
    if active_only:
        query = query.filter(Employee.is_active == "active")
    return query.order_by(Employee.id.desc()).all()


@app.get("/employees/{employee_id}", response_model=EmployeeOut)
def get_employee(employee_id: int, db: Session = Depends(get_db)):
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


@app.patch("/employees/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: int, data: EmployeeUpdate, db: Session = Depends(get_db)):
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_active") not in (None, "active", "inactive"):
        raise HTTPException(status_code=400, detail="Status must be active or inactive")

    for field, value in changes.items():
        setattr(employee, field, value)
    db.commit()
    db.refresh(employee)
    return employee


# ATTENDANCE
@app.post("/attendance/check-in/{employee_id}")
def check_in(employee_id: int, db: Session = Depends(get_db)):
    employee = db.get(Employee, employee_id)
    if not employee or employee.is_active != "active":
        raise HTTPException(status_code=404, detail="Active employee not found")

    today = date.today()
    record = db.query(Attendance).filter_by(employee_id=employee_id, attendance_date=today).first()
    if record:
        raise HTTPException(status_code=409, detail="Attendance already exists today")

    now = datetime.now()
    record = Attendance(employee_id=employee_id, attendance_date=today, check_in=now, status="present")
    db.add(record)
    db.commit()
    return {"message": "Check-in successful", "check_in": now}


@app.post("/attendance/check-out/{employee_id}")
def check_out(employee_id: int, db: Session = Depends(get_db)):
    record = db.query(Attendance).filter_by(
        employee_id=employee_id, attendance_date=date.today()
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Check in before checking out")
    if record.check_out:
        raise HTTPException(status_code=409, detail="Already checked out")

    now = datetime.now()
    record.check_out = now
    db.commit()
    hours = round((now - record.check_in).total_seconds() / 3600, 2)
    return {"message": "Check-out successful", "working_hours": hours}


@app.get("/attendance/")
def attendance_report(start_date: date = date.today(), end_date: date = date.today(),
                      employee_id: Optional[int] = None, db: Session = Depends(get_db)):
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="Invalid date range")

    query = db.query(Attendance, Employee.full_name, Employee.employee_code).join(
        Employee, Employee.id == Attendance.employee_id
    ).filter(Attendance.attendance_date >= start_date, Attendance.attendance_date <= end_date)
    if employee_id is not None:
        query = query.filter(Attendance.employee_id == employee_id)

    results = []
    for record, name, code in query.order_by(Attendance.attendance_date.desc()).all():
        hours = None
        if record.check_in and record.check_out:
            hours = round((record.check_out - record.check_in).total_seconds() / 3600, 2)
        results.append({
            "id": record.id, "employee_id": record.employee_id,
            "employee_code": code, "employee_name": name,
            "attendance_date": record.attendance_date, "check_in": record.check_in,
            "check_out": record.check_out, "working_hours": hours, "status": record.status
        })
    return results


# LEAVE MANAGEMENT
@app.post("/leaves/", response_model=LeaveOut, status_code=201)
def request_leave(data: LeaveCreate, db: Session = Depends(get_db)):
    employee = db.get(Employee, data.employee_id)
    if not employee or employee.is_active != "active":
        raise HTTPException(status_code=404, detail="Active employee not found")
    if data.end_date < data.start_date or data.start_date < date.today():
        raise HTTPException(status_code=400, detail="Please choose valid leave dates")
    if data.leave_type not in ("casual", "sick", "earned", "unpaid"):
        raise HTTPException(status_code=400, detail="Invalid leave type")

    days = (data.end_date - data.start_date).days + 1
    if data.leave_type != "unpaid" and days > employee.annual_leave_balance:
        raise HTTPException(status_code=400, detail="Insufficient leave balance")

    overlap = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == data.employee_id,
        LeaveRequest.status.in_(["pending", "approved"]),
        LeaveRequest.start_date <= data.end_date,
        LeaveRequest.end_date >= data.start_date
    ).first()
    if overlap:
        raise HTTPException(status_code=409, detail="An overlapping pending or approved leave already exists")

    leave = LeaveRequest(**data.model_dump())
    db.add(leave)
    db.commit()
    db.refresh(leave)
    return leave


@app.get("/leaves/", response_model=list[LeaveOut])
def list_leaves(status: Optional[str] = None, employee_id: Optional[int] = None,
                db: Session = Depends(get_db)):
    query = db.query(LeaveRequest)
    if status:
        if status not in ("pending", "approved", "rejected"):
            raise HTTPException(status_code=400, detail="Invalid leave status")
        query = query.filter(LeaveRequest.status == status)
    if employee_id is not None:
        query = query.filter(LeaveRequest.employee_id == employee_id)
    return query.order_by(LeaveRequest.created_at.desc()).all()


@app.patch("/leaves/{leave_id}/review", response_model=LeaveOut)
def review_leave(leave_id: int, data: LeaveReview, db: Session = Depends(get_db)):
    leave = db.get(LeaveRequest, leave_id)
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if leave.status != "pending":
        raise HTTPException(status_code=409, detail="Request already reviewed")
    if data.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Decision must be approved or rejected")

    employee = db.get(Employee, leave.employee_id)
    days = (leave.end_date - leave.start_date).days + 1
    if data.decision == "approved" and leave.leave_type != "unpaid":
        if employee.annual_leave_balance < days:
            raise HTTPException(status_code=400, detail="Insufficient leave balance")
        employee.annual_leave_balance -= days

    leave.status = data.decision
    leave.reviewer_comment = data.reviewer_comment
    leave.reviewed_at = datetime.now()
    db.commit()
    db.refresh(leave)
    return leave


# DASHBOARD
@app.get("/dashboard/")
def dashboard(db: Session = Depends(get_db)):
    today = date.today()
    total = db.query(Employee).filter(Employee.is_active == "active").count()
    present = db.query(Attendance).filter(
        Attendance.attendance_date == today, Attendance.status == "present"
    ).count()
    pending = db.query(LeaveRequest).filter(LeaveRequest.status == "pending").count()
    leaves = db.query(LeaveRequest).filter(
        LeaveRequest.status == "approved",
        LeaveRequest.start_date <= today,
        LeaveRequest.end_date >= today
    ).all()
    on_leave = len(set(item.employee_id for item in leaves))
    absent = max(0, total - present - on_leave)

    return {
        "date": today, "total_employees": total, "present_today": present,
        "on_leave_today": on_leave, "absent_estimate": absent,
        "pending_leave_requests": pending,
        "attendance_rate": round(present / total * 100, 2) if total else 0
    }
