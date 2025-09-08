from django.urls import path
from . import views

urlpatterns = [
    # -------------------------
    # User Authentication
    # -------------------------
    path('signup/', views.signup, name='signup'),
    path('login/', views.login, name='login'),
    path('logout/', views.logout, name='logout'),
    path('dashboard/', views.dashboard_metrics, name='dashboard_metrics'),
    path('businesses/', views.list_user_businesses, name='list-businesses'),
    path('businesses/add/', views.add_business, name='add-business'),

    # -------------------------
    # Products (CORRECTED)
    # -------------------------
    path('products/', views.product_list, name='product_list'),
    path('products/<int:pk>/', views.product_list, name='product_detail'),
    path('products/delete/<str:sku>/', views.delete_product, name='delete_product'),

    # -------------------------
    # Orders
    # -------------------------
    path('orders/', views.orders_list, name='orders_list'),
    path('orders/add/', views.add_edit_order, name='add_edit_order'),
    path('orders/<int:pk>/delete/', views.delete_order, name='delete-order'),

    # -------------------------
    # Returns
    # -------------------------
    path('returns/', views.returns_list, name='returns_list'),
    path('returns/add/', views.add_edit_return, name='add_edit_return'),
    path('returns/remove/<int:pk>/', views.remove_return, name='remove-return'),
    path('returns/remove/<int:pk>', views.remove_return, name='remove-return-no-slash'),
    # Alternate delete endpoints for compatibility
    path('returns/<int:pk>/delete/', views.delete_return, name='delete-return'),
    path('returns/<int:pk>/remove/', views.remove_return, name='remove-return-alt'),
    path('returns/<int:pk>/remove', views.remove_return, name='remove-return-alt-no-slash'),

    # -------------------------
    # Analysis
    # -------------------------
    path("analysis/sales-overview/", views.sales_overview, name="sales-overview"),
    path('analysis/returns-analysis/', views.returns_analysis, name='returns-analysis'),
    path('analysis/revenue-profit-analysis/', views.revenue_profit_analysis, name="revenue-profit-analysis"),
    path('analysis/inventory-analysis/', views.inventory_analysis, name='inventory-analysis'),
    path('analysis/customer-sales-analysis/', views.customer_sales_analysis, name='customer-sales-analysis'),

    path('analysis/sales-overview-report/', views.sales_overview_report, name='sales-overview-report'),
    path('analysis/returns-analysis-report/', views.returns_analysis_report, name='returns-analysis-report'),
    path('analysis/revenue-profit-analysis-report/', views.revenue_profit_analysis_report, name='revenue-profit-analysis-report'),
    path('analysis/inventory-analysis-report/', views.inventory_analysis_report, name='inventory-analysis-report'),
    path('analysis/customer-sales-analysis-report/', views.customer_sales_analysis_report, name='customer-sales-analysis-report'),

    path("sales-forecast/", views.sales_forecast_analysis, name="sales_forecast"),
    path("sales-forecast/retrain/", views.retrain_forecast_model, name="retrain_forecast"),
]