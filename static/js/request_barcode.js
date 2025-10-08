function requestBarcode(assetName, id) {
    fetch('/generate-barcode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ assetName, id })
    })
    .then(res => res.json())
    .then(data => alert(data.message));
}