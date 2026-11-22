"""The image pipeline (attnd/images.py, {% img %}): right-sized Cloudinary copies with srcset, dimensions on every
<img>, self-hosting outside images (never Google Places photos), the image budget of the home page, QR caching."""
import io
import re
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.utils import timezone

from attnd import images
from events.models import Event, Location

CLOUD = 'https://res.cloudinary.com/demo/image/upload/v1/media/place.jpg'


def _jpeg(w=3000, h=2000):
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (w, h), (10, 120, 200)).save(out, 'JPEG')
    return out.getvalue()


class VariantTests(TestCase):
    def test_cloudinary_transforms(self):
        self.assertEqual(images.variant(CLOUD, 384, 216, crop=True),
                         'https://res.cloudinary.com/demo/image/upload/f_auto,q_auto,c_fill,g_auto,w_384,h_216/v1/media/place.jpg')
        self.assertEqual(images.variant(CLOUD, 800),
                         'https://res.cloudinary.com/demo/image/upload/f_auto,q_auto,c_limit,w_800/v1/media/place.jpg')
        done = 'https://res.cloudinary.com/demo/image/upload/w_100/v1/x.jpg'
        self.assertEqual(images.variant(done, 50), done)
        # Our media files on Cloudinary have no extension.
        self.assertEqual(images.variant('https://res.cloudinary.com/demo/image/upload/v1/media/cover-38-22fa_ln1erh', 96),
                         'https://res.cloudinary.com/demo/image/upload/f_auto,q_auto,c_limit,w_96/v1/media/cover-38-22fa_ln1erh')
        for url in ('https://res.cloudinary.com/demo/image/upload/v1/x.pdf', '/static/home/img/og.png',
                    'https://example.com/a.jpg'):
            self.assertEqual(images.variant(url, 50), url)

    def test_responsive_srcset(self):
        src, srcset = images.responsive(CLOUD, 48, 48, crop=True)
        self.assertIn('w_48,h_48', src)
        self.assertEqual(len(srcset.split(', ')), 3)
        self.assertIn('w_144,h_144', srcset)
        self.assertEqual(images.responsive('/static/x.svg', 48), ('/static/x.svg', ''))

    def test_the_tag(self):
        html = Template('{% load attnd_images %}{% img url 384 216 crop=True alt="Cover" priority=True %}'
                        ).render(Context({'url': CLOUD}))
        for part in ('width="384"', 'height="216"', 'srcset="', 'sizes="384px"', 'fetchpriority="high"',
                     'decoding="async"', 'alt="Cover"'):
            self.assertIn(part, html)
        self.assertNotIn('loading=', html)
        lazy = Template('{% load attnd_images %}{% img url 48 48 %}').render(Context({'url': CLOUD}))
        self.assertIn('loading="lazy"', lazy)
        self.assertEqual(Template('{% load attnd_images %}{% img "" 48 48 %}').render(Context()), '')


class TemplateRuleTests(TestCase):
    def test_every_img_has_dimensions_or_goes_through_the_helper(self):
        """Every <img> in our templates: {% img %} (right-sized, srcset, dimensions) or explicit width and height
        (static files, QR SVGs)."""
        root = Path(settings.BASE_DIR)
        bad = []
        for path in root.glob('*/templates/**/*.html'):
            if '.venv' in path.parts:
                continue
            for tag in re.findall(r'<img\b[^>]*>', path.read_text()):
                if not (re.search(r'\bwidth=', tag) and re.search(r'\bheight=', tag)):
                    bad.append(f'{path.relative_to(root)}: {tag[:90]}')
        self.assertEqual(bad, [])

    def test_content_images_use_the_helper(self):
        """Images that come from our storage or organizers (covers, location photos, flyers) never skip the helper."""
        root = Path(settings.BASE_DIR)
        for name in ('home/templates/home/_card.html', 'console/templates/components/location_row.html',
                     'console/templates/components/event_flyer.html', 'console/templates/components/_form_blocks.html'):
            text = (root / name).read_text()
            self.assertIn('{% img ', text, name)
            self.assertNotRegex(text, r'<img[^>]*src="\{\{ (c\.cover|d\.image_url|d\.flyer_url|r\.src) \}\}"')


