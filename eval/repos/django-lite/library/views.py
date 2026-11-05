import json

from django.http import JsonResponse

from . import services
from .models import Workshop


def workshop_list(request):
    data = [{"id": w.id, "title": w.title} for w in Workshop.objects.all()]
    return JsonResponse(data, safe=False)


def register_view(request, workshop_id):
    body = json.loads(request.body)
    workshop = Workshop.objects.get(pk=workshop_id)
    try:
        reg = services.register(workshop, body["email"])
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"id": reg.id, "waitlisted": reg.waitlisted}, status=201)
