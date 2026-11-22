"""Stage D: place images found automatically, stored in our own media storage, with credit (API-022)."""

import io
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from attnd.testing import fake_claude
from events import place_images
from events.content import location_v2
from events.models import Event, Location, PlaceImageAttempt

GOOD = {'usable': True, 'confidence': 0.92, 'kind': 'photo', 'reason': 'The stage itself.'}
BAD = {'usable': False, 'confidence': 0.9, 'kind': 'logo', 'reason': 'A logo.'}


def jpeg(width=1200, height=800, colour=(120, 60, 200), exif=True):
    image = Image.new('RGB', (width, height), colour)
    out = io.BytesIO()
    kwargs = {}
    if exif:
        data = Image.Exif()
        data[0x8825] = {2: (41.0, 52.0, 0.0)}  # GPS info
        data[0x010F] = 'CameraMaker'
        kwargs['exif'] = data.tobytes()
    image.save(out, 'JPEG', **kwargs)
    return out.getvalue()


PAGE = b"""<html><head><meta property="og:image" content="https://fest.example/og.jpg"></head><body>
<img src="/img/site-logo.png" alt="Main Stage logo">
<figure><img src="/img/main-stage-night.jpg"><figcaption>The Main Stage at night</figcaption></figure>
<img src="/img/food.jpg" alt="Tacos">
</body></html>"""


