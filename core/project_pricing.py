"""Previewable project-only cost overlays. Recorded jobs are never overwritten."""
from decimal import Decimal
import hashlib
import json
from core import db
from core.filament import conversion,material_cost,number,reconcile_display,project_price_from_form


def pricing_inputs(project):
    """Only project-local pricing; names and notes are not pricing changes."""
    return {key:project.get(key) for key in ('hourly_rate_override','filament_cost_per_kg_override','filament_price_json','labor_only')}


def inputs_from_form(form):
    """Use the same explicit-unit parser as Save Project Defaults, without saving."""
    from core.numeric import optional_finite_float
    per_kg,spec=project_price_from_form(form)
    if per_kg is None:
        per_kg=optional_finite_float(form.get('filament_cost_per_kg_override'),label='Filament $/kg',nonnegative=True)
    return dict(hourly_rate_override=optional_finite_float(form.get('hourly_rate_override'),label='Hourly rate',nonnegative=True),
        filament_cost_per_kg_override=per_kg,filament_price_json=json.dumps(spec,sort_keys=True) if spec else None,
        labor_only=bool(form.get('labor_only')))


def _plan_fingerprint(source,inputs):
    return source if inputs is None else hashlib.sha256(json.dumps({'source':source,'inputs':inputs},sort_keys=True,default=str).encode()).hexdigest()


def _completed(row):
    return str(row.get('status') or '').strip().upper() in ('COMPLETED','COMPLETE')


def calculate_job(row,project,profile=None,*,factor_override=None):
    measurement=conversion(row,profile,factor_override=factor_override)
    known={key:row.get(key) not in (None,'') for key in ('time_cost','material_cost','total_cost')}
    exact_total=row.get('override_total_cost') not in (None,'')
    explicit_time=project.get('hourly_rate_override') is not None
    explicit_material=bool(project.get('labor_only')) or project.get('filament_cost_per_kg_override') is not None
    if not known['time_cost'] and not explicit_time:
        raise ValueError('Original time cost is unavailable. Choose an explicit project hourly rate before previewing new costs.')
    if not known['material_cost'] and not explicit_material:
        raise ValueError('Original material cost is unavailable. Choose explicit project filament pricing or Labor-only before previewing new costs.')
    if known['total_cost'] and not all(known[key] for key in ('time_cost','material_cost')) and not exact_total:
        raise ValueError('Original cost components are incomplete, so the historical signed adjustment cannot be verified. Restore the missing original components or retain an explicit saved exact total override.')
    if not known['total_cost'] and not exact_total and not (explicit_time and explicit_material):
        raise ValueError('Original total is unavailable. Choose both an explicit project hourly rate and filament pricing (or Labor-only) to initialize a new price with zero adjustment.')
    time=number(row.get('time_cost') or 0);material=number(row.get('material_cost') or 0)
    original_total=number(row.get('total_cost') or 0,signed=True)
    adjustment=original_total-time-material if all(known.values()) else Decimal(0)
    before={'time_cost':time,'material_cost':material,'adjustment':adjustment,'total_cost':original_total}
    source=[]
    if not known['total_cost'] and not exact_total:
        source.append('Unpriced original: explicit project pricing initializes a new price with zero adjustment')
    rate=project.get('hourly_rate_override')
    if rate is not None:
        hours=row.get('billable_hours')
        if hours is None:
            recorded_rate=number(row.get('rate_per_hour') or 0)
            if recorded_rate>0 and time>0:hours=time/recorded_rate
            else:
                seconds=number(row.get('duration_seconds') or 0)-number(row.get('paused_seconds_total') or 0)
                hours=max(Decimal(1),seconds/3600) if seconds>0 else Decimal(0)
        time=number(hours)*number(rate);source.append('Project $/hour')
    else:source.append('Saved historical time cost')
    if project.get('labor_only'):
        material=Decimal(0);source.append('Project labor-only')
    elif project.get('filament_cost_per_kg_override') is not None:
        spec=json.loads(project['filament_price_json']) if project.get('filament_price_json') else {'mode':'per_kg','rate':project['filament_cost_per_kg_override']}
        if measurement['grams'] is None and spec.get('grams_per_meter'):
            measurement=conversion(row,profile,factor_override=spec['grams_per_meter'])
        material=material_cost(measurement,spec);source.append('Project '+spec['mode']+' rate '+str(spec['rate']))
    else:source.append('Saved historical material cost')
    if row.get('override_total_cost') is not None:
        adjustment=number(row['override_total_cost'])-time-material
        source.append('Saved exact total override retained')
    return {'job_uid':row['job_uid'],'filename':row.get('filename',''),'before':before,
            'original_price_known':row.get('total_cost') not in (None,''),
            'recorded_costs':{key:row.get(key) for key in ('time_cost','material_cost','total_cost')},
            'after':{'time_cost':time,'material_cost':material,'adjustment':adjustment,'total_cost':time+material+adjustment},
            **measurement,'pricing_source':'; '.join(source)}


