"""The shared quantity component keeps audit data available but unobtrusive."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, select_autoescape


class MeasurementMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.primary_count = 0
        self.open_details = 0
        self.visible = []
        self.detail_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'details':
            self.depth += 1
            self.open_details += int('open' in attrs)
        self.primary_count += int('data-filament-primary' in attrs)

    def handle_endtag(self, tag):
        if tag == 'details':
            self.depth -= 1

    def handle_data(self, data):
        (self.detail_text if self.depth else self.visible).append(data)


class MeasurementViewTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.env = Environment(loader=FileSystemLoader(root / 'templates'),
                               autoescape=select_autoescape(['html']))

    def render(self, **changes):
        value = dict(text='436.35 g', grams='436.35', meters='145.45', factor='3.0',
                     source='Recorded job g/m', kind='estimated')
        value.update(changes)
        template = self.env.from_string(
            '{% from "partials/_filament_measurement.html" import measurement with context %}'
            '{{ measurement(value, row) }}')
        return template.render(value=value, row={'filament_material': '<script>unsafe</script>',
                                                'filament_mm': '145450'})

    def test_one_primary_quantity_and_closed_audit_details(self):
        html = self.render()
        parsed = MeasurementMarkup()
        parsed.feed(html)
        self.assertEqual(parsed.primary_count, 1)
        self.assertEqual(parsed.open_details, 0)
        self.assertEqual(''.join(parsed.visible).strip(), '436.35 g')
        details = ''.join(parsed.detail_text)
        for expected in ('145.45 m', '436.35 g', '3.0 g/m', 'Estimated weight',
                         'Recorded job g/m', '145450 mm'):
            self.assertIn(expected, details)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_missing_conversion_stays_unknown(self):
        html = self.render(text='Unknown length', meters=None, factor=None,
                           source='No verified conversion', kind='recorded')
        parsed = MeasurementMarkup()
        parsed.feed(html)
        self.assertEqual(''.join(parsed.visible).strip(), 'Unknown length')
        self.assertIn('Recorded weight', ''.join(parsed.detail_text))
        self.assertIn('Unavailable', ''.join(parsed.detail_text))
        self.assertEqual(parsed.open_details, 0)

    def test_changed_templates_compile(self):
        for name in ('base.html', 'index.html', 'projects.html', 'project_pricing.html',
                     'printer_detail.html', 'reports.html', 'estimates.html', 'inventory.html',
                     'settings/partials/other.html'):
            with self.subTest(template=name):
                self.env.get_template(name)

    def test_long_filename_is_retained_in_closed_selectable_details(self):
        name = ('very-long-print-name-' * 20) + '<script>.gcode'
        html = self.env.from_string(
            '{% from "partials/_filename.html" import filename %}{{ filename(name) }}'
        ).render(name=name)
        parsed = MeasurementMarkup()
        parsed.feed(html)
        self.assertEqual(parsed.open_details, 0)
        self.assertIn(name, ''.join(parsed.visible))
        self.assertIn(name, ''.join(parsed.detail_text))
        self.assertIn('data-filename-preview', html)
        self.assertIn('data-filename-full', html)
        self.assertIn('Full filename', html)
        self.assertNotIn('<script>', html)


if __name__ == '__main__':
    unittest.main()
