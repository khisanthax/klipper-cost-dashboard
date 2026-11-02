"""Explicit filament units and per-job length/mass conversion.

Nozzle diameter is intentionally not used: the consumed input filament's
diameter and density determine mass, independent of extruded line geometry.
"""
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from core.numeric import NumericValidationError

PI=Decimal('3.141592653589793238462643383279502884')
CENT=Decimal('.01')


def number(value,label='value',*,positive=False,signed=False):
    try:result=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):raise NumericValidationError(label+' must be a finite number')
    if not result.is_finite() or (positive and result<=0) or (not signed and result<0):
        raise NumericValidationError(label+(' must be greater than zero' if positive else ' must be finite and nonnegative'))
    return result


def grams_per_meter_from_geometry(density,diameter):
    return PI*number(diameter,'Filament diameter (mm)',positive=True)**2/4*number(density,'Density (g/cm³)',positive=True)


def price_spec(mode,amount,net_spool_grams=1000):
    amount=number(amount,'Filament price')
    if mode=='spool':
        net=number(1000 if net_spool_grams in (None,'') else net_spool_grams,'Net spool filament weight (g)',positive=True)
        return {'mode':'per_kg','rate':amount*1000/net,'input_mode':'spool','spool_price':amount,'net_spool_grams':net}
    if mode not in {'per_kg','per_gram','per_meter'}:raise NumericValidationError('Select an explicit filament pricing unit')
    return {'mode':mode,'rate':amount,'input_mode':mode}


def configuration_from_form(form):
    """Normalize new explicit units without reinterpreting any legacy field."""
    result={}
    if str(form.get('pricing_amount','')).strip():
        spec=price_spec(form.get('pricing_mode','spool'),form['pricing_amount'],form.get('net_spool_grams') or 1000)
        result.update(filament_mode=spec['mode'],filament_rate=float(spec['rate']),price_input_mode=spec['input_mode'])
        if spec['input_mode']=='spool':
            result.update(spool_price=float(spec['spool_price']),net_spool_grams=float(spec['net_spool_grams']))
        if spec['mode']=='per_kg':result['cost_per_kg']=float(spec['rate'])
    if form.get('conversion_basis') in ('factor','geometry'):result['conversion_basis']=form['conversion_basis']
    if form.get('conversion_basis')=='geometry':
        density=number(form.get('density_g_cm3'),'Density',positive=True)
        diameter=number(form.get('diameter_mm') or '1.75','Filament diameter',positive=True)
        result.update(density_g_cm3=float(density),diameter_mm=float(diameter),grams_per_meter=float(grams_per_meter_from_geometry(density,diameter)))
    elif str(form.get('grams_per_meter','')).strip():
        result['grams_per_meter']=float(number(form['grams_per_meter'],'Grams per meter',positive=True))
    if result.get('filament_mode')=='per_gram':result['cost_per_kg']=result['filament_rate']*1000
    elif result.get('filament_mode')=='per_meter' and result.get('grams_per_meter'):
        result['cost_per_kg']=result['filament_rate']*1000/result['grams_per_meter']
    return result


def project_price_from_form(form):
    selected=str(form.get('project_filament_profile_id') or '').strip()
    if selected:
        from core import profiles
        profile=profiles.get_profile(selected)
        if not profile:raise NumericValidationError('Selected project filament profile no longer exists; select another profile or custom pricing')
        profile=normalize_profile(profile)
        mode=profile.get('price_input_mode') or profile['filament_mode']
        amount=profile.get('spool_price') if mode=='spool' else profile['filament_rate']
        spec=price_spec(mode,amount,profile.get('net_spool_grams') or 1000)
        factor=number(profile.get('grams_per_meter'),'Profile grams per meter',positive=True)
        spec.update(grams_per_meter=factor,profile_id=selected,profile_name=profile.get('name') or selected)
        per_kg=spec['rate'] if spec['mode']=='per_kg' else spec['rate']*1000 if spec['mode']=='per_gram' else spec['rate']*1000/factor
        return float(per_kg),{k:str(v) for k,v in spec.items()}
    if not str(form.get('pricing_amount','')).strip():return None,None
    spec=price_spec(form.get('pricing_mode','spool'),form['pricing_amount'],form.get('net_spool_grams',1000))
    conversion_config=configuration_from_form(form)
    if conversion_config.get('grams_per_meter'):
        spec['grams_per_meter']=conversion_config['grams_per_meter']
    if spec['mode']=='per_kg':per_kg=spec['rate']
    elif spec['mode']=='per_gram':per_kg=spec['rate']*1000
    else:
        factor=number(spec.get('grams_per_meter'),'A project per-meter price requires verified g/m for weight-based manual and planned jobs',positive=True)
        per_kg=spec['rate']*1000/factor
    return float(per_kg),{k:str(v) for k,v in spec.items()}


