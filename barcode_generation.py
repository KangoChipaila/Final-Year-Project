import barcode
import jsonify
from barcode.writer import ImageWriter
import os

BARCODE_DIR = os.path.join('static', 'barcodes')
os.makedirs(BARCODE_DIR, exist_ok=True)

def generate_barcode():
    asset_id = request.json.get('asset_id')
    if not asset_id:
        return jsonify({'error': 'No asset_id provided'}), 400

    barcode_path = os.path.join(BARCODE_DIR, f"{asset_id}.png")
    code128 = barcode.get('code128', asset_id, writer=ImageWriter())
    code128.save(barcode_path[:-4])  # python-barcode adds .png

    return jsonify({'barcode_url': f"/static/barcodes/{asset_id}.png"})

# Example: POST {"asset_id": "ABC123456"} to /generate-barcode