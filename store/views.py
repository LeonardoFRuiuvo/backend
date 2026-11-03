from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

from PIL import Image, UnidentifiedImageError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.middleware.csrf import get_token

from .api import (
    ApiError,
    api_endpoint,
    category_names,
    decimal_value,
    integer_value,
    json_body,
    MAX_MONEY,
    paginated_response,
    parse_uuid,
    required_text,
    serialize_category,
    serialize_movement,
    serialize_product,
    serialize_sale,
)
from .models import Category, Product, Sale, SaleItem, StockMovement

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP'}
CENT = Decimal('0.01')


def _get_categories(names):
    categories = []
    for name in names:
        category = Category.objects.filter(name__iexact=name).first()
        if category is None:
            category = Category.objects.create(name=name)
        categories.append(category)
    return categories


def _validate_image(uploaded_image):
    if uploaded_image is None:
        return
    if uploaded_image.size > MAX_IMAGE_BYTES:
        raise ApiError('A imagem deve ter no máximo 5 MB.', details={'image': 'Limite de 5 MB.'})
    try:
        image = Image.open(uploaded_image)
        if image.width * image.height > 24_000_000:
            raise ApiError('A imagem não pode exceder 24 megapixels.')
        image.verify()
        if image.format not in ALLOWED_IMAGE_FORMATS:
            raise ApiError('Formato de imagem não suportado. Use JPEG, PNG ou WebP.')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ApiError('O arquivo enviado não é uma imagem válida.')
    finally:
        uploaded_image.seek(0)


def _set_product_categories(product, names):
    product.categories.set(_get_categories(names))


@api_endpoint('GET')
def csrf_token(request):
    return JsonResponse({'data': {'csrf_token': get_token(request)}})


@api_endpoint('GET')
def health(request):
    return JsonResponse({'data': {'status': 'ok'}})


@api_endpoint('GET')
def categories(request):
    queryset = Category.objects.all()
    return JsonResponse({'data': [serialize_category(category) for category in queryset]})


@api_endpoint('GET', 'POST')
def products(request):
    if request.method == 'GET':
        queryset = Product.objects.filter(is_active=True).prefetch_related('categories')
        search = request.GET.get('q', '').strip()
        category = request.GET.get('category', '').strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(sku__icontains=search))
        if category:
            queryset = queryset.filter(categories__name__iexact=category)
        queryset = queryset.distinct().order_by('name', 'id')
        return paginated_response(request, queryset, serialize_product)

    data = json_body(request)
    name = required_text(data, 'name')
    sku = required_text(data, 'sku', maximum=80, optional=True) or None
    price = decimal_value(data.get('price'), 'price')
    stock = integer_value(data.get('stock', 0), 'stock')
    category_list = category_names(data.get('categories'))
    image = request.FILES.get('image')
    _validate_image(image)

    with transaction.atomic():
        product = Product.objects.create(name=name, sku=sku, price=price, stock=stock, image=image)
        _set_product_categories(product, category_list)
        if stock > 0:
            StockMovement.objects.create(
                product=product,
                previous_stock=0,
                new_stock=stock,
                quantity_delta=stock,
                reason=StockMovement.Reason.INITIAL,
                note='Estoque inicial do cadastro.',
            )
    product = Product.objects.prefetch_related('categories').get(pk=product.pk)
    return JsonResponse({'data': serialize_product(product, request)}, status=201)


