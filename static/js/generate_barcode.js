function generateBarcodeNumber(assetName) {
    // Use asset name initials + timestamp for uniqueness
    const initials = assetName
        .split(' ')
        .map(word => word[0])
        .join('')
        .toUpperCase();
    const timestamp = Date.now().toString().slice(-6); // last 6 digits of timestamp
    //console.log(initials + timestamp);

    const fs = require('fs')
    
    const filePath = './test_asset_data.json'

    let rawData = fs.readFileSync(filePath);

    let jsonData = JSON.parse(rawData);

    jsonData.barcode_number = initials + timestamp;

    let updatedJsonData = JSON.stringify(jsonData, null, 2);

    fs.writeFileSync(filePath, updatedJsonData);

}