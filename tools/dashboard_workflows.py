"""Browser mutations confined to browser_dashboard_audit's disposable server."""
from decimal import Decimal
import math
import time
import uuid
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing


def exercise(page,context,base,project,data,artifacts):
    def query(sql,*args):
        with closing(sqlite3.connect(Path(data)/'kcd.db')) as conn:
            conn.row_factory=sqlite3.Row
            return [dict(row) for row in conn.execute(sql,args)]
    def form(action,within=None):
        return (within or page).locator('form').filter(has=page.locator('input[name=action][value="'+action+'"]'))
    def click(control):
        with page.expect_navigation(wait_until='domcontentloaded'):control.click()
    def open_project(uid):
        page.goto(base+'/projects?edit_project='+uid)
        panel=page.locator('[id="proj-'+uid+'"]')
        if not panel.is_visible():page.locator('[data-bs-target="#proj-'+uid+'"]').click()
        panel.wait_for(state='visible')
        return panel
    # Read-only current observations must not modify persisted event state.
    live_before=query("SELECT value_json FROM user_settings WHERE key='live_jobs'")
    response=context.request.get(base+'/api/printer-status');assert response.ok
    states=response.json()['printers']
    for name,state in [('Idle','standby'),('Printing','printing'),('Paused','paused'),('Cancelled','cancelled'),('Completed','complete'),('Offline','disconnected')]:assert states[name]['state']==state,(name,states[name])
    assert states['Cancelled']['tracking_warning']
    assert abs(states['Printing']['total_cost']-(2.5+600/3600))<1e-8
    assert states['Paused']['time_cost']==states['Printing']['time_cost']
    assert live_before==query("SELECT value_json FROM user_settings WHERE key='live_jobs'")
    page.goto(base+'/')
    page.keyboard.press('Tab');assert page.get_by_role('link',name='Skip to main content').evaluate('(e)=>document.activeElement===e')
    page.set_viewport_size({'width':390,'height':844})
    scroll=page.locator('#historyPostForm .table-responsive');scroll.focus();page.keyboard.press('ArrowRight')
    page.wait_for_function("document.querySelector('#historyPostForm .table-responsive').scrollLeft>0")
    page.set_viewport_size({'width':1440,'height':1000})
    page.wait_for_function("document.querySelector('[data-kcd-printer=Cancelled] [data-live-state]').textContent==='CANCELLED'")
    assert 'differs' in page.locator('[data-kcd-printer=Cancelled] [data-live-message]').inner_text()
    # Page-scoped tab/session policy: refresh preserves IDs, pagination resets.
    boxes=page.locator('#historyPostForm input[name=delete_rows]');uid=boxes.first.input_value();boxes.first.check()
    page.reload();assert page.locator('#historyPostForm input[value="'+uid+'"]').is_checked()
    page.locator('#history-table th[data-col=filename]').click()
    assert page.locator('#historyPostForm input[value="'+uid+'"]').is_checked()
    page.get_by_role('button',name='Clear Selection',exact=True).click();assert page.locator('#historyPostForm input:checked').count()==0
    boxes.first.check();page.goto(base+'/?history_page=2');assert page.locator('#historyPostForm input:checked').count()==0
    page.goto(base+'/');assert page.locator('#historyPostForm input:checked').count()==0
    page.locator('#historyPerPage').select_option('100');page.wait_for_url('**history_per_page=100*')
    assert page.locator('#historyPostForm input[name=delete_rows]').count()==100
    # Existing RaRa acceptance: saved historical totals, project-only snapshot.
    originals=query('SELECT * FROM jobs ORDER BY job_uid')
    page.goto(base+'/projects/'+project+'/pricing')
    assert '40.83' in page.locator('tfoot').inner_text()
    assert '2721.882' in page.locator('body').inner_text() or '2721.882' in str(sum(Decimal(str(r['filament_grams'])) for r in query("SELECT filament_grams FROM jobs WHERE job_uid LIKE 'mask-%'")))
    click(page.get_by_role('button',name='Apply project-adjusted snapshot',exact=True))
    assert len(query('SELECT * FROM project_job_pricing'))==15
    assert query('SELECT * FROM jobs ORDER BY job_uid')==originals
    page.goto(base+'/projects/'+project+'/pricing');click(page.get_by_role('button',name='Use saved historical costs',exact=True))
    assert not query('SELECT * FROM project_job_pricing')
    # Create with a project profile; manual/planned costs are distinct actual/estimated views.
    page.goto(base+'/projects');create=form('create_project')
    create.locator('[name=name]').fill("Maker's browser workflow")
    create.locator('[name=hourly_rate_override]').fill('0')
    create.locator('[name=project_filament_profile_id]').select_option('petg-15')
    click(create.get_by_role('button',name='Create',exact=True))
    created=query('SELECT * FROM projects WHERE name=?',"Maker's browser workflow")[0];pid=created['project_uid']
    assert created['hourly_rate_override']==0 and created['filament_cost_per_kg_override']==15
    assert json.loads(created['filament_price_json'])['profile_id']=='petg-15'
    panel=open_project(pid);edit=form('update_project',panel);edit.locator('[name=notes]').fill('Verified through actual forms');click(edit.get_by_role('button',name='Save',exact=True))
    assert query('SELECT notes FROM projects WHERE project_uid=?',pid)[0]['notes']=='Verified through actual forms'
    # Assign and remove exactly one existing job; history remains unchanged.
    assignment=page.locator('form').filter(has=page.locator('button[value=assign_jobs]'))
    assigned=assignment.locator('input[name=job_uid]').first.input_value()
    assignment.locator('input[name=job_uid]').first.check();assignment.locator('[name=project_id]').select_option(pid)
    click(assignment.get_by_role('button',name='Assign Selected',exact=True))
    assert query('SELECT project_id FROM project_assignments WHERE job_uid=?',assigned)[0]['project_id']==created['id']
    panel=open_project(pid);remove=form('unassign_job',panel);click(remove.get_by_role('button',name='Unassign',exact=True))
    assert not query('SELECT * FROM project_assignments WHERE job_uid=?',assigned)
    panel=open_project(pid)
    page.evaluate("window.manualModalReady=false;document.getElementById('kcdManualJobModal').addEventListener('shown.bs.modal',()=>window.manualModalReady=true,{once:true})")
    panel.get_by_role('button',name='Add Manual Job',exact=True).click()
    modal=page.get_by_role('dialog');modal.wait_for(state='visible')
    page.wait_for_function('window.manualModalReady')
    assert modal.get_attribute('aria-labelledby')=='kcdManualJobTitle'
    page.keyboard.press('Escape');modal.wait_for(state='hidden')
    panel.get_by_role('button',name='Add Manual Job',exact=True).click();modal.wait_for(state='visible')
    modal.get_by_label('Description',exact=True).fill('Hand finishing')
    modal.get_by_label('Hours',exact=True).fill('1')
    modal.get_by_label('Filament (g)',exact=True).fill('1000')
    modal.get_by_label('Cost override ($)',exact=True).fill('2')
    click(modal.get_by_role('button',name='Save',exact=True))
    manual=query('SELECT * FROM manual_jobs WHERE project_id=?',created['id'])[0]
    assert manual['cost_override']==2 and manual['filament_g']==1000
    panel=open_project(pid);assert '-$13.00' in panel.inner_text()
    panel.locator('[data-kcd-manual-open=edit][data-manual-job-id="'+manual['manual_job_id']+'"]').click()
    modal=page.get_by_role('dialog');modal.get_by_label('Cost override ($)',exact=True).fill('0')
    click(modal.get_by_role('button',name='Save',exact=True))
    assert query('SELECT cost_override FROM manual_jobs WHERE manual_job_id=?',manual['manual_job_id'])[0]['cost_override']==0
    panel=open_project(pid)
    upload=form('upload_plan_gcode',panel)
    upload.locator('[name=gcode_file]').set_input_files({'name':'planned.gcode','mimeType':'text/plain','buffer':b'; estimated printing time (normal mode) = 1h\n; filament used [g] = 500\nG1 X0\n'})
    upload.locator('[name=manual_time_hours]').fill('1');upload.locator('[name=manual_filament_g]').fill('500')
    click(upload.get_by_role('button',name='Add Planned Item',exact=True))
    plan=query('SELECT * FROM planned_items WHERE project_id=?',created['id'])[0]
    assert abs(plan['est_cost']-7.5)<1e-8,plan
    panel=open_project(pid);panel.locator('[data-kcd-plan-open=edit][data-plan-id="'+plan['plan_id']+'"]').click()
    page.locator('#kcdPlanFilamentG').fill('750')
    click(page.get_by_role('dialog').get_by_role('button',name='Save',exact=True))
    assert abs(query('SELECT est_cost FROM planned_items WHERE plan_id=?',plan['plan_id'])[0]['est_cost']-11.25)<1e-8
    panel=open_project(pid);click(form('convert_plan_to_manual',panel).get_by_role('button',name='Convert → Manual',exact=True))
    converted=query('SELECT * FROM planned_items WHERE plan_id=?',plan['plan_id'])[0]
    assert converted['converted_to_manual_job_id'] and converted['status']=='fulfilled'
    assert len(query('SELECT * FROM manual_jobs WHERE project_id=?',created['id']))==2
    panel=open_project(pid);page.once('dialog',lambda dialog:dialog.accept());click(form('delete_plan_item',panel).get_by_role('button',name='Delete',exact=True))
    assert not query('SELECT * FROM planned_items WHERE plan_id=?',plan['plan_id'])
    assert len(query('SELECT * FROM manual_jobs WHERE project_id=?',created['id']))==2
    panel=open_project(pid);deletion=form('delete_manual_job',panel).filter(has=page.locator('input[name=manual_job_id][value="'+manual['manual_job_id']+'"]'))
    page.once('dialog',lambda dialog:dialog.accept());click(deletion.get_by_role('button',name='Delete',exact=True))
    assert not query('SELECT * FROM manual_jobs WHERE manual_job_id=?',manual['manual_job_id'])
    # Project defaults can be explicitly cleared without modifying saved history.
    panel=open_project(pid);edit=form('update_project',panel)
    edit.locator('[name=project_filament_profile_id]').select_option('');edit.locator('[name=pricing_amount]').fill('');edit.locator('[name=hourly_rate_override]').fill('')
    click(edit.get_by_role('button',name='Save',exact=True))
    cleared=query('SELECT * FROM projects WHERE project_uid=?',pid)[0]
    assert cleared['hourly_rate_override'] is None and cleared['filament_cost_per_kg_override'] is None
    # Delete project after reviewing the confirmation; jobs are never deleted.
    panel=open_project(pid);confirmations=[]
    page.once('dialog',lambda dialog:(confirmations.append(dialog.message),dialog.accept()));click(form('delete_project',panel).get_by_role('button',name='Delete Project',exact=True))
    assert confirmations and "Maker's browser workflow" in confirmations[0]
    assert not query('SELECT * FROM projects WHERE project_uid=?',pid)
    assert query('SELECT * FROM jobs ORDER BY job_uid')==originals
    # Spool inputs drive actual completed-job calculations for several materials.
    created_profiles={}
    for material,price,spool,density,diameter in [('PETG',15,1000,1.25,1.75),('PLA',9,500,1.24,1.75),('ABS',13.5,250,1.04,2.85)]:
        page.goto(base+'/settings/profiles');add=form('add_filament_profile')
        name='Browser '+material+' spool'
        add.locator('[name=profile_name]').fill(name);add.locator('[name=material]').fill(material)
        assert add.locator('[name=pricing_mode]').input_value()=='spool'
        add.locator('[name=pricing_amount]').fill(str(price));add.locator('[name=net_spool_grams]').fill(str(spool))
        add.locator('[name=conversion_basis]').select_option('geometry');add.locator('[name=density_g_cm3]').fill(str(density));add.locator('[name=diameter_mm]').fill(str(diameter))
        click(add.get_by_role('button',name='Add',exact=True))
        saved=query('SELECT * FROM filament_profiles WHERE name=?',name)[0];fid=saved['profile_uid']
        created_profiles[material]=fid
        factor=math.pi*diameter**2/4*density
        assert abs(saved['grams_per_meter']-factor)<1e-10
        page.goto(base+'/settings/printers')
        mapping=form('set_active_filament_profile').filter(has=page.locator('input[name=printer][value=Idle]'))
        mapping.locator('[name=profile_id]').select_option(fid);click(mapping.get_by_role('button',name='Set',exact=True))
        hourly=form('set_active_rate_profile').filter(has=page.locator('input[name=printer][value=Idle]'))
        hourly.locator('[name=rate_profile_id]').select_option('free');click(hourly.get_by_role('button',name='Apply',exact=True))
        response=context.request.post(base+'/log-print',headers={'X-API-Key':'disposable-ux-key'},data=dict(printer='Idle',filename=name+'.gcode',duration_seconds=3600,filament_mm=100000,timestamp=time.time(),event_id=str(uuid.uuid4())))
        assert response.ok,response.text()
        job=query('SELECT * FROM jobs WHERE filename=?',name+'.gcode')[0]
        expected=100*factor*price/spool
        assert job['time_cost']==0 and abs(job['material_cost']-expected)<1e-8 and abs(job['total_cost']-expected)<1e-8,job
        assert job['filament_mode']=='per_kg' and abs(job['filament_rate']-price*1000/spool)<1e-10
        print(material+' independent spool expectation: '+str(expected))
    # Active profile deletion must not break readiness or switch future prices.
    saved_jobs=query('SELECT * FROM jobs ORDER BY job_uid')
    page.goto(base+'/settings/profiles')
    deletion=form('delete_filament_profile').filter(has=page.locator('input[name=profile_id][value="'+created_profiles['ABS']+'"]'))
    page.once('dialog',lambda dialog:dialog.accept());click(deletion.get_by_role('button',name='Delete',exact=True))
    assert 'Select replacement or base pricing' in page.locator('body').inner_text()
    deletion=form('delete_rate_profile').filter(has=page.locator('input[name=rate_profile_id][value=free]'))
    page.once('dialog',lambda dialog:dialog.accept());click(deletion.get_by_role('button',name='Delete',exact=True))
    assert 'Select replacement or base pricing' in page.locator('body').inner_text()
    assert context.request.get(base+'/health').json()['ready']
    # Hourly profile CRUD preserves zero; inactive filament deletion retains jobs.
    add=form('add_rate_profile');add.locator('[name=rate_profile_name]').fill("Maker's zero hours");add.locator('[name=rate_profile_rate]').fill('0')
    click(add.get_by_role('button',name='Add',exact=True))
    hourly=query('SELECT * FROM hourly_rate_profiles WHERE name=?',"Maker's zero hours")[0];rid=hourly['profile_uid'];assert hourly['rate_per_hour']==0
    page.locator('input[form="rate-update-'+rid+'"][name=rate_profile_rate]').fill('2.5')
    click(page.locator('button[form="rate-update-'+rid+'"]'))
    assert query('SELECT rate_per_hour FROM hourly_rate_profiles WHERE profile_uid=?',rid)[0]['rate_per_hour']==2.5
    deletion=form('delete_rate_profile').filter(has=page.locator('input[name=rate_profile_id][value="'+rid+'"]'))
    confirmations=[];page.once('dialog',lambda dialog:(confirmations.append(dialog.message),dialog.accept()));click(deletion.get_by_role('button',name='Delete',exact=True))
    assert confirmations and "Maker's zero hours" in confirmations[0]
    assert not query('SELECT * FROM hourly_rate_profiles WHERE profile_uid=?',rid)
    petg=created_profiles['PETG'];page.locator('input[form="profile-update-'+petg+'"][name=profile_name]').fill("Maker's PETG spool")
    click(page.locator('button[form="profile-update-'+petg+'"]'))
    assert query('SELECT name FROM filament_profiles WHERE profile_uid=?',petg)[0]['name']=="Maker's PETG spool"
    deletion=form('delete_filament_profile').filter(has=page.locator('input[name=profile_id][value="'+petg+'"]'))
    page.once('dialog',lambda dialog:dialog.accept());click(deletion.get_by_role('button',name='Delete',exact=True))
    assert not query('SELECT * FROM filament_profiles WHERE profile_uid=?',petg)
    assert query('SELECT * FROM jobs ORDER BY job_uid')==saved_jobs
    # SQL-only backup/display/pause controls are actual UI actions.
    page.goto(base+'/settings/pause');page.locator('#pause_include_default').check();click(page.get_by_role('button',name='Save pause settings',exact=True))
    assert json.loads(query("SELECT value_json FROM user_settings WHERE key='display_settings'")[0]['value_json'])['pause_include_paused_time_default']
    page.goto(base+'/settings/other')
    display=form('update_columns').filter(has=page.locator('input[name=table][value=recalc_jobs]'))
    display.locator('input[name=columns][value=material_cost]').uncheck()
    click(display.get_by_role('button',name='Save Recalculate Columns',exact=True))
    assert 'material_cost' not in json.loads(query("SELECT value_json FROM user_settings WHERE key='display_settings'")[0]['value_json'])['tables']['recalc_jobs']['visible_columns']
    page.goto(base+'/recalculate');material_header=page.locator('#recalc-table th[data-col=material_cost]')
    assert material_header.count()==1 and not material_header.is_visible()
    page.goto(base+'/settings/other');click(form('backup_now').get_by_role('button',name='Backup now',exact=True))
    assert list(Path(data,'backups').glob('kcd_backup_*.tar.gz'))
    assert 'Backup' in page.locator('body').inner_text()
    with closing(sqlite3.connect(Path(data)/'kcd.db')) as conn:
        assert conn.execute('pragma integrity_check').fetchone()[0]=='ok';assert not conn.execute('pragma foreign_key_check').fetchall()
    page.screenshot(path=str(artifacts/'workflow-complete.png'),full_page=True)
    job_before = query("SELECT * FROM jobs WHERE job_uid='mask-0'")
    assert job_before
    page.goto(base + '/inventory')
    create = form('create')
    create.get_by_label('Material', exact=True).fill('Test PLA')
    create.get_by_label('Net weight (g)', exact=True).fill('100')
    create.get_by_label('Opening balance (g)', exact=True).fill('80')
    create.get_by_label('Low-stock threshold (g)', exact=True).fill('10')
    click(create.get_by_role('button', name='Add spool'))
    page.get_by_text('Assign printer', exact=True).click()
    assignment = form('assign')
    assignment.get_by_label('Printer', exact=True).select_option(label='Idle')
    click(assignment.get_by_role('button', name='Save assignment'))
    for _ in range(2):
        page.get_by_text('Record verified job use', exact=True).click()
        consumption = form('consume')
        consumption.get_by_label('History job UID').fill('mask-0')
        consumption.get_by_label('Measured consumption (g)').fill('77.5')
        consumption.get_by_role('checkbox').check()
        click(consumption.get_by_role('button', name='Deduct measured amount'))
    assert page.get_by_text('2.500 g remaining', exact=True).is_visible()
    assert page.get_by_text('Low stock', exact=True).is_visible()
    assert len(query("SELECT * FROM filament_stock_events WHERE kind='verified_consumption'")) == 1
    assert job_before == query("SELECT * FROM jobs WHERE job_uid='mask-0'")
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(artifacts / 'inventory-mobile.png'), full_page=True)
    print('Dashboard workflows passed: six live states, retained SQL state, History session policy, RaRa project overlay, project CRUD, assignment/removal, signed manual adjustment, planned upload, cleared overrides, pause save and backup')
