import secrets
import string
import uuid

from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
from django.utils import timezone


def get_file_path(instance, filename):
    file, ext = filename.split('.')
    simple_uuid = str(uuid.uuid4())[:5]
    return f'{file}_{simple_uuid}.{ext}'


def generate_event_code(length: int = 8) -> str:
    """Return a random uppercase alphanumeric code that doesn't already exist."""
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = ''.join(secrets.choice(alphabet) for _ in range(length))
        if not Event.objects.filter(event_code=code).exists():
            return code
    raise RuntimeError('Could not generate a unique event code after 20 attempts.')


class Event(models.Model):
    name = models.CharField(max_length=256)
    event_code = models.CharField(max_length=16, unique=True)
    description = models.CharField(max_length=2048)
    latitude = models.DecimalField(max_digits=24, decimal_places=20)
    longitude = models.DecimalField(max_digits=24, decimal_places=20)
    start_time = models.DateTimeField()
    paid = models.BooleanField(default=False)
    public = models.BooleanField(default=False)
    flyer = models.FileField(upload_to=get_file_path, blank=True)
    address = models.CharField(max_length=512)
    order_id = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'name', 'event_code', 'latitude', 'longitude'])
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
    image = models.ImageField(upload_to=get_file_path, blank=True, null=True)
    image_url = models.URLField(max_length=1024, blank=True, null=True)
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


class ScheduledItem(models.Model):
    name = models.CharField(max_length=256)
    description = models.CharField(max_length=1024)
    start_time = models.DateTimeField()
    location = models.ForeignKey(Location, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        indexes = [
            models.Index(fields=['event', 'start_time']),
            models.Index(fields=['location', 'start_time']),
        ]
        ordering = ['start_time']

    def __str__(self):
        return f'{self.name} @ {self.location.name}'


class Notification(models.Model):
    title = models.CharField(max_length=60)
    body = models.CharField(max_length=256)
    send_time = models.DateTimeField()
    sent = models.BooleanField(default=False)
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
