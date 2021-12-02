from math import sqrt

from rest_framework import serializers

from events.models import Location, Event, Notification


class LocationSerializer(serializers.ModelSerializer):
    # this will override the image in the model with an image that has the full url
    image = serializers.SerializerMethodField()

    def get_image(self, location):
        request = self.context.get('request')
        return request.build_absolute_uri(location.image.url)

    class Meta:
        model = Location
        fields = '__all__'


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = '__all__'


class EntireEventSerializer(serializers.ModelSerializer):
    locations = LocationSerializer(many=True, read_only=True)
    notifications = NotificationSerializer(many=True, read_only=True)
    flyer = serializers.SerializerMethodField()

    def get_flyer(self, event):
        request = self.context.get('request')
        return request.build_absolute_uri(event.flyer.url)

    class Meta:
        model = Event
        fields = '__all__'


class NearbyEventSerializer(serializers.ModelSerializer):
    distance = serializers.SerializerMethodField()

    def get_distance(self, instance):
        latitude = self.context.get('latitude')
        longitude = self.context.get('longitude')
        latitude_distance_from_user = latitude - instance.latitude
        longitude_distance_from_user = longitude - instance.longitude
        distance = sqrt((latitude_distance_from_user * latitude_distance_from_user) + (longitude_distance_from_user * longitude_distance_from_user))
        distance_in_miles = round(distance * 69, 1)
        return distance_in_miles

    class Meta:
        model = Event
        fields = '__all__'
        asdf = ''


class EventByNameOrCodeSerializer(serializers.ModelSerializer):

    class Meta:
        model = Event
        fields = '__all__'
