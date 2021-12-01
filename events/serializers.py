from rest_framework import serializers

from events.models import Location


class LocationSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    def get_image(self, location):
        request = self.context.get('request')
        return request.build_absolute_uri(location.image.url)

    class Meta:
        model = Location
        fields = '__all__'
