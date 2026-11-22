from zoneinfo import available_timezones

from decimal import Decimal

from django import forms

from events.display import (LABELS, LOCATIONS_STYLES, PRESETS, SCHEDULE_STYLES, SECONDARY_PANES, TABS)
from django.utils import timezone

from console.forms.assistant_context import AssistantContextMixin
from events import context as assistant_context
from events.models import Event, EventMember, Location, Notification, ScheduledItem, is_send_slot, normalize_tags

DT_WIDGET = forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')
DT_FORMATS = ['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M']
TIMEZONE_CHOICES = [(tz, tz.replace('_', ' ')) for tz in sorted(available_timezones()) if '/' in tz or tz == 'UTC']


class CoordinateField(forms.DecimalField):
    """Latitude/longitude compared at 7 decimal places, so re-saving a form never 'changes' them."""

    def __init__(self, **kwargs):
        kwargs.pop('max_digits', None)
        kwargs.pop('decimal_places', None)
        super().__init__(**kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        return round(value, 7) if value is not None else None

    def has_changed(self, initial, data):
        try:
            data = self.to_python(data)
        except forms.ValidationError:
            return True
        initial = round(Decimal(str(initial)), 7) if initial not in (None, '') else None
        return initial != data


class StyledFormMixin:
    """Shared set-up for console forms. Controls are styled by element inside `.field` (console.css), so no
    widget needs a CSS class here."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Show coordinates at a sensible precision instead of 20 decimal places
        instance = getattr(self, 'instance', None)
        for f in ('latitude', 'longitude'):
            value = getattr(instance, f, None) if instance is not None else None
            if f in self.fields and value is not None and not self.is_bound:
                self.initial[f] = round(float(value), 6)


class AIImportForm(StyledFormMixin, forms.Form):
    urls = forms.CharField(
        label='Event website(s)', required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'https://yourevent.com\nhttps://yourevent.com/schedule'}),
        help_text='One per line. We read up to 15 pages per site.')
    upload = forms.FileField(
        label='Flyer, schedule or map (PDF or image)', required=False,
        help_text='Optional — handy when the schedule only exists as a PDF or photo.')
    text = forms.CharField(
        label='Or paste details', required=False,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Paste an email, a program, a list of vendors…'}))
    instructions = forms.CharField(
        label='Notes for the AI', required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'e.g. Ignore the 2025 schedule. Group vendors by aisle.'}))

    MAX_UPLOAD = 15 * 1024 * 1024
    ALLOWED_TYPES = ('application/pdf', 'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/heic')

    def clean_urls(self):
        urls = [u.strip() for u in self.cleaned_data['urls'].splitlines() if u.strip()]
        for u in urls:
            if not u.startswith(('http://', 'https://')):
                raise forms.ValidationError(f'"{u}" must start with http:// or https://')
        return urls[:5]

    def clean_upload(self):
        f = self.cleaned_data.get('upload')
        if f:
            if f.size > self.MAX_UPLOAD:
                raise forms.ValidationError('Files must be under 15 MB.')
            if (f.content_type or '') not in self.ALLOWED_TYPES:
                raise forms.ValidationError('Upload a PDF or an image (PNG, JPEG, WebP).')
        return f

    def clean(self):
        data = super().clean()
        if not (data.get('urls') or data.get('upload') or data.get('text', '').strip()):
            raise forms.ValidationError('Add a website, a file, or some text for the AI to read.')
        return data


class BlankEventForm(StyledFormMixin, forms.ModelForm):
    start_time = forms.DateTimeField(widget=DT_WIDGET, input_formats=DT_FORMATS)
    timezone = forms.ChoiceField(choices=TIMEZONE_CHOICES, initial='America/Chicago')

    class Meta:
        model = Event
        fields = ['name', 'start_time', 'timezone', 'address']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:  # console.js starts it on the browser's zone
            self.fields['timezone'].widget.attrs['data-browser-tz'] = '1'


class EventForm(AssistantContextMixin, StyledFormMixin, forms.ModelForm):
    context_kind = assistant_context.EVENT

    start_time = forms.DateTimeField(widget=DT_WIDGET, input_formats=DT_FORMATS)
    end_time = forms.DateTimeField(widget=DT_WIDGET, input_formats=DT_FORMATS, required=False)
    timezone = forms.ChoiceField(choices=TIMEZONE_CHOICES)

    class Meta:
        field_classes = {'latitude': CoordinateField, 'longitude': CoordinateField}
        model = Event
        fields = ['name', 'description', 'start_time', 'end_time', 'timezone', 'address',
                  'latitude', 'longitude', 'flyer', 'accent_color', 'logo_url']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'accent_color': forms.TextInput(attrs={'type': 'color'}),
            'latitude': forms.NumberInput(attrs={'step': 'any'}),
            'longitude': forms.NumberInput(attrs={'step': 'any'}),
        }
        help_texts = {
            'latitude': 'Leave blank to look it up from the address.',
            'accent_color': 'Tints buttons and highlights in the app.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_context()

    def clean_end_time(self):
        end = self.cleaned_data.get('end_time')
        start = self.cleaned_data.get('start_time')
        if end and start and end < start:
            raise forms.ValidationError('The end must be after the start.')
        return end

    def save(self, commit=True):
        self._save_context()
        return super().save(commit)


class EventContextForm(AssistantContextMixin, StyledFormMixin, forms.ModelForm):
    """Only the event's Details for the assistant (the canvas's card): saves nothing else."""
    context_kind = assistant_context.EVENT

    class Meta:
        model = Event
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_context()

    def save(self, commit=True):
        self._save_context()
        if commit:
            self.instance.save(update_fields=['context'])
        return self.instance


class EventFlyerForm(StyledFormMixin, forms.ModelForm):
    """Only the event's flyer (image or PDF): saves nothing else."""

    class Meta:
        model = Event
        fields = ['flyer']


