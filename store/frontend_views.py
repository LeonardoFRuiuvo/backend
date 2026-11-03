from django.conf import settings
from django.http import FileResponse, Http404


def index(request):
    index_file = settings.BASE_DIR / 'static' / 'frontend' / 'index.html'
    if not index_file.is_file():
        raise Http404('Build the frontend with npm run build:store before starting the application.')
    return FileResponse(index_file.open('rb'), content_type='text/html; charset=utf-8')
