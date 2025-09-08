# utils.py
import pandas as pd

def _get_daily_sales(business):
    orders = business.orders.all().order_by("created_at")

    if not orders.exists():
        return None, "No sales data available."

    df = pd.DataFrame(list(orders.values("created_at", "total_amount")))
    df["date"] = pd.to_datetime(df["created_at"]).dt.date
    daily_sales = df.groupby("date")["total_amount"].sum().reset_index()
    daily_sales.rename(columns={"total_amount": "sales"}, inplace=True)

    daily_sales["date"] = pd.to_datetime(daily_sales["date"])
    return daily_sales, None