@override_settings(SECURE_SSL_REDIRECT=False)
class BudgetTests(TestCase):
    def setUp(self):
        from django.core.cache import caches
        caches['local'].clear()
        from prospects.models import Prospect
        user = User.objects.create_user('o', 'o@example.com', 'pw')
        now = timezone.now()
        for i in range(6):
            e = Event.objects.create(name=f'Show {i}', event_code=f'SHOW{i}', user=user, status=Event.STATUS_PUBLISHED,
                                     showcase=True, timezone='UTC', start_time=now, end_time=now + timedelta(hours=5))
            Location.objects.create(event=e, name='Stage', image_url=CLOUD.replace('place', f'place{i}'))
        e = Event.objects.create(name='Near', event_code='NEAR1', user=user, status=Event.STATUS_PUBLISHED,
                                 testing_only=True, timezone='UTC', start_time=now + timedelta(days=2),
                                 end_time=now + timedelta(days=2, hours=5), latitude=32.7, longitude=-97.3)
        Location.objects.create(event=e, name='Stage',
                                image_url='https://lh3.googleusercontent.com/places/abc=s4800')  # a Places photo
        Prospect.objects.create(name='Near', event=e)

    def test_home_page_covers_are_right_sized(self):
        html = self.client.get('/home/', secure=True).content.decode()
        covers = re.findall(r'<div class="event-card__cover"><img ([^>]*)>', html)
        self.assertEqual(len(covers), 6)
        for tag in covers:
            src = re.search(r'src="([^"]+)"', tag).group(1)
            self.assertIn('w_384,h_216', src)
            widths = [int(w) for w in re.findall(r' (\d+)w', tag)]
            self.assertLessEqual(max(widths), 1152)
        self.assertEqual(html.count('fetchpriority="high"'), 1)
        self.assertNotIn('googleusercontent', html)  # never a Google Places photo


class SelfHostTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(name='E', event_code='SELF1', user=self.user, status=Event.STATUS_PUBLISHED,
                                          timezone='UTC', start_time=timezone.now(),
                                          logo_url='https://organizer.example/logo.png')

    def test_location_and_logo_are_copied_and_shipped(self):
        loc = Location.objects.create(event=self.event, name='Stage', image_url='https://organizer.example/huge.jpg')
        with patch('events.place_images._fetch', return_value=(_jpeg(), 'image/jpeg', '')):
            self.assertEqual(images.host_location(loc), 'hosted')
            self.assertEqual(images.host_event_logo(self.event), 'hosted')
        loc.refresh_from_db()
        self.assertTrue(loc.image)
        self.assertIsNone(loc.image_url)
        self.assertEqual(loc.image_source_url, 'https://organizer.example/huge.jpg')
        from PIL import Image
        with loc.image.open('rb') as fh:
            self.assertLessEqual(max(Image.open(fh).size), images.MAX_SIDE)
        self.event.refresh_from_db()
        self.assertTrue(images.event_logo_url(self.event).startswith(settings.MEDIA_URL))
        from events.content import event_v2
        self.assertNotIn('organizer.example', event_v2(self.event)['logo_url'])
        # A new logo_url is shown as given until it is copied again.
        self.event.logo_url = 'https://organizer.example/new.png'
        self.assertEqual(images.event_logo_url(self.event), 'https://organizer.example/new.png')

    def test_google_places_photos_are_dropped_not_copied(self):
        loc = Location.objects.create(event=self.event, name='Stage',
                                      image_url='https://lh3.googleusercontent.com/places/abc=s4800')
        with patch('events.place_images._fetch') as fetch, patch('events.place_images.schedule'):
            self.assertEqual(images.host_location(loc), 'dropped')
        fetch.assert_not_called()
        loc.refresh_from_db()
        self.assertIsNone(loc.image_url)

    def test_ours_is_left_alone_and_the_command(self):
        from io import StringIO

        from django.core.management import call_command
        Location.objects.create(event=self.event, name='A', image_url=CLOUD)
        Location.objects.create(event=self.event, name='B', image_url='https://organizer.example/b.jpg')
        out = StringIO()
        call_command('self_host_images', '--dry-run', stdout=out)
        self.assertIn('would copy', out.getvalue())
        self.assertNotIn('place.jpg', out.getvalue())
        with patch('events.place_images._fetch', return_value=(_jpeg(800, 600), 'image/jpeg', '')):
            call_command('self_host_images', stdout=out)
        self.assertFalse(Location.objects.filter(image_url__startswith='https://organizer.example').exists())


class QrCacheTests(TestCase):
    def test_join_qr_is_cached_a_day(self):
        user = User.objects.create_user('o', 'o@example.com', 'pw')
        Event.objects.create(name='E', event_code='QRC1', user=user, status=Event.STATUS_PUBLISHED, timezone='UTC',
                             start_time=timezone.now())
        resp = self.client.get('/join/QRC1/qr.svg', secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('max-age=86400', resp['Cache-Control'])
        self.assertIn('stale-while-revalidate', resp['Cache-Control'])
