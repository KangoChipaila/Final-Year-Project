# ...existing code...
@app.route('/sales-overview')
@login_required
def sales_overview():
    # existing try/except and data gathering above...
    try:
        # gather customers and inventory for the order-entry UI (best-effort)
        customers = []
        if 'Customer' in globals():
            try:
                rows = db.session.query(Customer).order_by(getattr(Customer, "name", Customer)).all()
                for c in rows:
                    customers.append({"id": getattr(c, "id", None), "name": getattr(c, "name", "") or ""})
            except Exception:
                app.logger.exception("Failed to load customers for sales_overview")

        inventory = []
        if 'InventoryItem' in globals():
            try:
                inv_rows = db.session.query(InventoryItem).order_by(getattr(InventoryItem, "product", InventoryItem)).all()
                for it in inv_rows:
                    inventory.append({
                        "id": getattr(it, "id", None),
                        "product": getattr(it, "product", None) or getattr(it, "name", "") or f"Item {getattr(it,'id', '')}",
                        "quantity": float(getattr(it, "quantity", 0) or 0),
                        "unit_cost": float(getattr(it, "unit_cost", 0) or 0),
                        "reorder_level": float(getattr(it, "reorder_level", 0) or 0)
                    })
            except Exception:
                app.logger.exception("Failed to load inventory for sales_overview")

        # compute KPIs (safe, adaptable to missing models/columns)
        try:
            orders_count = 0
            customers_count = 0
            total_sales_val = 0.0

            if 'SalesOrder' in globals():
                orders_count = int(db.session.query(func.count()).select_from(SalesOrder).scalar() or 0)
                value_col = getattr(SalesOrder, "total", None) or getattr(SalesOrder, "amount", None)
                if value_col is not None:
                    total_sales_val = float(db.session.query(func.coalesce(func.sum(value_col), 0)).scalar() or 0.0)

            if 'Customer' in globals():
                customers_count = int(db.session.query(func.count()).select_from(Customer).scalar() or 0)

            kpi = {
                "total_sales": f"{total_sales_val:,.2f}",
                "orders": int(orders_count),
                "customers": int(customers_count),
                "revenue": f"{total_sales_val:,.2f}"
            }
        except Exception:
            app.logger.exception("Failed to compute sales_overview KPIs")
            kpi = {"total_sales": "N/A", "orders": "N/A", "customers": "N/A", "revenue": "N/A"}

        # reuse the precomputed graphs from module-level variables if present
        sales_trend = globals().get("sales_trend_graph", {"data": [], "layout": {}})
        goods_perf = globals().get("goods_performance_pie_chart", {"data": [], "layout": {}})
        cust_expenditure = globals().get("customer_expenditure_pie_chart", {"data": [], "layout": {}})

        return render_template('sales_overview.html',
                               sales_trend_graph=sales_trend,
                               goods_performance_pie_chart=goods_perf,
                               customer_expenditure_pie_chart=cust_expenditure,
                               customers=customers,
                               inventory=inventory,
                               kpi=kpi)
    except Exception:
        app.logger.exception("Failed to render sales_overview")
        # safe fallback
        return render_template('sales_overview.html',
                               sales_trend_graph={"data": [], "layout": {}},
                               goods_performance_pie_chart={"data": [], "layout": {}},
                               customer_expenditure_pie_chart={"data": [], "layout": {}},
                               customers=[], inventory[], kpi={"total_sales":"N/A","orders":"N/A","customers":"N/A","revenue":"N/A"})
# ...existing code...
```# filepath: c:\Users\Kango Chipaila\Documents\GitHub\Final-Year-Project\app.py