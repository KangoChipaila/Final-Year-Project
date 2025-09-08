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
            "<td class='px-6 py-4'>" + object.colour + "</td>" +
            "<td class='px-6 py-4'>" + object.category + "</td>" +
            "<td class='px-6 py-4'>" + object.current_valuation + "</td>" +
            "<td class='px-6 py-4'>" + object.barcode_number + "</td>";
            table_body.appendChild(table_row);
        })
    }
}