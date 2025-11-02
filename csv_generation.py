"""
Generate realistic CSV datasets (>=2000 rows each) for the models in models.py.

Usage:
    python scripts\generate_seed_data.py

Output:
    ./data/*.csv   (one CSV per model/table)
"""
from faker import Faker
import random
import csv
import os
import json
from decimal import Decimal
from datetime import datetime, timedelta

fake = Faker()
Faker.seed(42)
random.seed(42)

OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))
os.makedirs(OUT_DIR, exist_ok=True)

N = 2000  # number of rows per table (minimum)

def write_csv(filename, fieldnames, rows):
    path = os.path.join(OUT_DIR, filename)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"Wrote {len(rows)} rows to {path}")

def gen_dates_between(start_year=2018, end_year=2025):
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31)
    delta = end - start
    for _ in range(N):
        yield (start + timedelta(days=random.randrange(delta.days))).date().isoformat()

# 1) Customers
customers = []
for i in range(1, N+1):
    name = fake.company()
    customers.append({
        "id": i,
        "name": name,
        "contact_person": fake.name(),
        "email": fake.company_email(),
        "phone": fake.phone_number(),
        "status": random.choice(["Active", "Inactive"]),
        "created_at": fake.date_between(start_date='-5y', end_date='today').isoformat()
    })
write_csv("customers.csv", ["id","name","contact_person","email","phone","status","created_at"], customers)

# 2) Employees
employees = []
for i in range(1, N+1):
    employees.append({
        "id": i,
        "external_id": f"E{100000 + i}",
        "name": fake.name(),
        "department": random.choice(["Sales","Finance","HR","Production","IT","Logistics","Support"]),
        "role": random.choice(["Manager","Analyst","Operator","Technician","Supervisor","Clerk"]),
        "email": fake.email(),
        "phone": fake.phone_number(),
        "status": random.choice(["Active","On Leave","Inactive"]),
        "hire_date": fake.date_between(start_date='-10y', end_date='today').isoformat(),
        "created_at": fake.date_time_between(start_date='-10y', end_date='now').isoformat()
    })
write_csv("employees.csv", ["id","external_id","name","department","role","email","phone","status","hire_date","created_at"], employees)

# 3) Sales Orders
sales_orders = []
order_ids_set = set()
for i in range(1, N+1):
    # ensure unique order_id simple pattern
    oid = f"SO-{20250000 + i}"
    order_ids_set.add(oid)
    cust_id = random.randint(1, N)
    date = fake.date_between(start_date='-5y', end_date='today').isoformat()
    amount = round(random.uniform(50.0, 50000.0), 2)
    sales_orders.append({
        "id": i,
        "order_id": oid,
        "customer_id": cust_id,
        "date": date,
        "amount": f"{amount:.2f}",
        "status": random.choice(["Pending","Completed","Cancelled","Shipped"]),
        "created_at": fake.date_time_between(start_date='-5y', end_date='now').isoformat()
    })
write_csv("sales_orders.csv", ["id","order_id","customer_id","date","amount","status","created_at"], sales_orders)

# 4) Sales Forecasts
sales_forecasts = []
for i in range(1, N+1):
    payload = {
        "forecast_id": f"FCAST-{i}",
        "series": [{"x": [f"2025-{m:02d}" for m in range(1,7)], "y": [round(random.uniform(1000,50000),2) for _ in range(6)]}],
        "meta": {"created_by": fake.name(), "notes": fake.sentence()}
    }
    sales_forecasts.append({
        "id": i,
        "name": f"Forecast {i}",
        "payload": json.dumps(payload),
        "created_at": fake.date_time_between(start_date='-2y', end_date='now').isoformat()
    })
write_csv("sales_forecasts.csv", ["id","name","payload","created_at"], sales_forecasts)

# 5) Inventory Items
inventory_items = []
for i in range(1, N+1):
    inventory_items.append({
        "id": i,
        "product": fake.word().title() + " " + fake.color_name(),
        "sku": f"SKU-{100000+i}",
        "warehouse": random.choice(["WH-A","WH-B","WH-C","Main Warehouse"]),
        "quantity": random.randint(0, 2000),
        "reorder_level": random.randint(10, 500),
        "status": random.choice(["Available","Low Stock","Backordered","Discontinued"]),
        "updated_at": fake.date_time_between(start_date='-3y', end_date='now').isoformat()
    })
write_csv("inventory_items.csv", ["id","product","sku","warehouse","quantity","reorder_level","status","updated_at"], inventory_items)

