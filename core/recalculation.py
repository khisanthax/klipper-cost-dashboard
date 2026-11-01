"""Shared explicit-unit, per-job recalculation plan used by preview and apply."""
from decimal import Decimal
import hashlib
import json
from core import profiles
from core.filament import conversion,material_cost,price_spec,number


def enrich_compute(base,plan,form):
    amount=str(form.get('pricing_amount') or '').strip()
    spec=price_spec(form.get('pricing_mode','spool'),amount,form.get('net_spool_grams',1000)) if amount else None
    factor=form.get('conversion_factor_override') or None
    density=form.get('conversion_density_override') or None
    diameter=form.get('conversion_diameter_override') or '1.75'
    if factor:number(factor,'Explicit g/m',positive=True)
    if density:number(density,'Density',positive=True);number(diameter,'Diameter',positive=True)
    if spec:plan['filament_price']={k:str(v) for k,v in spec.items()}
    plan['conversion_factor_override']=factor
    plan['conversion_density_override']=density
    plan['conversion_diameter_override']=diameter if density else None
    explicit_material=bool(spec or factor or density or plan.get('apply_filament_profile') or plan.get('filament_rate_per_meter_override') is not None)
    explicit_time=bool(plan.get('apply_rate_profile') or plan.get('rate_per_hour_override') is not None)
    explicit_any=explicit_material or explicit_time
    from core.sql_only import is_sql_only
    if not explicit_any and not factor and not density and not is_sql_only():
        return base,plan
    def compute(printer,duration,filament,paused=0):
        return base(printer,duration,filament,paused)
    def for_row(row):
        result=dict(base(row['printer'],row.get('duration_seconds') or 0,row.get('filament_mm') or 0,row.get('paused_seconds_total') or 0))
        profile_id=plan.get('filament_profile_id') if plan.get('apply_filament_profile') else row.get('filament_profile_id')
        profile=profiles.get_profile(profile_id) if profile_id else {}
        selected_factor=factor
        if plan.get('apply_filament_profile') and not selected_factor and row.get('filament_weight_kind')!='recorded':
            selected_factor=(profile or {}).get('grams_per_meter')
        measure=conversion(row,profile,factor_override=selected_factor,density_override=density,diameter_override=diameter)
        result['filament_grams']=float(measure['grams']) if measure['grams'] is not None else None
        result['filament_weight_kind']=measure['weight_kind']
        selected=spec
        if selected is None and plan.get('filament_rate_per_meter_override') is not None:
            selected=price_spec('per_meter',plan['filament_rate_per_meter_override'])
        if selected is None and plan.get('apply_filament_profile'):
            selected=price_spec(profile['filament_mode'],profile['filament_rate'])
        if selected is None and (factor or density):
            selected=price_spec(row['filament_mode'],row['filament_rate'])
        if explicit_any and not explicit_time and row.get('time_cost') not in (None,''):
            result['time_cost']=row.get('time_cost') or 0
            result['rate_per_hour']=row.get('rate_per_hour') or 0
            result['hourly_rate_profile_id']=row.get('hourly_rate_profile_id')
            if row.get('billable_hours') not in (None,''):
                result['billable_hours']=row['billable_hours']
        elif plan.get('apply_rate_profile'):
            result['hourly_rate_profile_id']=plan.get('rate_profile_id')
            result['override_rate_per_hour']=None
        if explicit_time:
            result['override_rate_per_hour']=plan.get('rate_per_hour_override')
            if not plan.get('apply_rate_profile'):result['hourly_rate_profile_id']=None
            hours=row.get('billable_hours')
            if hours=='':hours=None
            if hours is None and number(row.get('rate_per_hour') or 0)>0 and number(row.get('time_cost') or 0)>0:
                hours=number(row.get('time_cost') or 0)/number(row['rate_per_hour'])
            if hours is not None:
                result['billable_hours']=float(number(hours))
                result['time_cost']=float(number(hours)*number(result['rate_per_hour']))
        if explicit_any and not explicit_material and row.get('material_cost') not in (None,''):
            for key in ('material_cost','filament_mode','filament_rate','grams_per_meter','filament_profile_id','filament_material'):
                if row.get(key) not in (None,''):result[key]=row[key]
        elif selected:
            result['material_cost']=float(material_cost(measure,selected))
            result['filament_mode']=selected['mode'];result['filament_rate']=float(selected['rate'])
            result['grams_per_meter']=float(measure['grams_per_meter']) if measure['grams_per_meter'] is not None else row.get('grams_per_meter')
            result['filament_grams']=float(measure['grams']) if measure['grams'] is not None else None
            result['filament_weight_kind']=measure['weight_kind']
            result['filament_profile_id']=profile_id if plan.get('apply_filament_profile') else row.get('filament_profile_id')
            result['filament_material']=(profile or {}).get('material') or row.get('filament_material')
            result['override_material_cost']=None
        adjustment=(number(row.get('total_cost') or 0,signed=True)-number(row.get('time_cost') or 0)-number(row.get('material_cost') or 0)) if all(row.get(k) not in (None,'') for k in ('time_cost','material_cost','total_cost')) else Decimal(0)
        result['adjustment']=float(adjustment)
        result['total_cost']=float(number(result['time_cost'])+number(result['material_cost'])+adjustment)
        if row.get('override_total_cost') is not None:
            result['total_cost']=float(row['override_total_cost'])
            result['adjustment']=float(number(result['total_cost'])-number(result['time_cost'])-number(result['material_cost']))
        result['conversion_source']=measure['conversion_source']
        result['pricing_source']='Explicit recalculation plan' if explicit_any else 'Current printer defaults'
        if row.get('override_total_cost') not in (None,''):
            result['pricing_source']+='; saved exact total override retained'
        result['conversion_grams']=float(measure['grams']) if measure['grams'] is not None else None
        return result
    compute.for_row=for_row
    return compute,plan


