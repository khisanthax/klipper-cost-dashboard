"""Previewable project-only cost overlays. Recorded jobs are never overwritten."""
from decimal import Decimal
import hashlib
import json
from core import db
from core.filament import conversion,material_cost,number,reconcile_display


def calculate_job(row,project,profile=None,*,factor_override=None):
    measurement=conversion(row,profile,factor_override=factor_override)
    time=number(row.get('time_cost') or 0);material=number(row.get('material_cost') or 0)
    original_total=number(row.get('total_cost') or 0,signed=True)
    adjustment=original_total-time-material
    before={'time_cost':time,'material_cost':material,'adjustment':adjustment,'total_cost':original_total}
    source=[]
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
            'after':{'time_cost':time,'material_cost':material,'adjustment':adjustment,'total_cost':time+material+adjustment},
            **measurement,'pricing_source':'; '.join(source)}


def _snapshot(conn,project_uid):
    project=conn.execute('SELECT * FROM projects WHERE project_uid=?',(project_uid,)).fetchone()
    if not project:raise ValueError('Project not found')
    rows=[dict(r) for r in conn.execute('SELECT j.* FROM jobs j JOIN project_assignments a ON a.job_uid=j.job_uid WHERE a.project_id=? ORDER BY j.job_uid',(project['id'],))]
    profiles={str(r['profile_uid'] or r['id']):dict(r) for r in conn.execute('SELECT * FROM filament_profiles ORDER BY id')}
    source={'project':dict(project),'rows':rows,'profiles':profiles}
    fingerprint=hashlib.sha256(json.dumps(source,sort_keys=True,default=str).encode()).hexdigest()
    return dict(project),rows,profiles,fingerprint


def preview(project_uid):
    with db.connect_db() as conn:
        db.apply_migrations(conn)
        project,rows,profiles,fingerprint=_snapshot(conn,project_uid)
    calculations=[];errors=[]
    for row in rows:
        try:calculations.append(calculate_job(row,project,profiles.get(row.get('filament_profile_id'))))
        except ValueError as exc:errors.append({'job_uid':row['job_uid'],'filename':row['filename'],'error':str(exc)})
    before_totals=reconcile_display([r['before'] for r in calculations])
    after_totals=reconcile_display([r['after'] for r in calculations])
    return {'project':project,'rows':calculations,'errors':errors,'fingerprint':fingerprint,
            'before_totals':before_totals,'after_totals':after_totals,
            'unknown_weights':len(errors)+sum(r['grams'] is None for r in calculations),
            'grams':sum((r['grams'] for r in calculations if r['grams'] is not None),Decimal(0)),
            'meters':sum((r['meters'] for r in calculations if r['meters'] is not None),Decimal(0))}


def apply(project_uid,fingerprint):
    plan=preview(project_uid)
    if plan['errors']:raise ValueError('Resolve missing filament conversions before applying project pricing')
    if fingerprint!=plan['fingerprint']:raise ValueError('Pricing or membership changed. Review a fresh preview before applying')
    with db.connect_db() as conn:
        db.apply_migrations(conn);conn.execute('BEGIN IMMEDIATE')
        project,_rows,_profiles,current=_snapshot(conn,project_uid)
        if current!=fingerprint:raise ValueError('Source changed while applying. Preview again')
        conn.execute('DELETE FROM project_job_pricing WHERE project_id=?',(project['id'],))
        for row in plan['rows']:
            conn.execute('INSERT INTO project_job_pricing VALUES (?,?,?,?,?)',
                (project['id'],row['job_uid'],json.dumps(row,default=str),fingerprint,db._utc_now_iso()))
        from core import system_events
        system_events.emit_event('activity','Project pricing snapshot applied',
            f"Applied project-only pricing to {len(plan['rows'])} tracked jobs; historical prices retained.",
            meta={'project_uid':project_uid,'fingerprint':fingerprint,'before':plan['before_totals'],'after':plan['after_totals'],'comparisons':plan['rows']},connection=conn)
    return len(plan['rows'])


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