# 6) Shipments
shipments = []
for i in range(1, N+1):
    shipments.append({
        "id": i,
        "shipment_id": f"SHP-{1000 + i}",
        "date": fake.date_between(start_date='-5y', end_date='today').isoformat(),
        "carrier": random.choice(["DHL","FedEx","UPS","Local Carrier","Maersk"]),
        "destination": f"{fake.city()}, {fake.country()}",
        "status": random.choice(["In Transit","Delivered","Pending","Cancelled"]),
        "created_at": fake.date_time_between(start_date='-5y', end_date='now').isoformat()
    })
write_csv("shipments.csv", ["id","shipment_id","date","carrier","destination","status","created_at"], shipments)

# 7) Payments
payments = []
for i in range(1, N+1):
    payments.append({
        "id": i,
        "payment_id": f"PMT-{10000 + i}",
        "party": fake.company(),
        "due_date": fake.date_between(start_date='-2y', end_date='+6m').isoformat(),
        "amount": f"{round(random.uniform(20,200000),2):.2f}",
        "status": random.choice(["Paid","Pending","Overdue"]),
        "created_at": fake.date_time_between(start_date='-3y', end_date='now').isoformat()
    })
write_csv("payments.csv", ["id","payment_id","party","due_date","amount","status","created_at"], payments)

# 8) CashFlow Records
cashflow = []
for i in range(1, N+1):
    inflow = round(random.uniform(0, 50000),2)
    outflow = round(random.uniform(0, 40000),2)
    balance = round(inflow - outflow, 2)
    cashflow.append({
        "id": i,
        "date": fake.date_between(start_date='-5y', end_date='today').isoformat(),
        "description": fake.sentence(nb_words=6),
        "inflow": f"{inflow:.2f}",
        "outflow": f"{outflow:.2f}",
        "balance": f"{balance:.2f}",
        "created_at": fake.date_time_between(start_date='-5y', end_date='now').isoformat()
    })
write_csv("cashflow_records.csv", ["id","date","description","inflow","outflow","balance","created_at"], cashflow)

# 9) Payroll Records
payrolls = []
for i in range(1, N+1):
    emp_id = random.randint(1, N)
    start = fake.date_between(start_date='-3y', end_date='-1y')
    end = start + timedelta(days=30)
    payrolls.append({
        "id": i,
        "employee_id": emp_id,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "gross_pay": f"{round(random.uniform(500,10000),2):.2f}",
        "net_pay": f"{round(random.uniform(400,9500),2):.2f}",
        "paid": random.choice([True, False]),
        "created_at": fake.date_time_between(start_date='-3y', end_date='now').isoformat()
    })
write_csv("payroll_records.csv", ["id","employee_id","period_start","period_end","gross_pay","net_pay","paid","created_at"], payrolls)

# 10) Attendance Records
attendance = []
for i in range(1, N+1):
    attendance.append({
        "id": i,
        "employee_id": random.randint(1, N),
        "date": fake.date_between(start_date='-2y', end_date='today').isoformat(),
        "status": random.choice(["Present","Absent","Leave"]),
        "note": fake.sentence(nb_words=8),
        "created_at": fake.date_time_between(start_date='-2y', end_date='now').isoformat()
    })
write_csv("attendance_records.csv", ["id","employee_id","date","status","note","created_at"], attendance)

# 11) Leave Requests
leaves = []
for i in range(1, N+1):
    emp_id = random.randint(1, N)
    start = fake.date_between(start_date='-2y', end_date='today')
    end = start + timedelta(days=random.randint(1,14))
    leaves.append({
        "id": i,
        "employee_id": emp_id,
        "leave_type": random.choice(["Sick","Annual","Maternity","Paternity","Unpaid"]),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days": (end - start).days + 1,
        "status": random.choice(["Pending","Approved","Denied"]),
        "created_at": fake.date_time_between(start_date='-2y', end_date='now').isoformat()
    })
write_csv("leave_requests.csv", ["id","employee_id","leave_type","start_date","end_date","days","status","created_at"], leaves)

# 12) Purchase Requests
purchase_requests = []
for i in range(1, N+1):
    purchase_requests.append({
        "id": i,
        "request_id": f"PR-{10000+i}",
        "requested_by": fake.name(),
        "date": fake.date_between(start_date='-3y', end_date='today').isoformat(),
        "item": fake.word().title(),
        "quantity": random.randint(1,500),
        "status": random.choice(["Pending","Approved","Cancelled"]),
        "created_at": fake.date_time_between(start_date='-3y', end_date='now').isoformat()
    })
write_csv("purchase_requests.csv", ["id","request_id","requested_by","date","item","quantity","status","created_at"], purchase_requests)

