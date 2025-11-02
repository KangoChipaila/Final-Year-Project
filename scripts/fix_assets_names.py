import csv
import random
import os

random.seed(42)

INPUT = os.path.join(os.path.dirname(__file__), "..", "data", "assets.csv")
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "assets_fixed.csv")

# realistic asset name pools by category
NAME_POOLS = {
    "Furniture": [
        "Office Chair", "Desk", "Filing Cabinet", "Conference Table", "Bookshelf",
        "Reception Sofa", "Visitor Chair", "Standing Desk", "Drawer Unit", "Whiteboard"
    ],
    "Office Equipment": [
        "Printer", "Photocopier", "Paper Shredder", "Label Printer", "Projector",
        "Scanner", "Telephone System", "Time Clock", "Postal Scale", "Card Reader"
    ],
    "Tools": [
        "Drill", "Hammer", "Wrench Set", "Angle Grinder", "Impact Driver",
        "Saw", "Soldering Station", "Multimeter", "Workshop Bench", "Tool Trolley"
    ],
    "Vehicles": [
        "Delivery Van", "Forklift", "Box Truck", "Service Car", "Pickup Truck",
        "Trailer", "Electric Pallet Jack", "Company Motorcycle", "Utility Cart", "Shuttle Bus"
    ],
    "IT Equipment": [
        "Laptop", "Desktop PC", "Monitor", "Server", "Network Switch",
        "Router", "Wireless Access Point", "External HDD", "NAS Unit", "UPS"
    ],
    # fallback pool
    "default": [
        "Generic Asset", "Misc Equipment", "Spare Part", "Consumable Kit", "Instrument"
    ],
}

# read, replace name, write
with open(INPUT, newline='', encoding='utf-8') as fin:
    reader = csv.DictReader(fin)
    rows = list(reader)
    fieldnames = reader.fieldnames

for i, row in enumerate(rows, start=1):
    cat = (row.get("category") or "").strip()
    pool = NAME_POOLS.get(cat, NAME_POOLS["default"])
    # choose a name and add an index to reduce repetition
    base = random.choice(pool)
    row["name"] = f"{base} #{i}" if base not in ("Generic Asset", "Misc Equipment") else base

os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
with open(OUTPUT, "w", newline='', encoding='utf-8') as fout:
    writer = csv.DictWriter(fout, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote {len(rows)} rows to {OUTPUT}")
print("Original file left unchanged. Rename or move the fixed file to replace the original if desired.")