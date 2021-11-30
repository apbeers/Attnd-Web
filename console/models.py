from django.contrib.auth.models import User
from django.db import models

from events.models import Event


class Order(models.Model):
    order_date = models.DateTimeField(auto_now_add=True)
    order_id = models.CharField(max_length=256)
    event = models.OneToOneField(Event, on_delete=models.DO_NOTHING)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    refunded = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.event.name} - {self.user.username}'