def preview_fingerprint(rows,compute_fn,plan):
    keys=('job_uid','filename','status','duration_seconds','filament_mm','filament_meters',
          'paused_seconds_total','time_cost','material_cost','total_cost','rate_per_hour',
          'filament_mode','filament_rate','grams_per_meter','filament_grams','filament_weight_kind',
          'filament_profile_id','hourly_rate_profile_id','override_total_cost','override_material_cost')
    payload=[]
    for row in sorted(rows,key=lambda r:r['job_uid']):
        expected=compute_fn.for_row(row) if hasattr(compute_fn,'for_row') else compute_fn(row['printer'],row.get('duration_seconds') or 0,row.get('filament_mm') or 0,row.get('paused_seconds_total') or 0)
        payload.append({'source':{k:row.get(k) for k in keys},'expected':expected})
    return hashlib.sha256(json.dumps({'plan':plan,'jobs':payload},sort_keys=True,default=str).encode()).hexdigest()


def restore_originals(job_uids,*,apply=False):
    from core import db
    fields=('rate_per_hour','filament_mode','filament_rate','grams_per_meter','time_cost','material_cost','total_cost',
            'filament_profile_id','filament_material','hourly_rate_profile_id','override_rate_per_hour',
            'override_material_cost','override_total_cost','filament_grams','filament_weight_kind','billable_hours')
    report=[]
    with db.connect_db() as conn:
        db.apply_migrations(conn)
        for uid in job_uids:
            saved=conn.execute('SELECT original_json FROM job_cost_originals WHERE job_uid=?',(uid,)).fetchone()
            current=conn.execute('SELECT * FROM jobs WHERE job_uid=?',(uid,)).fetchone()
            if not saved or not current:continue
            original=json.loads(saved[0]);values={k:original.get(k) for k in fields}
            report.append({'job_uid':uid,'before':{k:current[k] for k in fields},'original':values})
            if apply:
                conn.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+',updated_at=? WHERE job_uid=?',[*values.values(),db._utc_now_iso(),uid])
    return {'applied':apply,'jobs':report}
