"""
Metrics — the ORIGINAL stn.py logic, unchanged.

Only the data source changed (Postgres instead of CSV). Every formula,
threshold and classification below is copied from the original functions:
calculate_profit_metrics, calculate_velocity_metrics, calculate_seasonality_metrics,
and the risk block in run_global_supply_chain_optimization.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from app import config

VELOCITY_ANALYSIS_DAYS = [5, 10, 30]


# --- loading -----------------------------------------------------------------

def cap_by_value(quantity: int, cost_price) -> int:
    """No single order may exceed config.MAX_ORDER_VALUE rupees."""
    cost = float(cost_price or 0)
    if cost <= 0 or quantity <= 0:
        return max(0, quantity)
    affordable = int(config.MAX_ORDER_VALUE // cost)
    return max(0, min(quantity, affordable))


async def load_products(pool) -> list[dict]:
    """Every product the engines should look at, narrowed by ONLY_PRODUCTS."""
    if config.ONLY_PRODUCTS:
        rows = await pool.fetch(
            "SELECT * FROM v_product_stock WHERE product_id = ANY($1::int[]) "
            "ORDER BY product_id", config.ONLY_PRODUCTS)
    else:
        rows = await pool.fetch("SELECT * FROM v_product_stock ORDER BY product_id")
    return [dict(r) for r in rows]


async def load_daily(pool, product_id: int, as_of: date, days: int = 45) -> list[int]:
    rows = await pool.fetch("""
        SELECT units FROM daily_sales
        WHERE product_id = $1 AND sale_date <= $2 AND sale_date > $3
        ORDER BY sale_date
    """, product_id, as_of, as_of - timedelta(days=days))
    return [r["units"] for r in rows]


async def load_monthly_list(pool, product_id: int) -> list[int]:
    """Monthly sales in year/month order — same ordering the CSV had."""
    rows = await pool.fetch("""
        SELECT units FROM monthly_sales
        WHERE product_id = $1 ORDER BY year, month
    """, product_id)
    return [r["units"] for r in rows]


# --- profit (calculate_profit_metrics) ---------------------------------------

def calculate_profit_metrics(product: dict) -> dict:
    selling_price = float(product["selling_price"])
    cost_price = float(product["cost_price"])
    inventory_quantity = product["warehouse_qty"]

    profit_per_unit = selling_price - cost_price
    profit_margin = (profit_per_unit / selling_price) * 100 if selling_price > 0 else 0
    total_inventory_value = inventory_quantity * cost_price

    if profit_margin > 25:
        profitability_status = "Excellent"
    elif profit_margin > 15:
        profitability_status = "High"
    elif profit_margin > 8:
        profitability_status = "Medium"
    else:
        profitability_status = "Low"

    return {
        "profit_per_unit": profit_per_unit,
        "profit_margin_percent": round(profit_margin, 2),
        "total_inventory_value": total_inventory_value,
        "profitability_status": profitability_status,
    }


# --- velocity (calculate_velocity_metrics) -----------------------------------

def calculate_velocity_metrics(daily_sales: list[int]) -> dict:
    if len(daily_sales) < max(VELOCITY_ANALYSIS_DAYS):
        return {"error": f"Insufficient sales data (have {len(daily_sales)} days, "
                         f"need {max(VELOCITY_ANALYSIS_DAYS)})"}

    all_sales = np.array(daily_sales)

    velocity_metrics = {}
    for period in VELOCITY_ANALYSIS_DAYS:
        period_sales = all_sales[-period:] if len(all_sales) >= period else all_sales
        velocity_metrics[f"day_{period}_avg"] = round(float(np.mean(period_sales)), 2)

    day_5_avg = velocity_metrics["day_5_avg"]
    day_30_avg = velocity_metrics["day_30_avg"]

    velocity_category = "Regular_Analysis_Needed"
    if day_5_avg > 50 and velocity_metrics["day_10_avg"] > 45 and day_30_avg > 40:
        velocity_category = "Category_1_All_High"
    elif day_5_avg > day_30_avg * 1.5 and day_5_avg > 50:
        velocity_category = "Category_4_Demand_Spike"

    lead_time = 3  # default, as in the original
    safety_factor = config.TOTAL_INVENTORY_CAPACITY / config.NUMBER_OF_STORES
    safety_buffer_per_store = safety_factor / day_30_avg if day_30_avg > 0 else 1
    demand_during_leadtime = day_5_avg * lead_time
    optimal_order_quantity = int(
        demand_during_leadtime * safety_buffer_per_store * config.SAFETY_STOCK_FACTOR
    )

    velocity_metrics.update({
        "velocity_category": velocity_category,
        "demand_spike_ratio": round(day_5_avg / day_30_avg if day_30_avg > 0 else 0, 2),
        "optimal_order_quantity": optimal_order_quantity,
        "total_data_points": len(daily_sales),
    })
    return velocity_metrics


# --- seasonality (calculate_seasonality_metrics) -----------------------------

def calculate_seasonality_metrics(monthly_sales_list: list[int],
                                  current_month: int) -> dict:
    if len(monthly_sales_list) < 12:
        return {"error": f"Insufficient monthly data (have {len(monthly_sales_list)} "
                         f"months, need 12)"}

    monthly_avg = {}
    years_of_data = min(len(monthly_sales_list) // 12, config.SEASONALITY_YEARS)

    for month in range(1, 13):
        month_values = []
        for year in range(years_of_data):
            index = year * 12 + (month - 1)
            if index < len(monthly_sales_list):
                month_values.append(monthly_sales_list[index])
        monthly_avg[month] = sum(month_values) / len(month_values) if month_values else 0

    max_month = max(monthly_avg, key=monthly_avg.get) if monthly_avg else 1
    min_month = min(monthly_avg, key=monthly_avg.get) if monthly_avg else 1

    avg_sales = sum(monthly_avg.values()) / 12 if monthly_avg else 0
    max_sales = monthly_avg[max_month]
    min_sales = monthly_avg[min_month]

    seasonality_ratio = max_sales / min_sales if min_sales > 0 else 1

    if seasonality_ratio > 8:
        classification, confidence = "Extremely Seasonal", 95
    elif seasonality_ratio > 4:
        classification, confidence = "Highly Seasonal", 90
    elif seasonality_ratio > 2:
        classification, confidence = "Moderately Seasonal", 75
    else:
        classification, confidence = "Non-Seasonal", 60

    current_sales = monthly_avg.get(current_month, avg_sales)

    if current_sales > avg_sales * 1.8:
        phase, multiplier = "Peak", 2.0
        recommendation = "Order Now - Peak Season"
    elif current_sales > avg_sales * 1.3:
        phase, multiplier = "High Season", 1.6
        recommendation = "Order Now - High Demand"
    elif current_sales < avg_sales * 0.4:
        phase, multiplier = "Off-Season", 0.4
        recommendation = "Reduce Orders - Off Season"
    elif current_sales < avg_sales * 0.7:
        phase, multiplier = "Low Season", 0.7
        recommendation = "Minimal Orders - Low Demand"
    else:
        peak_proximity = min(abs(current_month - max_month),
                             abs(current_month - max_month + 12),
                             abs(current_month - max_month - 12))
        if peak_proximity <= 1:
            if current_month < max_month or (max_month == 1 and current_month == 12):
                phase, multiplier = "Rising", 1.4
                recommendation = "Prepare for Peak - Rising Demand"
            else:
                phase, multiplier = "Declining", 0.8
                recommendation = "Monitor Closely - Declining Season"
        else:
            phase, multiplier = "Stable", 1.0
            recommendation = "Standard Management"

    return {
        "seasonality_classification": classification,
        "current_season_phase": phase,
        "demand_multiplier": round(multiplier, 2),
        "season_confidence": confidence,
        "peak_months": [max_month],
        "low_months": [min_month],
        "recommendation": recommendation,
        "reasoning": f"Peak month {max_month} (avg: {max_sales:.0f}), current month "
                     f"{current_month} (avg: {current_sales:.0f}) shows {phase} pattern",
        "seasonality_ratio": round(seasonality_ratio, 2),
        "total_months_analyzed": len(monthly_sales_list),
    }


# --- risk (the block inside the original main loop) --------------------------

def calculate_risk(product: dict, velocity_data: dict) -> dict:
    current_total_stock = product["warehouse_qty"] + product["store_qty"]
    daily_velocity = velocity_data.get("day_5_avg", 0) if "error" not in velocity_data else 0
    days_until_stockout = current_total_stock / max(daily_velocity, 0.1)
    lead_time = product["lead_time_days"]

    if days_until_stockout <= lead_time:
        risk_level = "CRITICAL"
    elif days_until_stockout <= lead_time * 1.5:
        risk_level = "HIGH"
    elif days_until_stockout <= lead_time * 2:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "current_total_stock": current_total_stock,
        "days_until_stockout": round(days_until_stockout, 1),
        "lead_time_days": lead_time,
        "risk_level": risk_level,
    }


async def compute_all(pool, product: dict, as_of: date) -> dict:
    daily = await load_daily(pool, product["product_id"], as_of)
    monthly = await load_monthly_list(pool, product["product_id"])

    profit_data = calculate_profit_metrics(product)
    velocity_data = calculate_velocity_metrics(daily)
    seasonality_data = calculate_seasonality_metrics(monthly, as_of.month)
    risk_data = calculate_risk(product, velocity_data)

    return {"profit": profit_data, "velocity": velocity_data,
            "seasonality": seasonality_data, "risk": risk_data}
