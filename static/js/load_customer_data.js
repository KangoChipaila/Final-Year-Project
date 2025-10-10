document.addEventListener("DOMContentLoaded", loadCustomerData);

function loadCustomerData() {
    fetch('/static/js/test_customer_data.json')
        .then(response => response.json())
        .then(data => renderCustomerSection(data));
}

function renderCustomerSection(customers) {
    const section = document.getElementById('customers-main-content');
    if (!section) return;

    // Customer Table
    let tableRows = customers.map(c => `
        <tr>
            <td class="px-4 py-2">${c.name}</td>
            <td class="px-4 py-2">${c.contact_person}</td>
            <td class="px-4 py-2"><a href = "mailto:${c.email}" class = "hover:text-blue-500">${c.email}</a></td>
            <td class="px-4 py-2">${c.phone}</td>
            <td class="px-4 py-2">${c.status}</td>
            <td class="px-4 py-2">
                <button type="button" onclick="showCustomerDetails('${c.id}', '${c.name}', '${c.contact_person}')" class="font-medium rounded-lg text-xs px-5 py-2.5 text-center me-2 mb-2 border border-black hover:text-white hover:bg-black focus:ring-black">View Details</button>
            </td>
            <td class="px-4 py-2">
                <button type="button" onclick="editCustomerDetails('${c.id}')" class="font-medium rounded-lg text-xs px-5 py-2.5 text-center me-2 mb-2 border border-black hover:text-white hover:bg-black focus:ring-black">Edit Details</button>
            </td>
        </tr>
    `).join('');

    section.innerHTML = `
        <div class="mb-8 w-4/5 mx-auto">
            <h2 class="text-lg font-bold mb-2">Customer List</h2>
            <table class="w-full text-sm text-left text-black shadow-md">
                <thead class="text-xs uppercase text-white bg-black">
                    <tr>
                        <th class="px-4 py-2">Name</th>
                        <th class="px-4 py-2">Contact Person</th>
                        <th class="px-4 py-2">Email</th>
                        <th class="px-4 py-2">Phone</th>
                        <th class="px-4 py-2">Status</th>
                        <th class="px-4 py-2 text-center">Actions</th>
                        <th class="px-4 py-2"></th>
                    </tr>
                </thead>
                <tbody>
                    ${tableRows}
                </tbody>
            </table>
        </div>
        <div id="customer-details" class = "mx-auto w-1/2" ></div>
    `;
}

function showCustomerDetails(customerId, customerName, customerContactPerson) {
    document.getElementById('customer-details').innerHTML = `
        <div class="mb-8 mx-auto w-full border border-black">
            <h2 class="text-lg font-bold mb-2">Customer Details</h2>
            <div class="bg-white p-4 rounded shadow">
                <p><strong>Name:</strong> ${customerName}</p>
                <p><strong>Contact Person:</strong> ${customerContactPerson}</p>
                <p><strong>Email:</strong> example@email.com</p>
            </div>
        </div>
        <!-- Add dynamic interaction log, opportunities, tasks here -->
    `;
}