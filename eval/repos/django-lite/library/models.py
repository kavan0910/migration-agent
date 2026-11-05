from django.db import models


class Workshop(models.Model):
    title = models.CharField(max_length=120)
    seats = models.PositiveIntegerField(default=10)
    starts_at = models.DateTimeField()

    def seats_left(self):
        return self.seats - self.registrations.count()


class Registration(models.Model):
    workshop = models.ForeignKey(Workshop, related_name="registrations", on_delete=models.CASCADE)
    email = models.EmailField()
    waitlisted = models.BooleanField(default=False)
