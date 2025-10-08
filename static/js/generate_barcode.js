function generateBarcodeNumber(assetName, id) {
    const initials = assetName
        .split(' ')
        .map(word => word[0])
        .join('')
        .toUpperCase();
    const timestamp = Date.now().toString().slice(-6);

    const fs = require('fs');
    const filePath = './test_asset_data.json';

    let rawData = fs.readFileSync(filePath);
    let jsonData = JSON.parse(rawData);

    // If jsonData is an array of assets
    let found = false;
    jsonData.forEach(asset => {
        if (asset.id === id) {
            asset.barcode_number = initials + timestamp;
            found = true;
        }
    });

    if (!found) {
        console.log('Asset not found:', id);
    }

    let updatedJsonData = JSON.stringify(jsonData, null, 2);
    fs.writeFileSync(filePath, updatedJsonData);
}