class SourcesForm(StyledFormMixin, forms.ModelForm):
    source_urls_text = forms.CharField(
        label='Source websites', required=False, widget=forms.Textarea(attrs={'rows': 2}),
        help_text='Your event pages. One per line. The pages the AI reads from them are watched too.')

    class Meta:
        model = Event
        fields = ['refresh_schedule', 'update_mode', 'source_text']
        labels = {'source_text': 'Extra source text', 'refresh_schedule': 'Keep in sync with your website',
                  'update_mode': 'When the website changes'}
        help_texts = {'update_mode': 'Even when applying automatically, removals and anything someone edited by hand '
                                     'wait for review.'}
        widgets = {'source_text': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['source_urls_text'].initial = '\n'.join(self.instance.source_urls or [])

    def clean_source_urls_text(self):
        urls = [u.strip() for u in self.cleaned_data['source_urls_text'].splitlines() if u.strip()]
        for u in urls:
            if not u.startswith(('http://', 'https://')):
                raise forms.ValidationError(f'"{u}" must start with http:// or https://')
        return urls[:5]

    def save(self, commit=True):
        self.instance.source_urls = self.cleaned_data['source_urls_text']
        return super().save(commit)


class RefreshForm(StyledFormMixin, forms.Form):
    upload = AIImportForm.base_fields['upload']
    text = forms.CharField(label='Paste new details (optional)', required=False,
                           widget=forms.Textarea(attrs={'rows': 3}))
    instructions = AIImportForm.base_fields['instructions']

    clean_upload = AIImportForm.clean_upload
    MAX_UPLOAD = AIImportForm.MAX_UPLOAD
    ALLOWED_TYPES = AIImportForm.ALLOWED_TYPES


class DuplicateEventForm(StyledFormMixin, forms.Form):
    """Stage I12: a copy of the event as a new draft, moved to a new first day (clock times kept)."""
    name = forms.CharField(max_length=256, label='Name of the copy')
    start_date = forms.DateField(label='New first day', widget=forms.DateInput(attrs={'type': 'date'}),
                                 help_text='Every schedule item moves by the same number of days; times stay the same.')
    keep_team = forms.BooleanField(label='Keep the team', required=False, initial=True)


class PasteImportForm(StyledFormMixin, forms.Form):
    """Stage I3: pasted text or a dropped file (flyer image, PDF, CSV / XLSX schedule, an email) read into an existing
    event as AI suggestions to review. Only that source: no website crawl, no removals."""
    XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    ALLOWED_TYPES = AIImportForm.ALLOWED_TYPES + ('text/plain', 'text/csv', XLSX)
    MAX_UPLOAD = AIImportForm.MAX_UPLOAD
    upload = forms.FileField(label='File', required=False,
                             help_text='A flyer (image or PDF), a schedule (CSV or Excel) or a text file.')
    text = forms.CharField(label='Pasted text', required=False, widget=forms.Textarea(attrs={'rows': 4}),
                           help_text='An email, a lineup, a menu… anything with details of the event.')
    instructions = AIImportForm.base_fields['instructions']

    def clean_upload(self):
        f = self.cleaned_data.get('upload')
        if f:
            if f.size > self.MAX_UPLOAD:
                raise forms.ValidationError('Files must be under 15 MB.')
            head = f.read(8)
            f.seek(0)
            kind = f.content_type or ''
            ok = (kind in AIImportForm.ALLOWED_TYPES or kind in ('text/plain', 'text/csv')
                  or (kind == self.XLSX and head.startswith(b'PK\x03\x04')))
            if not ok:
                raise forms.ValidationError('Upload a PDF, an image, a CSV or Excel (.xlsx) file or a text file.')
        return f

    def clean(self):
        data = super().clean()
        if not (data.get('upload') or (data.get('text') or '').strip()):
            raise forms.ValidationError('Paste some text or add a file.')
        return data


TAGS_HELP = ('Comma separated, lower case, e.g. live music, bbq, kid-friendly. Attendees can filter and search by '
             'them. Reuse the same words across the event.')


class TagsFieldMixin:
    """A comma-separated ``tags_text`` field for the model's ``tags`` list (API-010). Saving normalises the
    tags and, when they changed, reports ``tags`` in ``changed_data`` so the view locks the field."""

    def _init_tags(self):
        self.fields['tags_text'].initial = ', '.join(normalize_tags(self.instance.tags))

    def clean_tags_text(self):
        return normalize_tags(self.cleaned_data.get('tags_text', ''))

    def _save_tags(self):
        tags = self.cleaned_data.get('tags_text') or []
        if tags != normalize_tags(self.instance.tags):
            if self.instance.pk and 'tags' not in self.changed_data:
                self.changed_data.append('tags')
        self.instance.tags = tags


class LocationForm(AssistantContextMixin, TagsFieldMixin, StyledFormMixin, forms.ModelForm):
    context_kind = assistant_context.LOCATION
    tags_text = forms.CharField(label='Tags', required=False, help_text=TAGS_HELP)

    class Meta:
        field_classes = {'latitude': CoordinateField, 'longitude': CoordinateField}
        model = Location
        fields = ['name', 'category', 'snippet', 'description', 'address', 'latitude', 'longitude',
                  'website', 'image', 'image_url', 'hidden']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'latitude': forms.NumberInput(attrs={'step': 'any'}),
            'longitude': forms.NumberInput(attrs={'step': 'any'}),
        }
        labels = {'snippet': 'One-line summary', 'image_url': 'Image URL', 'hidden': 'Hide from attendees'}

    def __init__(self, *args, categories=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].widget.attrs['list'] = 'category-options'
        self.categories = categories
        self._init_tags()
        self._init_context()

    def save(self, commit=True):
        self._save_tags()
        self._save_context()
        loc = self.instance
        if loc.image_auto and {'image', 'image_url'} & set(self.changed_data):
            # The organizer's own image (or the found one cleared): no credit, and never look again when cleared.
            loc.image_never_refetch = not loc.image and not self.cleaned_data.get('image_url')
            loc.image_auto = False
            loc.image_credit = loc.image_license = loc.image_source_url = ''
        return super().save(commit)