@api_endpoint('GET', 'PATCH')
def product_detail(request, product_id):
    product_id = parse_uuid(product_id, 'product_id')
    try:
        product = Product.objects.prefetch_related('categories').get(pk=product_id)
    except Product.DoesNotExist:
        raise ApiError('Produto não encontrado.', status=404)

    if request.method == 'GET':
        return JsonResponse({'data': serialize_product(product, request)})

    data = json_body(request)
    if 'stock' in data:
        raise ApiError(
            'Estoque deve ser alterado pelo endpoint de movimentações.',
            details={'stock': 'Use POST /api/v1/stock-movements.'},
        )
    allowed_fields = {'name', 'sku', 'price', 'categories', 'is_active', 'remove_image'}
    unexpected = set(data) - allowed_fields
    if unexpected:
        raise ApiError('A requisição contém campos não permitidos.', details={'fields': sorted(unexpected)})

    uploaded_image = request.FILES.get('image')
    _validate_image(uploaded_image)
    old_image_name = None
    with transaction.atomic():
        if 'name' in data:
            product.name = required_text(data, 'name')
        if 'sku' in data:
            product.sku = required_text(data, 'sku', maximum=80, optional=True) or None
        if 'price' in data:
            product.price = decimal_value(data['price'], 'price')
        if 'is_active' in data:
            if not isinstance(data['is_active'], bool):
                raise ApiError('O campo "is_active" deve ser booleano.')
            product.is_active = data['is_active']
        if 'remove_image' in data:
            if data['remove_image'] is not True:
                raise ApiError('O campo "remove_image" deve ser true para remover a imagem.')
            if product.image:
                old_image_name = product.image.name
                product.image = None
        if uploaded_image:
            if product.image and old_image_name is None:
                old_image_name = product.image.name
            product.image = uploaded_image
        product.save()
        if 'categories' in data:
            _set_product_categories(product, category_names(data['categories']))
        if old_image_name and (not product.image or old_image_name != product.image.name):
            image_storage = Product._meta.get_field('image').storage
            transaction.on_commit(
                lambda storage=image_storage, image_name=old_image_name: storage.delete(image_name)
            )

    product = Product.objects.prefetch_related('categories').get(pk=product.pk)
    return JsonResponse({'data': serialize_product(product, request)})


@api_endpoint('GET', 'POST')
def stock_movements(request):
    if request.method == 'GET':
        queryset = StockMovement.objects.select_related('product', 'sale').all()
        product_filter = request.GET.get('product_id')
        reason_filter = request.GET.get('reason')
        if product_filter:
            queryset = queryset.filter(product_id=parse_uuid(product_filter, 'product_id'))
        if reason_filter:
            valid_reasons = {choice[0] for choice in StockMovement.Reason.choices}
            if reason_filter not in valid_reasons:
                raise ApiError('Motivo de movimentação inválido.')
            queryset = queryset.filter(reason=reason_filter)
        return paginated_response(request, queryset, serialize_movement)

    data = json_body(request)
    product_id = parse_uuid(data.get('product_id'), 'product_id')
    delta = integer_value(
        data.get('quantity_delta'),
        'quantity_delta',
        minimum=-2_147_483_647,
        maximum=2_147_483_647,
    )
    if delta == 0:
        raise ApiError('A quantidade da movimentação não pode ser zero.')
    note = required_text(data, 'note', maximum=240, optional=True)

    with transaction.atomic():
        try:
            product = Product.objects.select_for_update().get(pk=product_id, is_active=True)
        except Product.DoesNotExist:
            raise ApiError('Produto não encontrado.', status=404)
        new_stock = product.stock + delta
        if new_stock < 0:
            raise ApiError(
                'A movimentação deixaria o estoque negativo.',
                details={'available_stock': product.stock},
            )
        if new_stock > 2_147_483_647:
            raise ApiError('A quantidade resultante excede o limite permitido.')
        previous_stock = product.stock
        product.stock = new_stock
        product.save(update_fields=['stock', 'updated_at'])
        movement = StockMovement.objects.create(
            product=product,
            previous_stock=previous_stock,
            new_stock=new_stock,
            quantity_delta=delta,
            reason=StockMovement.Reason.MANUAL,
            note=note,
        )
    movement = StockMovement.objects.select_related('product').get(pk=movement.pk)
    return JsonResponse({'data': serialize_movement(movement)}, status=201)


