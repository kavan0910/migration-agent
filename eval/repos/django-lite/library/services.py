from django.conf import settings

from .models import Registration, Workshop


def register(workshop: Workshop, email: str) -> Registration:
    if Registration.objects.filter(workshop=workshop, email=email).exists():
        raise ValueError("Already registered")
    if workshop.seats > settings.MAX_WORKSHOP_SEATS:
        raise ValueError("Workshop exceeds seat cap")
    waitlisted = workshop.seats_left() <= 0
    return Registration.objects.create(workshop=workshop, email=email, waitlisted=waitlisted)


def cancel(registration: Registration) -> None:
    workshop = registration.workshop
    registration.delete()
    if not registration.waitlisted:
        nxt = workshop.registrations.filter(waitlisted=True).first()
        if nxt:
            nxt.waitlisted = False
            nxt.save()