def _snapshot(conn,project_uid):
    project=conn.execute('SELECT * FROM projects WHERE project_uid=?',(project_uid,)).fetchone()
    if not project:raise ValueError('Project not found')
    rows=[dict(r) for r in conn.execute('SELECT j.* FROM jobs j JOIN project_assignments a ON a.job_uid=j.job_uid WHERE a.project_id=? ORDER BY j.job_uid',(project['id'],))]
    manual_rows=[dict(r) for r in conn.execute('SELECT * FROM manual_jobs WHERE project_id=? ORDER BY manual_job_id',(project['id'],))]
    existing={r['job_uid']:dict(r) for r in conn.execute('SELECT * FROM project_job_pricing WHERE project_id=?',(project['id'],))}
    retained={r['job_uid']:existing[r['job_uid']] for r in rows if not _completed(r) and r['job_uid'] in existing}
    profiles={str(r['profile_uid'] or r['id']):dict(r) for r in conn.execute('SELECT * FROM filament_profiles ORDER BY id')}
    source={'project':dict(project),'rows':rows,'profiles':profiles,'manual_rows':manual_rows,'retained':retained}
    fingerprint=hashlib.sha256(json.dumps(source,sort_keys=True,default=str).encode()).hexdigest()
    return dict(project),rows,profiles,fingerprint,manual_rows,retained


def preview(project_uid,inputs=None):
    with db.connect_db() as conn:
        db.apply_migrations(conn)
        conn.execute('BEGIN')
        project,rows,profiles,source_fingerprint,manual_rows,retained=_snapshot(conn,project_uid)
    saved_inputs=pricing_inputs(project)
    effective={**project,**(inputs or {})}
    fingerprint=_plan_fingerprint(source_fingerprint,inputs)
    calculations=[];errors=[];unchanged=[]
    for row in rows:
        if not _completed(row):
            overlay=json.loads(retained[row['job_uid']]['calculation_json']) if row['job_uid'] in retained else None
            values=overlay['after'] if overlay else row
            known=values.get('total_cost') not in (None,'')
            time=number(values.get('time_cost') or 0);material=number(values.get('material_cost') or 0)
            total=number(values.get('total_cost') or 0,signed=True)
            costs=dict(time_cost=time if known else 0,material_cost=material if known else 0,
                adjustment=total-time-material if known else 0,total_cost=total if known else 0)
            unchanged.append(dict(job_uid=row['job_uid'],filename=row['filename'],status=row['status'],
                original_price_known=known,retained_overlay=bool(overlay),before=dict(costs),after=dict(costs)))
            continue
        try:calculations.append(calculate_job(row,effective,profiles.get(row.get('filament_profile_id'))))
        except ValueError as exc:errors.append({'job_uid':row['job_uid'],'filename':row['filename'],'error':str(exc)})
    from core.projects import Project,ManualJob,compute_manual_job_cost_components
    project_object=Project(id=project_uid,name=project['name'],hourly_rate_override=project['hourly_rate_override'],
        filament_cost_per_kg_override=project['filament_cost_per_kg_override'],labor_only=bool(project['labor_only']))
    manual=[]
    for row in manual_rows:
        item=ManualJob(manual_job_id=row['manual_job_id'],project_id=project_uid,title=row['title'],hours=row['hours'],
            filament_g=row['filament_g'],cost_override=row['cost_override'])
        costs={key:number(value,signed=True) for key,value in compute_manual_job_cost_components(item,project=project_object).items()}
        manual.append({'title':row['title'],'before':dict(costs),'after':dict(costs)})
    before_totals=reconcile_display([r['before'] for r in calculations+manual+unchanged])
    after_totals=reconcile_display([r['after'] for r in calculations+manual+unchanged])
    return {'project':project,'rows':calculations,'errors':errors,'fingerprint':fingerprint,
            'source_fingerprint':source_fingerprint,'inputs':inputs,'saved_inputs':saved_inputs,
            'reviewed_inputs':pricing_inputs(effective),
            'unknown_originals':sum(not r['original_price_known'] for r in calculations+unchanged),
            'unknown_new':sum(not r['original_price_known'] for r in unchanged),
            'unchanged_rows':unchanged,'retained_overlay_count':sum(r['retained_overlay'] for r in unchanged),
            'unchanged_before':sum((r['before']['display_total_cost'] for r in unchanged),Decimal(0)),
            'unchanged_after':sum((r['after']['display_total_cost'] for r in unchanged),Decimal(0)),
            'known_original_total':sum((r['before']['display_total_cost'] for r in calculations+unchanged if r['original_price_known']),Decimal(0))+sum((r['before']['display_total_cost'] for r in manual),Decimal(0)),
            'manual_rows':manual,'manual_before':sum((r['before']['display_total_cost'] for r in manual),Decimal(0)),
            'manual_after':sum((r['after']['display_total_cost'] for r in manual),Decimal(0)),
            'before_totals':before_totals,'after_totals':after_totals,
            'unknown_weights':len(errors)+sum(r['grams'] is None for r in calculations),
            'grams':sum((r['grams'] for r in calculations if r['grams'] is not None),Decimal(0)),
            'meters':sum((r['meters'] for r in calculations if r['meters'] is not None),Decimal(0))}


