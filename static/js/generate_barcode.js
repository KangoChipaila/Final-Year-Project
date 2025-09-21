function generateBarcodeNumber(assetName) {
    // Use asset name initials + timestamp for uniqueness
    const initials = assetName
        .split(' ')
        .map(word => word[0])
        .join('')
        .toUpperCase();
    const timestamp = Date.now().toString().slice(-6); // last 6 digits of timestamp
    return initials + timestamp;
}