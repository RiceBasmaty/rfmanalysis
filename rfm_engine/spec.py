"""Field spec, default segment rules and segment descriptions.

Mirrors the RFM field spec workbook (RFM_Specs.xlsx, "Field Spec" tab). Change it here
and the app's column-mapping screen, the L0 checks and the report all follow.
"""
from __future__ import annotations

TABLES = ("transactions", "customers", "items", "fx_rates")

# table -> column -> (type, requirement, description)
SPEC: dict[str, dict[str, tuple[str, str, str]]] = {
    "transactions": {
        "transaction_id": ("string", "Required", "Unique ID of the purchase or return"),
        "transaction_date": ("date", "Required", "YYYY-MM-DD, market local time"),
        "hashed_email": ("hash", "Required*", "SHA-256 of trimmed, lowercased email"),
        "customer_id": ("string", "Required*", "CRM / loyalty ID (pseudonymised)"),
        "transaction_amount": ("number", "Required", "Net, ex VAT and shipping; negative for returns"),
        "transaction_type": ("string", "Recommended", "purchase or return"),
        "original_transaction_id": ("string", "Optional", "For returns: the purchase being returned"),
        "basket_size": ("number", "Optional", "Units in the transaction"),
        "currency": ("string", "Required for L2", "ISO 4217 code"),
        "market": ("string", "Required for L2", "ISO 3166-1 alpha-2 country"),
        "medium": ("string", "Required for L4", "ecommerce or retail"),
        "store_id": ("string", "Optional", "Physical store ID"),
    },
    "customers": {
        "hashed_email": ("hash", "Required*", "Same format as in transactions"),
        "customer_id": ("string", "Required*", "Same format as in transactions"),
        "first_purchase_date": ("date", "Recommended", "First-ever purchase, incl. before the window"),
        "loyalty_member": ("bool", "Optional", "true / false"),
        "marketing_consent": ("bool", "Optional", "true / false"),
    },
    "items": {
        "transaction_id": ("string", "Required", "Join key to transactions"),
        "item_sku": ("string", "Required", "Product identifier"),
        "item_quantity": ("number", "Required", "Units; negative on return lines"),
        "item_price": ("number", "Required", "Line total, net, ex VAT"),
        "item_category": ("string", "Required", "Top-level category"),
        "item_sub_category": ("string", "Optional", "Second-level category"),
        "item_discount": ("number", "Optional", "Discount on the line"),
    },
    "fx_rates": {
        "month": ("string", "Required", "YYYY-MM"),
        "currency": ("string", "Required", "ISO 4217 code"),
        "rate": ("number", "Required", "Multiply to get the reporting currency"),
    },
}

TABLE_INFO = {
    "transactions": ("Transactions", "Required", "One row per purchase or return"),
    "customers": ("Customers", "Recommended", "Links email ↔ ID, first purchase date, consent"),
    "items": ("Items", "Optional", "Item lines — enables the items check and category mix"),
    "fx_rates": ("FX rates", "If several currencies", "Monthly rate to the reporting currency"),
}

# Common client column names -> spec column, used to pre-fill the mapping screen.
SYNONYMS: dict[str, list[str]] = {
    "transaction_id": ["order_id", "orderid", "order_number", "order_no", "transactionid", "txn_id", "receipt_id"],
    "transaction_date": ["order_date", "orderdate", "date", "purchase_date", "created_at", "transactiondate"],
    "hashed_email": ["email_hash", "email_sha256", "sha256_email", "hashedemail", "hashed_e_mail"],
    "customer_id": ["customerid", "member_id", "loyalty_id", "client_id", "cust_id", "crm_id"],
    "transaction_amount": ["amount", "total", "revenue", "net_amount", "order_total", "total_ex_tax", "value"],
    "transaction_type": ["type", "order_type", "txn_type"],
    "original_transaction_id": ["original_order_id", "parent_order_id", "return_of"],
    "basket_size": ["units", "quantity", "items_count", "qty"],
    "currency": ["currency_code", "order_currency", "curr"],
    "market": ["country", "country_code", "shipping_country"],
    "medium": ["channel", "sales_channel"],
    "store_id": ["store", "shop_id", "location_id"],
    "first_purchase_date": ["first_order_date", "first_purchase", "customer_since"],
    "loyalty_member": ["is_member", "loyalty", "member"],
    "marketing_consent": ["consent", "newsletter", "opt_in", "email_consent"],
    "item_sku": ["sku", "product_sku", "ean", "product_id"],
    "item_quantity": ["quantity", "qty", "units"],
    "item_price": ["price", "line_total", "line_amount", "amount"],
    "item_category": ["category", "product_category", "folder"],
    "item_sub_category": ["sub_category", "subcategory"],
    "item_discount": ["discount", "line_discount"],
    "month": ["period", "year_month"],
    "rate": ["fx_rate", "exchange_rate"],
}

DEFAULT_RULES: list[tuple[str, str]] = [
    ("Champions", "R >= 4 and F >= 4 and M >= 4"),
    ("Loyal High Value", "R >= 3 and F >= 4 and M >= 4"),
    ("At Risk High Value", "R <= 2 and F >= 3 and M >= 4"),
    ("New Customers", "R >= 4 and F == 1 and not has_prior_history"),
    ("Reactivated", "R >= 4 and F == 1 and has_prior_history"),
    ("Potential Loyalists", "R >= 4"),
    ("Needs Attention", "R == 3"),
    ("Hibernating", "R <= 2"),
]

SEGMENT_INFO: dict[str, tuple[str, str]] = {
    "Champions": ("Bought recently, often and spend the most.",
                  "Reward, early access, referral; protect from discounting."),
    "Loyal High Value": ("Frequent, high-spending customers, not quite as recent.",
                         "Loyalty benefits, upsell premium ranges."),
    "At Risk High Value": ("Used to buy often and spend a lot, but have not purchased for a long time.",
                           "Win-back with personalised offers; highest revenue at stake."),
    "New Customers": ("First-ever purchase, made recently.", "Onboarding flow, second-purchase incentive."),
    "Reactivated": ("One recent purchase in the window, but with purchase history before it.",
                    "Re-engage quickly to rebuild the habit."),
    "Potential Loyalists": ("Recent customers with a few purchases.", "Cross-sell, membership invitation."),
    "Needs Attention": ("Average recency; starting to slip.", "Reminders, recommendations, limited-time offers."),
    "Hibernating": ("Last purchase long ago, low frequency.", "Low-cost reactivation or suppress to save budget."),
}

RULE_VARIABLES = ("R", "F", "M", "recency_days", "frequency", "monetary", "has_prior_history")
