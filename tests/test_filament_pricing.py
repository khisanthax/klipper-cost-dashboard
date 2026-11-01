from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.filament import conversion,material_cost,price_spec,grams_per_meter_from_geometry,reconcile_display,normalize_profile
from core.project_pricing import calculate_job


class FilamentUnitsTests(unittest.TestCase):
    def test_acceptance_project_weight_and_rounding(self):
        row=dict(job_uid='acceptance',filament_meters=835.02,grams_per_meter=3,time_cost=142.63,material_cost=208.75,total_cost=351.38)
        result=calculate_job(row,dict(filament_cost_per_kg_override=15))
        self.assertEqual(result['kilograms'],Decimal('2.50506'))
        self.assertEqual(result['after']['material_cost'],Decimal('37.57590'))
        totals=reconcile_display([result['after']])
        self.assertEqual(totals['total_cost'],Decimal('180.21'))
        self.assertEqual(row['total_cost'],351.38)

    def test_petg_spool_geometry_and_other_spool_sizes(self):
        factor=grams_per_meter_from_geometry('1.25','1.75')
        self.assertAlmostEqual(float(factor),3.0066023442558574)
        measure=conversion(dict(filament_meters=100),density_override='1.25',diameter_override='1.75')
        self.assertEqual(measure['grams'],factor*100)
        self.assertEqual(material_cost(measure,price_spec('spool',15,1000)),factor*Decimal('1.5'))
        self.assertEqual(material_cost(measure,price_spec('spool',15,500)),factor*3)
        profile=normalize_profile(dict(price_input_mode='spool',spool_price=15,net_spool_grams=1000,density_g_cm3=1.25,diameter_mm=1.75))
        self.assertEqual(profile['filament_mode'],'per_kg');self.assertEqual(profile['filament_rate'],15)
        self.assertAlmostEqual(profile['grams_per_meter'],float(factor))

    def test_explicit_units_are_never_interchangeable(self):
        measure=conversion(dict(filament_meters=100,grams_per_meter=3))
        for mode,rate,cost in [('per_kg',15,'4.5'),('per_gram','.015','4.5'),('per_meter','.25','25')]:
            self.assertEqual(material_cost(measure,price_spec(mode,rate)),Decimal(cost))
        legacy=normalize_profile(dict(filament_mode='per_meter',filament_rate=.25,grams_per_meter=3))
        self.assertEqual(legacy['filament_mode'],'per_meter');self.assertEqual(legacy['filament_rate'],.25)

    def test_recorded_weight_and_per_job_material_precedence(self):
        recorded=conversion(dict(filament_meters=100,filament_grams=350,filament_weight_kind='recorded',grams_per_meter=3),dict(grams_per_meter=4))
        self.assertEqual(recorded['grams'],350)
        self.assertEqual(recorded['weight_kind'],'recorded')
        self.assertEqual(conversion(dict(filament_meters=100,grams_per_meter=3),dict(grams_per_meter=4))['grams'],300)
        self.assertEqual(conversion(dict(filament_meters=100),dict(grams_per_meter=4))['grams'],400)
        self.assertEqual(conversion(dict(filament_meters=100),dict(density_g_cm3=1.25,diameter_mm=1.75))['grams'],grams_per_meter_from_geometry(1.25,1.75)*100)

    def test_missing_conversion_blocks_weight_price_not_meter_price(self):
        measure=conversion(dict(filament_meters=100))
        with self.assertRaises(ValueError):material_cost(measure,price_spec('per_kg',15))
        self.assertEqual(material_cost(measure,price_spec('per_meter','.25')),25)
        self.assertEqual(material_cost(conversion(dict(filament_meters=0)),price_spec('per_kg',15)),0)
        for weight in (0,-1,'nan'):
            with self.assertRaises(ValueError):price_spec('spool',15,weight)

    def test_signed_adjustments_and_exact_overrides_survive(self):
        row=dict(job_uid='discount',filament_meters=100,grams_per_meter=3,time_cost=10,material_cost=25,total_cost=30)
        calculated=calculate_job(row,dict(filament_cost_per_kg_override=15,hourly_rate_override=0))
        self.assertEqual(calculated['after']['adjustment'],-5)
        self.assertEqual(calculated['after']['total_cost'],Decimal('-.5'))
        row['override_total_cost']=20
        calculated=calculate_job(row,dict(filament_cost_per_kg_override=15,hourly_rate_override=0))
        self.assertEqual(calculated['after']['total_cost'],20)
        self.assertEqual(calculated['after']['adjustment'],Decimal('15.5'))

    def test_rounding_reconciles_many_mixed_rows(self):
        rows=[dict(time_cost=Decimal('.004'),material_cost=Decimal('.006'),adjustment=Decimal('-.001')) for _ in range(15)]
        totals=reconcile_display(rows)
        self.assertEqual(totals['time_cost'],Decimal('.06'))
        self.assertEqual(totals['material_cost'],Decimal('.09'))
        self.assertEqual(totals['adjustment'],Decimal('-.02'))
        self.assertEqual(sum(r['display_total_cost'] for r in rows),totals['total_cost'])
        for r in rows:self.assertEqual(r['display_total_cost'],r['display_time_cost']+r['display_material_cost']+r['display_adjustment'])

    def test_recalculate_preview_allocates_cents_and_normalizes_zero_adjustment(self):
        from core.recalculation import reconcile_preview
        rows=[dict(old_time_cost=1,old_material_cost=.25,old_adjustment=-1e-15,time_cost=0,material_cost=.004,adjustment=1e-15) for _ in range(15)]
        before,after=reconcile_preview(rows)
        self.assertEqual(after['material_cost'],Decimal('.06'))
        self.assertEqual(sum(r['display_material_cost'] for r in rows),Decimal('.06'))
        self.assertEqual(sum(r['display_delta'] for r in rows),after['total_cost']-before['total_cost'])
        self.assertTrue(all(r['display_adjustment']==0 for r in rows))
        self.assertTrue(all(r['material_cost']==.004 for r in rows),'Display allocation must not change SQL calculation precision')


