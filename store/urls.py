from django.urls import path

from . import views

app_name = 'store'

urlpatterns = [
    path('csrf/', views.csrf_token, name='csrf-token'),
    path('health/', views.health, name='health'),
    path('categories/', views.categories, name='categories'),
    path('products/', views.products, name='products'),
    path('products/<uuid:product_id>/', views.product_detail, name='product-detail'),
    path('stock-movements/', views.stock_movements, name='stock-movements'),
    path('sales/', views.sales, name='sales'),
]
