document.addEventListener("DOMContentLoaded", get_json_data)

function get_json_data()
{
    var json_data = "/static/js/test_asset_data.json";

    xmlhttp = new XMLHttpRequest();
    xmlhttp.onreadystatechange = function()
    {
        if (this.readyState == 4 && this.status == 200)
        {
            var data = JSON.parse(this.responseText);
            append_json(data);
        }
    }

    xmlhttp.open("GET", json_data, true);
    xmlhttp.setRequestHeader("Content-type", "application/x-www-form-urlencoded");
    xmlhttp.send();

    function append_json(data)
    {
        var table_body = document.getElementById("asset-table-body");
        data.forEach(function(object)
        {
            var table_row = document.createElement("tr");
            table_row.classList.add("bg-white");
            
             table_row.innerHTML = "<th scope='row' class='px-6 py-4 font-medium whitespace-nowrap'>" + object.asset_name + "</th>" +
                "<td class='px-6 py-4'>" + object.category + "</td>" +
                "<td class='px-6 py-4'>" + object.current_valuation + "</td>" +
                "<td class='px-6 py-4 text-center'>" + (object.barcode_number || `<button class = "bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded" onclick="generateBarcodeNumber('${(object.asset_name, object.id)}')">Generate Barcode</button>`) + "</td>" + 
                "<td class='px-6 py-4'>" + object.current_condition + "</td>" +
                "<td class='px-6 py-4'>" + object.assigned_department + "</td>" +
                "<td class='px-6 py-4'>" + object.last_serviced + "</td>" +
                "<td class='px-6 py-4'>" + object.use_status + "</td>" +
                "<td class='px-6 py-4 text-right'>" +
                "<a href='/assets/add-barcode' class='font-medium text-blue-600 dark:text-blue-500 hover:underline'>Edit</a>" +
                "</td>";
            table_body.appendChild(table_row);
        })
    }
}