def fake_fetch(files):
    def fetch(url, limit):
        if url not in files:
            from studio.importer import SiteImportError
            raise SiteImportError(f'Could not load {url}')
        data = files[url]
        kind = 'text/html' if data.startswith(b'<html') else 'image/jpeg'
        return data, kind, url
    return fetch


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), PLACE_IMAGES_DAILY_CAP_USD=1, GOOGLE_PLACES_API_KEY='')
class PlaceImageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')
        self.event = Event.objects.create(name='Fest', event_code='FEST', user=self.owner, start_time=timezone.now(),
                                          source_urls=['https://fest.example/'])
        self.stage = Location.objects.create(event=self.event, name='Main Stage', category='Stage')
        self.files = {'https://fest.example/': PAGE, 'https://fest.example/img/main-stage-night.jpg': jpeg()}

    def run_find(self, location, *answers, files=None):
        with mock.patch('studio.importer.fetch', side_effect=fake_fetch(files or self.files)), \
                mock.patch('events.place_images.from_wikimedia', return_value=[]), fake_claude(*answers) as claude:
            result = place_images.find_for(location)
        location.refresh_from_db()
        return result, claude

    def test_event_page_photo_named_like_the_place_is_stored_without_exif(self):
        result, claude = self.run_find(self.stage, GOOD)
        self.assertEqual(result, 'found')
        self.assertTrue(self.stage.image_auto)
        self.assertEqual(self.stage.image_source_url, 'https://fest.example/')
        self.assertEqual(self.stage.image_credit, 'Photo: fest.example')
        stored = Image.open(self.stage.image.open('rb'))
        self.assertEqual(stored.format, 'JPEG')
        self.assertFalse(dict(stored.getexif()))  # EXIF and GPS gone
        self.assertEqual(claude.last['messages'][0]['content'][0]['type'], 'image')
        attempt = PlaceImageAttempt.objects.get()
        self.assertEqual((attempt.result, attempt.source), ('found', 'event_page'))
        self.assertEqual(location_v2(self.stage)['image_credit'], 'Photo: fest.example')  # API-022

    def test_logos_small_images_and_rejected_photos_are_not_used(self):
        self.files['https://fest.example/img/main-stage-night.jpg'] = jpeg(400, 300)
        result, _ = self.run_find(self.stage, GOOD)
        self.assertEqual(result, 'none')
        self.assertIn('400 px wide', PlaceImageAttempt.objects.get().detail)
        self.files['https://fest.example/img/main-stage-night.jpg'] = jpeg()
        PlaceImageAttempt.objects.all().delete()  # a second automatic look only happens once the first is forgotten
        result, _ = self.run_find(self.stage, BAD)
        self.assertEqual(result, 'none')
        self.assertFalse(self.stage.image)
        self.assertIsNone(location_v2(self.stage)['image_credit'])

    def test_large_images_are_shrunk_to_2000px(self):
        self.files['https://fest.example/img/main-stage-night.jpg'] = jpeg(4000, 3000, exif=False)
        self.run_find(self.stage, GOOD)
        self.assertEqual(max(Image.open(self.stage.image.open('rb')).size), 2000)

    def test_organizer_images_locked_and_removed_places_are_never_touched(self):
        own = Location.objects.create(event=self.event, name='Main Stage West', image_url='https://own.example/x.jpg')
        locked = Location.objects.create(event=self.event, name='Main Stage East', locked_fields=['image'])
        removed = Location.objects.create(event=self.event, name='Main Stage North', image_never_refetch=True)
        uploaded = Location.objects.create(event=self.event, name='Main Stage South')
        uploaded.image.save('own.jpg', ContentFile(jpeg()))
        for loc in (own, locked, removed, uploaded):
            self.assertFalse(place_images.wanted(loc), loc.name)
            self.assertEqual(self.run_find(loc, GOOD)[0], 'skipped')
        google = Location.objects.create(event=self.event, name='Old',
                                         image_url='https://lh3.googleusercontent.com/places/abc')
        self.assertTrue(place_images.wanted(google))

    def test_the_same_picture_is_not_used_for_two_places(self):
        self.run_find(self.stage, GOOD)
        twin = Location.objects.create(event=self.event, name='Main Stage Night Market')
        files = dict(self.files)
        result, _ = self.run_find(twin, GOOD, files=files)
        self.assertEqual(result, 'none')
        self.assertIn('same picture', PlaceImageAttempt.objects.filter(location=twin).get().detail)

    def test_the_daily_cap_stops_looking(self):
        PlaceImageAttempt.objects.create(event=self.event, result='found', cost_usd=5)
        result, claude = self.run_find(self.stage, GOOD)
        self.assertEqual(result, 'skipped')
        self.assertEqual(claude.calls, [])
        PlaceImageAttempt.objects.update(created_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.run_find(self.stage, GOOD)[0], 'found')

    def test_wikimedia_candidates_carry_author_and_licence(self):
        info = {'query': {'pages': [{'title': 'File:Stage.jpg', 'imageinfo': [{
            'width': 2400, 'thumburl': 'https://upload.wikimedia.org/stage.jpg',
            'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Stage.jpg',
            'extmetadata': {'LicenseShortName': {'value': 'CC BY-SA 4.0'}, 'Artist': {'value': '<a>Jane Doe</a>'}}}]}]}}
        search = {'query': {'search': [{'title': 'File:Stage.jpg'}]}}
        with mock.patch('events.place_images._commons', side_effect=[search, info]):
            found = place_images.from_wikimedia(self.stage)
        self.assertEqual(found[0].credit, 'Jane Doe · CC BY-SA 4.0 · Wikimedia Commons')
        self.assertEqual(found[0].license, 'CC BY-SA 4.0')

    def test_console_shows_found_image_and_remove_never_looks_again(self):
        self.run_find(self.stage, GOOD)
        self.client.force_login(self.owner)
        page = self.client.get(reverse('console:location_edit', args=[self.event.pk, self.stage.pk]), secure=True)
        self.assertContains(page, 'Found automatically')
        self.assertContains(page, 'Photo: fest.example')
        self.assertContains(page, 'Remove photo')
        response = self.client.post(reverse('console:location_action', args=[self.event.pk, self.stage.pk]),
                                    {'action': 'remove_image'}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.stage.refresh_from_db()
        self.assertFalse(self.stage.image)
        self.assertTrue(self.stage.image_never_refetch)
        self.assertFalse(place_images.wanted(self.stage))
        self.assertTrue(response.has_header('X-Attnd-Undo'))  # undoable like every console change

    def test_the_organizers_upload_replaces_the_credit(self):
        self.run_find(self.stage, GOOD)
        from console.forms.event_forms import LocationForm
        upload = ContentFile(jpeg(), name='mine.jpg')
        form = LocationForm({'name': 'Main Stage'}, {'image': upload}, instance=self.stage, categories=[])
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.stage.refresh_from_db()
        self.assertFalse(self.stage.image_auto)
        self.assertEqual(self.stage.image_credit, '')
        self.assertFalse(self.stage.image_never_refetch)

    def test_new_locations_are_queued_after_commit(self):
        with override_settings(PLACE_IMAGES_ON_CREATE=True), \
                mock.patch('events.place_images.schedule') as schedule:
            loc = Location.objects.create(event=self.event, name='Taco Row')
            Location.objects.create(event=self.event, name='Has one', image_url='https://own.example/a.jpg')
        schedule.assert_called_once_with(loc.pk)
