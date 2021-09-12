from django.contrib.auth.models import User
from django.db import models


class Event(models.Model):
    name = models.CharField(max_length=256)
    event_code = models.CharField(max_length=16, unique=True)
    description = models.CharField(max_length=2048)
    latitude = models.DecimalField(max_digits=8, decimal_places=6)
    longitude = models.DecimalField(max_digits=8, decimal_places=6)
    start_date = models.DateTimeField()
    paid = models.BooleanField(default=False)
    flyer = models.FileField()
    public = models.BooleanField(default=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        indexes = [
            models.Index(fields=['user'])
        ]

    def __str__(self):
        return self.name


class Location(models.Model):
    name = models.CharField(max_length=256)
    snippet = models.CharField(max_length=200)
    description = models.CharField(max_length=1024)
    website = models.URLField(null=True, blank=True)
    image = models.ImageField()
    latitude = models.DecimalField(max_digits=8, decimal_places=6)
    longitude = models.DecimalField(max_digits=8, decimal_places=6)
    user = models.ForeignKey(User, on_delete=models.CASCADE, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, editable=False)

    class Meta:
        indexes = [
            models.Index(fields=['event', 'user'])
        ]

    def __str__(self):
        return self.name


class Notification(models.Model):
    title = models.CharField(max_length=60)
    body = models.CharField(max_length=256)
    send_time = models.DateTimeField()
    sent = models.BooleanField(default=False, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, editable=False)

    class Meta:
        indexes = [
            models.Index(fields=['event', 'user'])
        ]

    def __str__(self):
        return self.title
