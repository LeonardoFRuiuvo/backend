import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import JsonResponse


CENT = Decimal('0.01')
MAX_MONEY = Decimal('99999999.99')


class ApiError(Exception):
    def __init__(self, message, status=400, details=None):
        self.message = message
        self.status = status
        self.details = details or {}


def api_endpoint(*methods):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                response = JsonResponse({'error': 'Método não permitido.'}, status=405)
                response['Allow'] = ', '.join(methods)
                return response
            try:
                return view(request, *args, **kwargs)
            except ApiError as error:
                payload = {'error': error.message}
                if error.details:
                    payload['details'] = error.details
                return JsonResponse(payload, status=error.status)
            except ValidationError as error:
                return JsonResponse(
                    {'error': 'Dados inválidos.', 'details': error.message_dict if hasattr(error, 'message_dict') else error.messages},
                    status=400,
                )
            except IntegrityError:
                return JsonResponse(
                    {'error': 'Conflito de dados. Verifique o SKU e tente novamente.'},
                    status=409,
                )
        return wrapped
    return decorate


def json_body(request):
    if request.content_type.startswith('multipart/form-data'):
        return request.POST.dict()
    if not request.body:
        return {}
    if request.content_type != 'application/json':
        raise ApiError('Envie application/json ou multipart/form-data.', 415)
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError('O corpo da requisição não contém JSON válido.')
    if not isinstance(body, dict):
        raise ApiError('O corpo da requisição deve ser um objeto JSON.')
    return body


def required_text(data, field, *, maximum=180, optional=False):
    value = data.get(field)
    if optional and value in (None, ''):
        return ''
    if not isinstance(value, str) or not value.strip():
        raise ApiError(f'O campo "{field}" é obrigatório.', details={field: 'Informe um texto.'})
    value = value.strip()
    if len(value) > maximum:
        raise ApiError(
            f'O campo "{field}" excede {maximum} caracteres.',
            details={field: f'Máximo de {maximum} caracteres.'},
        )
    return value


def decimal_value(value, field, *, minimum=Decimal('0'), maximum=MAX_MONEY):
    if isinstance(value, bool) or value is None or value == '':
        raise ApiError(f'O campo "{field}" deve ser numérico.', details={field: 'Informe um número.'})
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ApiError(f'O campo "{field}" deve ser numérico.', details={field: 'Informe um número válido.'})
    if not amount.is_finite() or amount < minimum or amount > maximum:
        raise ApiError(
            f'O valor de "{field}" está fora do limite permitido.',
            details={field: f'Informe um valor entre {minimum} e {maximum}.'},
        )
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def integer_value(value, field, *, minimum=0, maximum=2_147_483_647):
    if isinstance(value, bool):
        raise ApiError(f'O campo "{field}" deve ser um número inteiro.')
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ApiError(f'O campo "{field}" deve ser um número inteiro.')
    if str(value).strip() != str(number) or not minimum <= number <= maximum:
        raise ApiError(f'O campo "{field}" deve ser um inteiro entre {minimum} e {maximum}.')
    return number


def category_names(value):
    if value in (None, ''):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise ApiError('O campo "categories" deve ser uma lista JSON de nomes.')
    if not isinstance(value, list):
        raise ApiError('O campo "categories" deve ser uma lista de nomes.')

    normalized = []
    seen = set()
    for category in value:
        if not isinstance(category, str) or not category.strip():
            raise ApiError('Cada categoria deve ser um texto não vazio.')
        name = category.strip()
        if len(name) > 80:
            raise ApiError('Cada categoria pode ter no máximo 80 caracteres.')
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(name)
    return normalized


def parse_uuid(value, field):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ApiError(f'O campo "{field}" deve conter um identificador válido.')


def page_parameters(request):
    try:
        page = int(request.GET.get('page', '1'))
        page_size = int(request.GET.get('page_size', '20'))
    except ValueError:
        raise ApiError('page e page_size devem ser números inteiros.')
    if page < 1 or page_size < 1 or page_size > 100:
        raise ApiError('Use page >= 1 e page_size entre 1 e 100.')
    return page, page_size


def paginated_response(request, queryset, serializer):
    page, page_size = page_parameters(request)
    total = queryset.count()
    start = (page - 1) * page_size
    objects = queryset[start:start + page_size]
    return JsonResponse({
        'data': [serializer(item, request) for item in objects],
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total': total,
            'pages': (total + page_size - 1) // page_size,
        },
    })


def serialize_category(category):
    return {'id': category.id, 'name': category.name}


def serialize_product(product, request):
    return {
        'id': str(product.id),
        'name': product.name,
        'sku': product.sku,
        'categories': [category.name for category in product.categories.all()],
        'stock': product.stock,
        'price': str(product.price),
        'image_url': request.build_absolute_uri(product.image.url) if product.image else None,
        'is_active': product.is_active,
        'created_at': product.created_at.isoformat(),
        'updated_at': product.updated_at.isoformat(),
    }


def serialize_movement(movement, _request=None):
    return {
        'id': str(movement.id),
        'product_id': str(movement.product_id),
        'product_name': movement.product.name,
        'previous_stock': movement.previous_stock,
        'new_stock': movement.new_stock,
        'quantity_delta': movement.quantity_delta,
        'reason': movement.reason,
        'note': movement.note,
        'sale_id': str(movement.sale_id) if movement.sale_id else None,
        'created_at': movement.created_at.isoformat(),
    }


def serialize_sale(sale, _request=None):
    return {
        'id': str(sale.id),
        'payment_method': sale.payment_method,
        'subtotal': str(sale.subtotal),
        'discount': str(sale.discount),
        'interest': str(sale.interest),
        'total': str(sale.total),
        'installments': sale.installments,
        'amount_received': str(sale.amount_received) if sale.amount_received is not None else None,
        'change_given': str(sale.change_given),
        'items': [
            {
                'product_id': str(item.product_id),
                'product_name': item.product_name,
                'sku': item.sku,
                'unit_price': str(item.unit_price),
                'quantity': item.quantity,
                'line_total': str(item.line_total),
            }
            for item in sale.items.all()
        ],
        'created_at': sale.created_at.isoformat(),
    }
