from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse


class Event(models.Model):
    name = models.CharField(max_length=256, unique=True)
    event_code = models.CharField(max_length=16, unique=True)
    description = models.CharField(max_length=2048)
    latitude = models.DecimalField(max_digits=24, decimal_places=20)
    longitude = models.DecimalField(max_digits=24, decimal_places=20)
    start_time = models.DateTimeField()
    paid = models.BooleanField(default=False)
    flyer = models.FileField()
    address = models.CharField(max_length=512)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        indexes = [
            models.Index(fields=['user'])
        ]

    def get_absolute_url(self):
        return reverse('events:get_single_event', args=[self.id])

    def __str__(self):
        return self.name


class Location(models.Model):
    name = models.CharField(max_length=256)
    snippet = models.CharField(max_length=200)
    description = models.CharField(max_length=1024)
    website = models.URLField(null=True, blank=True)
    image = models.ImageField()
    latitude = models.DecimalField(max_digits=24, decimal_places=20)
    longitude = models.DecimalField(max_digits=24, decimal_places=20)
    address = models.CharField(max_length=512)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)

    class Meta:
        indexes = [
            models.Index(fields=['event', 'user'])
        ]

    def get_absolute_url(self):
        return reverse('events:get_single_location', args=[self.event.id, self.id])

    def __str__(self):
        return self.name


class Notification(models.Model):
    title = models.CharField(max_length=60)
    body = models.CharField(max_length=256)
    send_time = models.DateTimeField()
    sent = models.BooleanField(default=False, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)

    class Meta:
        indexes = [
            models.Index(fields=['event', 'user'])
        ]

    def get_absolute_url(self):
        return reverse('events:get_single_notification', args=[self.event.id, self.id])

    def __str__(self):
        return self.title


class Device(models.Model):
    id = models.CharField(max_length=128, primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, null=True, blank=True)
    subscribed = models.BooleanField(default=True)
    platform = models.CharField(max_length=12)

    class Meta:
        indexes = [
            models.Index(fields=['event'])
        ]

    def __str__(self):
        return f'{self.event} - {self.id} - {self.subscribed}'
