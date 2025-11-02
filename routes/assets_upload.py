from flask import Blueprint, request, redirect, url_for, flash, send_file
import csv
import io
import os
from werkzeug.utils import secure_filename

bp = Blueprint('assets_upload', __name__)

ALLOWED_EXTENSIONS = {'csv'}
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@bp.route('/assets/upload', methods=['POST'])
def upload_assets():
    if 'csv_file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('asset_overview'))

    file = request.files['csv_file']
    if file.filename == '':
        flash('No selected file', 'error')
        return redirect(url_for('asset_overview'))

    if not allowed_file(file.filename):
        flash('Only CSV files are allowed', 'error')
        return redirect(url_for('asset_overview'))

    filename = secure_filename(file.filename)
    stream = io.StringIO(file.stream.read().decode('utf-8', errors='replace'))
    reader = csv.DictReader(stream)

    required_fields = {'name', 'category', 'purchase_date', 'value', 'status'}
    headers = set([h.strip() for h in reader.fieldnames or []])

    if not required_fields.issubset(headers):
        flash(f'CSV must include headers: {", ".join(sorted(required_fields))}', 'error')
        return redirect(url_for('asset_overview'))

    rows = [ {k: (v or '').strip() for k,v in row.items()} for row in reader ]

    # Try to persist using SQLAlchemy Asset model if available
    try:
        from app import db
        from models import Asset  # adjust import path if your Asset model lives elsewhere
        added = 0
        for r in rows:
            asset = Asset(
                name = r.get('name'),
                category = r.get('category'),
                purchase_date = r.get('purchase_date') or None,
                value = float(r.get('value') or 0),
                status = r.get('status') or 'Active'
            )
            db.session.add(asset)
            added += 1
        db.session.commit()
        flash(f'Successfully imported {added} assets.', 'success')
        return redirect(url_for('asset_overview'))
    except Exception:
        # Fallback: write parsed rows to a file so user or maintainer can inspect
        out_path = os.path.join(DATA_DIR, 'assets_imported.csv')
        with open(out_path, 'w', newline='', encoding='utf-8') as fout:
            writer = csv.DictWriter(fout, fieldnames=list(reader.fieldnames))
            writer.writeheader()
            writer.writerows(rows)
        flash(f'Imported {len(rows)} rows (saved to data/assets_imported.csv). Database save not available.', 'warning')
        return redirect(url_for('asset_overview'))

@bp.route('/assets/sample-csv')
def download_sample_csv():
    sample = io.StringIO()
    writer = csv.writer(sample)
    writer.writerow(['name','category','purchase_date','value','status'])
    writer.writerow(['Laptop','IT Equipment','2023-03-15','1200.00','Active'])
    writer.writerow(['Office Chair','Furniture','2022-01-10','150.00','Active'])
    sample.seek(0)
    return send_file(io.BytesIO(sample.getvalue().encode('utf-8')),
                     mimetype='text/csv',
                     download_name='assets_sample.csv',
                     as_attachment=True)