class ScheduledItemForm(AssistantContextMixin, TagsFieldMixin, StyledFormMixin, forms.ModelForm):
    context_kind = assistant_context.ITEM
    start_time = forms.DateTimeField(widget=DT_WIDGET, input_formats=DT_FORMATS)
    end_time = forms.DateTimeField(widget=DT_WIDGET, input_formats=DT_FORMATS, required=False)
    tags_text = forms.CharField(label='Tags', required=False, help_text=TAGS_HELP)

    class Meta:
        model = ScheduledItem
        fields = ['name', 'start_time', 'end_time', 'location', 'description', 'hidden']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}
        labels = {'hidden': 'Hide from attendees'}

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location'].queryset = (event.location_set.order_by('sort_order', 'name') if event is not None
                                            else Location.objects.none())  # none: Ask Attnd's schema only
        self.fields['location'].required = False
        self.fields['location'].empty_label = '— No specific location —'
        self._init_tags()
        self._init_context()

    def clean_end_time(self):
        end = self.cleaned_data.get('end_time')
        start = self.cleaned_data.get('start_time')
        if end and start and end < start:
            raise forms.ValidationError('The end must be after the start.')
        return end

    def save(self, commit=True):
        self._save_tags()
        self._save_context()
        return super().save(commit)


class NotificationForm(StyledFormMixin, forms.ModelForm):
    WHEN_NOW, WHEN_LATER = 'now', 'later'
    when = forms.ChoiceField(choices=[(WHEN_NOW, 'Send now'), (WHEN_LATER, 'Schedule for later')],
                             initial=WHEN_NOW, widget=forms.RadioSelect)
    send_time = forms.DateTimeField(
        required=False, input_formats=DT_FORMATS,
        # step is in seconds: the picker only offers 10-minute marks
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'step': 600},
                                   format='%Y-%m-%dT%H:%M'),
        help_text='Scheduled updates go out in 10-minute slots (:00, :10, :20 …).')
    urgent = forms.BooleanField(
        label='Urgent', required=False,
        help_text='For cancellations, safety and big changes only. It goes to everyone in the event right away, '
                  'even people who turned notifications off, as a time-sensitive alert (it can break through '
                  'Focus), and on Lock Screen Live Activities.')

    class Meta:
        model = Notification
        fields = ['title', 'body', 'send_time']
        widgets = {'body': forms.Textarea(attrs={'rows': 3, 'maxlength': 256}),
                   'title': forms.TextInput(attrs={'maxlength': 60})}

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event

    def clean(self):
        data = super().clean()
        send_time = data.get('send_time')
        if data.get('urgent') and data.get('when') == self.WHEN_NOW and self.event is not None:
            from events.push import urgent_sends_left
            if urgent_sends_left(self.event, now=timezone.now()) <= 0:  # exact, never the page's memoised count
                self.add_error('urgent', 'This event has sent the most urgent updates allowed in an hour. Send it as '
                                         'a normal update, or try again later.')
        if data.get('when') == self.WHEN_LATER:
            if not send_time:
                self.add_error('send_time', 'Pick a time.')
            elif not is_send_slot(send_time):
                self.add_error('send_time', 'Pick a time on a 10-minute mark (e.g. 6:00, 6:10, 6:20).')
            elif send_time <= timezone.now():
                self.add_error('send_time', 'Pick a time in the future, or choose “Send now”.')
        return data

    def save(self, commit=True):
        self.instance.priority = (Notification.PRIORITY_URGENT if self.cleaned_data.get('urgent')
                                  else Notification.PRIORITY_NORMAL)
        return super().save(commit)