# 13) Purchase Orders
purchase_orders = []
for i in range(1, N+1):
    purchase_orders.append({
        "id": i,
        "order_id": f"PO-{20000+i}",
        "vendor": fake.company(),
        "date": fake.date_between(start_date='-3y', end_date='today').isoformat(),
        "item": fake.word().title(),
        "quantity": random.randint(1,1000),
        "amount": f"{round(random.uniform(50,200000),2):.2f}",
        "status": random.choice(["Pending","Received","Cancelled"]),
        "created_at": fake.date_time_between(start_date='-3y', end_date='now').isoformat()
    })
write_csv("purchase_orders.csv", ["id","order_id","vendor","date","item","quantity","amount","status","created_at"], purchase_orders)

# 14) Suppliers
suppliers = []
for i in range(1, N+1):
    suppliers.append({
        "id": i,
        "name": fake.company(),
        "contact_person": fake.name(),
        "email": fake.company_email(),
        "phone": fake.phone_number(),
        "status": random.choice(["Active","Inactive"]),
        "created_at": fake.date_time_between(start_date='-5y', end_date='now').isoformat()
    })
write_csv("suppliers.csv", ["id","name","contact_person","email","phone","status","created_at"], suppliers)

# 15) Production Orders
production_orders = []
for i in range(1, N+1):
    start = fake.date_between(start_date='-2y', end_date='today')
    end = start + timedelta(days=random.randint(1,60))
    production_orders.append({
        "id": i,
        "order_id": f"PROD-{30000+i}",
        "product": fake.word().title(),
        "quantity": random.randint(1,2000),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "status": random.choice(["Planned","In Production","Completed","On Hold"]),
        "created_at": fake.date_time_between(start_date='-3y', end_date='now').isoformat()
    })
write_csv("production_orders.csv", ["id","order_id","product","quantity","start_date","end_date","status","created_at"], production_orders)

# 16) Bill of Materials
boms = []
for i in range(1, N+1):
    boms.append({
        "id": i,
        "product": fake.word().title(),
        "component": fake.word().title(),
        "quantity_required": random.randint(1,50),
        "unit": random.choice(["pcs","kg","m","litre"]),
        "created_at": fake.date_time_between(start_date='-5y', end_date='now').isoformat()
    })
write_csv("bill_of_materials.csv", ["id","product","component","quantity_required","unit","created_at"], boms)

# 17) Work Centers
work_centers = []
for i in range(1, N+1):
    work_centers.append({
        "id": i,
        "name": f"WC-{i}",
        "current_task": fake.sentence(nb_words=4),
        "status": random.choice(["Running","Idle","Maintenance"]),
        "operator": fake.name(),
        "updated_at": fake.date_time_between(start_date='-2y', end_date='now').isoformat()
    })
write_csv("work_centers.csv", ["id","name","current_task","status","operator","updated_at"], work_centers)

# 18) Assets
assets = []
for i in range(1, N+1):
    purchase_date = fake.date_between(start_date='-10y', end_date='today').isoformat()
    assets.append({
        "id": i,
        "name": fake.word().title() + " Asset",
        "category": random.choice(["IT Equipment","Vehicles","Office Equipment","Furniture","Tools"]),
        "purchase_date": purchase_date,
        "value": f"{round(random.uniform(50,80000),2):.2f}",
        "depreciation_rate": float(random.choice([5,10,15,20,25,30])),
        "status": random.choice(["Active","Retired","Disposed"]),
        "created_at": fake.date_time_between(start_date='-10y', end_date='now').isoformat()
    })
write_csv("assets.csv", ["id","name","category","purchase_date","value","depreciation_rate","status","created_at"], assets)

# 19) Users
from werkzeug.security import generate_password_hash
users = []
for i in range(1, N+1):
    username = f"user{i}"
    pwd = generate_password_hash("password123")
    users.append({
        "id": i,
        "username": username,
        "email": fake.email(),
        "password_hash": pwd,
        "full_name": fake.name(),
        "group_id": random.randint(1,10),
        "group_name": random.choice(["staff","admin","managers","finance"]),
        "role": random.choice(["user","admin","manager"]),
        "is_active": random.choice([True, True, True, False]),
        "last_login": fake.date_time_between(start_date='-1y', end_date='now').isoformat(),
        "created_at": fake.date_time_between(start_date='-5y', end_date='now').isoformat(),
        "updated_at": fake.date_time_between(start_date='-2y', end_date='now').isoformat()
    })
# ensure admin exists
users[0]["username"] = "admin"
users[0]["email"] = "admin@example.com"
users[0]["password_hash"] = generate_password_hash("admin123")
users[0]["role"] = "admin"
write_csv("users.csv", ["id","username","email","password_hash","full_name","group_id","group_name","role","is_active","last_login","created_at","updated_at"], users)

print("\nAll datasets generated in:", OUT_DIR)
print("Each CSV has at least", N, "rows. Import into DB or use as fixtures.")