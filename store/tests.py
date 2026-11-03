import json
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Category, Product, Sale, SaleItem, StockMovement


class StoreApiTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.media_directory.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.product = Product.objects.create(
            name='Colar de teste',
            sku='COL-001',
            price=Decimal('12.50'),
            stock=5,
        )
        self.products_url = reverse('store:products')
        self.movements_url = reverse('store:stock-movements')
        self.sales_url = reverse('store:sales')

    def post_json(self, url, payload, **extra):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            **extra,
        )

    def test_product_list_supports_search_and_pagination(self):
        response = self.client.get(self.products_url, {'q': 'COL-001', 'page_size': 1})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['pagination']['total'], 1)
        self.assertEqual(body['data'][0]['sku'], 'COL-001')
        self.assertEqual(body['data'][0]['price'], '12.50')

    def test_product_creation_creates_tags_and_initial_stock_movement(self):
        response = self.post_json(self.products_url, {
            'name': 'Brinco novo',
            'sku': 'BR-001',
            'price': '25.90',
            'stock': 3,
            'categories': ['Brincos', 'Promoção'],
        })

        self.assertEqual(response.status_code, 201)
        product = Product.objects.get(sku='BR-001')
        self.assertEqual(set(product.categories.values_list('name', flat=True)), {'Brincos', 'Promoção'})
        self.assertEqual(product.stock_movements.get().quantity_delta, 3)
        self.assertEqual(Category.objects.count(), 2)

    def test_product_creation_accepts_valid_multipart_image(self):
        from io import BytesIO
        from PIL import Image

        output = BytesIO()
        Image.new('RGB', (2, 2), color='red').save(output, format='PNG')
        response = self.client.post(self.products_url, {
            'name': 'Produto com imagem',
            'price': '10.00',
            'categories': json.dumps(['Acessórios']),
            'image': SimpleUploadedFile('produto.png', output.getvalue(), content_type='image/png'),
        })

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Product.objects.get(name='Produto com imagem').image.name)

    def test_stock_movement_updates_stock_and_records_audit_entry(self):
        response = self.post_json(self.movements_url, {
            'product_id': str(self.product.id),
            'quantity_delta': -2,
            'note': 'Ajuste de contagem',
        })

        self.assertEqual(response.status_code, 201)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.previous_stock, movement.new_stock), (5, 3))
        self.assertEqual(response.json()['data']['quantity_delta'], -2)

    def test_stock_movement_cannot_make_stock_negative(self):
        response = self.post_json(self.movements_url, {
            'product_id': str(self.product.id),
            'quantity_delta': -6,
        })

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_product_patch_does_not_allow_direct_stock_update(self):
        response = self.client.patch(
            reverse('store:product-detail', args=[self.product.id]),
            data=json.dumps({'stock': 99}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_sale_calculates_prices_and_decrements_stock_atomically(self):
        response = self.post_json(self.sales_url, {
            'payment_method': 'credit',
            'installments': 3,
            'discount_type': 'percentage',
            'discount_value': '10',
            'interest_type': 'percentage',
            'interest_value': '5',
            'items': [{'product_id': str(self.product.id), 'quantity': 2}],
        })

        self.assertEqual(response.status_code, 201)
        body = response.json()['data']
        self.assertEqual(body['subtotal'], '25.00')
        self.assertEqual(body['discount'], '2.50')
        self.assertEqual(body['interest'], '1.13')
        self.assertEqual(body['total'], '23.63')
        self.assertEqual(body['installments'], 3)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(SaleItem.objects.count(), 1)
        movement = StockMovement.objects.get(reason=StockMovement.Reason.SALE)
        self.assertEqual(movement.quantity_delta, -2)

    def test_sale_rejects_insufficient_stock_without_partial_changes(self):
        response = self.post_json(self.sales_url, {
            'payment_method': 'pix',
            'items': [{'product_id': str(self.product.id), 'quantity': 6}],
        })

        self.assertEqual(response.status_code, 409)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_sale_idempotency_key_prevents_duplicate_charge(self):
        payload = {
            'payment_method': 'pix',
            'items': [{'product_id': str(self.product.id), 'quantity': 1}],
        }
        first = self.post_json(self.sales_url, payload, HTTP_IDEMPOTENCY_KEY='request-123')
        second = self.post_json(self.sales_url, payload, HTTP_IDEMPOTENCY_KEY='request-123')

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()['data']['id'], second.json()['data']['id'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)
        self.assertEqual(Sale.objects.count(), 1)

    def test_reusing_idempotency_key_with_different_payload_is_rejected(self):
        first_payload = {
            'payment_method': 'pix',
            'items': [{'product_id': str(self.product.id), 'quantity': 1}],
        }
        conflicting_payload = {
            'payment_method': 'pix',
            'items': [{'product_id': str(self.product.id), 'quantity': 2}],
        }
        self.post_json(self.sales_url, first_payload, HTTP_IDEMPOTENCY_KEY='request-456')
        response = self.post_json(
            self.sales_url,
            conflicting_payload,
            HTTP_IDEMPOTENCY_KEY='request-456',
        )

        self.assertEqual(response.status_code, 409)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)
        self.assertEqual(Sale.objects.count(), 1)

    def test_cash_sale_records_change_and_rejects_insufficient_received_amount(self):
        insufficient = self.post_json(self.sales_url, {
            'payment_method': 'cash',
            'amount_received': '10.00',
            'items': [{'product_id': str(self.product.id), 'quantity': 1}],
        })
        self.assertEqual(insufficient.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)

        response = self.post_json(self.sales_url, {
            'payment_method': 'cash',
            'amount_received': '20.00',
            'items': [{'product_id': str(self.product.id), 'quantity': 1}],
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['data']['amount_received'], '20.00')
        self.assertEqual(response.json()['data']['change_given'], '7.50')

    def test_csrf_endpoint_sets_cookie_for_mutating_requests(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.get(reverse('store:csrf-token'))
        self.assertEqual(response.status_code, 200)
        token = response.json()['data']['csrf_token']

        response = csrf_client.post(
            self.movements_url,
            data=json.dumps({'product_id': str(self.product.id), 'quantity_delta': 1}),
            content_type='application/json',
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 201)