def apply(project_uid,fingerprint,inputs=None):
    plan=preview(project_uid,inputs)
    if plan['errors']:raise ValueError('Resolve the pricing calculation errors shown in a fresh preview before applying project pricing.')
    if not plan['rows']:raise ValueError('No completed prints are available for project pricing')
    if fingerprint!=plan['fingerprint']:raise ValueError('Pricing or membership changed. Review a fresh preview before applying')
    with db.connect_db() as conn:
        db.apply_migrations(conn);conn.execute('BEGIN IMMEDIATE')
        project,_rows,_profiles,current,_manual,_retained=_snapshot(conn,project_uid)
        if _plan_fingerprint(current,inputs)!=fingerprint:raise ValueError('Source changed while applying. Preview again')
        conn.execute('DELETE FROM project_job_pricing WHERE project_id=? AND job_uid IN ('+','.join('?' for _ in plan['rows'])+')',
            (project['id'],*[row['job_uid'] for row in plan['rows']]))
        for row in plan['rows']:
            row['reviewed_inputs']=plan['reviewed_inputs']
            row['saved_inputs']=plan['saved_inputs']
            row['source_fingerprint']=plan['source_fingerprint']
            conn.execute('INSERT INTO project_job_pricing VALUES (?,?,?,?,?)',
                (project['id'],row['job_uid'],json.dumps(row,default=str),fingerprint,db._utc_now_iso()))
        from core import system_events
        system_events.emit_event('activity','Project pricing snapshot applied',
            f"Applied project-only pricing to {len(plan['rows'])} tracked jobs; historical prices retained.",
            meta={'project_uid':project_uid,'fingerprint':fingerprint,'before':plan['before_totals'],'after':plan['after_totals'],
                'comparisons':plan['rows'],'manual_unchanged':plan['manual_rows'],'other_outcomes_unchanged':plan['unchanged_rows'],
                'reviewed_inputs':plan['reviewed_inputs'],'saved_inputs':plan['saved_inputs']},connection=conn)
    return len(plan['rows'])


def overlay_status(project_uid,overlays):
    if not overlays:return {'active':False,'stale':False}
    with db.connect_db() as conn:
        db.apply_migrations(conn);conn.execute('BEGIN')
        project,rows,profiles,current,_manual,_retained=_snapshot(conn,project_uid)
        fingerprints={r['job_uid']:r['source_fingerprint'] for r in conn.execute('SELECT job_uid,source_fingerprint FROM project_job_pricing WHERE project_id=?',(project['id'],))}
    stale=any(row.get('saved_inputs')!=pricing_inputs(project) for row in overlays.values() if 'saved_inputs' in row)
    # Old snapshots did not record reviewed inputs. Their original fingerprint
    # still detects changed project defaults, membership, job data or profiles.
    stale=stale or any(row.get('source_fingerprint',fingerprints.get(uid))!=current for uid,row in overlays.items())
    return {'active':True,'stale':stale}


def saved_overlays(project_uid):
    with db.connect_db() as conn:
        db.apply_migrations(conn)
        return {r['job_uid']:json.loads(r['calculation_json']) for r in conn.execute('SELECT pp.* FROM project_job_pricing pp JOIN projects p ON p.id=pp.project_id JOIN project_assignments a ON a.project_id=pp.project_id AND a.job_uid=pp.job_uid WHERE p.project_uid=?',(project_uid,))}


def clear(project_uid):
    with db.connect_db() as conn:
        db.apply_migrations(conn)
        changed=conn.execute('DELETE FROM project_job_pricing WHERE project_id=(SELECT id FROM projects WHERE project_uid=?)',(project_uid,)).rowcount
        if changed:
            from core import system_events
            system_events.emit_event('activity','Project historical price view restored',
                f'Removed {changed} project-only pricing snapshots; saved job prices were retained.',meta={'project_uid':project_uid,'count':changed},connection=conn)
