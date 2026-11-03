from django.db import models

# Create your models here.
import uuid

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


class Category(models.Model):
    name = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(Lower('name'), name='store_category_name_ci_unique'),
        ]

    def __str__(self):
        return self.name


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=180)
    sku = models.CharField(max_length=80, unique=True, null=True, blank=True)
    categories = models.ManyToManyField(Category, related_name='products', blank=True)
    stock = models.PositiveIntegerField(default=0)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to='products/%Y/%m/', null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        constraints = [
            models.UniqueConstraint(
                Lower('sku'),
                condition=Q(sku__isnull=False),
                name='store_product_sku_ci_unique',
            ),
        ]

    def __str__(self):
        return self.name


class Sale(models.Model):
    class PaymentMethod(models.TextChoices):
        PIX = 'pix', 'Pix'
        CASH = 'cash', 'Dinheiro'
        DEBIT = 'debit', 'Débito'
        CREDIT = 'credit', 'Crédito'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    interest = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    installments = models.PositiveSmallIntegerField(default=1)
    amount_received = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    change_given = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    idempotency_key = models.CharField(max_length=100, unique=True, null=True, blank=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='sale_items')
    product_name = models.CharField(max_length=180)
    sku = models.CharField(max_length=80, blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()
    line_total = models.DecimalField(max_digits=10, decimal_places=2)


class StockMovement(models.Model):
    class Reason(models.TextChoices):
        MANUAL = 'manual', 'Ajuste manual'
        SALE = 'sale', 'Venda'
        INITIAL = 'initial', 'Estoque inicial'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='stock_movements')
    sale = models.ForeignKey(
        Sale,
        on_delete=models.PROTECT,
        related_name='stock_movements',
        null=True,
        blank=True,
    )
    previous_stock = models.PositiveIntegerField()
    new_stock = models.PositiveIntegerField()
    quantity_delta = models.IntegerField()
    reason = models.CharField(max_length=10, choices=Reason.choices)
    note = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.product.name}: {self.previous_stock} -> {self.new_stock}'