class ProjectPricingSnapshotTests(unittest.TestCase):
    def test_profile_name_collision_cannot_replace_a_referenced_identity(self):
        from core import db,profiles,rates
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'KCD_STORAGE_BACKEND':'sql'}),patch.object(db,'_db_path',return_value=str(Path(tmp)/'kcd.db')):
            original=profiles.upsert_profile(dict(id='original',name='PETG',filament_mode='per_kg',filament_rate=15,grams_per_meter=3))
            with self.assertRaisesRegex(ValueError,'already exists'):
                profiles.upsert_profile(dict(id='replacement',name='PETG',filament_mode='per_kg',filament_rate=99,grams_per_meter=3))
            self.assertEqual(profiles.get_profile(original)['filament_rate'],15)
            self.assertIsNone(profiles.get_profile('replacement'))
            rates.upsert_rate_profile(dict(id='original-rate',name='Free',rate_per_hour=0))
            with self.assertRaisesRegex(ValueError,'already exists'):
                rates.upsert_rate_profile(dict(id='replacement-rate',name='Free',rate_per_hour=10))
            self.assertEqual(rates.get_rate_profile('original-rate')['rate_per_hour'],0)

    def test_overlay_preview_apply_restore_and_stale_guard(self):
        from core import db,projects,project_pricing
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'KCD_STORAGE_BACKEND':'sql'}),patch.object(db,'_db_path',return_value=str(Path(tmp)/'kcd.db')):
            p=projects.create_project('Price test',filament_cost_per_kg_override=15)
            with db.connect_db() as conn:
                db.upsert_printer(conn,'Fixture')
                for i,factor in enumerate((3,4)):
                    db.upsert_job(conn,dict(job_uid=str(i),printer='Fixture',filename=str(i),status='completed',filament_mm=100000,filament_meters=100,grams_per_meter=factor,time_cost=10,material_cost=25,total_cost=34))
            projects.assign_jobs(['0','1'],p.id)
            plan=project_pricing.preview(p.id)
            self.assertEqual(plan['after_totals']['material_cost'],Decimal('10.50'))
            self.assertEqual(plan['after_totals']['adjustment'],Decimal('-2.00'))
            self.assertEqual(project_pricing.saved_overlays(p.id),{})
            self.assertEqual(project_pricing.apply(p.id,plan['fingerprint']),2)
            self.assertEqual(len(project_pricing.saved_overlays(p.id)),2)
            with db.connect_db() as conn:
                self.assertEqual(conn.execute('SELECT SUM(total_cost) FROM jobs').fetchone()[0],68)
                conn.execute("UPDATE jobs SET total_cost=35 WHERE job_uid='0'")
            with self.assertRaises(ValueError):project_pricing.apply(p.id,plan['fingerprint'])
            project_pricing.clear(p.id)
            self.assertEqual(project_pricing.saved_overlays(p.id),{})


if __name__=='__main__':unittest.main()