def normalize_profile(data):
    result=dict(data)
    if str(data.get('pricing_amount','')).strip():
        result.update(configuration_from_form(data))
    elif data.get('price_input_mode')=='spool' and data.get('spool_price') is not None:
        spec=price_spec('spool',data['spool_price'],data.get('net_spool_grams',1000))
        result.update(filament_mode=spec['mode'],filament_rate=float(spec['rate']),cost_per_kg=float(spec['rate']),net_spool_grams=float(spec['net_spool_grams']))
    if data.get('density_g_cm3') and data.get('diameter_mm') and (data.get('conversion_basis')=='geometry' or data.get('grams_per_meter') in (None,'')):
        result['grams_per_meter']=float(grams_per_meter_from_geometry(data['density_g_cm3'],data['diameter_mm']))
        result['conversion_basis']='geometry'
    return result


def conversion(row,profile=None,*,factor_override=None,density_override=None,diameter_override=None):
    profile=profile or {}
    meters=None
    if row.get('filament_meters') not in (None,''):
        meters=number(row['filament_meters'],'Filament meters')
    elif row.get('filament_mm') not in (None,''):
        meters=number(row['filament_mm'],'Filament mm')/1000
    grams=None;factor=None;source='Missing conversion information';kind='estimated'
    if factor_override not in (None,''):
        factor=number(factor_override,'Explicit grams per meter',positive=True);source='Explicit g/m override'
    elif density_override not in (None,''):
        factor=grams_per_meter_from_geometry(density_override,diameter_override or '1.75');source='Explicit density and filament diameter'
    elif row.get('filament_grams') not in (None,'') and row.get('filament_weight_kind')!='estimated':
        grams=number(row['filament_grams'],'Recorded filament grams');source='Recorded weight';kind='recorded'
        if meters:factor=grams/meters
    elif row.get('grams_per_meter') not in (None,'') and number(row['grams_per_meter'])>0:
        factor=number(row['grams_per_meter']);source='Recorded job g/m'
    elif row.get('filament_density') and row.get('filament_diameter'):
        factor=grams_per_meter_from_geometry(row['filament_density'],row['filament_diameter']);source='Recorded density and filament diameter'
    elif profile.get('grams_per_meter') not in (None,'') and number(profile['grams_per_meter'])>0:
        factor=number(profile['grams_per_meter']);source='Job profile density and filament diameter (current fallback)' if profile.get('conversion_basis')=='geometry' else 'Job profile g/m (current fallback)'
    elif profile.get('density_g_cm3') and profile.get('diameter_mm'):
        factor=grams_per_meter_from_geometry(profile['density_g_cm3'],profile['diameter_mm']);source='Job profile density and filament diameter (current fallback)'
    if grams is None and meters is not None:
        if factor is not None:grams=meters*factor
        elif meters==0:grams=Decimal(0);source='Zero recorded length'
    return {'meters':meters,'grams':grams,'kilograms':None if grams is None else grams/1000,
            'grams_per_meter':factor,'conversion_source':source,'weight_kind':kind}


def material_cost(measurement,spec):
    unit=spec['mode'];rate=number(spec['rate'],'Material rate')
    value=measurement['meters'] if unit=='per_meter' else measurement['grams']
    if value is None:raise NumericValidationError('Weight/length conversion is missing; supply a per-job factor or recorded weight')
    return value*rate/(1000 if unit=='per_kg' else 1)


def quantize(value):
    return number(value,signed=True).quantize(CENT,rounding=ROUND_HALF_UP)


def describe_job(row,profile=None):
    """Presentation metadata only; never recalculate a saved historical cost."""
    try:
        measure=conversion(row,profile)
    except NumericValidationError as exc:
        # A malformed historical measurement must not hide its saved price or
        # prevent the rest of a history/report page from rendering.
        measure={'grams':None,'grams_per_meter':None,'conversion_source':'Invalid recorded measurement: '+str(exc)}
    row['_weight_grams']=float(measure['grams']) if measure['grams'] is not None else None
    row['_weight_factor']=measure['grams_per_meter']
    row['_weight_source']=measure['conversion_source']
    row['_price_unit']={'per_meter':'$/m','per_gram':'$/g','per_kg':'$/kg'}.get(row.get('filament_mode'),'Unknown unit')
    return row


def reconcile_display(rows):
    """Allocate fractional cents so totals equal the displayed job components."""
    fields=('time_cost','material_cost','adjustment')
    for field in fields:
        exact=[number(row[field],signed=True) for row in rows]
        rounded=[quantize(v) for v in exact]
        delta=int((quantize(sum(exact,Decimal(0)))-sum(rounded,Decimal(0)))/CENT)
        order=sorted(range(len(rows)),key=lambda i:exact[i]-rounded[i],reverse=delta>0)
        for i in order[:abs(delta)]:rounded[i]+=CENT if delta>0 else -CENT
        for row,value in zip(rows,rounded):row['display_'+field]=value
    for row in rows:row['display_total_cost']=sum((row['display_'+f] for f in fields),Decimal(0))
    return {f:sum((r['display_'+f] for r in rows),Decimal(0)) for f in (*fields,'total_cost')}