def _sale_items(data):
    items = data.get('items')
    if not isinstance(items, list) or not items:
        raise ApiError('A venda precisa conter ao menos um item.')
    quantities = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ApiError('Cada item da venda deve ser um objeto.')
        product_id = parse_uuid(item.get('product_id'), f'items[{index}].product_id')
        quantity = integer_value(item.get('quantity'), f'items[{index}].quantity', minimum=1)
        quantities[product_id] = quantities.get(product_id, 0) + quantity
        if quantities[product_id] > 2_147_483_647:
            raise ApiError('A quantidade do item excede o limite permitido.')
    return quantities


@api_endpoint('GET', 'POST')
def sales(request):
    if request.method == 'GET':
        queryset = Sale.objects.prefetch_related('items').all()
        payment_method = request.GET.get('payment_method')
        if payment_method:
            valid_methods = {choice[0] for choice in Sale.PaymentMethod.choices}
            if payment_method not in valid_methods:
                raise ApiError('Forma de pagamento inválida.')
            queryset = queryset.filter(payment_method=payment_method)
        return paginated_response(request, queryset, serialize_sale)

    data = json_body(request)
    valid_methods = {choice[0] for choice in Sale.PaymentMethod.choices}
    payment_method = data.get('payment_method')
    if not isinstance(payment_method, str) or payment_method not in valid_methods:
        raise ApiError('Forma de pagamento inválida.', details={'payment_method': sorted(valid_methods)})

    discount_type = data.get('discount_type', 'amount')
    interest_type = data.get('interest_type', 'amount')
    if not isinstance(discount_type, str) or discount_type not in {'amount', 'percentage'}:
        raise ApiError('discount_type deve ser "amount" ou "percentage".')
    if not isinstance(interest_type, str) or interest_type not in {'amount', 'percentage'}:
        raise ApiError('interest_type deve ser "amount" ou "percentage".')

    discount_value = decimal_value(
        data.get('discount_value', 0),
        'discount_value',
        maximum=Decimal('100') if discount_type == 'percentage' else MAX_MONEY,
    )
    interest_value = decimal_value(
        data.get('interest_value', 0),
        'interest_value',
        maximum=Decimal('1000') if interest_type == 'percentage' else MAX_MONEY,
    )
    if discount_type == 'percentage' and discount_value > 100:
        raise ApiError('O desconto percentual deve estar entre 0 e 100.')
    if payment_method != Sale.PaymentMethod.CREDIT and interest_value != 0:
        raise ApiError('Juros só podem ser informados para pagamento no crédito.')
    if payment_method != Sale.PaymentMethod.CREDIT and interest_type != 'amount':
        raise ApiError('interest_type só se aplica ao pagamento no crédito.')

    installments = integer_value(data.get('installments', 1), 'installments', minimum=1, maximum=12)
    if payment_method != Sale.PaymentMethod.CREDIT and installments != 1:
        raise ApiError('Parcelamento só pode ser informado para pagamento no crédito.')
    raw_amount_received = data.get('amount_received')
    if payment_method != Sale.PaymentMethod.CASH and raw_amount_received not in (None, ''):
        raise ApiError('amount_received só se aplica ao pagamento em dinheiro.')
    amount_received_input = (
        decimal_value(raw_amount_received, 'amount_received')
        if payment_method == Sale.PaymentMethod.CASH and raw_amount_received not in (None, '')
        else None
    )

    item_quantities = _sale_items(data)
    idempotency_key = request.headers.get('Idempotency-Key', '').strip() or None
    if idempotency_key and len(idempotency_key) > 100:
        raise ApiError('Idempotency-Key deve ter no máximo 100 caracteres.')
    fingerprint_payload = {
        'payment_method': payment_method,
        'discount_type': discount_type,
        'discount_value': str(discount_value),
        'interest_type': interest_type,
        'interest_value': str(interest_value),
        'installments': installments,
        'items': sorted((str(product_id), quantity) for product_id, quantity in item_quantities.items()),
        'amount_received': str(amount_received_input) if amount_received_input is not None else None,
    }
    request_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()

    with transaction.atomic():
        if idempotency_key:
            existing_sale = Sale.objects.prefetch_related('items').filter(idempotency_key=idempotency_key).first()
            if existing_sale:
                if existing_sale.request_fingerprint != request_fingerprint:
                    raise ApiError(
                        'Idempotency-Key já foi usado com dados diferentes.',
                        status=409,
                    )
                return JsonResponse({'data': serialize_sale(existing_sale)}, status=200)

        products_by_id = {
            product.id: product
            for product in Product.objects.select_for_update()
            .filter(id__in=item_quantities, is_active=True)
            .order_by('id')
        }
        missing_ids = set(item_quantities) - set(products_by_id)
        if missing_ids:
            raise ApiError(
                'Um ou mais produtos não foram encontrados ou estão inativos.',
                details={'product_ids': [str(product_id) for product_id in sorted(missing_ids)]},
                status=404,
            )

        subtotal = Decimal('0.00')
        line_items = []
        for product_id, quantity in item_quantities.items():
            product = products_by_id[product_id]
            if product.stock < quantity:
                raise ApiError(
                    f'Estoque insuficiente para "{product.name}".',
                    details={
                        'product_id': str(product.id),
                        'available_stock': product.stock,
                        'requested_quantity': quantity,
                    },
                    status=409,
                )
            line_total = (product.price * quantity).quantize(CENT, rounding=ROUND_HALF_UP)
            subtotal += line_total
            line_items.append((product, quantity, line_total))
        subtotal = subtotal.quantize(CENT, rounding=ROUND_HALF_UP)

        discount = (
            subtotal * discount_value / Decimal('100')
            if discount_type == 'percentage'
            else discount_value
        ).quantize(CENT, rounding=ROUND_HALF_UP)
        if discount > subtotal:
            raise ApiError('O desconto não pode ser maior que o subtotal.')
        amount_after_discount = subtotal - discount
        interest = (
            amount_after_discount * interest_value / Decimal('100')
            if interest_type == 'percentage'
            else interest_value
        ).quantize(CENT, rounding=ROUND_HALF_UP)
        total = (amount_after_discount + interest).quantize(CENT, rounding=ROUND_HALF_UP)
        amount_received = None
        change_given = Decimal('0.00')
        if payment_method == Sale.PaymentMethod.CASH:
            amount_received = amount_received_input if amount_received_input is not None else total
            if amount_received < total:
                raise ApiError(
                    'O valor recebido é menor que o total da venda.',
                    details={'amount_due': str(total - amount_received)},
                )
            change_given = (amount_received - total).quantize(CENT, rounding=ROUND_HALF_UP)
        if max(subtotal, discount, interest, total) > Decimal('99999999.99'):
            raise ApiError('O valor total da venda excede o limite permitido.')

        sale = Sale.objects.create(
            payment_method=payment_method,
            subtotal=subtotal,
            discount=discount,
            interest=interest,
            total=total,
            installments=installments,
            amount_received=amount_received,
            change_given=change_given,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint if idempotency_key else '',
        )
        for product, quantity, line_total in line_items:
            previous_stock = product.stock
            product.stock -= quantity
            product.save(update_fields=['stock', 'updated_at'])
            SaleItem.objects.create(
                sale=sale,
                product=product,
                product_name=product.name,
                sku=product.sku or '',
                unit_price=product.price,
                quantity=quantity,
                line_total=line_total,
            )
            StockMovement.objects.create(
                product=product,
                sale=sale,
                previous_stock=previous_stock,
                new_stock=product.stock,
                quantity_delta=-quantity,
                reason=StockMovement.Reason.SALE,
                note=f'Venda {sale.id}.',
            )

    sale = Sale.objects.prefetch_related('items').get(pk=sale.pk)
    return JsonResponse({'data': serialize_sale(sale)}, status=201)