class DisplayForm(forms.Form):
    preset = forms.ChoiceField(choices=[(p, LABELS['preset'][p]) for p in PRESETS],
                               widget=forms.RadioSelect)
    tabs = forms.MultipleChoiceField(choices=[(t, LABELS['tabs'][t]) for t in TABS if t != 'home'],
                                     widget=forms.CheckboxSelectMultiple,
                                     required=False)
    tab_order = forms.CharField(widget=forms.HiddenInput, required=False)
    default_tab = forms.ChoiceField(label='Default tab (apps without Home)',
                                    choices=[('', 'Automatic')] + [(t, LABELS['tabs'][t]) for t in TABS if t != 'home'],
                                    required=False)
    locations_style = forms.ChoiceField(choices=[('', 'Automatic')] + [(s, LABELS['locations_style'][s]) for s in LOCATIONS_STYLES], required=False)
    schedule_style = forms.ChoiceField(choices=[('', 'Automatic')] + [(s, LABELS['schedule_style'][s]) for s in SCHEDULE_STYLES], required=False)
    secondary_pane = forms.ChoiceField(
        label='Beside the map on big screens', required=False,
        choices=[('', 'Automatic')] + [(s, LABELS['secondary_pane'][s]) for s in SECONDARY_PANES])
    map_clustering = forms.ChoiceField(label='Group nearby map pins', required=False,
                                       choices=[('', 'Automatic'), ('1', 'Yes'), ('0', 'No')])
    map_fit_all = forms.ChoiceField(label='Map opens zoomed to fit every pin', required=False,
                                    choices=[('', 'Automatic'), ('1', 'Yes'), ('0', 'No')])
    show_location_images = forms.ChoiceField(label='Show photos in lists', required=False,
                                             choices=[('', 'Automatic'), ('1', 'Yes'), ('0', 'No')])
    enable_search = forms.ChoiceField(label='Search box', required=False,
                                      choices=[('', 'Automatic'), ('1', 'Yes'), ('0', 'No')])
    group_by_category = forms.ChoiceField(label='Category filters', required=False,
                                          choices=[('', 'Automatic'), ('1', 'Yes'), ('0', 'No')])
    customize_tabs = forms.BooleanField(label='Choose tabs myself', required=False)

    BOOL_KEYS = ['map_clustering', 'map_fit_all', 'show_location_images', 'enable_search', 'group_by_category']

    def __init__(self, *args, empty=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.empty = set(empty)  # tabs with nothing to show: left out of the apps, so they don't count
    CHOICE_KEYS = ['default_tab', 'locations_style', 'schedule_style', 'secondary_pane']

    @classmethod
    def initial_from(cls, overrides):
        initial = {'preset': overrides.get('preset', 'auto'),
                   'customize_tabs': 'tabs' in overrides,
                   # Home is always on (first and the default; never in the list organizers edit).
                   'tabs': [t for t in overrides.get('tabs', TABS) if t != 'home'],
                   'tab_order': ','.join(t for t in overrides.get('tabs', TABS) if t != 'home')}
        for key in cls.CHOICE_KEYS:
            initial[key] = overrides.get(key, '')
        for key in cls.BOOL_KEYS:
            initial[key] = {True: '1', False: '0'}.get(overrides.get(key), '')
        return initial

    def clean(self):
        from events.display import MAX_TABS, TAB_LIMIT_NOTE
        data = super().clean()
        shown = [t for t in (data.get('tabs') or []) if t not in self.empty]
        if data.get('customize_tabs') and len(shown) > MAX_TABS - 1:  # + Home
            self.add_error('tabs', TAB_LIMIT_NOTE)
        return data

    def to_overrides(self):
        d = self.cleaned_data
        out = {}
        if d['preset'] != 'auto':
            out['preset'] = d['preset']
        if d.get('customize_tabs'):
            order = [t for t in (d.get('tab_order') or '').split(',') if t in TABS and t != 'home']
            order += [t for t in TABS if t not in order and t != 'home']
            out['tabs'] = [t for t in order if t in d.get('tabs', [])]
        for key in self.CHOICE_KEYS:
            if d.get(key):
                out[key] = d[key]
        for key in self.BOOL_KEYS:
            if d.get(key) in ('0', '1'):
                out[key] = d[key] == '1'
        return out


class NotificationEditForm(StyledFormMixin, forms.Form):
    """Change a scheduled notification before it goes out. Empty fields keep their current value (so Ask Attnd can
    pass only what changes); urgency can't be changed here (that goes through Send's confirmation)."""
    title = forms.CharField(max_length=60, required=False)
    body = forms.CharField(max_length=256, required=False, widget=forms.Textarea(attrs={'rows': 3, 'maxlength': 256}))
    send_time = forms.DateTimeField(
        required=False, input_formats=DT_FORMATS, label='Send at',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'step': 600}, format='%Y-%m-%dT%H:%M'),
        help_text='A 10-minute mark in the future.')

    def clean_send_time(self):
        send_time = self.cleaned_data.get('send_time')
        if send_time:
            if not is_send_slot(send_time):
                raise forms.ValidationError('Pick a time on a 10-minute mark (e.g. 6:00, 6:10, 6:20).')
            if send_time <= timezone.now():
                raise forms.ValidationError('Pick a time in the future.')
        return send_time


class TeamInviteForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label='Email')
    role = forms.ChoiceField(label='Role', choices=EventMember.ROLE_CHOICES, initial=EventMember.ROLE_CONTRIBUTOR)


class EventThemeForm(forms.Form):
    """API-023: the event's six colours (light and dark × primary, secondary, tertiary) as exact hex codes. The
    console draws a colour picker next to each field (console.js) and a contrast note under it (events.theme.checks)."""

    ROLE_HELP = {'primary': 'Buttons, active chips and links', 'secondary': 'Soft tints: selected rows, chips, the page glow',
                 'tertiary': 'Highlights: tab underlines, hovers, gradients'}

    light_primary = forms.CharField(label='Light · Primary', max_length=7)
    dark_primary = forms.CharField(label='Dark · Primary', max_length=7)
    light_secondary = forms.CharField(label='Light · Secondary', max_length=7)
    dark_secondary = forms.CharField(label='Dark · Secondary', max_length=7)
    light_tertiary = forms.CharField(label='Light · Tertiary', max_length=7)
    dark_tertiary = forms.CharField(label='Dark · Tertiary', max_length=7)

    def __init__(self, *args, theme=None, **kwargs):
        from events import theme as T
        current = T.normalize(theme) or T.default_theme()
        kwargs.setdefault('initial', {f'{m}_{r}': current[m][r] for m in T.MODES for r in T.ROLES})
        super().__init__(*args, **kwargs)
        checks = T.checks(current)
        for mode in T.MODES:
            for role in T.ROLES:
                f = self.fields[f'{mode}_{role}']
                f.widget.attrs.update({'data-hex': '', 'pattern': '#?[0-9A-Fa-f]{6}', 'maxlength': '7', 'size': '8',
                                       'spellcheck': 'false', 'autocomplete': 'off', 'inputmode': 'text'})
                note = checks[mode][role]['note']
                f.help_text = self.ROLE_HELP[role] + (f'. {note}' if note else '.')

    def _clean_hex(self, name):
        from events.theme import clean_hex
        value = clean_hex(self.cleaned_data.get(name))
        if not value:
            raise forms.ValidationError('Enter a colour as #RRGGBB, e.g. #431F86.')
        return value

    def clean(self):
        data = super().clean()
        for name in list(self.fields):
            if name in data:
                try:
                    data[name] = self._clean_hex(name)
                except forms.ValidationError as e:
                    self.add_error(name, e)
        return data

    def to_theme(self):
        from events import theme as T
        d = self.cleaned_data
        return {m: {r: d[f'{m}_{r}'] for r in T.ROLES} for m in T.MODES}


class EventThemeSwitchesForm(forms.Form):
    theme_web = forms.BooleanField(label='Use custom theme on the website', required=False,
                                   help_text='The console’s event pages and the join and shared-plan pages')
    theme_apps = forms.BooleanField(label='Use custom theme in the apps', required=False,
                                    help_text='Sent to the iPhone and Android apps (they adopt it in a later update)')


class ThemeGenerateForm(StyledFormMixin, forms.Form):
    instruction = forms.CharField(label='Anything to keep in mind?', required=False, max_length=300,
                                  help_text='Optional: “warmer”, “match our logo”, “more contrast”…')
