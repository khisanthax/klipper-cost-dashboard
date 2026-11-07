"""Project previews require evidence for preserved historical components."""
from decimal import Decimal
import unittest

from core.project_pricing import calculate_job


class ProjectPricingBasisTests(unittest.TestCase):
    def row(self,**values):
        return dict(job_uid='job',filename='part.gcode',filament_meters=10,grams_per_meter=3,
            duration_seconds=3600,rate_per_hour=1,**values)

    def pricing(self):
        return dict(hourly_rate_override=0,filament_cost_per_kg_override=15)

    def test_known_total_cannot_invent_missing_original_adjustment(self):
        for costs in (dict(time_cost=None,material_cost=2,total_cost=10),
                      dict(time_cost=3,material_cost=None,total_cost=10),
                      dict(time_cost=None,material_cost=None,total_cost=10)):
            with self.subTest(costs=costs),self.assertRaisesRegex(ValueError,'signed adjustment cannot be verified'):
                calculate_job(self.row(**costs),self.pricing())

    def test_unpriced_requires_explicit_time_and_material(self):
        row=self.row(time_cost=None,material_cost=None,total_cost=None)
        for pricing,message in (({},'Original time cost'),
                                (dict(filament_cost_per_kg_override=15),'Original time cost'),
                                (dict(hourly_rate_override=0),'Original material cost')):
            with self.subTest(pricing=pricing),self.assertRaisesRegex(ValueError,message):
                calculate_job(row,pricing)

    def test_missing_total_with_recorded_components_requires_explicit_initialization(self):
        row=self.row(time_cost=1,material_cost=2,total_cost=None)
        with self.assertRaisesRegex(ValueError,'Choose both an explicit'):
            calculate_job(row,dict(filament_cost_per_kg_override=15))

    def test_null_original_explicit_initialization_retains_null_evidence(self):
        result=calculate_job(self.row(time_cost=None,material_cost=None,total_cost=None),self.pricing())
        self.assertFalse(result['original_price_known'])
        self.assertEqual(result['recorded_costs'],dict(time_cost=None,material_cost=None,total_cost=None))
        self.assertEqual(result['after']['total_cost'],Decimal('.45'))
        self.assertEqual(result['after']['adjustment'],0)
        self.assertIn('zero adjustment',result['pricing_source'])

    def test_exact_total_override_retained_with_explicit_new_components(self):
        for total in (0,10,None):
            with self.subTest(total=total):
                result=calculate_job(self.row(time_cost=None,material_cost=None,total_cost=total,override_total_cost=0),self.pricing())
                self.assertEqual(result['after']['total_cost'],0)
                self.assertEqual(result['after']['adjustment'],Decimal('-.45'))
                self.assertIn('Saved exact total override retained',result['pricing_source'])

    def test_complete_zero_and_signed_adjustments_remain_valid(self):
        for total,expected in ((0,Decimal('0')), (2,Decimal('-.55')), (5,Decimal('2.45'))):
            row=self.row(time_cost=0 if total==0 else 1,material_cost=0 if total==0 else 2,total_cost=total)
            pricing=dict(hourly_rate_override=0,filament_cost_per_kg_override=0) if total==0 else self.pricing()
            result=calculate_job(row,pricing)
            self.assertTrue(result['original_price_known'])
            self.assertEqual(result['after']['total_cost'],expected)
            self.assertEqual(result['after']['time_cost']+result['after']['material_cost']+result['after']['adjustment'],expected)
